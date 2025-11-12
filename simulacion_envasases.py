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
# UTILIDAD DB: ENVASES (NUEVO)
# =================================================================================================

def obtener_envases_disponibles(conn):
    """Obtiene la lista completa de envases disponibles con IDs y capacidad."""
    cursor = conn.cursor()
    cursor.execute("SELECT id, descripcion, capacidad_litros FROM envases ORDER BY descripcion")
    return [dict(row) for row in cursor.fetchall()]

def obtener_precio_envase_actual(conn, envase_id):
    """
    Obtiene el último precio unitario registrado de un envase (ASUMIDO USD BASE) y su capacidad en litros.
    """
    if envase_id is None or envase_id == -1:
        # Se asume 0.0 USD y 0.0 Litros
        return 0.0, 0.0 # precio_unitario_usd_base, capacidad_litros

    cursor = conn.cursor()
    
    # 1. Obtener el último precio unitario (asume que la entrada más reciente es el precio actual)
    cursor.execute("""
        SELECT precio_unitario
        FROM entradas_envases
        WHERE envase_id = ?
        ORDER BY fecha_ingreso DESC, id DESC
        LIMIT 1
    """, (envase_id,))
    
    entrada = cursor.fetchone()
    # Se asume que este es el precio base en USD para aplicar la cotización del día
    precio_unitario_usd_base = entrada['precio_unitario'] if entrada and entrada['precio_unitario'] is not None else 0.0

    # 2. Obtener la capacidad en litros
    cursor.execute("""
        SELECT capacidad_litros
        FROM envases
        WHERE id = ?
    """, (envase_id,))
    
    envase = cursor.fetchone()
    capacidad_litros = envase['capacidad_litros'] if envase and envase['capacidad_litros'] is not None else 0.0
    
    return precio_unitario_usd_base, capacidad_litros


# =================================================================================================
# FUNCIONES DE COSTEO DE MATERIA PRIMA (MODIFICADA LA LÓGICA DE RETORNO)
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
    costo_total_mp_usd = 0.0 # <-- AÑADIDO: Costo total de MP (Base + Recargo) en USD
    
    for index, ingrediente in ingredientes_df.iterrows():
        materia_prima_id = ingrediente["materia_prima_id"]
        cantidad_usada = ingrediente["cantidad_simulada"]
        # FIX: Ahora se espera la columna 'Materia Prima'
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
        costo_total_mp_usd += costo_total_usd_ingrediente # <-- AÑADIDO: Acumular costo total MP en USD
        
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
    
    # <<< CORRECCIÓN: Se usa detalle_df en lugar de detalle_costo_df >>>
    return costo_mp_total, detalle_df, costo_total_mp_ars, costo_total_recargo_mp_ars, costo_total_mp_usd


# =================================================================================================
# FUNCIONES DE GENERACIÓN DE REPORTE (PDF con ReportLab) (MODIFICADO)
# =================================================================================================

def generate_pdf_reportlab(data):
    """
    Genera el contenido PDF del presupuesto usando la librería ReportLab.
    
    MODIFICACIÓN: Muestra el precio unitario por UNIDAD DE ENVASE (ARS/u. y USD/u.) en lugar de por Litro.
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
    
    # MODIFICACIÓN: Nuevo Título principal con nombre del cliente
    story.append(Paragraph(f"CLIENTE: {cliente_nombre}", styles['PresupuestoTitle']))
    story.append(Spacer(1, 0.2*inch))
    
    # MODIFICACIÓN: Número de Presupuesto encima de la fecha
    story.append(Paragraph(f"**Número de Presupuesto:** {presupuesto_id}", styles['BodyTextBold']))
    story.append(Paragraph(f"**Fecha del Presupuesto:** {fecha_hoy}", styles['BodyTextBold']))
    story.append(Paragraph(f"**Cotización del Dólar (Referencia):** ${cotizacion_dolar_actual:,.2f} ARS/USD", styles['BodyTextBold']))
    story.append(Spacer(1, 0.3*inch))
    
    story.append(Paragraph("Detalle del Pedido", styles['PresupuestoHeading2']))
    
    table_data = []
    # MODIFICACIÓN DE ENCABEZADO: Se cambian las columnas a Precio Unitario por UNIDAD (u.)
    table_data.append([
        "Producto", 
        "Cantidad (L)", 
        "Tipo de Envase",  
        "Unidades de Envase",  
        "Precio Unitario (ARS/u.)", # MODIFICADO
        "Precio Unitario (USD/u.)", # MODIFICADO
        "Total a Pagar (ARS)",
        "Total a Pagar (USD)" 
    ])
    
    total_width = 10.1 * inch 
    # MANTENER: Ajuste de anchos para 8 columnas (se reajustan los pesos).
    col_widths = [
        total_width * 0.19, # Producto (was 0.26)
        total_width * 0.08, # Cantidad (L) (was 0.10)
        total_width * 0.12, # Tipo de Envase (was 0.18)
        total_width * 0.12, # Unidades de Envase (was 0.18)
        total_width * 0.13, # Precio Unitario (ARS/u.) (NEW)
        total_width * 0.13, # Precio Unitario (USD/u.) (NEW)
        total_width * 0.12, # Total a Pagar (ARS) (was 0.14)
        total_width * 0.11  # Total a Pagar (USD) (was 0.14)
    ]

    for index, row in df_detalle_final.iterrows():
        
        # Precio unitario por Litro (no se usa en la tabla, pero se mantiene si se necesita)
        # precio_unitario_cliente_ars = row['Precio_Venta_Unitario_ARS'] 
        # precio_unitario_cliente_usd = row['Precio_Venta_Unitario_USD']
        
        total_a_pagar_ars = row['Precio_Venta_Total_ARS']
        total_a_pagar_usd = row['Precio_Venta_Total_USD'] 
        
        # NUEVOS DATOS DEL ENVASE
        envase_nombre = row.get('Envase_Nombre', 'N/A')
        unidades_envase_total = row.get('Unidades_Envase_Total', 0)
        
        # CÁLCULO DEL PRECIO UNITARIO POR ENVASE (SOLICITADO)
        precio_por_envase_ars = 0.0
        precio_por_envase_usd = 0.0
        
        if unidades_envase_total > 0:
            precio_por_envase_ars = total_a_pagar_ars / unidades_envase_total
            if cotizacion_dolar_actual > 0:
                 precio_por_envase_usd = precio_por_envase_ars / cotizacion_dolar_actual
            
        table_data.append([
            row['Receta'], 
            f"{row['Litros']:,.2f} L", 
            envase_nombre, 
            f"{unidades_envase_total:,.0f} u.", 
            # MODIFICACIÓN: Precio unitario por UNIDAD de Envase
            f"${precio_por_envase_ars:,.2f}", 
            f"USD ${precio_por_envase_usd:,.2f}", 
            # FIN MODIFICACIÓN
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
                    'Beneficiario': gasto_beneficiario if gasto_beneficiario else 'Temporal',
                    'Monto_ARS': gasto_monto,
                    'ID_Gasto_Unico': f"TEMP_{len(st.session_state.gastos_temporales_simulacion)}_{gasto_monto}" 
                })
                st.sidebar.success(f"Gasto Temporal de ${gasto_monto:,.2f} agregado.")
            else:
                st.sidebar.warning("El monto debe ser mayor a cero.")
                
    st.sidebar.markdown("---")
    
    # -----------------------------------------------------------
    # 3. EDITOR DE GASTOS OPERATIVOS (GASTO FIJO)
    # -----------------------------------------------------------
    
    MES_GASTOS = MES_SIMULACION
    ANIO_GASTOS = anio_simulacion
    
    with st.sidebar.expander(f"Detalle y Edición de Gastos Fijos (Simulación {calendar.month_name[MES_GASTOS].capitalize()} {ANIO_GASTOS})"):
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
                "ID_Gasto_Unico": st.column_config.TextColumn(disabled=True, help="Identificador único para DB o Temporal."),
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
            st.session_state.gasto_fijo_mensual_total = total_db_simulacion
            
            # Botón para limpiar los gastos temporales
            if st.button("🗑️ Limpiar Gastos Temporales", key="clear_temp_gastos_button"):
                st.session_state.gastos_temporales_simulacion = [g for g in st.session_state.gastos_temporales_simulacion if not g['ID_Gasto_Unico'].startswith('TEMP_')]
                st.rerun()
        else:
            st.warning("No hay gastos fijos registrados para este mes y año. Por favor, agregue un Gasto Temporal si desea simular un Overhead.")
            st.session_state.gasto_fijo_mensual_total = 0.0

    st.sidebar.markdown("---")

    # -----------------------------------------------------------
    # 4. ENTRADA DEL FLETE BASE (ARS)
    # -----------------------------------------------------------
    st.sidebar.subheader("Costo de Flete (ARS)")
    flete_base_200l = st.sidebar.number_input(
        f"Costo Base de Flete para {BASE_LITROS:.0f}L (ARS):", 
        min_value=0.0, 
        value=st.session_state.get('flete_base_200l', 5000.0), 
        step=100.0, 
        format="%.2f", 
        key="flete_base_input"
    )
    st.session_state['flete_base_200l'] = flete_base_200l
    
    st.sidebar.markdown("---")

    # -----------------------------------------------------------
    # 5. CÁLCULO Y AJUSTE DE OVERHEAD
    # -----------------------------------------------------------
    st.sidebar.subheader("Overhead (Costo Indirecto)")
    # Se calcula el volumen mensual automático (32000L por defecto)
    volumen_mensual_litros = st.sidebar.number_input(
        "Volumen Mensual a Producir (Litros):", 
        min_value=BASE_LITROS, 
        value=VOLUMEN_MENSUAL_AUTOMATICO, 
        step=BASE_LITROS,
        format="%.2f",
        key="volumen_mensual_input",
        help=f"Volumen total de producción en Litros utilizado para calcular el Overhead por Litro. (Valor Automático: {VOLUMEN_MENSUAL_AUTOMATICO:,.0f} L)"
    )
    
    # Cálculo automático del costo indirecto por litro (Overhead)
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

    # -----------------------------------------------------------
    # 7. ENTRADA DEL ENVASE (NUEVO)
    # -----------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("Costo de Envase")
    
    envases_disponibles = obtener_envases_disponibles(conn)
    envases_map = {e['descripcion']: e for e in envases_disponibles}
    envases_nombres = ["--- Seleccionar Envase ---"] + [e['descripcion'] for e in envases_disponibles]
    
    envase_seleccionado_nombre = st.sidebar.selectbox(
        "Envase a utilizar:", 
        envases_nombres, 
        key="envase_seleccionado"
    )

    costo_envase_total_ars = 0.0
    costo_envase_por_litro = 0.0
    unidades_necesarias = 0
    capacidad_litros = 0.0
    precio_envase_unitario_ars = 0.0 # Variable para el precio convertido a ARS
    precio_envase_unitario_usd_base = 0.0
    
    envase_id_actual = None

    # LÓGICA DE CÁLCULO DE ENVASE
    if envase_seleccionado_nombre != "--- Seleccionar Envase ---":
        envase_info = envases_map[envase_seleccionado_nombre]
        envase_id_actual = envase_info['id']
        
        # Obtener precio base y capacidad (Se asume que el precio devuelto es en USD base)
        precio_envase_unitario_usd_base, capacidad_litros = obtener_precio_envase_actual(conn, envase_id_actual)
        
        # <<< Aplicar cotización del dólar al precio del envase >>>
        precio_envase_unitario_ars = precio_envase_unitario_usd_base * cotizacion_dolar_actual
        
        st.sidebar.markdown(f"**Capacidad:** {capacidad_litros:,.3f} Litros")
        st.sidebar.markdown(f"**Costo Unitario (USD Base):** ${precio_envase_unitario_usd_base:,.2f}") 
        st.sidebar.markdown(f"**Costo Unitario (ARS Actual):** ${precio_envase_unitario_ars:,.2f}")

        # Se necesita saber la cantidad_litros para calcular los envases.
        cantidad_litros = st.session_state.get('litros', BASE_LITROS)
        
        if cantidad_litros > 0 and capacidad_litros > 0.0:
            # Cálculo principal: Cantidad de envases = Volumen Total / Capacidad del Envase
            unidades_necesarias = int(cantidad_litros / capacidad_litros)
            
            # Ajustar unidades: si el volumen total no es divisible, se necesita un envase más.
            # Se usa ceil implícito para asegurar que se cubra el volumen total.
            unidades_necesarias = int(cantidad_litros / capacidad_litros)
            if cantidad_litros % capacidad_litros > 0.0:
                 unidades_necesarias += 1
            
            # Se calcula el costo total usando el precio unitario ya convertido a ARS.
            costo_envase_total_ars = unidades_necesarias * precio_envase_unitario_ars
            
            if cantidad_litros > 0:
                costo_envase_por_litro = costo_envase_total_ars / cantidad_litros
            else:
                costo_envase_por_litro = 0.0
            
            st.sidebar.metric(
                f"Envases Necesarios ({unidades_necesarias})", 
                f"${costo_envase_total_ars:,.2f} ARS (Total)",
                help=f"Costo por Litro: ${costo_envase_por_litro:,.2f} ARS/L"
            )
        else:
            st.sidebar.warning("Ajuste la cantidad de Litros (>0) y la capacidad del Envase (>0).")


    # =================================================================================================
    # LADO PRINCIPAL: SIMULACIÓN DE RECETA
    # =================================================================================================
    
    st.markdown("---")
    col_sel_receta, col_cant_litros = st.columns([0.5, 0.5])
    
    # Obtener lista de recetas
    df_recetas = fetch_df("SELECT id, nombre FROM recetas ORDER BY nombre")
    recetas_map = {r['nombre']: r for r in df_recetas.to_dict('records')}
    recetas_nombres = ["--- Seleccionar Receta ---"] + [r['nombre'] for r in df_recetas.to_dict('records')]
    
    receta_seleccionada_nombre = col_sel_receta.selectbox(
        "Seleccione la Receta Base:", 
        recetas_nombres, 
        key="receta_seleccionada_nombre"
    )

    receta_id_seleccionada = None
    if receta_seleccionada_nombre != "--- Seleccionar Receta ---":
        receta_id_seleccionada = recetas_map[receta_seleccionada_nombre]['id']
        st.session_state.receta_id_actual = receta_id_seleccionada
    else:
        st.session_state.receta_id_actual = None
        
    cantidad_litros = col_cant_litros.number_input(
        f"Litros a Simular (Base: {BASE_LITROS:.0f}L):", 
        min_value=0.0, 
        value=st.session_state.get('litros', BASE_LITROS), 
        step=BASE_LITROS, 
        format="%.2f", 
        key="cantidad_litros_simulacion"
    )
    st.session_state['litros'] = cantidad_litros

    # --- INPUT DE EDICIÓN MANUAL DE INGREDIENTES (PARA RECETA BASE) ---
    st.markdown("---")
    st.header("⚙️ Edición Manual de Materia Prima")
    st.markdown("Aquí puede editar las cantidades base (para 200L) o los precios de compra de las Materias Primas para la simulación.")

    # --- LÓGICA DE CARGA DE INGREDIENTES ---
    ingredientes_receta = []
    if receta_id_seleccionada is not None:
        ingredientes_receta = obtener_ingredientes_receta(conn, receta_id_seleccionada)

    ingredientes_receta_df = pd.DataFrame(ingredientes_receta)

    # FIX: Asegurar que los nombres de las columnas de la DB coincidan con lo esperado en calcular_costo_total
    if not ingredientes_receta_df.empty:
        ingredientes_receta_df.rename(columns={'nombre': 'Materia Prima', 'unidad': 'Unidad'}, inplace=True)
        # Asegurar que las columnas manuales existan para la fusión/cálculo
        ingredientes_receta_df['precio_unitario_manual'] = 0.0
        ingredientes_receta_df['cotizacion_usd_manual'] = 1.0
        ingredientes_receta_df['Temporal'] = False
        ingredientes_receta_df.rename(columns={'cantidad': 'Cantidad Base (200L)'}, inplace=True)

    # --- Aplicar ingredientes temporales/manuales ---
    ingredientes_a_calcular = ingredientes_receta_df.copy()

    if st.session_state.ingredientes_temporales:
        temp_df = pd.DataFrame(st.session_state.ingredientes_temporales)
        temp_df.rename(columns={'cantidad_base': 'Cantidad Base (200L)', 'nombre': 'Materia Prima', 
                                'unidad': 'Unidad', 'precio_unitario': 'precio_unitario_manual', 
                                'cotizacion_usd': 'cotizacion_usd_manual'}, inplace=True)
        temp_df['Temporal'] = True
        
        # 1. Crear un índice de los IDs temporales
        temp_ids = temp_df['materia_prima_id'].tolist()
        
        # 2. Filtrar los ingredientes base que NO tienen una versión temporal (o son MP nuevas temp, que tienen ID -1)
        base_filtered = ingredientes_a_calcular[~ingredientes_a_calcular['materia_prima_id'].isin(temp_ids)].copy()
        
        # 3. Combinar (concatenar) los ingredientes base no modificados con los temporales
        # Esto reemplaza efectivamente los ingredientes base con sus versiones temporales si el ID coincide.
        # Las MP nuevas (ID=-1) también se añaden.
        ingredientes_a_calcular = pd.concat([base_filtered, temp_df], ignore_index=True)
        
    # --- CÁLCULOS DE CANTIDADES ---
    if not ingredientes_a_calcular.empty:
        # 1. Calcular el factor de escalado
        factor_escalado = cantidad_litros / BASE_LITROS
        
        # 2. Calcular la cantidad simulada
        ingredientes_a_calcular['cantidad_simulada'] = ingredientes_a_calcular['Cantidad Base (200L)'] * factor_escalado

        # 3. PREVIO DEL PRECIO UNITARIO BASE (USD) PARA LA VISUALIZACIÓN
        ingredientes_a_calcular['Precio Unitario (USD) BASE'] = 0.0
        
        for index, row in ingredientes_a_calcular.iterrows():
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
            
            ingredientes_a_calcular.loc[index, 'Precio Unitario (USD) BASE'] = precio_base_usd_final
            
        # 4. Configurar el editor de datos (vista Excel)
        column_config = {
            "id_ingrediente_receta": st.column_config.TextColumn(disabled=True),
            "Materia Prima": st.column_config.TextColumn(disabled=True),
            "materia_prima_id": st.column_config.TextColumn(disabled=True),
            "Unidad": st.column_config.TextColumn(disabled=True),
            "Temporal": st.column_config.TextColumn(disabled=True, help="Indica si es un ingrediente temporal/modificado."),
            "Cantidad Base (200L)": st.column_config.NumberColumn(
                help="Cantidad requerida para la base (200L). Editable para la simulación.", 
                format="%.4f",
            ),
            "cantidad_simulada": st.column_config.NumberColumn("Cantidad (Total L)", format="%.4f", disabled=True),
            "Precio Unitario (USD) BASE": st.column_config.NumberColumn(
                "Precio Unit. (USD) BD/Auto", format="$ %.4f", disabled=True, 
                help="Precio base de compra de la BD, solo si está en USD."
            ),
            "precio_unitario_manual": st.column_config.NumberColumn(
                "Precio Unit. Manual (USD)", 
                help="Sobrescribe el precio de la BD. Usar 0.0 para usar la BD. Solo tiene efecto si 'Cotización USD Manual' es > 1.0",
                format="%.4f"
            ),
            "cotizacion_usd_manual": st.column_config.NumberColumn(
                "Cotización USD Manual", 
                help="Cotización USD a usar si se aplica el Precio Unitario Manual.",
                min_value=1.0, 
                format="%.2f"
            ),
            "Quitar": st.column_config.CheckboxColumn(
                "Quitar de Receta", help="Marque para excluir este ingrediente de la simulación.", default=False
            )
        }

        edited_ingredientes_df = st.data_editor(
            ingredientes_a_calcular,
            column_config=column_config,
            use_container_width=True,
            height=400,
            hide_index=True,
            column_order=["Materia Prima", "Unidad", "Cantidad Base (200L)", "cantidad_simulada", "Precio Unitario (USD) BASE", "precio_unitario_manual", "cotizacion_usd_manual", "Quitar", "Temporal"],
            key="editor_ingredientes_receta"
        )
        
        # 5. Guardar los cambios manuales en el session_state para que persistan
        st.session_state.ingredientes_temporales = []
        for index, row in edited_ingredientes_df.iterrows():
            # Si la cantidad base o el precio manual se modificó, o si es temporal (ID -1), guardar en temporales
            is_modified = row['Cantidad Base (200L)'] != ingredientes_a_calcular.loc[index, 'Cantidad Base (200L)']
            is_modified = is_modified or row['precio_unitario_manual'] != ingredientes_a_calcular.loc[index, 'precio_unitario_manual']
            is_modified = is_modified or row['cotizacion_usd_manual'] != ingredientes_a_calcular.loc[index, 'cotizacion_usd_manual']
            is_modified = is_modified or row['Temporal'] == True

            if (is_modified or row['materia_prima_id'] == -1) and not row['Quitar']:
                st.session_state.ingredientes_temporales.append({
                    'nombre': row['Materia Prima'],
                    'unidad': row['Unidad'],
                    'cantidad_base': row['Cantidad Base (200L)'],
                    'precio_unitario': row['precio_unitario_manual'],
                    'cotizacion_usd': row['cotizacion_usd_manual'],
                    'materia_prima_id': row['materia_prima_id'],
                })

        # Sincronizar el DataFrame local con los valores editados de Cantidad Base
        ingredientes_a_calcular['Cantidad Base (200L)'] = edited_ingredientes_df['Cantidad Base (200L)']
        ingredientes_a_calcular['precio_unitario_manual'] = edited_ingredientes_df['precio_unitario_manual']
        ingredientes_a_calcular['cotizacion_usd_manual'] = edited_ingredientes_df['cotizacion_usd_manual']

        # Recalcular la cantidad simulada después de una posible edición de Cantidad Base
        factor_escalado_recalc = cantidad_litros / BASE_LITROS
        ingredientes_a_calcular['cantidad_simulada'] = ingredientes_a_calcular['Cantidad Base (200L)'] * factor_escalado_recalc

        # Aplicar filtro de Quitar ingredientes
        ingredientes_a_calcular['Quitar'] = edited_ingredientes_df.get('Quitar', False)
        ingredientes_a_calcular = ingredientes_a_calcular[ingredientes_a_calcular['Quitar'] == False]
        
        # Calcular costos
        costo_mp_total, detalle_costo_df, costo_mp_base_ars, costo_recargo_mp_ars, costo_total_mp_usd = \
            calcular_costo_total(ingredientes_a_calcular, cotizacion_dolar_actual, conn)
            
    else:
        st.warning(f"No hay ingredientes para la receta '{receta_seleccionada_nombre}'. Por favor, agregue Materias Primas.")
        costo_mp_total = 0.0
        detalle_costo_df = pd.DataFrame()
        costo_mp_base_ars = 0.0
        costo_recargo_mp_ars = 0.0
        costo_total_mp_usd = 0.0


    # =================================================================================================
    # CÁLCULOS DE COSTOS FIJOS Y TOTALES
    # =================================================================================================
    
    # --------------------------------------------------------------------------------------
    # 1. CÁLCULO DEL FLETE (ESCALADO)
    # --------------------------------------------------------------------------------------
    # Flete escalado: (Costo Base / Litros Base) * Litros Actuales
    if BASE_LITROS > 0:
        costo_flete_total_ars = (flete_base_200l / BASE_LITROS) * cantidad_litros
    else:
        costo_flete_total_ars = 0.0

    # --------------------------------------------------------------------------------------
    # 2. CÁLCULO DEL GASTO INDIRECTO (OVERHEAD)
    # --------------------------------------------------------------------------------------
    # Gasto Indirecto (Overhead) = Costo Indirecto por Litro * Litros Actuales
    gasto_indirecto_tanda = costo_indirecto_por_litro * cantidad_litros

    # --------------------------------------------------------------------------------------
    # 3. CÁLCULO DEL COSTO DE ENVASE (YA SE HIZO ARRIBA)
    # --------------------------------------------------------------------------------------
    # costo_envase_total_ars ya calculado

    # --------------------------------------------------------------------------------------
    # 4. CALCULAR COSTOS FINALES (SUMA DE TODOS LOS COMPONENTES)
    # --------------------------------------------------------------------------------------
    costo_total_mp_y_recargo = costo_mp_total # Costo Materia Prima + Recargo (ARS)
    costo_flete_general_tanda = costo_flete_total_ars # Flete (ARS)
    gasto_indirecto_tanda = costo_indirecto_por_litro * cantidad_litros # Overhead (ARS)

    # NUEVO: Sumar el costo del envase
    costo_total_final = costo_total_mp_y_recargo + costo_flete_general_tanda + gasto_indirecto_tanda + costo_envase_total_ars

    if cantidad_litros > 0:
        costo_por_litro_ars = costo_total_final / cantidad_litros
        costo_por_litro_usd = costo_por_litro_ars / cotizacion_dolar_actual
    else:
        costo_por_litro_ars = 0.0
        costo_por_litro_usd = 0.0

    st.markdown("---")
    st.header(f"💰 Resumen de Costos (para {cantidad_litros:,.2f} Litros)")
    col_res1, col_res2, col_res3 = st.columns(3)

    # --------------------------------------------------------------------------------------
    # MÉTRICAS DE RESULTADO (MOSTRAR USD COMO VALOR PRINCIPAL Y ENVASE)
    # --------------------------------------------------------------------------------------
    
    # Se calcula el costo de MP (Base, sin recargo) en USD
    if cotizacion_dolar_actual > 0:
        costo_mp_base_solo_usd = costo_total_mp_usd - (costo_recargo_mp_ars / cotizacion_dolar_actual)
    else:
        costo_mp_base_solo_usd = 0.0

    col_res1.metric(
        "Costo Materia Prima (Base)",
        f"USD ${costo_mp_base_solo_usd:,.2f}", # <--- CAMBIO PRINCIPAL: Muestra USD
        help=f"Equivalente a ARS ${costo_mp_base_ars:,.2f} (a la cotización actual)" # <--- CAMBIO EN HELP: Muestra ARS
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

    col_res2.metric(
        "Overhead Operativo (Escalado)",
        f"${gasto_indirecto_tanda:,.2f} ARS",
        help=f"Costo Indirecto por Litro: ${costo_indirecto_por_litro:,.2f} ARS/L"
    )

    # NUEVA MÉTRICA: COSTO DE ENVASE
    col_res3.metric(
        "Costo Envase (Total)",
        f"${costo_envase_total_ars:,.2f} ARS",
        help=f"Unidades: {unidades_necesarias} @ ${precio_envase_unitario_ars:,.2f} c/u (ARS actualizado)"
    )

    col_res3.metric(
        "COSTO TOTAL FINAL (ARS)",
        f"${costo_total_final:,.2f} ARS",
        delta_color="inverse",
        help="Suma de MP + Flete + Overhead + Envase"
    )

    st.metric(
        "Costo Por Litro (ARS/L)",
        f"${costo_por_litro_ars:,.2f} ARS/L",
        delta_color="inverse",
        help=f"Costo por Litro en USD: USD ${costo_por_litro_usd:,.2f}/L"
    )

    # --------------------------------------------------------------------------------------
    # DETALLE DE COSTO DE MATERIA PRIMA
    # --------------------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("Detalle del Costo de Materia Prima")

    if not detalle_costo_df.empty:
        col_config_detalle = {
            "Costo Unit. ARS (Base)": st.column_config.NumberColumn(format="%.4f", help="Costo de MP sin recargo, a la cotización actual."),
            "Recargo 3% ARS (Unit.)": st.column_config.NumberColumn(format="%.4f"),
            "Costo Unit. ARS (Total)": st.column_config.NumberColumn(format="%.4f"),
            "Costo Total ARS": st.column_config.NumberColumn(format="$ %.2f"),
            "Costo Unit. USD (Base)": st.column_config.NumberColumn(format="%.4f"),
            "Recargo 3% USD (Unit.)": st.column_config.NumberColumn(format="%.4f"),
            "Costo Unit. USD (Total)": st.column_config.NumberColumn(format="%.4f"),
            "Costo Total USD": st.column_config.NumberColumn(format="$ %.2f"),
        }
        
        st.dataframe(
            detalle_costo_df, 
            column_config=col_config_detalle, 
            use_container_width=True, 
            hide_index=True,
            # Mostrar solo columnas esenciales
            column_order=["Materia Prima", "Cantidad (Simulada)", "Unidad", "Moneda Origen", "Costo Unit. ARS (Total)", "Costo Total ARS", "Costo Total USD"]
        )
    else:
        st.info("No se ha calculado el costo de Materia Prima.")


    # =================================================================================================
    # CONTROLES DE SIMULACIÓN Y PRESUPUESTO (REORDENADO)
    # =================================================================================================

    # --------------------------------------------------------------------------------------
    # 1. AGREGAR A PRESUPUESTO
    # --------------------------------------------------------------------------------------
    st.markdown("---")
    st.header("🛒 Agregar a Presupuesto")

    if receta_seleccionada_nombre != "--- Seleccionar Receta ---" and cantidad_litros > 0 and costo_total_final > 0:
        with st.form("form_agregar_simulacion"):
            col_margen, col_cant_tandas = st.columns(2)
            
            margen_ganancia_inicial = col_margen.number_input(
                "Margen de Ganancia Inicial (%):", 
                min_value=0.0, 
                value=35.0, 
                step=1.0, 
                format="%.2f", 
                key="margen_ganancia_input",
                help="Margen de ganancia a aplicar sobre el costo total (MP + Flete + Overhead + Envase) para esta simulación."
            )
            
            cantidad_a_agregar = col_cant_tandas.number_input(
                f"Nro. de Tandas a Agregar (c/u de {cantidad_litros:,.2f} L):", 
                min_value=1, 
                value=1, 
                step=1, 
                key="cantidad_tandas_input"
            )
            
            if st.form_submit_button(f"Agregar {receta_seleccionada_nombre} (x{cantidad_a_agregar}) al Presupuesto"):
                # Se guarda la simulación en el estado
                simulacion_data = {
                    'nombre_receta': receta_seleccionada_nombre,
                    'litros': cantidad_litros * cantidad_a_agregar,
                    'costo_total_ars': costo_total_final * cantidad_a_agregar,
                    'costo_por_litro_ars': costo_por_litro_ars,
                    'gasto_indirecto_tanda': gasto_indirecto_tanda * cantidad_a_agregar,
                    'costo_flete_total_ars': costo_flete_total_ars * cantidad_a_agregar,
                    'costo_envase_total_ars': costo_envase_total_ars * cantidad_a_agregar, # NUEVO
                    'margen_ganancia': margen_ganancia_inicial,
                    'cantidad_tandas': cantidad_a_agregar,
                    'detalle_mp_json_unitario': detalle_costo_df.to_json(orient='records'),
                    'envase_info_json': json.dumps({ # NUEVO
                        'envase_nombre': envase_seleccionado_nombre,
                        # Unidades_necesarias es para 1 tanda, se multiplica por la cantidad a agregar
                        'unidades_necesarias_total': unidades_necesarias * cantidad_a_agregar, 
                        'precio_unitario_ars': precio_envase_unitario_ars # Precio en ARS ya convertido
                    })
                }
                st.session_state['simulaciones_presupuesto'].append(simulacion_data)
                st.session_state['presupuesto_data_for_print'] = {} # Limpiar datos de impresión
                st.success(f"Simulación de {receta_seleccionada_nombre} (x{cantidad_a_agregar}) agregada con Margen Inicial del {margen_ganancia_inicial:.2f}%.")
                st.rerun()

    else:
        st.warning("Seleccione una Receta, ingrese Litros a Simular (>0) y asegúrese de tener costos calculados para agregar al presupuesto.")

    # --------------------------------------------------------------------------------------
    # 2. TABLA DE PRESUPUESTO ACUMULADO (MUEVE AQUÍ)
    # --------------------------------------------------------------------------------------
    if st.session_state['simulaciones_presupuesto']:
        st.markdown("---")
        st.header("📝 Presupuesto Acumulado (Edición)")

        # Convertir datos a DataFrame
        df_presupuesto = pd.DataFrame(st.session_state['simulaciones_presupuesto'])
        
        # Añadir la columna de ID para la edición
        df_presupuesto['ID'] = df_presupuesto.index + 1
        
        # CORRECCIÓN TEMPORAL: Asegurar que la columna 'litros' esté como 'Litros' para la tabla de edición
        df_presupuesto.rename(columns={'litros': 'Litros'}, inplace=True) # <-- CORRECCIÓN APLICADA AQUÍ PARA LA EDICIÓN
        

        # Calcular el Costo Total (Base, sin margen, con envase)
        df_presupuesto['Costo Total ARS'] = df_presupuesto['costo_total_ars']
        # Calcular el costo de Envase para la tabla
        df_presupuesto['Costo Envase ARS'] = df_presupuesto['costo_envase_total_ars']

        # Preparar para el editor
        df_presupuesto_editor = df_presupuesto[[
            'ID', 'nombre_receta', 'Litros', 'Costo Total ARS', 'Costo Envase ARS', 
            'margen_ganancia'
        ]].copy()

        df_presupuesto_editor.rename(columns={
            'nombre_receta': 'Receta',
            'margen_ganancia': 'Margen_Ganancia'
        }, inplace=True)

        # CORRECCIÓN CLAVE: Agregar la columna 'Eliminar' al DataFrame de entrada con valor False
        df_presupuesto_editor['Eliminar'] = False 

        column_config_presupuesto = {
            "ID": st.column_config.TextColumn(disabled=True),
            "Receta": st.column_config.TextColumn(disabled=True),
            "Litros": st.column_config.NumberColumn(disabled=True, format="%.2f"),
            "Costo Total ARS": st.column_config.NumberColumn(disabled=True, format="$ %.2f"),
            "Costo Envase ARS": st.column_config.NumberColumn(disabled=True, format="$ %.2f"),
            "Margen_Ganancia": st.column_config.NumberColumn(
                "Margen_Ganancia (%)", 
                min_value=0.0, 
                format="%.2f",
                help="Margen de ganancia editable para el precio final."
            ),
            "Eliminar": st.column_config.CheckboxColumn("Eliminar", default=False)
        }

        edited_presupuesto_df = st.data_editor(
            df_presupuesto_editor,
            column_config=column_config_presupuesto,
            use_container_width=True,
            hide_index=True,
            column_order=['ID', 'Receta', 'Litros', 'Costo Total ARS', 'Costo Envase ARS', 'Margen_Ganancia', 'Eliminar'],
            key="editor_presupuesto_acumulado"
        )
        
        # Aplicar los cambios al session_state
        nuevas_simulaciones = []
        for index, row in edited_presupuesto_df.iterrows():
            if not row['Eliminar']:
                # Buscar la simulación original por ID para mantener los datos internos (como JSON de detalle)
                original_sim = df_presupuesto[df_presupuesto['ID'] == row['ID']].iloc[0].to_dict()
                original_sim['margen_ganancia'] = row['Margen_Ganancia']
                
                # Se revierte el nombre de la columna 'Litros' a 'litros' para guardar en el session state
                if 'Litros' in original_sim:
                    original_sim['litros'] = original_sim.pop('Litros')
                    
                nuevas_simulaciones.append(original_sim)

        st.session_state['simulaciones_presupuesto'] = nuevas_simulaciones

        # Botón para recalcular y generar presupuesto final
        if st.button("🛒 Calcular Precio Final y Generar Presupuesto"):
            if not st.session_state['simulaciones_presupuesto']:
                st.warning("Debe haber al menos una simulación en el presupuesto.")
            else:
                # Forzar un rerun para aplicar los márgenes editados y calcular el final
                st.success("Recalculando el Presupuesto Final...")
                st.rerun() # Esto hará que el cálculo final se ejecute en la siguiente pasada


    # =================================================================================================
    # PRESUPUESTO FINAL Y REPORTE
    # =================================================================================================

    if st.session_state['simulaciones_presupuesto'] and st.session_state.get('presupuesto_data_for_print') == {}:
        # Lógica para calcular y mostrar el presupuesto final
        
        # Recalcular el Precio de Venta (con el margen de ganancia posiblemente editado)
        df_final = pd.DataFrame(st.session_state['simulaciones_presupuesto'])
        
        # CORRECCIÓN: Renombrar 'litros' a 'Litros' para su uso en los cálculos
        df_final.rename(columns={'litros': 'Litros'}, inplace=True) # <-- ESTA ES LA CORRECCIÓN CLAVE
        
        # Calcular el Precio de Venta Total
        df_final['Precio_Venta_Total_ARS'] = df_final['costo_total_ars'] * (1 + df_final['margen_ganancia'] / 100)
        
        litros_total_acumulado = df_final['Litros'].sum()
        costo_total_acumulado = df_final['costo_total_ars'].sum()
        precio_final_ars_total = df_final['Precio_Venta_Total_ARS'].sum()
        
        df_final['Precio_Venta_Unitario_ARS'] = df_final['Precio_Venta_Total_ARS'] / df_final['Litros']
        df_final['Precio_Venta_Unitario_USD'] = df_final['Precio_Venta_Unitario_ARS'] / cotizacion_dolar_actual
        df_final['Costo_Unitario_ARS'] = df_final['costo_total_ars'] / df_final['Litros']

        precio_unitario_ars_litro_AVG = precio_final_ars_total / litros_total_acumulado
        
        st.markdown("---")
        st.header("📝 Guardar y Generar Presupuesto Final")
        cliente_nombre = st.text_input("Nombre del Cliente:", key="cliente_nombre_input")
        
        if cliente_nombre and st.button("Guardar Presupuesto en DB y Generar PDF"):
            if not st.session_state['simulaciones_presupuesto']:
                st.warning("El presupuesto está vacío.")
                return

            # 1. Guardar en la Base de Datos
            try:
                cliente_id = get_or_create_client(conn, cliente_nombre)
                
                # Columnas finales para el JSON de detalle
                # CREAMOS UN DATAFRAME LIMPIO CON SOLO LAS COLUMNAS REQUERIDAS
                df_final_detalle = df_final[[
                    'Litros', 'costo_total_ars', 'costo_por_litro_ars', 
                    'gasto_indirecto_tanda', 'costo_flete_total_ars', 'costo_envase_total_ars',
                    'margen_ganancia', 'cantidad_tandas', 'detalle_mp_json_unitario', 
                    'envase_info_json', 'nombre_receta', 
                    'Precio_Venta_Total_ARS', 'Precio_Venta_Unitario_ARS'
                ]].copy()
                
                # Renombrado de columnas para el JSON
                df_final_detalle.rename(columns={
                    'nombre_receta': 'Receta', 
                    'margen_ganancia': 'Margen_Ganancia',
                    'costo_total_ars': 'Costo_Total_ARS', 
                    'costo_envase_total_ars': 'Costo_Envase_ARS',
                    'costo_por_litro_ars': 'Costo_Unitario_ARS_Litro',
                    'Precio_Venta_Total_ARS': 'Venta_Total_ARS',
                    'Precio_Venta_Unitario_ARS': 'Venta_Unitario_ARS_Litro'
                }, inplace=True)
                
                # Se genera el JSON con nombres de columna únicos (FIX al error 'DataFrame columns must be unique')
                detalle_simulaciones_json = df_final_detalle.to_json(orient='records') 
                
                # Porcentaje de ganancia global (se podría mejorar, usando el promedio ponderado o el margen más alto)
                porcentaje_ganancia_global_bd = df_final_detalle['Margen_Ganancia'].mean()

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

                df_presentacion = df_final_detalle[['Receta', 'Litros', 'Costo_Total_ARS', 'Margen_Ganancia', 'Venta_Total_ARS', 'Venta_Unitario_ARS_Litro']].copy()
                
                df_presentacion.rename(columns={
                    'Costo_Total_ARS': 'Costo_Total_ARS',
                    'Margen_Ganancia': 'Margen_Aplicado (%)',
                    'Venta_Total_ARS': 'Venta_Total_ARS',
                    'Venta_Unitario_ARS_Litro': 'Venta_Unitario_ARS/L'
                }, inplace=True)
                
                df_presentacion['Ganancia_ARS'] = df_presentacion['Venta_Total_ARS'] - df_presentacion['Costo_Total_ARS']
                df_presentacion['Venta_Unitario_USD/L'] = df_presentacion['Venta_Unitario_ARS/L'] / cotizacion_dolar_actual
                df_presentacion['Venta_Total_USD'] = df_presentacion['Venta_Total_ARS'] / cotizacion_dolar_actual

                # Preparar datos para el PDF
                
                # FIX FINAL PARA EL PDF: Se extrae la información de envase para el reporte
                df_pdf_data = df_final[['nombre_receta', 'Litros', 'Precio_Venta_Unitario_ARS', 'Precio_Venta_Unitario_USD', 'Precio_Venta_Total_ARS', 'envase_info_json']].copy()

                # 1. Deserializar el JSON y extraer la información del envase
                def extract_envase_info(envase_json):
                    info = json.loads(envase_json)
                    # Retorna una Serie de Pandas
                    return pd.Series([info.get('envase_nombre', 'N/A'), info.get('unidades_necesarias_total', 0)])

                # Aplicar la función y expandir a nuevas columnas en df_pdf_data
                df_pdf_data[['Envase_Nombre', 'Unidades_Envase_Total']] = df_pdf_data['envase_info_json'].apply(extract_envase_info)
                
                df_pdf_data.rename(columns={'nombre_receta': 'Receta'}, inplace=True)
                # Calcular Precio_Venta_Total_USD aquí para evitar problemas en el PDF
                df_pdf_data['Precio_Venta_Total_USD'] = df_pdf_data['Precio_Venta_Total_ARS'] / cotizacion_dolar_actual
                
                # Quitar la columna envase_info_json antes de guardar en session_state, ya que se extrajo la información
                df_pdf_data.drop(columns=['envase_info_json'], inplace=True)
                
                # Guardar en session state para el bloque de impresión
                st.session_state['presupuesto_data_for_print'] = {
                    'cliente_nombre': cliente_nombre,
                    'cotizacion_dolar_actual': cotizacion_dolar_actual,
                    'presupuesto_id': presupuesto_id,
                    'precio_unitario_ars_litro': precio_unitario_ars_litro_AVG,
                    'precio_final_ars': precio_final_ars_total,
                    'litros_total_acumulado': litros_total_acumulado,
                    'df_detalle_final_presupuesto': df_pdf_data
                }


                st.rerun() # Volver a correr para mostrar el PDF

            except sqlite3.Error as e: 
                st.error(f"❌ Error al guardar el presupuesto en la base de datos: {e}")
            except Exception as e: 
                # Este error ahora debería estar solucionado
                st.error(f"❌ Ocurrió un error inesperado al generar el presupuesto: {e}")

    # Este bloque ahora está correctamente fuera del try/except
    if st.session_state.get('presupuesto_data_for_print') != {}: 
        # Muestra el detalle final y el botón de descarga del PDF
        data_pdf = st.session_state['presupuesto_data_for_print']
        
        # Muestra la tabla de presentación final si existe
        if 'df_detalle_final_presupuesto' in data_pdf:
            st.markdown("**Detalle de Venta por Producto**")
            df_display = data_pdf['df_detalle_final_presupuesto'].copy()
            
            # CALCULAR PRECIO UNITARIO POR ENVASE (SOLICITADO EN LA INTERFAZ)
            df_display['Precio_Venta_Unitario_ARS_UNIDAD'] = df_display.apply(
                lambda row: row['Precio_Venta_Total_ARS'] / row['Unidades_Envase_Total'] if row['Unidades_Envase_Total'] > 0 else 0.0,
                axis=1
            )
            # Se usa el total USD calculado / unidades de envase
            df_display['Precio_Venta_Unitario_USD_UNIDAD'] = df_display.apply(
                lambda row: row['Precio_Venta_Total_USD'] / row['Unidades_Envase_Total'] if row['Unidades_Envase_Total'] > 0 else 0.0,
                axis=1
            )
            
            # Renombrar para display
            df_display.rename(columns={
                'Precio_Venta_Unitario_ARS': 'Precio Unitario ARS/L (Ref.)', # Renombrado para ser referencia
                'Precio_Venta_Unitario_USD': 'Precio Unitario USD/L (Ref.)', # Renombrado para ser referencia
                'Precio_Venta_Unitario_ARS_UNIDAD': 'Precio Unitario ARS/u.', # NUEVO
                'Precio_Venta_Unitario_USD_UNIDAD': 'Precio Unitario USD/u.', # NUEVO
                'Precio_Venta_Total_ARS': 'Precio Total ARS',
                'Precio_Venta_Total_USD': 'Precio Total USD',
                'Envase_Nombre': 'Tipo de Envase', 
                'Unidades_Envase_Total': 'Unidades de Envase', 
            }, inplace=True)

            st.dataframe(
                df_display, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    'Litros': st.column_config.NumberColumn(format="%.2f"),
                    'Tipo de Envase': st.column_config.TextColumn(), 
                    'Unidades de Envase': st.column_config.NumberColumn(format="%.0f"), 
                    'Precio Unitario ARS/u.': st.column_config.NumberColumn(format="$ %.2f"), # NUEVO
                    'Precio Unitario USD/u.': st.column_config.NumberColumn(format="$ %.2f"), # NUEVO
                    'Precio Unitario ARS/L (Ref.)': st.column_config.NumberColumn(format="$ %.2f"), # Ref.
                    'Precio Unitario USD/L (Ref.)': st.column_config.NumberColumn(format="$ %.2f"), # Ref.
                    'Precio Total ARS': st.column_config.NumberColumn(format="$ %.2f"),
                    'Precio Total USD': st.column_config.NumberColumn(format="$ %.2f"),
                },
                # Priorizar las unidades por envase y añadir las de referencia al final
                column_order=['Receta', 'Litros', 'Tipo de Envase', 'Unidades de Envase', 'Precio Unitario ARS/u.', 'Precio Unitario USD/u.', 'Precio Total ARS', 'Precio Total USD', 'Precio Unitario ARS/L (Ref.)', 'Precio Unitario USD/L (Ref.)']
            )

        # Generar el PDF
        pdf_content = generate_pdf_reportlab(data_pdf)

        st.download_button(
            label="Descargar Presupuesto PDF",
            data=pdf_content,
            file_name=f"Presupuesto_{data_pdf['presupuesto_id']}_{data_pdf['cliente_nombre']}_{date.today().isoformat()}.pdf",
            mime="application/pdf"
        )
        
        # Botón para limpiar el presupuesto final y empezar uno nuevo
        if st.button("Limpiar Presupuesto Actual y Empezar Nuevo"):
            st.session_state['simulaciones_presupuesto'] = []
            st.session_state['presupuesto_data_for_print'] = {}
            st.rerun()

    # =================================================================================================
    # CONTROLES DE EDICIÓN AUXILIAR (MOVIDOS AL FINAL)
    # =================================================================================================
    
    st.markdown("---")
    st.header("Herramientas de Simulación (Ajuste de MP)")

    # --------------------------------------------------------------------------------------
    # 1. AGREGAR NUEVA MATERIA PRIMA (TEMPORAL)
    # --------------------------------------------------------------------------------------
    st.subheader("➕ Agregar MP Nueva (Solo Simulación)")
    
    with st.form("form_mp_nueva"):
        col_mp_nombre, col_mp_unidad = st.columns(2)
        mp_nombre = col_mp_nombre.text_input("Nombre de la Nueva MP:", key="mp_nueva_nombre")
        mp_unidad = col_mp_unidad.text_input("Unidad (Kg/L/u):", value="Kg", key="mp_nueva_unidad")
        
        col_cant, col_precio, col_cot = st.columns(3)
        cantidad_base = col_cant.number_input("Cantidad Base (para 200L):", min_value=0.0001, value=0.0001, step=0.01, format="%.4f", key="mp_nueva_cantidad")
        precio_unitario_usd = col_precio.number_input("Precio Unitario (USD):", min_value=0.0, value=0.0, step=0.01, format="%.4f", key="mp_nueva_precio_usd")
        cotizacion_usd = col_cot.number_input("Cotización USD de Compra/Referencia:", min_value=1.0, value=cotizacion_dolar_actual, step=1.0, format="%.2f", 
                                              help="USD usado para calcular el costo real en ARS.", key="mp_nueva_cot_usd")
        
        if st.form_submit_button("Agregar MP Nueva"):
            if mp_nombre and cantidad_base > 0 and precio_unitario_usd > 0:
                st.session_state.ingredientes_temporales.append({
                    'nombre': mp_nombre, 'unidad': mp_unidad, 'cantidad_base': cantidad_base,
                    'precio_unitario': precio_unitario_usd, 'cotizacion_usd': cotizacion_usd, 'materia_prima_id': -1 
                })
                st.success(f"MP '{mp_nombre}' (Temporal) agregada.")
                st.rerun()
            else:
                st.warning("Debe completar todos los campos para la nueva MP.")


    # --------------------------------------------------------------------------------------
    # 2. AGREGAR / ACTUALIZAR MP EXISTENTE
    # --------------------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("➕ Actualizar MP Existente / Base")
    
    materias_primas = obtener_todas_materias_primas(conn)
    mp_map = {mp['nombre']: mp for mp in materias_primas}
    mp_nombres = ["--- Seleccionar MP Existente ---"] + [mp['nombre'] for mp in materias_primas]
    
    with st.form("form_mp_existente"):
        col_mp, col_cant_existente, col_precio_usd = st.columns([0.4, 0.3, 0.3])
        
        mp_existente_nombre = col_mp.selectbox("Materia Prima a Actualizar:", mp_nombres, key="mp_existente_nombre")
        
        cantidad_base_existente = col_cant_existente.number_input(
            "Cantidad Base (para 200L):", 
            min_value=0.0,  
            value=0.0, 
            step=0.01, 
            format="%.4f", 
            key="mp_existente_cantidad"
        )
        
        precio_unitario_usd_existente = col_precio_usd.number_input("Precio Unitario (USD):", min_value=0.0, value=0.0, step=0.01, format="%.4f", key="mp_existente_precio_usd")
        
        col_cot_usd, _, _ = st.columns(3)
        cotizacion_usd_existente = col_cot_usd.number_input("Cotización USD de Compra/Referencia:", min_value=1.0, value=cotizacion_dolar_actual, step=1.0, format="%.2f", 
                                                            help="USD usado cuando la MP se 'registró' esta compra (ARS/USD).", key="mp_existente_cot_usd")
        
        if st.form_submit_button("Agregar/Actualizar MP Existente"):
            if mp_existente_nombre != "--- Seleccionar MP Existente ---" and cantidad_base_existente > 0 and precio_unitario_usd_existente > 0:
                mp_info = mp_map[mp_existente_nombre]
                # Se busca si ya existe en temporales para reemplazar
                existente = next((item for item in st.session_state.ingredientes_temporales if item.get('materia_prima_id') == mp_info['id'] and item['nombre'] == mp_info['nombre']), None)
                
                new_data = {
                    'nombre': mp_info['nombre'],
                    'unidad': mp_info['unidad'],
                    'cantidad_base': cantidad_base_existente,
                    'precio_unitario': precio_unitario_usd_existente,
                    'cotizacion_usd': cotizacion_usd_existente,
                    'materia_prima_id': mp_info['id']
                }
                
                if existente:
                    # En lugar de remover y agregar, se busca y se actualiza en el DataFrame si existe, pero el patrón es remover y agregar.
                    st.session_state.ingredientes_temporales.remove(existente)
                    st.session_state.ingredientes_temporales.append(new_data)
                    st.success(f"MP '{mp_info['nombre']}' (Existente) actualizada.")
                else:
                    st.session_state.ingredientes_temporales.append(new_data)
                    st.success(f"MP '{mp_info['nombre']}' (Existente) agregada/actualizada en la simulación.")

                st.rerun()

            else:
                st.warning("Debe seleccionar una MP y/o las cantidades/precios deben ser mayores a cero.")


    st.markdown("---")
    if st.button("🔄 Aplicar Cambios Manuales y Recalcular Costo", help="Aplica los cambios de precios y cantidades manuales/temporales al estado de la simulación..."):
        # Esto realmente solo fuerza un rerun, la lógica de guardado está en los botones de agregar/actualizar MP
        st.success("Estado de simulación actualizado.")
        st.rerun()
        
    if st.button("🗑️ Limpiar TODOS los Cambios Manuales (MP y Gastos) y Recargar Receta Base"):
        st.session_state.ingredientes_temporales = []
        st.session_state.gastos_temporales_simulacion = []
        st.session_state.gasto_fijo_mensual_total = 0.0 # Esto forzará la recarga de los valores de la DB en el editor
        st.success("Cambios temporales eliminados. Recargando receta base y gastos DB.")
        st.rerun()

    conn.close()

if __name__ == "__main__":
    main()