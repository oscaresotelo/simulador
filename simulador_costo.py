import streamlit as st
import sqlite3
import pandas as pd
from datetime import date
import calendar 
import json 
import base64 
import io

# =================================================================================================
# IMPORTACIONES REPORTLAB (NUEVAS)
# =================================================================================================
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape # Importamos landscape 


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
    # Corrección: Uso correcto de sqlite3.Row
    conn.row_factory = sqlite3.Row 
    return conn

def fetch_df(query, params=()):
    """Ejecuta una consulta SELECT y devuelve los resultados como un DataFrame de Pandas."""
    conn = get_connection()
    try:
        df = pd.read_sql_query(query, conn, params=params)
    except sqlite3.Error as e:
        st.error(f"Error al ejecutar la consulta: {e}")
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

def get_detalle_gastos_mensual(mes_simulacion, anio_simulacion):
    """
    Recupera el detalle de los gastos fijos para el mes y año seleccionado,
    EXCLUYENDO el registro de parámetro de flete (si existe), y devuelve la suma total.
    """
    mes_str = f"{anio_simulacion:04d}-{mes_simulacion:02d}"
    
    # Aseguramos que el FLETE_BASE_RECETA no se sume al Overhead, aunque ahora es manual, 
    # si existía en la BD, podría distorsionar los gastos operativos.
    FLETE_CATEGORIA_ID_PARAMETRO = get_categoria_id_by_name('FLETE_BASE_RECETA')

    # Filtra la consulta solo si la categoría existe
    filtro_flete = f"AND g.categoria_id != {FLETE_CATEGORIA_ID_PARAMETRO}" if FLETE_CATEGORIA_ID_PARAMETRO is not None else ""
    
    query = f"""
        SELECT 
            g.importe_total AS Importe_ARS
        FROM gastos g
        JOIN categorias_imputacion c ON g.categoria_id = c.id
        WHERE (g.fecha_pago LIKE '{mes_str}-%' OR g.fecha_factura LIKE '{mes_str}-%')
           {filtro_flete} 
    """
    df_gastos = fetch_df(query)
    
    if df_gastos.empty:
        return 0.0, "No se encontraron gastos fijos para este mes (excluyendo Flete)."
    
    total_gasto = df_gastos['Importe_ARS'].sum()
    return total_gasto, None

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
    
    # Tabla Presupuestos (Guarda el resultado final de la simulación para un cliente)
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
# FUNCIONES DE COSTEO DE MATERIA PRIMA 
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
    Obtiene el último precio unitario, flete, otros costos y la cotización USD registrada.
    """
    if materia_prima_id == -1:
        return 0.0, 0.0, 0.0, 1.0

    cursor = conn.cursor()
    # Usamos la tabla compras_materia_prima para una mejor coherencia en la lógica
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
        # Los costos asociados de la BD se ignoran ahora (Flete/Otros Unitarios)
        costo_flete = 0.0 
        otros_costos = 0.0 
        cotizacion_usd_reg = compra['cotizacion_usd'] if compra['cotizacion_usd'] is not None else 1.0
        moneda = compra['moneda']
        
        # Si la moneda es ARS y la cotización no se usó/registró, asumimos que el precio es ARS final.
        if moneda == 'ARS' and cotizacion_usd_reg <= 1.0:
            # Retorna el precio en ARS y cotización 1.0 (no dolarizado)
            return precio_base, costo_flete, otros_costos, 1.0
        else:
            # Si es USD o tiene cotización registrada, el precio_unitario es el USD base.
            # Retorna el precio en USD y la cotización registrada
            return precio_base, costo_flete, otros_costos, cotizacion_usd_reg
    else:
        return 0.0, 0.0, 0.0, 1.0

def calcular_costo_total(ingredientes_df, cotizacion_dolar_actual, conn):
    """
    Calcula el costo total SÓLO de la Materia Prima en ARS.
    MODIFICADO: Se agregan columnas de detalle en USD para la visualización.
    """
    detalle_costo = []
    
    # CONSTANTE DEL NUEVO RECARGO
    RECARGO_FIJO_USD_PERCENT = 0.03 # 3%
    
    # NUEVAS VARIABLES DE AGREGACIÓN
    costo_total_mp_ars = 0.0
    costo_total_recargo_mp_ars = 0.0 
    
    for index, ingrediente in ingredientes_df.iterrows():
        materia_prima_id = ingrediente["materia_prima_id"]
        cantidad_usada = ingrediente["cantidad_simulada"]
        nombre_mp = ingrediente["Materia Prima"]
        
        # 1. Obtener precios registrados/manuales
        precio_manual_unitario = ingrediente.get('precio_unitario_manual', 0.0)
        cotizacion_manual = ingrediente.get('cotizacion_usd_manual', 1.0)

        cotizacion_usd_reg_bd = 1.0 

        precio_base_usd_final = 0.0 
        costo_unitario_ars_registrado = 0.0 

        # Determinar valores de la MP
        if precio_manual_unitario > 0.0:
            # Caso 1: Precio Manual
            if cotizacion_manual > 1.0:
                # Precio manual es USD
                precio_base_usd_final = precio_manual_unitario
                cotizacion_usd_reg_bd = cotizacion_manual
            else:
                # Precio manual es ARS
                costo_unitario_ars_registrado = precio_manual_unitario
        
        elif materia_prima_id != -1:
            # Caso 2: MP de la Base de Datos (compras_materia_prima)
            precio_unitario_reg_base, _, _, cotizacion_usd_reg_bd = \
                obtener_precio_actual_materia_prima(conn, materia_prima_id)
            
            if cotizacion_usd_reg_bd > 1.0:
                 # Precio de BD es el USD base 
                precio_base_usd_final = precio_unitario_reg_base 
            else:
                # Precio de BD es el ARS final 
                costo_unitario_ars_registrado = precio_unitario_reg_base
                
        # 2. CÁLCULO DEL RECARGO (3% sobre el valor USD base)
        recargo_unitario_ars = 0.0
        recargo_unitario_usd = 0.0 # Nuevo: Recargo unitario en USD
        
        if precio_base_usd_final > 0.0:
            # A) Si la MP tiene un precio USD base, aplicamos el 3% sobre ese precio USD.
            recargo_unitario_usd = precio_base_usd_final * RECARGO_FIJO_USD_PERCENT
            
            # Convertimos el recargo USD a ARS usando la cotización actual de simulación
            recargo_unitario_ars = recargo_unitario_usd * cotizacion_dolar_actual
            
            # Convertir el USD base (sin recargo) a ARS usando la cotización actual de simulación
            costo_base_mp_ars_real = precio_base_usd_final * cotizacion_dolar_actual
            moneda_origen = f'USD ({cotizacion_usd_reg_bd:.2f})'
        else:
            # B) Si la MP tiene un precio en ARS fijo, el recargo es 0.
            # Usar el ARS registrado o manual como costo base.
            costo_base_mp_ars_real = costo_unitario_ars_registrado
            moneda_origen = 'ARS (Fijo)'
            # El costo base en USD será 0, ya que es ARS fijo
            precio_base_usd_final = 0.0
        
        # 3. Agregación de costos por componente
        
        # Costo total de la MP (Base, sin recargo)
        costo_base_mp_total_ars = cantidad_usada * costo_base_mp_ars_real
        
        # Costo total del Recargo (3% USD o 0 si es ARS) para el ingrediente actual
        recargo_mp_total_ars_ingrediente = cantidad_usada * recargo_unitario_ars
        
        # 4. Cálculo del Costo Unitario Final (para el detalle)
        costo_unitario_final_ars = costo_base_mp_ars_real + recargo_unitario_ars
        costo_ingrediente_total = cantidad_usada * costo_unitario_final_ars
        
        # ** CÁLCULOS EN USD PARA EL DETALLE (NUEVOS) **
        costo_unitario_usd_total = precio_base_usd_final + recargo_unitario_usd # Costo unitario final en USD
        costo_total_usd_ingrediente = cantidad_usada * costo_unitario_usd_total # Costo total en USD
        # **********************************************
        
        # AGREGACIÓN PARA EL RESUMEN
        costo_total_mp_ars += costo_base_mp_total_ars
        costo_total_recargo_mp_ars += recargo_mp_total_ars_ingrediente 
        
        # 5. Detalle de Costo para la tabla
        detalle_costo.append({
            "Materia Prima": nombre_mp,
            "Unidad": ingrediente["Unidad"],
            "Cantidad (Simulada)": cantidad_usada,
            "Moneda Origen": moneda_origen, 
            # COLUMNAS PARA ARS (Mantenemos por las dudas, pero se ocultan/renombran en main)
            "Costo Unit. ARS (Base)": costo_base_mp_ars_real, 
            "Recargo 3% ARS (Unit.)": recargo_unitario_ars, 
            "Costo Unit. ARS (Total)": costo_unitario_final_ars,
            "Costo Total ARS": costo_ingrediente_total,
            # *********************************************************************************
            # COLUMNAS REQUERIDAS EN USD
            # *********************************************************************************
            "Costo Unit. USD (Base)": precio_base_usd_final, # Costo Base Unitario en USD
            "Recargo 3% USD (Unit.)": recargo_unitario_usd, # Recargo Unitario en USD
            "Costo Unit. USD (Total)": costo_unitario_usd_total, # Costo Total Unitario en USD
            "Costo Total USD": costo_total_usd_ingrediente # Costo Total del Ingrediente en USD
        })

    detalle_df = pd.DataFrame(detalle_costo) # <--- Variable definida
    # Costo MP Total (incluye base + recargo, excluye Flete General y Overhead)
    costo_mp_total = detalle_df["Costo Total ARS"].sum() 
    
    # Retornamos los costos desagregados
    # CORRECCIÓN: Se cambia detalle_costo_df a detalle_df
    return costo_mp_total, detalle_df, costo_total_mp_ars, costo_total_recargo_mp_ars
# ***************************************************************************************************


# =================================================================================================
# FUNCIONES DE GENERACIÓN DE REPORTE (PDF con ReportLab)
# =================================================================================================

def generate_pdf_reportlab(data):
    """
    Genera el contenido PDF del presupuesto usando la librería ReportLab,
    enfocado en la vista del cliente.
    """
    
    # 1. Preparación de datos
    cliente_nombre = data['cliente_nombre']
    fecha_hoy = date.today().strftime('%d/%m/%Y')
    cotizacion_dolar_actual = data['cotizacion_dolar_actual']
    presupuesto_id = data['presupuesto_id']
    
    # Datos para el resumen final (promedios)
    precio_unitario_ars_litro_AVG = data['precio_unitario_ars_litro'] 
    precio_final_ars = data['precio_final_ars']
    litros_total_acumulado = data['litros_total_acumulado']
    sim_df = data['simulaciones_presupuesto_df'].copy()
    
    # NUEVO: Obtener el porcentaje de ganancia para aplicar por ítem
    porcentaje_ganancia = data['porcentaje_ganancia']
    factor_ganancia = 1 + (porcentaje_ganancia / 100.0)

    # CÁLCULOS EN USD (Final summary only)
    precio_unitario_usd_litro_AVG = precio_unitario_ars_litro_AVG / cotizacion_dolar_actual
    precio_final_usd = precio_final_ars / cotizacion_dolar_actual
    
    # 2. Configuración de ReportLab
    buffer = io.BytesIO()
    
    # ***************************************************************
    # CORRECCIÓN PARA ORIENTACIÓN HORIZONTAL
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), # <--- CAMBIO CLAVE
                            leftMargin=0.8*inch, rightMargin=0.8*inch,
                            topMargin=0.8*inch, bottomMargin=0.8*inch)
    # ***************************************************************
    
    story = []
    styles = getSampleStyleSheet()
    
    # Definición de estilos adicionales 
    styles.add(ParagraphStyle(name='PresupuestoTitle', fontSize=18, alignment=1, spaceAfter=12, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='PresupuestoHeading2', fontSize=14, alignment=0, spaceAfter=8, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='BodyTextBold', fontSize=12, alignment=0, spaceAfter=6, fontName='Helvetica-Bold'))
    # Nuevo estilo para el Total USD
    styles.add(ParagraphStyle(name='FinalTotalUSD', fontSize=14, alignment=0, spaceAfter=6, fontName='Helvetica-Bold', textColor=colors.blue))
    
    # 3. Encabezado del Documento
    story.append(Paragraph(f"PRESUPUESTO CLIENTE N° {presupuesto_id}", styles['PresupuestoTitle']))
    story.append(Spacer(1, 0.2*inch))
    
    # 4. Información General (Nombre, Fecha, Cotización)
    story.append(Paragraph(f"**Cliente:** {cliente_nombre}", styles['BodyTextBold']))
    story.append(Paragraph(f"**Fecha del Presupuesto:** {fecha_hoy}", styles['BodyTextBold']))
    story.append(Paragraph(f"**Cotización del Dólar (Referencia):** ${cotizacion_dolar_actual:,.2f} ARS/USD", styles['BodyTextBold']))
    story.append(Spacer(1, 0.3*inch))
    
    # 5. Detalle del Pedido (Tabla)
    story.append(Paragraph("Detalle del Pedido", styles['PresupuestoHeading2']))
    
    table_data = []
    # Header 
    table_data.append([
        "Producto", 
        "Cantidad (Litros)", 
        "Precio x Litro(ARS/L)", 
        "Precio x Litro(USD/L)", 
        "Total a Pagar (ARS)",
        "Total a Pagar (USD)" 
    ])
    
    # Body - CÁLCULO INDIVIDUAL POR ÍTEM
    # Se ajusta el ancho de las columnas para la nueva orientación horizontal (A4 ~ 11.69 pulgadas)
    total_width = 11.1 * inch 
    
    # ColWidths: [1.8in (Producto), 1.2in (Cantidad), 1.7in (P. Litro ARS), 1.7in (P. Litro USD), 1.9in (Total ARS), 1.9in (Total USD)]
    col_widths = [total_width * 0.22, total_width * 0.12, total_width * 0.18, total_width * 0.18, total_width * 0.18, total_width * 0.18]


    for index, row in sim_df.iterrows():
        
        # El 'costo_por_litro_ars' es el costo unitario de producción individual de esa receta
        costo_unitario_produccion_ars = row['costo_por_litro_ars']
        
        # 1. Precio Unitario Final al Cliente (ARS) = Costo * Factor_Ganancia
        precio_unitario_cliente_ars = costo_unitario_produccion_ars * factor_ganancia
        
        # 2. Precio Unitario Final al Cliente (USD)
        precio_unitario_cliente_usd = precio_unitario_cliente_ars / cotizacion_dolar_actual
        
        # 3. Total a Pagar por el Ítem (ARS)
        # CORRECCIÓN: Se usa 'Litros' con L mayúscula
        total_a_pagar_ars = row['Litros'] * precio_unitario_cliente_ars 
        
        # 4. Total a Pagar por el Ítem (USD)
        # CORRECCIÓN: Se usa 'Litros' con L mayúscula
        total_a_pagar_usd = row['Litros'] * precio_unitario_cliente_usd 

        # 5. Añadir a la tabla
        table_data.append([
            row['Receta'], # Usamos 'Receta' en lugar de 'nombre_receta' ya que viene de df_resumen
            f"{row['Litros']:,.2f} L", # CORRECCIÓN: 'Litros' con L mayúscula
            f"${precio_unitario_cliente_ars:,.2f}", # PRECIO UNITARIO ARS INDIVIDUAL
            f"USD ${precio_unitario_cliente_usd:,.2f}", # PRECIO UNITARIO USD INDIVIDUAL
            f"${total_a_pagar_ars:,.2f}",
            f"USD ${total_a_pagar_usd:,.2f}" # TOTAL USD INDIVIDUAL
        ])
    
    # Creación de la tabla y estilos
    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#DBEAFE')), # Azul claro para el encabezado
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1E3A8A')), # Texto azul oscuro
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10), # Aumentado el tamaño de fuente para aprovechar el ancho
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    
    story.append(table)
    story.append(Spacer(1, 0.5*inch))
    
    # 6. Totales Finales
    #story.append(Paragraph(f"**Volumen Total del Pedido:** {litros_total_acumulado:,.2f} Litros", styles['BodyTextBold']))
    
    # Usamos los promedios del resumen final (AVG)
    #story.append(Paragraph(f"**Precio Promedio por Litro a Pagar (Cliente):** ${precio_unitario_ars_litro_AVG:,.2f} ARS/L (USD ${precio_unitario_usd_litro_AVG:,.2f}/L)", styles['BodyTextBold']))
    
    story.append(Spacer(1, 0.1*inch))
    
    # Total ARS
    story.append(Paragraph(f"**TOTAL FINAL A PAGAR: ${precio_final_ars:,.2f} ARS**", 
                            ParagraphStyle(name='FinalTotal', fontSize=14, alignment=0, spaceAfter=6, 
                                           fontName='Helvetica-Bold', textColor=colors.red)))
    
    # Total USD (NUEVO)
    story.append(Paragraph(f"**TOTAL FINAL A PAGAR: USD ${precio_final_usd:,.2f}**", 
                            styles['FinalTotalUSD']))
    
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph("*Este presupuesto tiene validez de X días y está sujeto a cambios en los costos de materias primas y cotización del dólar a la fecha de facturación.", 
                            ParagraphStyle(name='Footer', fontSize=8, alignment=0, textColor=colors.grey)))

    # 7. Construcción del PDF
    doc.build(story)
    
    pdf_content = buffer.getvalue()
    buffer.close()
    return pdf_content


# =================================================================================================
# INTERFAZ STREAMLIT
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
        
    # NUEVA VARIABLE DE ESTADO PARA EL PRESUPUESTO
    if 'simulaciones_presupuesto' not in st.session_state:
        st.session_state['simulaciones_presupuesto'] = []
    
    # Variable para guardar la data del presupuesto generado
    if 'presupuesto_data_for_print' not in st.session_state:
        st.session_state['presupuesto_data_for_print'] = {}
        
    conn = get_connection()
    # Asegurar que las tablas existan
    create_tables_if_not_exists(conn)
    
    # --- Side Bar Configuration (Gasto Fijo, Flete, Overhead, Dólar) ---
    
    # --- FIJAR MES A SEPTIEMBRE (9) ---
    MES_SIMULACION = 9 
    
    st.sidebar.subheader(f"Costos Fijos Automáticos (Septiembre)")
    
    # -----------------------------------------------------------
    # 1. CÁLCULO AUTOMÁTICO DE GASTOS FIJOS OPERATIVOS (Septiembre)
    # -----------------------------------------------------------
    anio_simulacion = st.sidebar.number_input(
        "Año de Gasto Fijo a Simular:", 
        min_value=2020, 
        value=date.today().year, 
        step=1, 
        key="anio_simulacion_value"
    )
    
    gasto_fijo_mensual_auto, error_gasto = get_detalle_gastos_mensual(MES_SIMULACION, anio_simulacion)
    st.session_state['gasto_fijo_mensual'] = gasto_fijo_mensual_auto
    
    st.sidebar.markdown(f"**Gasto Operativo Total ({calendar.month_name[MES_SIMULACION].capitalize()} {anio_simulacion}):**")
    st.sidebar.success(f"${gasto_fijo_mensual_auto:,.2f} ARS (De Gastos de BD)")
    if error_gasto: st.sidebar.warning(f"⚠️ {error_gasto}")
    
    # -----------------------------------------------------------
    # 2. COSTO DE FLETE BASE (200L) - INGRESO MANUAL
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
    # 3. VOLUMEN MENSUAL Y CÁLCULO DE OVERHEAD POR LITRO (AUTOMÁTICO/MANUAL)
    # -----------------------------------------------------------
    st.sidebar.subheader("Asignación de Costos por Overhead (Gasto Indirecto Tanda)")
    
    # Cálculo automático del volumen mensual basado en las 8 recetas diarias
    volumen_mensual_litros = VOLUMEN_MENSUAL_AUTOMATICO
    
    st.sidebar.markdown(f"**Volumen Mensual de Producción (8 Recetas/Día):**")
    st.sidebar.info(f"{volumen_mensual_litros:,.0f} Litros/Mes")

    # Calcular Costo Indirecto por Litro (Automático)
    if volumen_mensual_litros > 0:
        costo_indirecto_por_litro_auto = gasto_fijo_mensual_auto / volumen_mensual_litros
    else:
        costo_indirecto_por_litro_auto = 0.0
        
    st.sidebar.metric("Costo Indirecto Operativo por Litro (Auto)", f"${costo_indirecto_por_litro_auto:,.2f} ARS/L")

    # --- NUEVO INPUT MANUAL PARA OVERHEAD ---
    costo_indirecto_por_litro_manual = st.sidebar.number_input(
        "Costo Indirecto por Litro (Manual ARS/L):",
        min_value=0.0,
        value=0.0, # Inicialmente en 0.0 para usar el automático por defecto
        step=0.1,
        format="%.2f",
        key="overhead_manual_input",
        help="Ingrese un valor manual para anular el cálculo automático de Overhead por Litro. (0.0 usa el valor Auto)"
    )

    # Lógica para determinar qué valor de Overhead usar
    if costo_indirecto_por_litro_manual > 0.0:
        costo_indirecto_por_litro = costo_indirecto_por_litro_manual
        st.sidebar.info(f"Usando Overhead Manual: ${costo_indirecto_por_litro:,.2f} ARS/L")
    else:
        costo_indirecto_por_litro = costo_indirecto_por_litro_auto
    # ----------------------------------------

    st.sidebar.markdown("---")
    
    # --- ENTRADA DEL DÓLAR DEL DÍA ---
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

    # ----------------------------------------------------------------------
    # 1. CÁLCULO PREVIO DEL PRECIO UNITARIO BASE (USD) PARA LA VISUALIZACIÓN
    # ----------------------------------------------------------------------
    ingredientes_df['Precio Unitario (USD) BASE'] = 0.0
    
    # Recorrer el DataFrame para determinar el precio base USD (BD o Manual)
    for index, row in ingredientes_df.iterrows():
        
        precio_base_usd_final = 0.0
        precio_manual_unitario = row.get('precio_unitario_manual', 0.0)
        cotizacion_manual = row.get('cotizacion_usd_manual', 1.0)
        materia_prima_id = row["materia_prima_id"]

        if precio_manual_unitario > 0.0 and cotizacion_manual > 1.0:
            # Caso Manual en USD
            precio_base_usd_final = precio_manual_unitario
        
        elif materia_prima_id != -1 and precio_manual_unitario == 0.0:
            # Caso de la Base de Datos (Si no tiene precio manual forzado)
            precio_unitario_reg_base, _, _, cotizacion_usd_reg_bd = \
                obtener_precio_actual_materia_prima(conn, materia_prima_id)
            
            if cotizacion_usd_reg_bd > 1.0:
                # Si es un precio dolarizado en BD
                precio_base_usd_final = precio_unitario_reg_base
                
        ingredientes_df.loc[index, 'Precio Unitario (USD) BASE'] = precio_base_usd_final
    # ----------------------------------------------------------------------

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
            disabled=True, # Esta columna es solo informativa, no editable
            help="Precio Unitario en USD de la MP (tomado de BD o Manual). Este valor se re-cotiza con el dólar actual de la simulación. NO es editable."
        ),
        "Quitar": st.column_config.CheckboxColumn(help="Marque para excluir de la simulación.",),
        "Temporal": st.column_config.CheckboxColumn(disabled=True),
        # Las columnas de precio manual y cotización manual deben estar presentes para la lógica,
        # pero ocultamos la cotización y la hacemos no editable en esta vista.
        "precio_unitario_manual": st.column_config.NumberColumn("Precio Manual Unit. (ARS/USD)", format="%.4f",
            help="Ingrese un precio unitario manual. Si es USD, use una Cotización USD > 1 en el formulario 'Agregar MP'."
        ),
        "cotizacion_usd_manual": st.column_config.NumberColumn("Cot. USD (Manual)", format="%.2f", min_value=1.0,),
    }
    
    # Columnas a mostrar: Ocultamos 'cotizacion_usd_manual'
    cols_display_final = ["Materia Prima", "Unidad", "Cantidad Base (200L)", "cantidad_simulada", 
                          "Precio Unitario (USD) BASE", "precio_unitario_manual", "Quitar"]


    edited_df = st.data_editor(
        ingredientes_df, # Usamos el DF completo que ahora tiene la columna 'Precio Unitario (USD) BASE'
        column_config=column_config,
        column_order=cols_display_final, # SOLO MOSTRAMOS ESTAS
        num_rows="fixed",
        use_container_width=True,
        key="data_editor_ingredientes"
    )

    # 3. Mapear de vuelta el DataFrame editado al original para el cálculo
    # Mapeamos solo las columnas editables: Cantidad Base, Quitar, Precio Manual
    ingredientes_df['Cantidad Base (200L)'] = edited_df['Cantidad Base (200L)']
    ingredientes_df['Quitar'] = edited_df['Quitar']
    ingredientes_df['precio_unitario_manual'] = edited_df['precio_unitario_manual']
    # Mantenemos cotizacion_usd_manual del DF original, ya que fue oculta y no se puede editar en edited_df.
    ingredientes_df['cantidad_simulada'] = ingredientes_df['Cantidad Base (200L)'] * factor_escala

    # --- LÓGICA DE CÁLCULO EN VIVO ---
    ingredientes_a_calcular = ingredientes_df[~ingredientes_df['Quitar']].copy()
    
    # Costo SÓLO de Materia Prima (Incluye Recargo 3% USD)
    # Corrección: Se espera 'detalle_df' como segundo retorno
    costo_mp_total, detalle_costo_df, costo_mp_base_ars, costo_recargo_mp_ars = calcular_costo_total(
        ingredientes_a_calcular, 
        cotizacion_dolar_actual, 
        conn
    )
    
    # Cálculo de la MP base en USD (sólo para mostrar en las métricas)
    costo_mp_base_usd = costo_mp_base_ars / cotizacion_dolar_actual
    
    # --------------------------------------------------------------------------
    # CÁLCULOS DE COSTOS FIJOS (LÓGICA AUTOMÁTICA/MANUAL)
    # --------------------------------------------------------------------------
    
    # CÁLCULO DEL GASTO INDIRECTO (OVERHEAD) - USA costo_indirecto_por_litro CALCULADO/SOBREESCRITO EN EL SIDEBAR
    gasto_indirecto_tanda = costo_indirecto_por_litro * cantidad_litros
    
    # CÁLCULO DEL FLETE GENERAL (COMO COSTO DIRECTO) - USANDO VALOR MANUAL
    costo_flete_total_ars = st.session_state['flete_base_200l'] * factor_escala
    
    # CÁLCULO DEL COSTO TOTAL FINAL
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
    
    # ... (Muestra de resultados y métricas)
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
        "Recargo 3% USD MP (Total)", # Nombre actualizado
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
    
    # Columna ARS
    col_final_ars.metric(
        "Costo Total de la Tanda (ARS)",
        f"${costo_total_final:,.2f} ARS"
    )
    col_final_ars.metric(
        "Costo por Litro (ARS/L)",
        f"${costo_por_litro_ars:,.2f} ARS/L"
    )
    
    # Columna USD
    col_final_usd.metric(
        "Costo Total de la Tanda (USD)",
        f"USD ${costo_total_final_usd:,.2f}"
    )
    col_final_usd.metric(
        "Costo por Litro (USD/L)",
        f"USD ${costo_por_litro_usd:,.2f} USD/L"
    )
    # ----------------------------------
    
    # ***************************************************************************************************
    # ** Detalle de costo por ingrediente (USD) **
    # ***************************************************************************************************
    with st.expander("Ver Detalle de Costo por Ingrediente 🔎"):
        
        # Definición de la configuración de columnas (solo para formato)
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
        
        # Columnas a mostrar: Las nuevas columnas en USD.
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
    # ***************************************************************************************************
    
    # --------------------------------------------------------------------------------------
    # NUEVA SECCIÓN: GESTIÓN DE SIMULACIONES PARA PRESUPUESTO
    # --------------------------------------------------------------------------------------
    st.markdown("---")
    st.header("🛒 Agregar Simulación al Presupuesto")
    
    col_cant_add, col_button_add = st.columns([0.3, 0.7])
    
    # CAMBIO 1: Entrada para la cantidad de tandas/simulaciones
    cantidad_a_agregar = col_cant_add.number_input(
        "Cantidad de Tandas a Agregar:",
        min_value=1,
        value=1,
        step=1,
        key="cantidad_a_agregar_input"
    )

    if col_button_add.button(f"➕ Agregar '{receta_seleccionada_nombre}' (x{cantidad_a_agregar}) al Presupuesto", use_container_width=True):
        
        # 1. Aseguramos que los cambios de Cantidad/Precio Manual de esta simulación 
        # queden reflejados en el estado temporal antes de agregar al presupuesto.
        st.session_state.ingredientes_temporales = []
        ingredientes_temporales_a_guardar = ingredientes_a_calcular.copy()
        
        for index, row in ingredientes_temporales_a_guardar.iterrows():
            if row['Temporal'] or row['precio_unitario_manual'] > 0.0:
                 st.session_state.ingredientes_temporales.append({
                    'nombre': row['Materia Prima'], 'unidad': row['Unidad'], 'cantidad_base': row['Cantidad Base (200L)'],
                    'precio_unitario': row['precio_unitario_manual'], 'cotizacion_usd': row['cotizacion_usd_manual'], 'materia_prima_id': row['materia_prima_id'],
                })
        
        # 2. Preparamos la data escalada
        simulacion_data = {
            'nombre_receta': receta_seleccionada_nombre,
            # Multiplicamos los totales por la cantidad_a_agregar
            'litros': cantidad_litros * cantidad_a_agregar,
            'costo_total_ars': costo_total_final * cantidad_a_agregar,
            # Los costos unitarios se mantienen igual
            'costo_por_litro_ars': costo_por_litro_ars, 
            # Multiplicamos los costos indirectos/fletes por la cantidad_a_agregar
            'gasto_indirecto_tanda': gasto_indirecto_tanda * cantidad_a_agregar,
            'costo_flete_total_ars': costo_flete_total_ars * cantidad_a_agregar,
            # El detalle MP JSON DEBE ser del original para poder ser reconstruido, 
            # pero es complicado. Lo guardaremos como una simulación escalada simple:
            'cantidad_tandas': cantidad_a_agregar,
            'detalle_mp_json_unitario': detalle_costo_df.to_json(orient='records'),
        }
        
        # Agregamos la simulación
        st.session_state['simulaciones_presupuesto'].append(simulacion_data)
        # Limpiar la data de impresión para forzar la re-generación
        st.session_state['presupuesto_data_for_print'] = {}
        st.success(f"Simulación de {receta_seleccionada_nombre} (x{cantidad_a_agregar}) agregada. Total de items: {len(st.session_state['simulaciones_presupuesto'])}")
        st.rerun()
        
    st.markdown("---")
    
    # --------------------------------------------------------------------------------------
    # NUEVA SECCIÓN: GENERACIÓN DE PRESUPUESTO FINAL
    # --------------------------------------------------------------------------------------
    st.header("📄 Generar Presupuesto Final")

    # Muestra de resumen de simulaciones cargadas
    if st.session_state['simulaciones_presupuesto']:
        st.subheader("Simulaciones Cargadas:")
        
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
                # Agregamos costo por litro al resumen para poder ser usado en el PDF
                'costo_por_litro_ars': sim['costo_por_litro_ars'] 
            })
            costo_total_acumulado += sim['costo_total_ars']
            litros_total_acumulado += sim['litros']
            
        df_resumen = pd.DataFrame(datos_resumen)
        st.dataframe(
            df_resumen[['ID', 'Receta', 'Tandas', 'Litros', 'Costo Total ARS']], # Mostramos sólo las columnas visibles
            hide_index=True, 
            use_container_width=True,
            column_config={
                'Tandas': st.column_config.NumberColumn(format="%.0f"),
                'Litros': st.column_config.NumberColumn(format="%.2f"),
                'Costo Total ARS': st.column_config.NumberColumn(format="$%f"),
            }
        )

        st.markdown(f"**Costo Total Acumulado (ARS):** ${costo_total_acumulado:,.2f}")
        st.markdown(f"**Volumen Total (Litros):** {litros_total_acumulado:,.2f} L")
        st.markdown("---")

        # --- FORMULARIO DE GENERACIÓN DE PRESUPUESTO ---
        # Se asegura que la lógica de guardado y presentación esté dentro de un form 
        # que solo usa st.form_submit_button
        with st.form("form_presupuesto_final"):
            st.subheader("Datos del Presupuesto")
            
            # 1. Ingreso del Cliente
            cliente_nombre = st.text_input("Nombre del Cliente:", key="cliente_nombre_input")
            
            # 2. Porcentaje de Ganancia
            porcentaje_ganancia = st.number_input(
                "Porcentaje de Ganancia (%):", 
                min_value=0.0, 
                value=30.0, 
                step=1.0, 
                format="%.2f",
                key="porcentaje_ganancia_input"
            )
            
            submitted = st.form_submit_button("Generar y Guardar Presupuesto")
            
            if submitted:
                if not cliente_nombre:
                    st.error("Debe ingresar el nombre del cliente.")
                else:
                    # CÁLCULO FINAL Y PRESENTACIÓN
                    
                    # Cálculo del precio de venta (GLOBAL)
                    ganancia_ars = costo_total_acumulado * (porcentaje_ganancia / 100.0)
                    precio_final_ars = costo_total_acumulado + ganancia_ars
                    
                    # CÁLCULO DEL PRECIO UNITARIO PROMEDIO (Para el resumen final, NO para la tabla)
                    precio_unitario_ars_litro = precio_final_ars / litros_total_acumulado
                    precio_unitario_usd_litro = precio_unitario_ars_litro / cotizacion_dolar_actual
                    
                    # 1. Guardar en la Base de Datos
                    try:
                        cliente_id = get_or_create_client(conn, cliente_nombre)
                        
                        # Almacenamos el DataFrame de simulaciones como JSON para la BD
                        detalle_simulaciones_json = json.dumps(st.session_state['simulaciones_presupuesto'])
                        
                        presupuesto_id = save_presupuesto(
                            conn, 
                            cliente_id, 
                            porcentaje_ganancia, 
                            litros_total_acumulado, 
                            costo_total_acumulado, 
                            precio_final_ars,
                            detalle_simulaciones_json
                        )
                        st.success(f"✅ Presupuesto Guardado (ID: {presupuesto_id}) para el Cliente: {cliente_nombre}.")
                        
                        # 2. Presentar al Cliente (en el formulario)
                        st.subheader(f"📊 Presentación Final para {cliente_nombre}")
                        
                        col_costo, col_ganancia, col_venta = st.columns(3)
                        col_costo.metric("Costo Total Producción", f"${costo_total_acumulado:,.2f} ARS")
                        col_ganancia.metric(f"Ganancia ({porcentaje_ganancia:.2f}%)", f"${ganancia_ars:,.2f} ARS")
                        col_venta.metric("Precio Venta Total", f"${precio_final_ars:,.2f} ARS", delta="FINAL")

                        st.markdown("---")
                        st.subheader(f"Precio Unitario Final (Promedio de {litros_total_acumulado:,.2f} L)")
                        
                        col_litro_ars, col_litro_usd = st.columns(2)
                        col_litro_ars.metric("Precio por Litro (ARS/L)", f"${precio_unitario_ars_litro:,.2f} ARS/L")
                        col_litro_usd.metric("Precio por Litro (USD/L)", f"USD ${precio_unitario_usd_litro:,.2f} USD/L")
                        
                        # Guardar la data en el session state para la función de impresión
                        st.session_state['presupuesto_data_for_print'] = {
                            'cliente_nombre': cliente_nombre,
                            'costo_total_acumulado': costo_total_acumulado,
                            'ganancia_ars': ganancia_ars,
                            'precio_final_ars': precio_final_ars,
                            'litros_total_acumulado': litros_total_acumulado,
                            # NOTA: Estos son los promedios, pero se usan solo para el resumen fuera de la tabla
                            'precio_unitario_ars_litro': precio_unitario_ars_litro, 
                            'precio_unitario_usd_litro': precio_unitario_usd_litro,
                            'porcentaje_ganancia': porcentaje_ganancia,
                            # Pasamos el DF_RESUMEN que ahora tiene 'costo_por_litro_ars'
                            'simulaciones_presupuesto_df': df_resumen, 
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
            
            # Generar el contenido PDF (ReportLab)
            pdf_bytes = generate_pdf_reportlab(data)
            download_file_name = f"Presupuesto_N{data['presupuesto_id']}_{data['cliente_nombre'].replace(' ', '_')}.pdf"
            
            # Usar st.download_button para la descarga de PDF
            col_print.download_button(
                label="⬇️ Descargar Presupuesto (PDF - ReportLab)",
                data=pdf_bytes,
                file_name=download_file_name,
                mime="application/pdf", # MIME type para PDF
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
    
    # ... (Resto del código de "Aplicar Cambios (Cantidades y Precios) al Estado Temporal - Mantener Receta")
    if st.button("Aplicar Cambios (Cantidades y Precios) al Estado Temporal - Mantener Receta"):
        
        st.info("Actualizando estado de la simulación...")
        st.session_state.ingredientes_temporales = []
        ingredientes_temporales_a_guardar = ingredientes_a_calcular.copy()
        
        # Solo guardamos los temporales (nuevos) o los que tienen precio manual
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