import streamlit as st
import sqlite3
import pandas as pd
from datetime import date
import calendar 
import json 
import base64 
import io

# =================================================================================================
# IMPORTACIONES REPORTLAB 
# =================================================================================================
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape 


# =================================================================================================
# CONFIGURACIÓN Y CONSTANTES
# =================================================================================================
DB_PATH = "minerva.db"
BASE_LITROS = 200.0 # Cantidad base de la receta original (Litros)
# CONSTANTES CLAVE BASADAS EN LA LÓGICA DE PRESUPUESTO
RECETAS_DIARIAS = 8.0 
DIAS_HABILES_FIJOS_MENSUAL = 20.0 
VOLUMEN_MENSUAL_AUTOMATICO = RECETAS_DIARIAS * DIAS_HABILES_FIJOS_MENSUAL * BASE_LITROS # 32000.0 L

# =================================================================================================
# UTILIDADES DB
# =================================================================================================

def get_connection():
    """Establece la conexión a la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row 
    return conn

def fetch_df(query, params=()):
    """Ejecuta una consulta SELECT y devuelve los resultados como un DataFrame de Pandas."""
    conn = get_connection()
    try:
        df = pd.read_sql_query(query, conn, params=params)
    except sqlite3.Error as e:
        # En el entorno de Streamlit, se debe usar st.error si el código se ejecuta en un servidor
        # print(f"Error al ejecutar la consulta: {e}") 
        return pd.DataFrame()
    finally:
        conn.close()
    return df

def get_categoria_id_by_name(category_name):
    """Busca el ID de una categoría por su nombre."""
    query = "SELECT id FROM categorias_imputacion WHERE nombre = ?"
    df = fetch_df(query, (category_name,))
    if df.empty:
        return None
    return df.iloc[0, 0]

# NOTA: La función 'get_detalle_gastos_mensual' fue eliminada ya que el total ahora se calcula
# directamente desde el st.data_editor en el sidebar.

def create_tables_if_not_exists(conn):
    """Crea las tablas de Clientes y Presupuestos si no existen."""
    cursor = conn.cursor()
    
    # Tabla Clientes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE
        )
    """)
    
    # Tabla Presupuestos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS presupuestos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            porcentaje_ganancia REAL NOT NULL, 
            volumen_total_litros REAL NOT NULL,
            costo_total_ars REAL NOT NULL,
            precio_final_ars REAL NOT NULL,
            detalle_simulaciones_json TEXT,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id)
        )
    """)
    
    conn.commit()

def get_or_create_client(conn, client_name):
    """Obtiene el ID del cliente o lo crea si no existe."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM clientes WHERE nombre = ?", (client_name,))
    row = cursor.fetchone()
    if row:
        return row['id']
    else:
        cursor.execute("INSERT INTO clientes (nombre) VALUES (?)", (client_name,))
        conn.commit()
        return cursor.lastrowid

def save_presupuesto(conn, cliente_id, porcentaje_ganancia, volumen_total_litros, costo_total_ars, precio_final_ars, detalle_simulaciones_json):
    """Guarda el presupuesto final en la base de datos."""
    fecha_hoy = date.today().isoformat()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO presupuestos (cliente_id, fecha, porcentaje_ganancia, volumen_total_litros, costo_total_ars, precio_final_ars, detalle_simulaciones_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (cliente_id, fecha_hoy, porcentaje_ganancia, volumen_total_litros, costo_total_ars, precio_final_ars, detalle_simulaciones_json))
    conn.commit()
    return cursor.lastrowid 


# =================================================================================================
# UTILIDAD DB: GASTOS OPERATIVOS
# =================================================================================================

def get_detalle_gastos_operativos_mensual(mes: int, anio: int):
    """Obtiene el detalle de gastos operativos para un mes y año específico."""
    query = f"""
    SELECT
        g.fecha_factura AS Fecha,
        ci.nombre AS Categoria,
        g.beneficiario_nombre AS Beneficiario,
        g.importe_total AS Monto_ARS
    FROM
        gastos g
    JOIN
        categorias_imputacion ci ON g.categoria_id = ci.id
    WHERE
        strftime('%Y', g.fecha_factura) = '{anio}' AND strftime('%m', g.fecha_factura) = '{mes:02d}'
    ORDER BY
        g.fecha_factura, ci.nombre;
    """
    df_gastos = fetch_df(query) 
    return df_gastos

# =================================================================================================
# FUNCIONES DE COSTEO DE MATERIA PRIMA (SIN CAMBIOS EN LA LÓGICA)
# =================================================================================================

def obtener_todas_materias_primas(conn):
    """Obtiene la lista completa de materias primas disponibles con IDs."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, unidad FROM materias_primas ORDER BY nombre")
    return [dict(row) for row in cursor.fetchall()]

def obtener_ingredientes_receta(conn, receta_id):
    """Obtiene los ingredientes actuales de una receta."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            ri.id AS id_ingrediente_receta,
            mp.id AS materia_prima_id,
            mp.nombre,
            mp.unidad,
            ri.cantidad
        FROM receta_ingredientes ri
        JOIN materias_primas mp ON ri.materia_prima_id = mp.id
        WHERE ri.receta_id = ?
        ORDER BY mp.nombre
    """, (receta_id,))
    return [dict(row) for row in cursor.fetchall()]

def obtener_precio_actual_materia_prima(conn, materia_prima_id):
    """
    Obtiene el último precio unitario y la cotización USD registrada.
    """
    if materia_prima_id == -1:
        return 0.0, 0.0, 0.0, 1.0

    cursor = conn.cursor()
    cursor.execute("""
        SELECT precio_unitario, cotizacion_usd, moneda
        FROM compras_materia_prima
        WHERE materia_prima_id = ?
        ORDER BY fecha DESC, id DESC
        LIMIT 1
    """, (materia_prima_id,))
    
    compra = cursor.fetchone()
    if compra:
        precio_base = compra['precio_unitario']
        costo_flete = 0.0 
        otros_costos = 0.0 
        cotizacion_usd_reg = compra['cotizacion_usd'] if compra['cotizacion_usd'] is not None else 1.0
        moneda = compra['moneda']
        
        if moneda == 'ARS' and cotizacion_usd_reg <= 1.0:
            return precio_base, costo_flete, otros_costos, 1.0
        else:
            return precio_base, costo_flete, otros_costos, cotizacion_usd_reg
    else:
        cursor.execute("""
            SELECT precio_unitario, costo_flete, otros_costos, cotizacion_usd
            FROM precios_materias_primas
            WHERE materia_prima_id = ?
            ORDER BY fecha DESC
            LIMIT 1
        """, (materia_prima_id,))
        precio = cursor.fetchone()
        if precio:
            return precio['precio_unitario'], precio['costo_flete'], precio['otros_costos'], precio['cotizacion_usd']
        
        return 0.0, 0.0, 0.0, 1.0

def calcular_costo_total(ingredientes_df, cotizacion_dolar_actual, conn):
    """
    Calcula el costo total SÓLO de la Materia Prima en ARS.
    """
    detalle_costo = []
    
    RECARGO_FIJO_USD_PERCENT = 0.03 # 3%
    
    costo_total_mp_ars = 0.0
    costo_total_recargo_mp_ars = 0.0 
    
    for index, ingrediente in ingredientes_df.iterrows():
        materia_prima_id = ingrediente["materia_prima_id"]
        cantidad_usada = ingrediente["cantidad_simulada"]
        nombre_mp = ingrediente["Materia Prima"]
        
        precio_manual_unitario = ingrediente.get('precio_unitario_manual', 0.0)
        cotizacion_manual = ingrediente.get('cotizacion_usd_manual', 1.0)

        cotizacion_usd_reg_bd = 1.0 

        precio_base_usd_final = 0.0 
        costo_unitario_ars_registrado = 0.0 

        if precio_manual_unitario > 0.0:
            if cotizacion_manual > 1.0:
                precio_base_usd_final = precio_manual_unitario
                cotizacion_usd_reg_bd = cotizacion_manual
            else:
                costo_unitario_ars_registrado = precio_manual_unitario
        
        elif materia_prima_id != -1:
            precio_unitario_reg_base, _, _, cotizacion_usd_reg_bd = \
                obtener_precio_actual_materia_prima(conn, materia_prima_id)
            
            if cotizacion_usd_reg_bd > 1.0:
                precio_base_usd_final = precio_unitario_reg_base 
            else:
                costo_unitario_ars_registrado = precio_unitario_reg_base
                
        recargo_unitario_ars = 0.0
        recargo_unitario_usd = 0.0 
        
        if precio_base_usd_final > 0.0:
            recargo_unitario_usd = precio_base_usd_final * RECARGO_FIJO_USD_PERCENT
            recargo_unitario_ars = recargo_unitario_usd * cotizacion_dolar_actual
            costo_base_mp_ars_real = precio_base_usd_final * cotizacion_dolar_actual
            moneda_origen = f'USD ({cotizacion_usd_reg_bd:.2f})'
        else:
            costo_base_mp_ars_real = costo_unitario_ars_registrado
            moneda_origen = 'ARS (Fijo)'
            precio_base_usd_final = 0.0
        
        costo_base_mp_total_ars = cantidad_usada * costo_base_mp_ars_real
        recargo_mp_total_ars_ingrediente = cantidad_usada * recargo_unitario_ars
        
        costo_unitario_final_ars = costo_base_mp_ars_real + recargo_unitario_ars
        costo_ingrediente_total = cantidad_usada * costo_unitario_final_ars
        
        costo_unitario_usd_total = precio_base_usd_final + recargo_unitario_usd 
        costo_total_usd_ingrediente = cantidad_usada * costo_unitario_usd_total 
        
        costo_total_mp_ars += costo_base_mp_total_ars
        costo_total_recargo_mp_ars += recargo_mp_total_ars_ingrediente 
        
        detalle_costo.append({
            "Materia Prima": nombre_mp,
            "Unidad": ingrediente["Unidad"],
            "Cantidad (Simulada)": cantidad_usada,
            "Moneda Origen": moneda_origen, 
            "Costo Unit. ARS (Base)": costo_base_mp_ars_real, 
            "Recargo 3% ARS (Unit.)": recargo_unitario_ars, 
            "Costo Unit. ARS (Total)": costo_unitario_final_ars,
            "Costo Total ARS": costo_ingrediente_total,
            "Costo Unit. USD (Base)": precio_base_usd_final, 
            "Recargo 3% USD (Unit.)": recargo_unitario_usd, 
            "Costo Unit. USD (Total)": costo_unitario_usd_total, 
            "Costo Total USD": costo_total_usd_ingrediente 
        })

    detalle_df = pd.DataFrame(detalle_costo) 
    costo_mp_total = detalle_df["Costo Total ARS"].sum() 
    
    return costo_mp_total, detalle_df, costo_total_mp_ars, costo_total_recargo_mp_ars


# =================================================================================================
# FUNCIONES DE GENERACIÓN DE REPORTE (PDF con ReportLab) (SIN CAMBIOS)
# =================================================================================================

def generate_pdf_reportlab(data):
    """
    Genera el contenido PDF del presupuesto usando la librería ReportLab.
    """
    
    cliente_nombre = data['cliente_nombre']
    fecha_hoy = date.today().strftime('%d/%m/%Y')
    cotizacion_dolar_actual = data['cotizacion_dolar_actual']
    presupuesto_id = data['presupuesto_id']
    
    precio_unitario_ars_litro_AVG = data['precio_unitario_ars_litro'] 
    precio_final_ars = data['precio_final_ars']
    litros_total_acumulado = data['litros_total_acumulado']
    
    df_detalle_final = data['df_detalle_final_presupuesto'].copy()
    
    precio_unitario_usd_litro_AVG = precio_unitario_ars_litro_AVG / cotizacion_dolar_actual
    precio_final_usd = precio_final_ars / cotizacion_dolar_actual
    
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                            leftMargin=0.8*inch, rightMargin=0.8*inch,
                            topMargin=0.8*inch, bottomMargin=0.8*inch)
    
    story = []
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(name='PresupuestoTitle', fontSize=18, alignment=1, spaceAfter=12, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='PresupuestoHeading2', fontSize=14, alignment=0, spaceAfter=8, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='BodyTextBold', fontSize=12, alignment=0, spaceAfter=6, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='FinalTotalUSD', fontSize=14, alignment=0, spaceAfter=6, fontName='Helvetica-Bold', textColor=colors.blue))
    
    story.append(Paragraph(f"PRESUPUESTO CLIENTE N° {presupuesto_id}", styles['PresupuestoTitle']))
    story.append(Spacer(1, 0.2*inch))
    
    story.append(Paragraph(f"**Cliente:** {cliente_nombre}", styles['BodyTextBold']))
    story.append(Paragraph(f"**Fecha del Presupuesto:** {fecha_hoy}", styles['BodyTextBold']))
    story.append(Paragraph(f"**Cotización del Dólar (Referencia):** ${cotizacion_dolar_actual:,.2f} ARS/USD", styles['BodyTextBold']))
    story.append(Spacer(1, 0.3*inch))
    
    story.append(Paragraph("Detalle del Pedido", styles['PresupuestoHeading2']))
    
    table_data = []
    table_data.append([
        "Producto", 
        "Cantidad (Litros)", 
        "Margen (%)", 
        "Precio x Litro Cliente (ARS/L)", 
        "Precio x Litro Cliente (USD/L)", 
        "Total a Pagar (ARS)",
        "Total a Pagar (USD)" 
    ])
    
    total_width = 10.1 * inch 
    col_widths = [total_width * 0.16, total_width * 0.10, total_width * 0.10, total_width * 0.18, total_width * 0.18, total_width * 0.14, total_width * 0.14]

    for index, row in df_detalle_final.iterrows():
        
        precio_unitario_cliente_ars = row['Precio_Venta_Unitario_ARS']
        precio_unitario_cliente_usd = row['Precio_Venta_Unitario_USD']
        total_a_pagar_ars = row['Precio_Venta_Total_ARS']
        total_a_pagar_usd = row['Precio_Venta_Total_USD'] 

        table_data.append([
            row['Receta'], 
            f"{row['Litros']:,.2f} L", 
            f"{row['Margen_Ganancia']:.2f} %", 
            f"${precio_unitario_cliente_ars:,.2f}", 
            f"USD ${precio_unitario_cliente_usd:,.2f}", 
            f"${total_a_pagar_ars:,.2f}",
            f"USD ${total_a_pagar_usd:,.2f}" 
        ])
    
    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#DBEAFE')), 
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1E3A8A')), 
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10), 
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    
    story.append(table)
    story.append(Spacer(1, 0.5*inch))
    
    story.append(Paragraph(f"**Volumen Total del Pedido:** {litros_total_acumulado:,.2f} Litros", styles['BodyTextBold']))
    
    story.append(Paragraph(f"**Precio Promedio por Litro a Pagar (Cliente):** ${precio_unitario_ars_litro_AVG:,.2f} ARS/L (USD ${precio_unitario_usd_litro_AVG:,.2f}/L)", styles['BodyTextBold']))
    
    story.append(Spacer(1, 0.1*inch))
    
    story.append(Paragraph(f"**TOTAL FINAL A PAGAR: ${precio_final_ars:,.2f} ARS**", 
                            ParagraphStyle(name='FinalTotal', fontSize=14, alignment=0, spaceAfter=6, 
                                           fontName='Helvetica-Bold', textColor=colors.red)))
    
    story.append(Paragraph(f"**TOTAL FINAL A PAGAR: USD ${precio_final_usd:,.2f}**", 
                            styles['FinalTotalUSD']))
    
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph("*Este presupuesto tiene validez de X días y está sujeto a cambios en los costos de materias primas y cotización del dólar a la fecha de facturación.", 
                            ParagraphStyle(name='Footer', fontSize=8, alignment=0, textColor=colors.grey)))

    doc.build(story)
    
    pdf_content = buffer.getvalue()
    buffer.close()
    return pdf_content


# =================================================================================================
# INTERFAZ STREAMLIT (LÓGICA ACTUALIZADA)
# =================================================================================================

def main():
    st.set_page_config(layout="wide")
    st.title("Simulador de Costo de Receta (ARS y USD) 💰 - SOLO SIMULACIÓN")

    # Inicializar Session State
    if 'ingredientes_temporales' not in st.session_state:
        st.session_state.ingredientes_temporales = []
    if 'receta_id_actual' not in st.session_state:
        st.session_state.receta_id_actual = None
    
    if 'costo_total' not in st.session_state:
        st.session_state['costo_total'] = 0.0
        st.session_state['detalle_costo'] = pd.DataFrame()
        st.session_state['litros'] = BASE_LITROS
        st.session_state['dolar'] = 1000.0
        st.session_state['gasto_fijo_mensual'] = 0.0 
        st.session_state['flete_base_200l'] = 5000.0 
        
    if 'simulaciones_presupuesto' not in st.session_state:
        st.session_state['simulaciones_presupuesto'] = []
    
    if 'presupuesto_data_for_print' not in st.session_state:
        st.session_state['presupuesto_data_for_print'] = {}
        
    # NUEVOS ESTADOS PARA LA EDICIÓN DE GASTOS
    if 'gastos_temporales_simulacion' not in st.session_state:
        st.session_state.gastos_temporales_simulacion = []
    # Usaremos esto para almacenar el último total de gastos fijos calculado por el editor/temporales
    if 'gasto_fijo_mensual_total' not in st.session_state:
        st.session_state.gasto_fijo_mensual_total = 0.0
        
    conn = get_connection()
    create_tables_if_not_exists(conn)
    
    # --- Side Bar Configuration (Gasto Fijo, Flete, Overhead, Dólar) ---
    
    # --- FIJAR MES A SEPTIEMBRE (9) ---
    MES_SIMULACION = 9 
    
    st.sidebar.subheader(f"Costos Fijos Operativos (Simulación de {calendar.month_name[MES_SIMULACION].capitalize()})")
    
    # -----------------------------------------------------------
    # 1. ENTRADA DEL AÑO DE SIMULACIÓN
    # -----------------------------------------------------------
    anio_simulacion = st.sidebar.number_input(
        "Año de Gasto Fijo a Simular:", 
        min_value=2020, 
        value=date.today().year, 
        step=1, 
        key="anio_simulacion_value"
    )
    
    # El valor 'gasto_fijo_mensual_auto' ahora se toma del Session State (actualizado por el editor)
    gasto_fijo_mensual_auto = st.session_state.gasto_fijo_mensual_total
    st.session_state['gasto_fijo_mensual'] = gasto_fijo_mensual_auto

    st.sidebar.markdown(f"**Gasto Operativo Total ({calendar.month_name[MES_SIMULACION].capitalize()} {anio_simulacion}):**")
    st.sidebar.success(f"${gasto_fijo_mensual_auto:,.2f} ARS (Calculado con Cambios)")

    # -----------------------------------------------------------
    # 2. SECCIÓN PARA CARGAR GASTO TEMPORAL (NUEVA IMPLEMENTACIÓN)
    # -----------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("➕ Cargar Gasto Temporal (Simulación)")
    
    # Obtener categorías de la DB para el selectbox
    df_categorias = fetch_df("SELECT nombre FROM categorias_imputacion ORDER BY nombre")
    categorias = df_categorias['nombre'].tolist() if not df_categorias.empty else ["Sin Categorías"]
    
    with st.sidebar.form("form_gasto_temporal_sidebar"):
        gasto_categoria = st.selectbox("Categoría:", categorias, key="temp_gasto_categoria")
        gasto_beneficiario = st.text_input("Beneficiario/Descripción:", key="temp_gasto_beneficiario")
        gasto_monto = st.number_input("Monto (ARS):", min_value=0.0, value=1000.0, step=100.0, format="%.2f", key="temp_gasto_monto")
        
        submitted_gasto = st.form_submit_button("Agregar Gasto Temporal")
        
        if submitted_gasto:
            if gasto_monto > 0:
                # Se agrega un gasto temporal con ID para poder limpiar o identificar
                st.session_state.gastos_temporales_simulacion.append({
                    'Fecha': date.today().isoformat(),
                    'Categoria': gasto_categoria,
                    'Beneficiario': gasto_beneficiario if gasto_beneficiario else "Gasto Temporal",
                    'Monto_ARS': gasto_monto,
                    # ID único temporal, necesario para dataframes
                    'ID_Gasto_Unico': f"TEMP_{len(st.session_state.gastos_temporales_simulacion) + 1}", 
                })
                st.success("Gasto temporal agregado. El total se actualizará al abrir/cerrar el detalle.")
                st.rerun()

    # Botón para limpiar los gastos temporales
    if st.sidebar.button("Limpiar Gastos Temporales"):
        st.session_state.gastos_temporales_simulacion = []
        st.session_state.gasto_fijo_mensual_total = 0.0
        st.rerun()
        
    st.sidebar.markdown("---")

    # -----------------------------------------------------------
    # 3. DETALLE DE GASTOS Y EDITOR (IMPLEMENTACIÓN DEL EDITOR)
    # -----------------------------------------------------------
    MES_GASTOS = MES_SIMULACION
    ANIO_GASTOS = anio_simulacion 
    
    # NUEVA FUNCIONALIDAD: DETALLE DE GASTOS con expander y EDITOR
    with st.sidebar.expander(f"Ver/Editar Detalle de Gasto Operativo ({calendar.month_name[MES_GASTOS].capitalize()} {ANIO_GASTOS})"):
        st.markdown(f"**Detalle de Gastos Operativos ({MES_GASTOS:02d}/{ANIO_GASTOS}):**")
        st.info("⚠️ Doble clic en el monto (ARS) para editarlo en la simulación.")
        
        # 1. Obtener el detalle de gastos de la DB
        df_detalle_db = get_detalle_gastos_operativos_mensual(MES_GASTOS, ANIO_GASTOS)
        
        # Agregar una columna de ID único para DB
        if not df_detalle_db.empty:
            df_detalle_db['ID_Gasto_Unico'] = df_detalle_db.index.map(lambda x: f"DB_{x}")
            
        # 2. Convertir gastos temporales a DataFrame
        df_gastos_temporales = pd.DataFrame(st.session_state.gastos_temporales_simulacion)
        
        # 3. Combinar datos
        if not df_detalle_db.empty and not df_gastos_temporales.empty:
            df_detalle_gastos = pd.concat([df_detalle_db, df_gastos_temporales], ignore_index=True)
        elif not df_detalle_db.empty:
            df_detalle_gastos = df_detalle_db
        elif not df_gastos_temporales.empty:
            df_detalle_gastos = df_gastos_temporales
        else:
            df_detalle_gastos = pd.DataFrame()
            
        if not df_detalle_gastos.empty:
            
            # Configurar el editor de datos (data_editor)
            column_config_gastos = {
                "Fecha": st.column_config.TextColumn(disabled=True),
                "Categoria": st.column_config.TextColumn(disabled=True),
                "Beneficiario": st.column_config.TextColumn(disabled=True),
                "ID_Gasto_Unico": st.column_config.TextColumn(disabled=True, help="Identificador único para BD o Temporal."),
                "Monto_ARS": st.column_config.NumberColumn(
                    "Monto (ARS)",
                    min_value=0.0,
                    format="$ %.2f",
                    # CLAVE: Permitir edición de esta columna
                    help="Haga doble clic para editar el monto en la simulación." 
                )
            }
            
            # 4. Mostrar el editor y capturar los cambios
            edited_df_gastos = st.data_editor(
                df_detalle_gastos,
                column_config=column_config_gastos,
                use_container_width=True,
                height=300,
                hide_index=True,
                # Solo mostramos columnas relevantes para el usuario
                column_order=["Fecha", "Categoria", "Beneficiario", "Monto_ARS"], 
                key="editor_gastos_operativos"
            )

            # 5. Recalcular el total y actualizar el session state
            total_db_simulacion = edited_df_gastos['Monto_ARS'].sum()
            # CLAVE: Actualizar el valor que usa el Overhead
            st.session_state.gasto_fijo_mensual_total = total_db_simulacion 
            
            # Recálculo de subtotales por categoría (usando el DF editado)
            total_por_categoria = edited_df_gastos.groupby('Categoria')['Monto_ARS'].sum().reset_index()
            total_por_categoria['Monto_ARS'] = total_por_categoria['Monto_ARS'].apply(lambda x: f"${x:,.2f}")
            
            st.markdown("---")
            st.markdown("**Resumen por Categoría (Editado):**")
            st.dataframe(
                total_por_categoria.rename(columns={'Monto_ARS': 'Subtotal (ARS)'}),
                use_container_width=True,
                hide_index=True
            )
            
            st.markdown(f"**Total General (Simulación):** **${total_db_simulacion:,.2f} ARS**")
            
            # CLAVE: Forzar rerun si el editor ha sido modificado para que el Overhead se actualice.
            # NOTA: st.data_editor fuerza un rerun por defecto si hay cambios.

        else:
            st.warning(f"No se encontraron gastos para {calendar.month_name[MES_GASTOS].capitalize()} de {ANIO_GASTOS} en la base de datos ni se han cargado gastos temporales.")

    # -----------------------------------------------------------
    # 4. COSTO DE FLETE BASE (200L) - INGRESO MANUAL
    # -----------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("Costo de Flete General (Directo)")
    
    costo_flete_x_receta_ars = st.sidebar.number_input(
        f"Costo Flete Base por Receta ({BASE_LITROS:.0f}L) ARS:",
        min_value=0.0,
        value=st.session_state.get('flete_base_200l', 5000.0),
        step=100.0,
        format="%.2f",
        key="flete_base_input",
        help="Costo fijo de flete asociado a un batch base de 200L."
    )
    st.session_state['flete_base_200l'] = costo_flete_x_receta_ars


    st.sidebar.markdown("---")
    
    # -----------------------------------------------------------
    # 5. VOLUMEN MENSUAL Y CÁLCULO DE OVERHEAD POR LITRO 
    # -----------------------------------------------------------
    st.sidebar.subheader("Asignación de Costos por Overhead (Gasto Indirecto Tanda)")
    
    volumen_mensual_litros = VOLUMEN_MENSUAL_AUTOMATICO
    
    st.sidebar.markdown(f"**Volumen Mensual de Producción (8 Recetas/Día):**")
    st.sidebar.info(f"{volumen_mensual_litros:,.0f} Litros/Mes")

    # Calcular Costo Indirecto por Litro (Automático) - USA EL VALOR EDITADO/TEMPORAL
    if volumen_mensual_litros > 0:
        costo_indirecto_por_litro_auto = gasto_fijo_mensual_auto / volumen_mensual_litros
    else:
        costo_indirecto_por_litro_auto = 0.0
        
    st.sidebar.metric("Costo Indirecto Operativo por Litro (Auto)", f"${costo_indirecto_por_litro_auto:,.2f} ARS/L")

    costo_indirecto_por_litro_manual = st.sidebar.number_input(
        "Costo Indirecto por Litro (Manual ARS/L):",
        min_value=0.0,
        value=0.0, 
        step=0.1,
        format="%.2f",
        key="overhead_manual_input",
        help="Ingrese un valor manual para anular el cálculo automático de Overhead por Litro. (0.0 usa el valor Auto)"
    )

    if costo_indirecto_por_litro_manual > 0.0:
        costo_indirecto_por_litro = costo_indirecto_por_litro_manual
        st.sidebar.info(f"Usando Overhead Manual: ${costo_indirecto_por_litro:,.2f} ARS/L")
    else:
        costo_indirecto_por_litro = costo_indirecto_por_litro_auto

    st.sidebar.markdown("---")
    
    # -----------------------------------------------------------
    # 6. ENTRADA DEL DÓLAR DEL DÍA
    # -----------------------------------------------------------
    st.sidebar.subheader("Cotización Dólar del Día")
    cotizacion_dolar_actual = st.sidebar.number_input(
        "Precio de Venta del Dólar (ARS)",
        min_value=1.0,
        value=st.session_state.get('dolar_value', 1000.0), 
        step=0.1,
        format="%.2f",
        key="dolar_input"
    )
    st.session_state['dolar_value'] = cotizacion_dolar_actual
    st.session_state['dolar'] = cotizacion_dolar_actual

    # ------------------------------------------------------------------------------------------------------------------
    
    # =======================================================================
    # MAIN APP LOGIC 
    # =======================================================================
    
    # --- SELECCIÓN DE RECETA ---
    cursor = conn.cursor()
    cursor.execute("SELECT id, nombre FROM recetas ORDER BY nombre")
    recetas_db = cursor.fetchall()
    recetas = {r["id"]: r["nombre"] for r in recetas_db}
    recetas_nombres = [r["nombre"] for r in recetas_db]

    if not recetas:
        st.error("No se encontraron recetas en la base de datos.")
        conn.close()
        return

    receta_seleccionada_nombre = st.selectbox("Seleccione la Receta Base a Simular:", recetas_nombres)
    receta_id = [id for id, nombre in recetas.items() if nombre == receta_seleccionada_nombre][0]

    if st.session_state.receta_id_actual != receta_id:
        st.session_state.ingredientes_temporales = []
        st.session_state.receta_id_actual = receta_id
        st.info(f"Receta base cambiada a '{receta_seleccionada_nombre}'. Ingredientes temporales reiniciados.")

    # --- ENTRADA DE LITROS A PRODUCIR ---
    st.header(f"Simulación para: {receta_seleccionada_nombre}")
    
    col_litros, col_base = st.columns([0.8, 0.2])
    cantidad_litros = col_litros.number_input(
        "Cantidad de Litros a Producir:",
        min_value=1.0,
        value=st.session_state.get('litros_input', BASE_LITROS),
        step=1.0,
        format="%.2f",
        key="litros_input"
    )
    col_base.info(f"Receta Base: {BASE_LITROS:.0f} L")
    factor_escala = cantidad_litros / BASE_LITROS
    st.info(f"Factor de Escala (Simulación): **{factor_escala:.4f}**")


    # --- CREACIÓN DEL DATAFRAME DE INGREDIENTES ---
    ingredientes_bd = obtener_ingredientes_receta(conn, receta_id)
    
    data = []
    # a) Ingredientes de la BD
    for ing in ingredientes_bd:
        data.append({
            'ID_Receta': ing['id_ingrediente_receta'],
            'Materia Prima': ing['nombre'],
            'materia_prima_id': ing['materia_prima_id'],
            'Unidad': ing['unidad'],
            'Cantidad Base (200L)': ing['cantidad'],
            'cantidad_simulada': ing['cantidad'] * factor_escala,
            'Quitar': False,
            'Temporal': False,
            'precio_unitario_manual': 0.0,
            'cotizacion_usd_manual': 1.0, 
        })
    # b) Ingredientes Temporales (Nuevos o Existentes agregados)
    for i, temp in enumerate(st.session_state.ingredientes_temporales):
        mp_id = temp.get('materia_prima_id', -1) 
        
        data.append({
            'ID_Receta': f"TEMP_{i}",
            'Materia Prima': temp['nombre'],
            'materia_prima_id': mp_id, 
            'Unidad': temp['unidad'],
            'Cantidad Base (200L)': temp['cantidad_base'],
            'cantidad_simulada': temp['cantidad_base'] * factor_escala,
            'Quitar': False,
            'Temporal': True,
            'precio_unitario_manual': temp['precio_unitario'], 
            'cotizacion_usd_manual': temp['cotizacion_usd'],
        })

    ingredientes_df = pd.DataFrame(data)

    st.subheader("Simulación de Costos (Vista Excel - LIVE)")

    # 1. CÁLCULO PREVIO DEL PRECIO UNITARIO BASE (USD) PARA LA VISUALIZACIÓN
    ingredientes_df['Precio Unitario (USD) BASE'] = 0.0
    
    for index, row in ingredientes_df.iterrows():
        
        precio_base_usd_final = 0.0
        precio_manual_unitario = row.get('precio_unitario_manual', 0.0)
        cotizacion_manual = row.get('cotizacion_usd_manual', 1.0)
        materia_prima_id = row["materia_prima_id"]

        if precio_manual_unitario > 0.0 and cotizacion_manual > 1.0:
            precio_base_usd_final = precio_manual_unitario
        
        elif materia_prima_id != -1 and precio_manual_unitario == 0.0:
            precio_unitario_reg_base, _, _, cotizacion_usd_reg_bd = \
                obtener_precio_actual_materia_prima(conn, materia_prima_id)
            
            if cotizacion_usd_reg_bd > 1.0:
                precio_base_usd_final = precio_unitario_reg_base
                
        ingredientes_df.loc[index, 'Precio Unitario (USD) BASE'] = precio_base_usd_final
    
    # 2. Configurar el editor de datos (vista Excel)
    column_config = {
        "ID_Receta": st.column_config.TextColumn(disabled=True),
        "Materia Prima": st.column_config.TextColumn(disabled=True),
        "materia_prima_id": st.column_config.TextColumn(disabled=True),
        "Unidad": st.column_config.TextColumn(disabled=True),
        "Cantidad Base (200L)": st.column_config.NumberColumn(help="Cantidad requerida para la base (200L).", format="%.4f",),
        "cantidad_simulada": st.column_config.NumberColumn("Cantidad (Total L)", format="%.4f", disabled=True,),
        "Precio Unitario (USD) BASE": st.column_config.NumberColumn(
            "Precio Unitario (USD) BASE", 
            format="%.4f",
            disabled=True, 
            help="Precio Unitario en USD de la MP (tomado de BD o Manual). Este valor se re-cotiza con el dólar actual de la simulación. NO es editable."
        ),
        "Quitar": st.column_config.CheckboxColumn(help="Marque para excluir de la simulación.",),
        "Temporal": st.column_config.CheckboxColumn(disabled=True),
        "precio_unitario_manual": st.column_config.NumberColumn("Precio Manual Unit. (ARS/USD)", format="%.4f",
            help="Ingrese un precio unitario manual. Si es USD, use una Cotización USD > 1 en el formulario 'Agregar MP'."
        ),
        "cotizacion_usd_manual": st.column_config.NumberColumn("Cot. USD (Manual)", format="%.2f", min_value=1.0,),
    }
    
    cols_display_final = ["Materia Prima", "Unidad", "Cantidad Base (200L)", "cantidad_simulada", 
                          "Precio Unitario (USD) BASE", "precio_unitario_manual", "Quitar"]


    edited_df = st.data_editor(
        ingredientes_df, 
        column_config=column_config,
        column_order=cols_display_final, 
        num_rows="fixed",
        use_container_width=True,
        key="data_editor_ingredientes"
    )

    # 3. Mapear de vuelta el DataFrame editado al original para el cálculo
    ingredientes_df['Cantidad Base (200L)'] = edited_df['Cantidad Base (200L)']
    ingredientes_df['Quitar'] = edited_df['Quitar']
    ingredientes_df['precio_unitario_manual'] = edited_df['precio_unitario_manual']
    ingredientes_df['cantidad_simulada'] = ingredientes_df['Cantidad Base (200L)'] * factor_escala

    # --- LÓGICA DE CÁLCULO EN VIVO ---
    ingredientes_a_calcular = ingredientes_df[~ingredientes_df['Quitar']].copy()
    
    costo_mp_total, detalle_costo_df, costo_mp_base_ars, costo_recargo_mp_ars = calcular_costo_total(
        ingredientes_a_calcular, 
        cotizacion_dolar_actual, 
        conn
    )
    
    costo_mp_base_usd = costo_mp_base_ars / cotizacion_dolar_actual
    
    # --------------------------------------------------------------------------
    # CÁLCULOS DE COSTOS FIJOS (USA EL VALOR EDITADO/TEMPORAL: gasto_fijo_mensual_auto)
    # --------------------------------------------------------------------------
    
    gasto_indirecto_tanda = costo_indirecto_por_litro * cantidad_litros
    
    costo_flete_total_ars = st.session_state['flete_base_200l'] * factor_escala
    
    # Costo Total Final = Costo MP Base ARS + Recargo 3% USD (ARS) + Flete General + Indirecto Operativo
    costo_total_final = costo_mp_base_ars + costo_recargo_mp_ars + costo_flete_total_ars + gasto_indirecto_tanda
    
    # --- CONVERSIÓN A DÓLARES ---
    costo_total_final_usd = costo_total_final / cotizacion_dolar_actual
    costo_por_litro_ars = costo_total_final / cantidad_litros 
    costo_por_litro_usd = costo_total_final_usd / cantidad_litros
    # -----------------------------
    
    # Actualizar Session State (para uso futuro)
    st.session_state['costo_total_mp_y_recargo'] = costo_mp_total
    st.session_state['costo_flete_general_tanda'] = costo_flete_total_ars
    st.session_state['costo_total_final'] = costo_total_final
    st.session_state['litros'] = cantidad_litros 
    
    st.subheader(f"✅ Resultado del Costo en Vivo para {cantidad_litros:.2f} Litros (Dólar: ${st.session_state['dolar']:.2f})")
    
    col_res1, col_res2, col_res3 = st.columns(3) 
    
    # --------------------------------------------------------------------------------------
    # CÁLCULOS DISCRIMINADOS
    # --------------------------------------------------------------------------------------
    col_res1.metric(
        "Costo Materia Prima (Base)",
        f"${costo_mp_base_ars:,.2f} ARS",
        help=f"Equivalente a USD ${costo_mp_base_usd:,.2f}"
    )
    col_res1.metric(
        "Recargo 3% USD MP (Total)", 
        f"${costo_recargo_mp_ars:,.2f} ARS"
    )
    
    col_res2.metric(
        "Flete General (Escalado)",
        f"${costo_flete_total_ars:,.2f} ARS",
        help=f"Costo Flete Base ({BASE_LITROS:.0f}L, Manual): ${st.session_state['flete_base_200l']:,.2f} ARS"
    )
    col_res3.metric(
        "Gasto Indirecto Tanda (Overhead)",
        f"${gasto_indirecto_tanda:,.2f} ARS"
    )
    # --------------------------------------------------------------------------------------

    # --- METRICAS FINALES ARS y USD ---
    st.markdown("---")
    st.header("COSTO TOTAL FINAL DE LA TANDA (ARS y USD)")
    col_final_ars, col_final_usd = st.columns(2)
    
    col_final_ars.metric(
        "Costo Total de la Tanda (ARS)",
        f"${costo_total_final:,.2f} ARS"
    )
    col_final_ars.metric(
        "Costo por Litro (ARS/L)",
        f"${costo_por_litro_ars:,.2f} ARS/L"
    )
    
    col_final_usd.metric(
        "Costo Total de la Tanda (USD)",
        f"USD ${costo_total_final_usd:,.2f}"
    )
    col_final_usd.metric(
        "Costo por Litro (USD/L)",
        f"USD ${costo_por_litro_usd:,.2f} USD/L"
    )
    # ----------------------------------
    
    # ** Detalle de costo por ingrediente (USD) **
    with st.expander("Ver Detalle de Costo por Ingrediente 🔎"):
        
        col_config_detalle = {
            "Costo Unit. USD (Base)": st.column_config.NumberColumn(
                "Costo Unit. USD (Base)", format="$ %.4f", help="Precio unitario en USD de la Materia Prima (tomado de BD o manual)."
            ),
            "Recargo 3% USD (Unit.)": st.column_config.NumberColumn(
                "Recargo 3% USD (Unit.)", format="$ %.4f"
            ),
            "Costo Unit. USD (Total)": st.column_config.NumberColumn(
                "Costo Unit. USD (Total)", format="$ %.4f"
            ),
            "Costo Total USD": st.column_config.NumberColumn(
                "Costo Total USD", format="$ %.2f"
            ),
        }
        
        cols_ordenadas = [
            "Materia Prima", 
            "Unidad", 
            "Cantidad (Simulada)", 
            "Moneda Origen", 
            "Costo Unit. USD (Base)",
            "Recargo 3% USD (Unit.)", 
            "Costo Unit. USD (Total)", 
            "Costo Total USD" 
        ]

        st.dataframe(
            detalle_costo_df[[c for c in cols_ordenadas if c in detalle_costo_df.columns]], 
            use_container_width=True, 
            column_config={k: v for k, v in col_config_detalle.items() if k in cols_ordenadas}
        )
    
    # --------------------------------------------------------------------------------------
    # SECCIÓN: GESTIÓN DE SIMULACIONES PARA PRESUPUESTO
    # --------------------------------------------------------------------------------------
    st.markdown("---")
    st.header("🛒 Agregar Simulación al Presupuesto")
    
    col_cant_add, col_margen_add, col_button_add = st.columns([0.3, 0.3, 0.4])
    
    cantidad_a_agregar = col_cant_add.number_input(
        "Cantidad de Tandas a Agregar:",
        min_value=1,
        value=1,
        step=1,
        key="cantidad_a_agregar_input"
    )
    
    margen_ganancia_inicial = col_margen_add.number_input(
        "Margen Inicial (%):", 
        min_value=0.0, 
        value=30.0, 
        step=1.0, 
        format="%.2f",
        key="margen_inicial_input"
    )

    if col_button_add.button(f"➕ Agregar '{receta_seleccionada_nombre}' (x{cantidad_a_agregar}) al Presupuesto", use_container_width=True):
        
        st.session_state.ingredientes_temporales = []
        ingredientes_temporales_a_guardar = ingredientes_a_calcular.copy()
        
        for index, row in ingredientes_temporales_a_guardar.iterrows():
            if row['Temporal'] or row['precio_unitario_manual'] > 0.0:
                 st.session_state.ingredientes_temporales.append({
                    'nombre': row['Materia Prima'], 'unidad': row['Unidad'], 'cantidad_base': row['Cantidad Base (200L)'],
                    'precio_unitario': row['precio_unitario_manual'], 'cotizacion_usd': row['cotizacion_usd_manual'], 'materia_prima_id': row['materia_prima_id'],
                })
        
        simulacion_data = {
            'nombre_receta': receta_seleccionada_nombre,
            'litros': cantidad_litros * cantidad_a_agregar,
            'costo_total_ars': costo_total_final * cantidad_a_agregar,
            'costo_por_litro_ars': costo_por_litro_ars, 
            'gasto_indirecto_tanda': gasto_indirecto_tanda * cantidad_a_agregar,
            'costo_flete_total_ars': costo_flete_total_ars * cantidad_a_agregar,
            'margen_ganancia': margen_ganancia_inicial, 
            'cantidad_tandas': cantidad_a_agregar,
            'detalle_mp_json_unitario': detalle_costo_df.to_json(orient='records'),
        }
        
        st.session_state['simulaciones_presupuesto'].append(simulacion_data)
        st.session_state['presupuesto_data_for_print'] = {}
        st.success(f"Simulación de {receta_seleccionada_nombre} (x{cantidad_a_agregar}) agregada con Margen Inicial del {margen_ganancia_inicial:.2f}%. Total de items: {len(st.session_state['simulaciones_presupuesto'])}")
        st.rerun()
        
    st.markdown("---")
    
    # --------------------------------------------------------------------------------------
    # SECCIÓN: GENERACIÓN DE PRESUPUESTO FINAL (CON MARGEN INDIVIDUAL EDITABLE)
    # --------------------------------------------------------------------------------------
    st.header("📄 Generar Presupuesto Final")

    if st.session_state['simulaciones_presupuesto']:
        st.subheader("Simulaciones Cargadas: (Edite el Margen de Ganancia Individual)")
        
        datos_resumen = []
        costo_total_acumulado = 0.0
        litros_total_acumulado = 0.0
        
        for i, sim in enumerate(st.session_state['simulaciones_presupuesto']):
            datos_resumen.append({
                'ID': i + 1,
                'Receta': sim['nombre_receta'],
                'Tandas': sim['cantidad_tandas'],
                'Litros': sim['litros'],
                'Costo Total ARS': sim['costo_total_ars'],
                'costo_por_litro_ars': sim['costo_por_litro_ars'],
                'Margen de Ganancia (%)': sim['margen_ganancia'] 
            })
            
        df_resumen = pd.DataFrame(datos_resumen)
        st.session_state['df_resumen_editable'] = df_resumen

        col_config_resumen = {
            'ID': st.column_config.NumberColumn(disabled=True),
            'Receta': st.column_config.TextColumn(disabled=True),
            'Tandas': st.column_config.NumberColumn(format="%.0f", disabled=True),
            'Litros': st.column_config.NumberColumn(format="%.2f", disabled=True),
            'Costo Total ARS': st.column_config.NumberColumn(format="$%f", disabled=True),
            'Margen de Ganancia (%)': st.column_config.NumberColumn( 
                "Margen de Ganancia (%)", 
                min_value=0.0, 
                format="%.2f",
                help="Margen de ganancia específico para este producto."
            ),
        }
        
        edited_df_resumen = st.data_editor(
            df_resumen[['ID', 'Receta', 'Tandas', 'Litros', 'Costo Total ARS', 'Margen de Ganancia (%)']],
            hide_index=True, 
            use_container_width=True,
            column_config=col_config_resumen,
            key="df_resumen_editor"
        )
        
        st.session_state['df_resumen_editable'] = edited_df_resumen.copy()
        
        costo_total_acumulado = edited_df_resumen['Costo Total ARS'].sum()
        litros_total_acumulado = edited_df_resumen['Litros'].sum()
        
        st.markdown(f"**Costo Total Acumulado de Producción (ARS):** ${costo_total_acumulado:,.2f}")
        st.markdown(f"**Volumen Total (Litros):** {litros_total_acumulado:,.2f} L")
        st.markdown("---")

        # --- FORMULARIO DE GENERACIÓN DE PRESUPUESTO ---
        with st.form("form_presupuesto_final"):
            st.subheader("Datos del Presupuesto (Guardado)")
            
            cliente_nombre = st.text_input("Nombre del Cliente:", key="cliente_nombre_input")
            
            submitted = st.form_submit_button("Generar y Guardar Presupuesto")
            
            if submitted:
                if not cliente_nombre:
                    st.error("Debe ingresar el nombre del cliente.")
                else:
                    
                    df_final = st.session_state['df_resumen_editable'].copy()
                    
                    df_final.rename(columns={'Margen de Ganancia (%)': 'Margen_Ganancia'}, inplace=True)
                    
                    df_final['Factor_Ganancia'] = 1 + (df_final['Margen_Ganancia'] / 100.0)
                    df_final['Precio_Venta_Total_ARS'] = df_final['Costo Total ARS'] * df_final['Factor_Ganancia']
                    df_final['Precio_Venta_Total_USD'] = df_final['Precio_Venta_Total_ARS'] / cotizacion_dolar_actual 
                    
                    ganancia_total_ars = df_final['Precio_Venta_Total_ARS'].sum() - costo_total_acumulado
                    precio_final_ars_total = df_final['Precio_Venta_Total_ARS'].sum()
                    
                    df_final['Precio_Venta_Unitario_ARS'] = df_final['Precio_Venta_Total_ARS'] / df_final['Litros']
                    df_final['Precio_Venta_Unitario_USD'] = df_final['Precio_Venta_Unitario_ARS'] / cotizacion_dolar_actual
                    df_final['Costo_Unitario_ARS'] = df_final['Costo Total ARS'] / df_final['Litros'] 

                    
                    precio_unitario_ars_litro_AVG = precio_final_ars_total / litros_total_acumulado
                    
                    # 1. Guardar en la Base de Datos
                    try:
                        cliente_id = get_or_create_client(conn, cliente_nombre)
                        
                        detalle_simulaciones_json = df_final.to_json(orient='records')
                        
                        porcentaje_ganancia_global_bd = 30.0 
                        
                        presupuesto_id = save_presupuesto(
                            conn, 
                            cliente_id, 
                            porcentaje_ganancia_global_bd, 
                            litros_total_acumulado, 
                            costo_total_acumulado, 
                            precio_final_ars_total,
                            detalle_simulaciones_json
                        )
                        st.success(f"✅ Presupuesto Guardado (ID: {presupuesto_id}) para el Cliente: {cliente_nombre}.")
                        
                        # 2. Presentación Final
                        st.subheader(f"📊 Presentación Final para {cliente_nombre}")
                        
                        st.markdown("**Detalle de Venta por Producto**")
                        
                        df_presentacion = df_final[['Receta', 'Litros', 'Costo Total ARS', 'Margen_Ganancia', 
                                                    'Precio_Venta_Total_ARS', 'Precio_Venta_Unitario_ARS']].copy()
                        df_presentacion.rename(columns={
                            'Costo Total ARS': 'Costo_Total_ARS',
                            'Margen_Ganancia': 'Margen_Aplicado (%)',
                            'Precio_Venta_Total_ARS': 'Venta_Total_ARS',
                            'Precio_Venta_Unitario_ARS': 'Venta_Unitario_ARS/L'
                        }, inplace=True)
                        
                        df_presentacion['Ganancia_ARS'] = df_presentacion['Venta_Total_ARS'] - df_presentacion['Costo_Total_ARS']
                        
                        df_presentacion = df_presentacion[['Receta', 'Litros', 'Costo_Total_ARS', 'Margen_Aplicado (%)', 'Ganancia_ARS', 'Venta_Total_ARS', 'Venta_Unitario_ARS/L']]

                        st.dataframe(
                            df_presentacion,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                'Litros': st.column_config.NumberColumn(format="%.2f"),
                                'Costo_Total_ARS': st.column_config.NumberColumn(format="$%f"),
                                'Margen_Aplicado (%)': st.column_config.NumberColumn(format="%.2f"),
                                'Ganancia_ARS': st.column_config.NumberColumn(format="$%f"),
                                'Venta_Total_ARS': st.column_config.NumberColumn(format="$%f"),
                                'Venta_Unitario_ARS/L': st.column_config.NumberColumn(format="$%.2f"),
                            }
                        )

                        # Totales Globales
                        st.markdown("---")
                        col_costo, col_ganancia, col_venta = st.columns(3)
                        col_costo.metric("Costo Total Producción", f"${costo_total_acumulado:,.2f} ARS")
                        col_ganancia.metric("Ganancia Total Aplicada", f"${ganancia_total_ars:,.2f} ARS")
                        col_venta.metric("Precio Venta Total", f"${precio_final_ars_total:,.2f} ARS", delta="FINAL")

                        st.markdown("---")
                        st.subheader(f"Precio Unitario Final (Promedio de {litros_total_acumulado:,.2f} L)")
                        
                        col_litro_ars, col_litro_usd = st.columns(2)
                        col_litro_ars.metric("Precio por Litro (ARS/L)", f"${precio_unitario_ars_litro_AVG:,.2f} ARS/L")
                        col_litro_usd.metric("Precio por Litro (USD/L)", f"USD ${precio_unitario_ars_litro_AVG / cotizacion_dolar_actual:,.2f} USD/L")
                        
                        st.session_state['presupuesto_data_for_print'] = {
                            'cliente_nombre': cliente_nombre,
                            'costo_total_acumulado': costo_total_acumulado,
                            'ganancia_ars': ganancia_total_ars, 
                            'precio_final_ars': precio_final_ars_total, 
                            'litros_total_acumulado': litros_total_acumulado,
                            'precio_unitario_ars_litro': precio_unitario_ars_litro_AVG, 
                            'porcentaje_ganancia': porcentaje_ganancia_global_bd, 
                            'df_detalle_final_presupuesto': df_final, 
                            'cotizacion_dolar_actual': cotizacion_dolar_actual,
                            'presupuesto_id': presupuesto_id
                        }
                        
                        
                    except Exception as e:
                        st.error(f"Error al guardar el presupuesto: {e}")

        # --- BOTONES DE ACCIÓN POST-PRESUPUESTO (FUERA DEL FORMULARIO) ---
        if 'presupuesto_data_for_print' in st.session_state and st.session_state['presupuesto_data_for_print']:
            st.markdown("---")
            col_print, col_clear = st.columns([0.5, 0.5])
            
            data = st.session_state['presupuesto_data_for_print']
            
            pdf_bytes = generate_pdf_reportlab(data)
            download_file_name = f"Presupuesto_N{data['presupuesto_id']}_{data['cliente_nombre'].replace(' ', '_')}.pdf"
            
            col_print.download_button(
                label="⬇️ Descargar Presupuesto (PDF - ReportLab)",
                data=pdf_bytes,
                file_name=download_file_name,
                mime="application/pdf", 
                use_container_width=True
            )
            
            if col_clear.button("Limpiar Presupuesto Cargado (Comenzar Nuevo)", use_container_width=True):
                st.session_state['simulaciones_presupuesto'] = []
                st.session_state['presupuesto_data_for_print'] = {}
                st.rerun()

    else:
        st.info("No hay simulaciones cargadas en el presupuesto. Agregue simulaciones usando el botón de arriba.")

    # --------------------------------------------------------------------------------------
    # Se mantienen las secciones de Gestión de Estado Temporal y Agregar Materia Prima
    # --------------------------------------------------------------------------------------
    
    st.markdown("---")
    st.header("⚙️ Guardar Estado de Simulación Temporal (Opcional)")
    
    if st.button("Aplicar Cambios (Cantidades y Precios) al Estado Temporal - Mantener Receta"):
        
        st.info("Actualizando estado de la simulación...")
        st.session_state.ingredientes_temporales = []
        ingredientes_temporales_a_guardar = ingredientes_a_calcular.copy()
        
        for index, row in ingredientes_temporales_a_guardar.iterrows():
            if row['Temporal'] or row['precio_unitario_manual'] > 0.0:
                 st.session_state.ingredientes_temporales.append({
                    'nombre': row['Materia Prima'], 'unidad': row['Unidad'], 'cantidad_base': row['Cantidad Base (200L)'],
                    'precio_unitario': row['precio_unitario_manual'], 'cotizacion_usd': row['cotizacion_usd_manual'], 'materia_prima_id': row['materia_prima_id'],
                })
            
        st.success("Estado de simulación actualizado. Estos cambios se mantendrán hasta que cambie la receta base o reinicie la app.")
        st.rerun()

    # --- GESTIÓN DE MATERIAS PRIMAS (Agregar) ---
    st.markdown("---")
    st.header("➕ Agregar Materia Prima a la Simulación")
    
    todas_mps = obtener_todas_materias_primas(conn)
    mp_map = {mp['nombre']: mp for mp in todas_mps}
    mp_nombres = sorted(mp_map.keys())
    
    tab_existente, tab_nueva = st.tabs(["MP Existente (Precio de BD)", "MP Nueva (Precio USD)"])

    with tab_existente:
        st.markdown("**Seleccione una MP existente y la cantidad base. El precio se tomará automáticamente del último registro de la BD.**")
        with st.form("form_agregar_mp_existente"):
            mp_existente_nombre = st.selectbox("Materia Prima:", mp_nombres, key="mp_existente_select_key")
            mp_info = mp_map[mp_existente_nombre]
            mp_id = mp_info['id']
            precio_unitario_ars_o_usd, _, _, cotizacion_usd_reg_bd = obtener_precio_actual_materia_prima(conn, mp_id)
            
            col_info_precio1, col_info_cot = st.columns(2)
            if cotizacion_usd_reg_bd > 1.0:
                col_info_precio1.metric("Precio Registrado (USD)", f"${precio_unitario_ars_o_usd:.4f} USD")
                col_info_cot.metric("Cotización Compra (ARS/USD)", f"${cotizacion_usd_reg_bd:.2f}")
            else:
                col_info_precio1.metric("Precio Registrado (ARS)", f"${precio_unitario_ars_o_usd:,.2f} ARS")
                col_info_cot.info("Precio en ARS fijo")
            
            st.markdown("---")
            col_cant, col_unidad_display = st.columns(2)
            cantidad_base = col_cant.number_input("Cantidad Base (para 200L):", min_value=0.0001, value=1.0, step=0.01, format="%.4f", key="temp_cantidad_existente")
            col_unidad_display.text_input("Unidad de Medida:", value=mp_info['unidad'], disabled=True)

            if st.form_submit_button("Agregar MP Existente"):
                st.session_state.ingredientes_temporales.append({
                    'nombre': mp_info['nombre'], 'unidad': mp_info['unidad'], 'cantidad_base': cantidad_base,
                    'precio_unitario': 0.0, 'cotizacion_usd': 1.0, 'materia_prima_id': mp_info['id'] 
                })
                st.success(f"Materia Prima '{mp_info['nombre']}' agregada con precio de la BD. Recalculando...")
                st.rerun()

    with tab_nueva:
        st.markdown("**Ingrese una MP que NO está en la BD. Debe especificar el precio unitario en USD.**")
        with st.form("form_agregar_mp_nueva"):
            col_nombre, col_unidad = st.columns(2)
            mp_nombre = col_nombre.text_input("Nombre de la Materia Prima:", key="temp_nombre_nueva")
            mp_unidad = col_unidad.text_input("Unidad de Medida (kg, L, gr):", value="kg", key="temp_unidad_nueva")
            col_cant, col_precio, col_cot = st.columns(3)
            cantidad_base = col_cant.number_input("Cantidad Base (para 200L):", min_value=0.0001, value=1.0, step=0.01, format="%.4f", key="temp_cantidad_nueva")
            precio_unitario_usd = col_precio.number_input("Precio Unitario Manual (USD):", min_value=0.01, value=1.0, step=0.01, format="%.4f", key="temp_precio_usd_nueva")
            cotizacion_usd = col_cot.number_input("Cotización USD de Compra:", min_value=1.0, value=cotizacion_dolar_actual, step=0.1, format="%.2f", help="Cotización del dólar con la que se 'registró' esta compra (ARS/USD).", key="temp_cot_usd_nueva")
            
            if st.form_submit_button("Agregar MP Nueva"):
                if mp_nombre and cantidad_base > 0 and precio_unitario_usd > 0:
                    st.session_state.ingredientes_temporales.append({
                        'nombre': mp_nombre, 'unidad': mp_unidad, 'cantidad_base': cantidad_base,
                        'precio_unitario': precio_unitario_usd, 'cotizacion_usd': cotizacion_usd, 'materia_prima_id': -1 
                    })
                    st.success(f"Materia Prima Temporal '{mp_nombre}' agregada (Precio USD). Recalculando...")
                    st.rerun()
                else:
                    st.error("Por favor, complete todos los campos (nombre, cantidad base > 0, precio USD > 0).")

    conn.close()
    
if __name__ == "__main__":
    main()