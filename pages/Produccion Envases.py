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
def obtener_costo_real_mp(materia_prima_id, conn):
    """
    Calcula el costo de una MP. Si es compuesta (como el SERUM), 
    suma el costo de sus componentes según su proporción.
    """
    cursor = conn.cursor()
    # 1. Buscamos si tiene componentes en 'composicion_colorantes'
    cursor.execute("""
        SELECT colorante_primario_id, proporcion 
        FROM composicion_colorantes 
        WHERE colorante_combinado_id = ?
    """, (materia_prima_id,))
    componentes = cursor.fetchall()

    if componentes:
        # Es una materia prima compuesta (Ej: SERUM)
        costo_total_combinado = 0.0
        for comp_id, proporcion in componentes:
            cursor.execute("""
                SELECT precio_unitario FROM precios_materias_primas 
                WHERE materia_prima_id = ? ORDER BY fecha DESC LIMIT 1
            """, (comp_id,))
            precio_comp = cursor.fetchone()
            if precio_comp:
                costo_total_combinado += precio_comp[0] * proporcion
        return costo_total_combinado
    else:
        # Es una materia prima simple
        cursor.execute("""
            SELECT precio_unitario FROM precios_materias_primas 
            WHERE materia_prima_id = ? ORDER BY fecha DESC LIMIT 1
        """, (materia_prima_id,))
        resultado = cursor.fetchone()
        return resultado[0] if resultado else 0.0

def obtener_precio_combinado(conn, materia_prima_id):
    """
    Busca si una MP es combinada. Si lo es, devuelve el costo sumado de sus componentes.
    Si no, devuelve None para seguir con la búsqueda normal.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT colorante_primario_id, proporcion 
        FROM composicion_colorantes 
        WHERE colorante_combinado_id = ?
    """, (materia_prima_id,))
    componentes = cursor.fetchall()

    if componentes:
        costo_total = 0.0
        for comp_id, proporcion in componentes:
            # Reutilizamos tu función existente para obtener el precio de cada parte
            p_base, _, _, _ = obtener_precio_actual_materia_prima(conn, comp_id)
            costo_total += (p_base * proporcion)
        return costo_total
    return None
            
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
    AHORA: Detecta si es una materia prima combinada (ej: SERUM) y suma sus partes.
    """
    if materia_prima_id == -1:
        return 0.0, 0.0, 0.0, 1.0

    cursor = conn.cursor()
    
    # -------------------------------------------------------------------------
    # NUEVA LÓGICA: VERIFICAR SI ES UNA MATERIA PRIMA COMBINADA (Tipo SERUM)
    # -------------------------------------------------------------------------
    cursor.execute("""
        SELECT colorante_primario_id, proporcion 
        FROM composicion_colorantes 
        WHERE colorante_combinado_id = ?
    """, (materia_prima_id,))
    componentes = cursor.fetchall()

    if componentes:
        precio_total_combinado = 0.0
        # Iteramos sobre los componentes que forman el SERUM
        for comp in componentes:
            # comp[0] es id_primario, comp[1] es proporcion
            id_primario = comp[0]
            proporcion = comp[1]
            
            # Llamada recursiva para obtener el precio de cada ingrediente del SERUM
            p_base, flete, otros, cotiz = obtener_precio_actual_materia_prima(conn, id_primario)
            
            # Sumamos el costo proporcional al total
            precio_total_combinado += (p_base * proporcion)
            
        # Retornamos el costo sumado. Usamos cotización 1.0 porque el cálculo ya resolvió el valor.
        return precio_total_combinado, 0.0, 0.0, 1.0
    # -------------------------------------------------------------------------
    # FIN LÓGICA DE COMBINADOS
    # -------------------------------------------------------------------------

    # Búsqueda normal en compras_materia_prima
    cursor.execute("""
        SELECT precio_unitario, cotizacion_usd, moneda
        FROM compras_materia_prima
        WHERE materia_prima_id = ?
        ORDER BY fecha DESC, id DESC
        LIMIT 1
    """, (materia_prima_id,))
    
    compra = cursor.fetchone()
    if compra:
        # Nota: He usado índices numéricos por si el row_factory no está como sqlite3.Row
        # Si usas row_factory = sqlite3.Row, puedes volver a ['precio_unitario']
        precio_base = compra[0]
        costo_flete = 0.0 
        otros_costos = 0.0 
        cotizacion_usd_reg = compra[1] if compra[1] is not None else 1.0
        moneda = compra[2]
        
        if moneda == 'ARS' and cotizacion_usd_reg <= 1.0:
            return precio_base, costo_flete, otros_costos, 1.0
        else:
            return precio_base, costo_flete, otros_costos, cotizacion_usd_reg
    else:
        # Búsqueda en precios_materias_primas
        cursor.execute("""
            SELECT precio_unitario, costo_flete, otros_costos, cotizacion_usd
            FROM precios_materias_primas
            WHERE materia_prima_id = ?
            ORDER BY fecha DESC
            LIMIT 1
        """, (materia_prima_id,))
        precio = cursor.fetchone()
        if precio:
            return precio[0], precio[1], precio[2], precio[3]
        
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
# FUNCIONES DE GENERACIÓN DE REPORTE (PDF con ReportLab) (CORREGIDO EL ERROR KEYERROR: 'BodyText')
# =================================================================================================

def generate_pdf_reportlab(data):
    """
    Genera el contenido PDF del presupuesto usando la librería ReportLab.
    
    MODIFICACIÓN: Muestra el precio unitario por UNIDAD DE ENVASE (ARS/u. y USD/u.) en lugar de por Litro.
    CORRECCIÓN: Ajuste de anchos y títulos de columna para evitar superposición.
    
    [MODIFICACIÓN CLAVE] Muestra el tipo de envase con su capacidad.
    """
    
    cliente_nombre = data['cliente_nombre']
    fecha_hoy = date.today().strftime('%d/%m/%Y')
    cotizacion_dolar_actual = data['cotizacion_dolar_actual']
    presupuesto_id = data['presupuesto_id']
    
    # Nota: Aquí se asume que df_detalle_final_presupuesto ya tiene la columna 'Litros' (L mayúscula)
    # y 'Receta' (R mayúscula) y Capacidad_Litros.
    df_detalle_final = data['df_detalle_final_presupuesto'].copy()
    
    precio_unitario_ars_litro_AVG = data['precio_unitario_ars_litro'] 
    precio_final_ars = data['precio_final_ars']
    litros_total_acumulado = data['litros_total_acumulado']
    
    precio_unitario_usd_litro_AVG = precio_unitario_ars_litro_AVG / cotizacion_dolar_actual
    precio_final_usd = precio_final_ars / cotizacion_dolar_actual
    
    buffer = io.BytesIO()
    
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), 
                            leftMargin=0.5*inch, rightMargin=0.5*inch, # Reducir márgenes
                            topMargin=0.5*inch, bottomMargin=0.5*inch)
    
    story = []
    styles = getSampleStyleSheet()
    
    styles.add(ParagraphStyle(name='PresupuestoTitle', fontSize=18, alignment=1, spaceAfter=12, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='PresupuestoHeading2', fontSize=14, alignment=0, spaceAfter=8, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='BodyTextBold', fontSize=11, alignment=0, spaceAfter=6, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(name='FinalTotalUSD', fontSize=14, alignment=0, spaceAfter=6, fontName='Helvetica-Bold', textColor=colors.blue))
    
    # NUEVO ESTILO PARA EL TEXTO DEL CUERPO DE LA TABLA (Para evitar el KeyError)
    style_table_body = ParagraphStyle(name='BodyTableText', fontSize=9, alignment=0)
    styles.add(style_table_body)
    
    # Título principal
    story.append(Paragraph(f"PRESUPUESTO N° {presupuesto_id} - CLIENTE: {cliente_nombre}", styles['PresupuestoTitle']))
    story.append(Spacer(1, 0.2*inch))
    
    # Información General
    story.append(Paragraph(f"**Fecha del Presupuesto:** {fecha_hoy}", styles['BodyTextBold']))
    story.append(Paragraph(f"**Cotización del Dólar (Ref):** ${cotizacion_dolar_actual:,.2f} ARS/USD", styles['BodyTextBold']))
    story.append(Spacer(1, 0.3*inch))
    
    story.append(Paragraph("Detalle del Pedido", styles['PresupuestoHeading2']))
    
    table_data = []
    
    # CORRECCIÓN DE ENCABEZADO: Eliminar "Tipo Envase"
    table_data.append([
        "Producto", 
        "Volumen (L)", 
        "Unidades",  
        "P. Unit. (ARS/u.)", 
        "P. Unit. (USD/u.)", 
        "Total (ARS)",
        "Total (USD)" 
    ])
    
    total_width = 10.3 * inch # A4 Horizontal es de 11.7, 10.3 es el espacio útil
    
    # CORRECCIÓN DE ANCHOS: Ajustar a 7 columnas
    # Distribuir el espacio que deja Envase_Nombre (0.15) entre Producto y Volumen
    col_widths = [
        total_width * 0.30, # Producto (Aumentado de 0.25 a 0.30)
        total_width * 0.10, # Volumen (L) (Aumentado de 0.08 a 0.10)
        # total_width * 0.15, # Tipo Envase (ELIMINADO)
        total_width * 0.10, # Unidades (Aumentado de 0.09 a 0.10)
        total_width * 0.13, # P. Unit. (ARS/u.) (Aumentado de 0.11 a 0.13)
        total_width * 0.13, # P. Unit. (USD/u.) (Aumentado de 0.11 a 0.13)
        total_width * 0.14, # Total (ARS) (Aumentado de 0.11 a 0.14)
        total_width * 0.10  # Total (USD)
    ]
    
    # [MODIFICACIÓN CLAVE] Obtener el conjunto único de envases con su capacidad
    unique_envases_display = set()
    
    # La lógica para poblar la tabla se mantiene igual, pero quitando la columna Envase_Nombre
    for index, row in df_detalle_final.iterrows():
        
        total_a_pagar_ars = row['Precio_Venta_Total_ARS']
        total_a_pagar_usd = row['Precio_Venta_Total_USD'] 
        
        envase_nombre = row.get('Envase_Nombre', 'N/A')
        capacidad = row.get('Capacidad_Litros', 0.0) # Se usa para el resumen, no para la tabla
        unidades_envase_total = row.get('Unidades_Envase_Total', 0)
        
        precio_por_envase_ars = 0.0
        precio_por_envase_usd = 0.0
        
        if unidades_envase_total > 0:
            precio_por_envase_ars = total_a_pagar_ars / unidades_envase_total
            if cotizacion_dolar_actual > 0:
                 precio_por_envase_usd = total_a_pagar_usd / unidades_envase_total
            
        table_data.append([
            # Usamos el nuevo estilo 'BodyTableText' para el wrap
            Paragraph(row['Receta'], style_table_body), 
            f"{row['Litros']:,.2f} L", # Se espera 'Litros' con L mayúscula
            # envase_nombre, # ELIMINADO
            f"{unidades_envase_total:,.0f}", 
            f"${precio_por_envase_ars:,.2f}", 
            f"USD ${precio_por_envase_usd:,.2f}", 
            f"${total_a_pagar_ars:,.2f}",
            f"USD ${total_a_pagar_usd:,.2f}" 
        ])
        
        # Llenar el conjunto de envases para el resumen
        if envase_nombre and envase_nombre != 'Sin Envase' and capacidad > 0:
            display_name = f"{envase_nombre} {capacidad:,.2f} L"
            unique_envases_display.add(display_name)
        elif envase_nombre and envase_nombre != 'Sin Envase' and capacidad == 0.0:
            unique_envases_display.add(envase_nombre)


    table = Table(table_data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#DBEAFE')), 
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#1E3A8A')), 
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        # Ajustar alineación a la derecha para las 6 columnas restantes (índices 2 a 6)
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'), # Unidades, Precios y Totales
        ('ALIGN', (1, 1), (1, -1), 'RIGHT'), # Volumen (L) a la derecha
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10), 
        ('FONTSIZE', (0, 1), (-1, -1), 9), # Reducir fuente del cuerpo para más espacio
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 4), # Reducir padding
    ]))
    
    story.append(table)
    story.append(Spacer(1, 0.3*inch))
    
    # Totales Finales
    story.append(Paragraph(f"**Volumen Total del Pedido:** {litros_total_acumulado:,.2f} Litros", styles['BodyTextBold']))
    
    # [MODIFICACIÓN CLAVE] Mostrar el detalle de envases utilizados con capacidad
    envases_str = ", ".join(unique_envases_display) if unique_envases_display else "No se especificó envase."
    story.append(Paragraph(f"**Tipos de Envase Utilizados:** {envases_str}", styles['BodyTextBold']))
    
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
    costo_total_final = 0.0

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

    if 'margen_deseado' not in st.session_state:
        st.session_state.margen_deseado = 30.0

    if 'precio_venta_manual' not in st.session_state:
        st.session_state.precio_venta_manual = 0.0

    if 'origen_edicion' not in st.session_state:
        st.session_state.origen_edicion = "margen"

    if 'presupuesto_data_for_print' not in st.session_state:
        st.session_state['presupuesto_data_for_print'] = {}
        
    # NUEVOS ESTADOS PARA LA EDICIÓN DE GASTOS
    if 'gastos_temporales_simulacion' not in st.session_state:
        st.session_state.gastos_temporales_simulacion = []
    # Usaremos esto para almacenar el último total de gastos fijos calculado por el editor/temporales
    if 'gasto_fijo_mensual_total' not in st.session_state:
        st.session_state.gasto_fijo_mensual_total = 0.0
    
    # [MODIFICACIÓN] Costos Unitarios de Empaque Adicionales
    if 'costo_etiqueta_por_envase' not in st.session_state:
        st.session_state['costo_etiqueta_por_envase'] = 0.0
    if 'costo_caja_por_envase' not in st.session_state:
        st.session_state['costo_caja_por_envase'] = 0.0
    if "margen_deseado" not in st.session_state:
        st.session_state.margen_deseado = 30.0

    if "precio_venta_manual" not in st.session_state:
        st.session_state.precio_venta_manual = 0.0
   
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
        key="volumen_mensual_litros"
    )

    costo_indirecto_por_litro_auto = 0.0
    if volumen_mensual_litros > 0:
        costo_indirecto_por_litro_auto = gasto_fijo_mensual_auto / volumen_mensual_litros
    
    st.sidebar.metric("Costo Indirecto por Litro (Automático)", f"${costo_indirecto_por_litro_auto:,.4f} ARS/L")

    costo_indirecto_por_litro_manual = st.sidebar.number_input(
        "Costo Indirecto por Litro (Manual ARS/L):", 
        min_value=0.0, 
        value=0.0, 
        step=0.001, 
        format="%.4f", 
        key="costo_indirecto_manual"
    )
    
    costo_indirecto_por_litro = costo_indirecto_por_litro_manual if costo_indirecto_por_litro_manual > 0.0 else costo_indirecto_por_litro_auto
    st.sidebar.metric("Costo Indirecto por Litro (Usado)", f"${costo_indirecto_por_litro:,.2f} ARS/L")

    # -----------------------------------------------------------
    # 6. COTIZACIÓN DEL DOLAR
    # -----------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("Cotización Dólar (ARS/USD)")
    cotizacion_dolar_actual = st.sidebar.number_input(
        "Dólar a Utilizar:", 
        min_value=1.0, 
        value=st.session_state.get('dolar', 1000.0), 
        step=1.0, 
        format="%.2f", 
        key="dolar_input", 
        help="Cotización del dólar a utilizar para la conversión ARS <-> USD."
    )
    st.session_state['dolar'] = cotizacion_dolar_actual

    # -----------------------------------------------------------
    # 7. SELECCIÓN Y CÁLCULO DEL ENVASE (MODIFICADO)
    # -----------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("Costo de Envase")
    
    # Inicialización de variables para envase y empaque
    costo_envase_total_ars = 0.0
    costo_envase_por_litro = 0.0
    unidades_necesarias = 0
    capacidad_litros = 0.0
    precio_envase_unitario_ars = 0.0 # Variable para el precio convertido a ARS
    precio_envase_unitario_usd_base = 0.0
    envase_id_actual = None
    envase_seleccionado_nombre_final = "Sin Envase"

    # [MODIFICACIÓN] Inputs para Envase Manual
    envases_disponibles = obtener_envases_disponibles(conn)
    envases_map = {e['descripcion']: e for e in envases_disponibles}
    envases_nombres = ["--- Seleccionar Envase ---"] + [e['descripcion'] for e in envases_disponibles]
    
    envase_seleccionado_nombre = st.sidebar.selectbox(
        "Envase a utilizar (DB):", 
        envases_nombres, 
        key="envase_seleccionado"
    )

    st.sidebar.markdown("##### O Ingresar Envase Manualmente (Prioridad sobre DB)")
    
    manual_envase_precio_unitario_ars = st.sidebar.number_input(
        "Precio Envase Unitario Manual (ARS/u.):", 
        min_value=0.0, 
        value=0.0, 
        step=0.01, 
        format="%.2f", 
        key="manual_envase_precio_unitario_ars"
    )
    
    manual_envase_capacidad_litros = st.sidebar.number_input(
        "Capacidad Envase  (Litros):", 
        min_value=0.0, 
        value=0.0, 
        step=0.01, 
        format="%.2f", 
        key="manual_envase_capacidad_litros"
    )
    
    # [MODIFICACIÓN] Inputs para Etiqueta y Caja
    st.sidebar.markdown("---")
    st.sidebar.subheader("Empaque Adicional (Por Envase)")
    
    costo_etiqueta_por_envase = st.sidebar.number_input(
        "Costo Etiqueta (ARS/u.):", 
        min_value=0.0, 
        value=st.session_state.get('costo_etiqueta_por_envase', 0.0), 
        step=0.01, 
        format="%.2f", 
        key="costo_etiqueta_input"
    )
    st.session_state['costo_etiqueta_por_envase'] = costo_etiqueta_por_envase
    
    costo_caja_por_envase = st.sidebar.number_input(
        "Costo Caja (ARS/u.):", 
        min_value=0.0, 
        value=st.session_state.get('costo_caja_por_envase', 0.0), 
        step=0.01, 
        format="%.2f", 
        key="costo_caja_input"
    )
    st.session_state['costo_caja_por_envase'] = costo_caja_por_envase
    st.sidebar.markdown("---")
    
    # -----------------------------------------------------------
    # 8. CÁLCULO FINAL DE COSTO DE ENVASE (LÓGICA ACTUALIZADA)
    # -----------------------------------------------------------
    
    # Cantidad de litros de la tanda que se está simulando (Input principal)
    cantidad_litros = st.session_state.get('litros', BASE_LITROS)
    
    costo_envase_total_ars = 0.0
    costo_etiqueta_total_ars = 0.0
    costo_caja_total_ars = 0.0
    costo_total_empaque_ars = 0.0
    costo_envase_por_litro = 0.0
    unidades_necesarias = 0
    capacidad_litros = 0.0
    precio_envase_unitario_ars = 0.0
    precio_envase_unitario_usd_base = 0.0

    # 1. Determinar Capacidad y Precio
    if envase_seleccionado_nombre != "--- Seleccionar Envase ---":
        # Se seleccionó un envase de la DB
        envase_info = envases_map[envase_seleccionado_nombre]
        envase_id_actual = envase_info['id']
        envase_seleccionado_nombre_final = envase_seleccionado_nombre
        
        # a. Obtener precio base USD y capacidad en litros de la DB
        precio_envase_unitario_usd_base_db, capacidad_litros_db = obtener_precio_envase_actual(conn, envase_id_actual)
        
        # b. Determinar Capacidad Final
        if manual_envase_capacidad_litros > 0.0:
            capacidad_litros = manual_envase_capacidad_litros
            st.sidebar.info(f"Usando Capacidad Manual: {capacidad_litros:,.2f} L (sobre DB)")
        else:
            capacidad_litros = capacidad_litros_db
            
        # c. Determinar Precio Final (Prioriza Manual ARS)
        if manual_envase_precio_unitario_ars > 0.0:
            precio_envase_unitario_ars = manual_envase_precio_unitario_ars
            precio_envase_unitario_usd_base = precio_envase_unitario_ars / cotizacion_dolar_actual 
            st.sidebar.info(f"Usando Precio Manual: ${precio_envase_unitario_ars:,.2f} ARS/u. (sobre DB)")
        else:
            # Usar precio de DB (convertido a ARS)
            precio_envase_unitario_usd_base = precio_envase_unitario_usd_base_db
            precio_envase_unitario_ars = precio_envase_unitario_usd_base_db * cotizacion_dolar_actual
            
    elif manual_envase_precio_unitario_ars > 0.0 and manual_envase_capacidad_litros > 0.0:
        # Se usa solo Envase Manual (DB envase no seleccionado)
        envase_id_actual = -1 
        envase_seleccionado_nombre_final = "Envase Manual"
        precio_envase_unitario_ars = manual_envase_precio_unitario_ars
        capacidad_litros = manual_envase_capacidad_litros
        precio_envase_unitario_usd_base = precio_envase_unitario_ars / cotizacion_dolar_actual
        st.sidebar.info(f"Usando Envase Manual: ${precio_envase_unitario_ars:,.2f} ARS/u. @ {capacidad_litros:,.2f} L")
    
    else:
        # Sin envase, sin costo
        envase_id_actual = None
        envase_seleccionado_nombre_final = "Sin Envase"
        precio_envase_unitario_usd_base = 0.0


    # 2. Cálculo del costo
    if capacidad_litros > 0 and (precio_envase_unitario_ars > 0.0 or costo_etiqueta_por_envase > 0.0 or costo_caja_por_envase > 0.0):
        
        # Calcular unidades necesarias (redondeo hacia arriba al entero)
        unidades_necesarias = int(cantidad_litros / capacidad_litros)
        if unidades_necesarias * capacidad_litros < cantidad_litros:
            unidades_necesarias += 1
        
        # Costo Total de Envase Principal
        costo_envase_total_ars = unidades_necesarias * precio_envase_unitario_ars
        
        # Cálculo del costo de Empaque Adicional (Etiqueta y Caja)
        costo_etiqueta_total_ars = costo_etiqueta_por_envase * unidades_necesarias # Costo de la Etiqueta (Adicional)
        costo_caja_total_ars = costo_caja_por_envase * unidades_necesarias # Costo de la Caja (Adicional)

        # Costo Total de Empaque
        costo_total_empaque_ars = costo_envase_total_ars + costo_etiqueta_total_ars + costo_caja_total_ars

        # [MODIFICACIÓN] Métricas de Desglose de Empaque
        st.sidebar.markdown(f"**Total Envase Principal ({envase_seleccionado_nombre_final}):** ${costo_envase_total_ars:,.2f} ARS")
        st.sidebar.markdown(f"**Total Etiqueta (Tanda):** ${costo_etiqueta_total_ars:,.2f} ARS")
        st.sidebar.markdown(f"**Total Caja (Tanda):** ${costo_caja_total_ars:,.2f} ARS")
        
        costo_envase_por_litro = costo_total_empaque_ars / cantidad_litros
        st.sidebar.metric(
            "Costo Empaque Total por Litro", 
            f"${costo_envase_por_litro:,.4f} ARS/L", 
            help="Costo total de Envase Principal + Etiqueta + Caja por Litro."
        )
    else:
        # Reiniciar variables
        costo_envase_total_ars = 0.0
        costo_etiqueta_total_ars = 0.0
        costo_caja_total_ars = 0.0
        costo_total_empaque_ars = 0.0
        costo_envase_por_litro = 0.0
        # Mostrar métrica a cero si no hay cálculo
        st.sidebar.metric(
            "Costo Empaque Total por Litro", 
            f"${costo_envase_por_litro:,.4f} ARS/L", 
            help="Costo total de Envase Principal + Etiqueta + Caja por Litro."
        )

    st.sidebar.markdown("---")
    
    # -----------------------------------------------------------
    # FIN CONFIGURACIÓN SIDEBAR
    # -----------------------------------------------------------

    # --- COLUMNAS PARA RECETA Y SIMULACIÓN ---
    col_receta, col_acciones = st.columns([0.7, 0.3])
    conn = get_connection()
    
    # <<<< INICIO FIX DEL ERROR DE BASE DE DATOS >>>>
    # Se eliminó 'descripcion' de la consulta SQL ya que no existe en la DB.
    df_recetas = fetch_df("SELECT id, nombre FROM recetas ORDER BY nombre")
    recetas_map = {r['nombre']: r for r in df_recetas.to_dict('records')}
    recetas_nombres = ["--- Seleccionar Receta ---"] + df_recetas['nombre'].tolist()
    
    receta_seleccionada_nombre = col_receta.selectbox(
        "Seleccione la Receta a Simular:", 
        recetas_nombres, 
        key="receta_seleccionada"
    )
    
    receta_id_seleccionada = None
    if receta_seleccionada_nombre != "--- Seleccionar Receta ---":
        receta_id_seleccionada = recetas_map[receta_seleccionada_nombre]['id']
        st.session_state.receta_id_actual = receta_id_seleccionada

    # -----------------------------------------------------------
    # CÁLCULO DE RECETA Y COSTOS
    # -----------------------------------------------------------
    
    ingredientes_receta = []
    if receta_id_seleccionada:
        ingredientes_receta = obtener_ingredientes_receta(conn, receta_id_seleccionada)
        if ingredientes_receta:
            ingredientes_a_calcular = pd.DataFrame(ingredientes_receta)
            ingredientes_a_calcular.rename(columns={'nombre': 'Materia Prima', 'unidad': 'Unidad', 'cantidad': 'Cantidad Base (200L)'}, inplace=True)
            # Agregar columnas manuales con valor 0.0 para poder ser editadas
            ingredientes_a_calcular['precio_unitario_manual'] = 0.0
            ingredientes_a_calcular['cotizacion_usd_manual'] = 1.0
            ingredientes_a_calcular['Temporal'] = False
            ingredientes_a_calcular['Quitar'] = False
        else:
            ingredientes_a_calcular = pd.DataFrame()
            st.warning("La receta seleccionada no tiene ingredientes registrados.")
    else:
        ingredientes_a_calcular = pd.DataFrame()
    
    # -----------------------------------------------------------
    # INTERFAZ PARA EDICIÓN DE MP Y AGREGAR MP TEMPORALES
    # -----------------------------------------------------------
    
    # 1. Aplicar modificaciones temporales guardadas
    if st.session_state.ingredientes_temporales and not ingredientes_a_calcular.empty:
        temp_df = pd.DataFrame(st.session_state.ingredientes_temporales)
        temp_df.rename(columns={'nombre': 'Materia Prima', 'unidad': 'Unidad', 'cantidad_base': 'Cantidad Base (200L)', 'precio_unitario': 'precio_unitario_manual', 'cotizacion_usd': 'cotizacion_usd_manual'}, inplace=True)
        temp_df['Temporal'] = True
        temp_df['Quitar'] = False # <<< CORRECCIÓN PARA KEYERROR 'Quitar'
        
        # 1. Crear un índice de los IDs temporales
        temp_ids = temp_df['materia_prima_id'].tolist()
        
        # 2. Filtrar los ingredientes base que NO tienen una versión temporal (o son MP nuevas temp, que tienen ID -1)
        base_filtered = ingredientes_a_calcular[~ingredientes_a_calcular['materia_prima_id'].isin(temp_ids) | (ingredientes_a_calcular['materia_prima_id'] == -1)]
        
        # 3. Concatenar los ingredientes base filtrados con los temporales (los temporales tienen prioridad)
        ingredientes_a_calcular = pd.concat([base_filtered, temp_df[temp_df['materia_prima_id'] != -1]], ignore_index=True)
        
        # 4. Concatenar los ingredientes nuevos temporales (MP ID -1)
        ingredientes_a_calcular = pd.concat([ingredientes_a_calcular, temp_df[temp_df['materia_prima_id'] == -1]], ignore_index=True)


    if not ingredientes_a_calcular.empty:
        
        # 1. Calcular la cantidad simulada total
        if BASE_LITROS > 0 and cantidad_litros > 0:
            ingredientes_a_calcular['cantidad_simulada'] = (ingredientes_a_calcular['Cantidad Base (200L)'] / BASE_LITROS) * cantidad_litros
        else:
            ingredientes_a_calcular['cantidad_simulada'] = 0.0
            
        # 2. Buscar el precio base de la DB (solo para visualización)
        ingredientes_a_calcular['Precio Unitario (USD) BASE'] = 0.0
        for index, row in ingredientes_a_calcular.iterrows():
            if row['materia_prima_id'] != -1 and row['precio_unitario_manual'] == 0.0:
                precio_unitario_reg_base, _, _, cotizacion_usd_reg_bd = obtener_precio_actual_materia_prima(conn, row['materia_prima_id'])
                if cotizacion_usd_reg_bd > 1.0:
                    ingredientes_a_calcular.loc[index, 'Precio Unitario (USD) BASE'] = precio_unitario_reg_base
            
        # 3. Data Editor
        st.subheader("Simulación de Ingredientes y Costos (ARS)")
        
        column_config = {
            "materia_prima_id": st.column_config.NumberColumn(disabled=True),
            "Materia Prima": st.column_config.TextColumn("Materia Prima", disabled=True),
            "Unidad": st.column_config.TextColumn(disabled=True),
            "Cantidad Base (200L)": st.column_config.NumberColumn(
                "Cantidad Base (200L)", 
                min_value=0.0, 
                format="%.4f", 
                help="Edite la cantidad base para esta simulación."
            ),
            "cantidad_simulada": st.column_config.NumberColumn("Cantidad (Simulada)", format="%.4f", disabled=True),
            "Precio Unitario (USD) BASE": st.column_config.NumberColumn(
                "P. Unit. (USD) DB", 
                format="%.4f", 
                disabled=True,
                help="Precio base en USD registrado en la DB (si aplica)."
            ),
            "precio_unitario_manual": st.column_config.NumberColumn(
                "P. Unit. Manual (USD/ARS)", 
                min_value=0.0, 
                format="%.4f",
                help="Ingrese un precio unitario manual. Se interpreta como USD si Cotización > 1.0, o ARS si Cotización = 1.0."
            ),
            "cotizacion_usd_manual": st.column_config.NumberColumn(
                "Cotización Manual (USD)", 
                min_value=1.0, 
                format="%.2f",
                help="Ingrese la cotización USD de compra para el precio manual."
            ),
            "Temporal": st.column_config.CheckboxColumn(disabled=True),
            "Quitar": st.column_config.CheckboxColumn("Quitar de Simulación")
        }
        
        edited_ingredientes_df = st.data_editor(
            ingredientes_a_calcular,
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
            column_order=[
                "Materia Prima", "Unidad", "Cantidad Base (200L)", "cantidad_simulada", 
                "Precio Unitario (USD) BASE", "precio_unitario_manual", "cotizacion_usd_manual", 
                "Temporal", "Quitar" 
            ],
            key="editor_ingredientes"
        )
        
        # 5. Actualizar el Session State con los cambios para persistencia
        st.session_state.ingredientes_temporales = []
        for index, row in edited_ingredientes_df.iterrows():
            # Si la cantidad base o el precio manual se modificó, o si es temporal (ID -1), guardar en temporales
            is_modified = row['Cantidad Base (200L)'] != ingredientes_a_calcular.loc[index, 'Cantidad Base (200L)']
            is_modified = is_modified or row['precio_unitario_manual'] != ingredientes_a_calcular.loc[index, 'precio_unitario_manual']
            is_modified = is_modified or row['cotizacion_usd_manual'] != ingredientes_a_calcular.loc[index, 'cotizacion_usd_manual']
            
            if is_modified or row['materia_prima_id'] == -1:
                # Guardar el estado modificado
                new_data = {
                    'materia_prima_id': row['materia_prima_id'],
                    'nombre': row['Materia Prima'],
                    'unidad': row['Unidad'],
                    'cantidad_base': row['Cantidad Base (200L)'],
                    'precio_unitario': row['precio_unitario_manual'],
                    'cotizacion_usd': row['cotizacion_usd_manual']
                }
                st.session_state.ingredientes_temporales.append(new_data)
        
        # 6. Filtrar ingredientes para el cálculo final (quitar los marcados para Quitar)
        ingredientes_a_calcular = edited_ingredientes_df[~edited_ingredientes_df['Quitar']].copy()
        
        # 7. Ejecutar el cálculo del costo
        costo_mp_total, detalle_costo_df, costo_total_mp_ars_base, costo_total_recargo_mp_ars, costo_total_mp_usd = \
            calcular_costo_total(ingredientes_a_calcular, cotizacion_dolar_actual, conn)
        
        # Guardar resultados en Session State
        st.session_state['costo_total'] = costo_mp_total
        st.session_state['detalle_costo'] = detalle_costo_df
    
    else:
        st.warning("Seleccione una receta o añada materias primas temporales para comenzar.")
        # Aunque ya están inicializados, se reasignan aquí por seguridad/claridad
        costo_mp_total = 0.0
        costo_total_mp_usd = 0.0
        detalle_costo_df = pd.DataFrame()
        costo_total_recargo_mp_ars = 0.0 


    # Reasignación de seguridad
    # =================================================================================================
    # CÁLCULO DE FLETE, OVERHEAD Y TOTALES (MODIFICADO)
    # =================================================================================================
    
    # 1. Costo de Flete
    if cantidad_litros > 0 and BASE_LITROS > 0:
        factor_escala = cantidad_litros / BASE_LITROS
        costo_flete_total_ars = flete_base_200l * factor_escala
        costo_flete_por_litro = costo_flete_total_ars / cantidad_litros
    else:
        costo_flete_total_ars = 0.0
        costo_flete_por_litro = 0.0

    # 2. Costo de Overhead (Gasto Indirecto)
    gasto_indirecto_tanda = costo_indirecto_por_litro * cantidad_litros
    
    # 3. Costo total de Envase y Empaque (ya calculado en el sidebar)
    costo_envase_total_ars_principal = costo_envase_total_ars # Renombrar para claridad
    
    # 4. Costo Total Final de Producción
    costo_total_final = costo_mp_total + costo_flete_total_ars + gasto_indirecto_tanda + costo_total_empaque_ars
    
    # 5. Costo por Litro
    costo_por_litro_ars = costo_total_final / cantidad_litros if cantidad_litros > 0 else 0.0
    costo_por_litro_usd = costo_por_litro_ars / cotizacion_dolar_actual
    
    # -----------------------------------------------------------
    # RESULTADOS DE COSTEO
    # -----------------------------------------------------------
    st.markdown("---")
    st.subheader("Resumen de Costos de la Simulación")
    col_res1, col_res2, col_res3 = st.columns(3)
    
    # Asegurarse de que el costo de MP (Base, sin recargo) en USD
    if cotizacion_dolar_actual > 0:
        costo_mp_base_solo_usd = costo_total_mp_usd - (costo_total_recargo_mp_ars / cotizacion_dolar_actual)
    else:
        costo_mp_base_solo_usd = 0.0

    col_res1.metric(
        "Costo TOTAL (ARS)", 
        f"${costo_total_final:,.2f}", 
        help="Costo Total de Producción (MP + Flete + Overhead + Empaque)."
    )
    # [MODIFICACIÓN] Métrica de Costo Total de Empaque
    col_res2.metric(
        "Costo Total Empaque (ARS)", 
        f"${costo_total_empaque_ars:,.2f}", 
        help=f"Costo Total de Envase Principal (${costo_envase_total_ars_principal:,.2f}) + Etiqueta (${costo_etiqueta_total_ars:,.2f}) + Caja (${costo_caja_total_ars:,.2f}) para {unidades_necesarias:,.0f} u."
    )
    col_res3.metric(
        "Costo por Litro (ARS/L)", 
        f"${costo_por_litro_ars:,.4f}", 
        help="Costo Total / Litros."
    )
    col_res1.metric(
        "Costo Materia Prima (USD)", 
        f"USD ${costo_total_mp_usd:,.2f}", 
        help="Costo Total de MP (Base USD + Recargo 3%)."
    )
    col_res2.metric(
        "Costo Total Flete (ARS)", 
        f"${costo_flete_total_ars:,.2f}", 
        help=f"Costo del flete escalado para {cantidad_litros:,.2f} L."
    )
    col_res3.metric(
        "Costo por Litro (USD/L)", 
        f"USD ${costo_por_litro_usd:,.4f}", 
        help="Costo Total / Litros."
    )
    st.markdown("---")

    st.subheader("Desglose de Costo de Materia Prima")
    if not detalle_costo_df.empty:
        col_config_detalle = {
            "Materia Prima": st.column_config.TextColumn(disabled=True),
            "Unidad": st.column_config.TextColumn(disabled=True),
            "Cantidad (Simulada)": st.column_config.NumberColumn(format="%.4f"),
            "Moneda Origen": st.column_config.TextColumn(disabled=True),
            "Costo Unit. ARS (Base)": st.column_config.NumberColumn(format="%.4f", help="Costo de MP sin recargo, a la cotización actual."),
            "Recargo 3% ARS (Unit.)": st.column_config.NumberColumn(format="%.4f"),
            "Costo Unit. ARS (Total)": st.column_config.NumberColumn(format="%.4f"),
            "Costo Total ARS": st.column_config.NumberColumn(format="%.2f"),
            "Costo Unit. USD (Base)": st.column_config.NumberColumn(format="%.4f"),
            "Recargo 3% USD (Unit.)": st.column_config.NumberColumn(format="%.4f"),
            "Costo Unit. USD (Total)": st.column_config.NumberColumn(format="%.4f"),
            "Costo Total USD": st.column_config.NumberColumn(format="%.2f")
        }
        st.dataframe(detalle_costo_df, column_config=col_config_detalle, use_container_width=True, hide_index=True)

    # --------------------------------------------------------------------------------------
    # ACCIONES: AGREGAR MATERIA PRIMA TEMPORAL / LIMPIAR
    # --------------------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("Acciones Adicionales de Simulación")
    col_mp_nueva, col_mp_existente, col_limpiar = st.columns(3)
    
    # --------------------------------------------------------------------------------------
    # A. AÑADIR MATERIA PRIMA NUEVA
    # --------------------------------------------------------------------------------------
    with col_mp_nueva.form("form_mp_nueva_simulacion"):
        col_mp_nueva.markdown("**Añadir Materia Prima (Nueva)**")
        mp_nombre_nuevo = st.text_input("Nombre de Materia Prima (Nueva):", key="mp_nueva_nombre")
        col_cant_n, col_unidad_n = col_mp_nueva.columns([0.6, 0.4])
        cantidad_base_n = col_cant_n.number_input(f"Cantidad Base ({BASE_LITROS:.0f}L):", min_value=0.0, value=0.0, step=0.01, format="%.4f", key="mp_nueva_cantidad")
        unidad_n = col_unidad_n.text_input("Unidad:", value="kg", key="mp_nueva_unidad")
        col_precio_n, col_cot_n = col_mp_nueva.columns(2)
        precio_unitario_usd_n = col_precio_n.number_input("Precio Manual (USD):", min_value=0.0, value=0.0, step=0.01, format="%.4f", key="mp_nueva_precio_usd")
        cotizacion_usd_n = col_cot_n.number_input("Cotización USD de Compra/Referencia:", min_value=1.0, value=cotizacion_dolar_actual, step=1.0, format="%.2f", key="mp_nueva_cotizacion_usd")
        
        submitted_nueva = st.form_submit_button("Agregar MP Temporal (Nueva)")
        
        if submitted_nueva:
            if mp_nombre_nuevo and cantidad_base_n > 0 and precio_unitario_usd_n > 0:
                st.session_state.ingredientes_temporales.append({
                    'materia_prima_id': -1, # ID -1 para nuevas temporales
                    'nombre': mp_nombre_nuevo,
                    'unidad': unidad_n,
                    'cantidad_base': cantidad_base_n,
                    'precio_unitario': precio_unitario_usd_n,
                    'cotizacion_usd': cotizacion_usd_n 
                })
                st.success(f"Materia Prima '{mp_nombre_nuevo}' agregada temporalmente a la simulación.")
                st.rerun()
            else:
                st.warning("Debe ingresar un nombre de MP, cantidad base y precio/cotización válidos.")

    # --------------------------------------------------------------------------------------
    # B. AÑADIR MATERIA PRIMA EXISTENTE EN DB
    # --------------------------------------------------------------------------------------
    with col_mp_existente.form("form_mp_existente_simulacion"):
        col_mp_existente.markdown("**Añadir/Actualizar Materia Prima (Existente en DB)**")
        mp_existentes = obtener_todas_materias_primas(conn)
        mp_map = {m['nombre']: m for m in mp_existentes}
        mp_nombres = ["--- Seleccionar MP Existente ---"] + [m['nombre'] for m in mp_existentes]
        
        mp_nombre_existente = col_mp_existente.selectbox("Materia Prima Existente:", mp_nombres, key="mp_existente_nombre")
        mp_id_seleccionada = None
        mp_info = None

        if mp_nombre_existente != "--- Seleccionar MP Existente ---":
            mp_info = mp_map[mp_nombre_existente]
            mp_id_seleccionada = mp_info['id']
            
            # Obtener cantidad base actual de la receta (si existe) o 0.0
            cantidad_base_actual = 0.0
            if receta_id_seleccionada:
                ingredientes_receta = obtener_ingredientes_receta(conn, receta_id_seleccionada)
                ingrediente_base = next((i for i in ingredientes_receta if i['materia_prima_id'] == mp_id_seleccionada), None)
                if ingrediente_base:
                    cantidad_base_actual = ingrediente_base['cantidad']
            
            col_cant_e, col_unidad_e = col_mp_existente.columns([0.6, 0.4])
            cantidad_base_e = col_cant_e.number_input(f"Cantidad Base ({BASE_LITROS:.0f}L) (Actual: {cantidad_base_actual:.4f}):", min_value=0.0, value=cantidad_base_actual, step=0.01, format="%.4f", key="mp_existente_cantidad")
            col_unidad_e.text_input("Unidad:", value=mp_info['unidad'] if mp_info else "", disabled=True, key="mp_existente_unidad")
            
            col_precio_e, col_cot_e = col_mp_existente.columns(2)
            precio_unitario_usd_e = col_precio_e.number_input("Precio Manual (USD):", min_value=0.0, value=0.0, step=0.01, format="%.4f", key="mp_existente_precio_usd")
            cotizacion_usd_e = col_cot_e.number_input("Cotización USD de Compra/Referencia:", min_value=1.0, value=cotizacion_dolar_actual, step=1.0, format="%.2f", key="mp_existente_cotizacion_usd")
            
            submitted_existente = st.form_submit_button("Aplicar Actualización Temporal")

            if submitted_existente:
                if cantidad_base_e > 0 or precio_unitario_usd_e > 0:
                    # Sobreescribir o agregar la MP existente a la lista temporal
                    # Primero, eliminar cualquier entrada anterior para esta MP_ID
                    st.session_state.ingredientes_temporales = [
                        item for item in st.session_state.ingredientes_temporales 
                        if item.get('materia_prima_id') != mp_id_seleccionada
                    ]
                    
                    # Luego agregar la nueva entrada
                    st.session_state.ingredientes_temporales.append({
                        'materia_prima_id': mp_id_seleccionada,
                        'nombre': mp_nombre_existente,
                        'unidad': mp_info['unidad'],
                        'cantidad_base': cantidad_base_e,
                        'precio_unitario': precio_unitario_usd_e,
                        'cotizacion_usd': cotizacion_usd_e 
                    })
                    st.success(f"Materia Prima '{mp_nombre_existente}' actualizada temporalmente.")
                    st.rerun()
                else:
                    st.warning("Debe ingresar una cantidad base o un precio manual válidos.")
        else:
            st.form_submit_button("Aplicar Actualización Temporal", disabled=True)
            
    # --------------------------------------------------------------------------------------
    # C. LIMPIAR SIMULACIÓN
    # --------------------------------------------------------------------------------------
    with col_limpiar.container():
        st.markdown("**Limpieza de Simulación**")
        if st.button("🗑️ Limpiar Valores Manuales (MP y Gastos) y Recargar Receta Base"):
            st.session_state.ingredientes_temporales = []
            st.session_state.gastos_temporales_simulacion = []
            st.session_state.gasto_fijo_mensual_total = 0.0
            st.session_state.litros = BASE_LITROS
            st.rerun()

        # =================================================================================================
        # PRESUPUESTO ACUMULADO
        # =================================================================================================
        # --------------------------------------------------------------------------------------
    # DEFINICIÓN DE MARGEN / PRECIO DE VENTA (EDITABLE)
    # --------------------------------------------------------------------------------------

    # --------------------------------------------------------------------------------------
    # DEFINICIÓN DE MARGEN / PRECIO DE VENTA (BIDIRECCIONAL)
    # --------------------------------------------------------------------------------------

    if costo_total_final > 0:

        st.subheader("Definir Margen y Precio de Venta")

        col_m1, col_m2 = st.columns(2)

        # ---- Margen ----
        # ------------------ MARGEN ------------------
        with col_m1:

            # FIX: evitar valores negativos en number_input
            margen_seguro = max(0.0, st.session_state.margen_deseado)

            margen_input = st.number_input(
                "Margen de ganancia deseado (%)",
                min_value=0.0,
                step=1.0,
                format="%.2f",
                value=margen_seguro,
                key="margen_input_principal"
            )

        # ---- Precio ----
        with col_m2:
            precio_input = st.number_input(
                "Precio de venta total manual (ARS)",
                min_value=0.0,
                step=100.0,
                format="%.2f",
                value=st.session_state.precio_venta_manual,
                key="precio_input"
            )

        # ---- LÓGICA BIDIRECCIONAL ----
        if margen_input != st.session_state.margen_deseado:
            st.session_state.margen_deseado = margen_input
            st.session_state.precio_venta_manual = costo_total_final * (1 + margen_input / 100)

        elif precio_input != st.session_state.precio_venta_manual:
            st.session_state.precio_venta_manual = precio_input

            margen_calculado = (
                (precio_input - costo_total_final) / costo_total_final * 100
            ) if costo_total_final > 0 else 0.0

            # FIX: nunca permitir margen negativo
            st.session_state.margen_deseado = max(0.0, margen_calculado)

        st.markdown(f"""
        **Costo Total (ARS):** ${costo_total_final:,.2f}  
        **Margen Calculado:** **{st.session_state.margen_deseado:,.2f}%**  
        **Precio de Venta Total:** ${st.session_state.precio_venta_manual:,.2f}
        """)

    st.markdown("---")

    if receta_id_seleccionada and costo_total_final > 0:

        col_agregar, col_margen = st.columns([0.6, 0.4])
        if receta_id_seleccionada and costo_total_final > 0:

            col_agregar, col_margen = st.columns([0.6, 0.4])

        # --------------------------------------------------------------------------------------
        # 1. FORMULARIO PARA AGREGAR AL PRESUPUESTO
        # --------------------------------------------------------------------------------------
        with col_agregar.form("form_agregar_presupuesto"):

            col_agregar.subheader("Añadir Simulación al Presupuesto")

            col_tanda, col_litros_total = col_agregar.columns(2)

            cantidad_a_agregar = col_tanda.number_input(
                f"Número de Tandas de {cantidad_litros:.2f}L:",
                min_value=1,
                value=1,
                step=1,
                key="cantidad_tandas"
            )

            costo_final_tandas = costo_total_final * cantidad_a_agregar

            # -------------------------------
            # PRECIO / MARGEN BIDIRECCIONAL
            # -------------------------------
            precio_venta_total_ars = st.session_state.precio_venta_manual * cantidad_a_agregar
            precio_venta_total_usd = precio_venta_total_ars / cotizacion_dolar_actual

            margen_ganancia_calculado = (
                (precio_venta_total_ars - costo_final_tandas) / costo_final_tandas * 100
            ) if costo_final_tandas > 0 else 0.0

            total_litros = cantidad_litros * cantidad_a_agregar
            col_litros_total.metric(
                "Volumen Total a Presupuestar",
                f"{total_litros:,.2f} Litros"
            )

            st.markdown(f"**Costo Total de Producción (ARS):** ${costo_final_tandas:,.2f}")
            st.markdown(f"**Margen de Ganancia Calculado:** **{margen_ganancia_calculado:,.2f}%**")
            st.markdown(f"**Precio de Venta Total (ARS):** ${precio_venta_total_ars:,.2f}")
            st.markdown(f"**Precio de Venta Total (USD):** USD ${precio_venta_total_usd:,.2f}")
            # -------------------------------
            # PRECIO FINAL POR LITRO (VISTA CLIENTE)
            # -------------------------------
            if total_litros > 0:
                precio_por_litro_ars = precio_venta_total_ars / total_litros
                precio_por_litro_usd = precio_por_litro_ars / cotizacion_dolar_actual

                st.markdown("### 💰 Precio Final por Litro")
                col_pl1, col_pl2 = st.columns(2)

                col_pl1.metric(
                    "ARS por Litro",
                    f"${precio_por_litro_ars:,.2f}"
                )

                col_pl2.metric(
                    "USD por Litro",
                    f"USD ${precio_por_litro_usd:,.2f}"
                )

            # ✅ EL BOTÓN VA DENTRO DEL FORM
            if st.form_submit_button("➕ Agregar al Presupuesto"):

                next_id = len(st.session_state['simulaciones_presupuesto']) + 1

                simulacion_data = {
                    'ID': next_id,
                    'nombre_receta': receta_seleccionada_nombre,
                    'litros': total_litros,
                    'costo_total_ars': costo_final_tandas,
                    'costo_por_litro_ars': costo_por_litro_ars,
                    'gasto_indirecto_tanda': gasto_indirecto_tanda * cantidad_a_agregar,
                    'costo_flete_total_ars': costo_flete_total_ars * cantidad_a_agregar,
                    'costo_envase_total_ars': costo_total_empaque_ars * cantidad_a_agregar,
                    'costo_mp_total_ars': costo_mp_total * cantidad_a_agregar,
                    'costo_total_mp_usd': costo_total_mp_usd * cantidad_a_agregar,
                    'cantidad_tandas': cantidad_a_agregar,
                    'margen_ganancia': margen_ganancia_calculado,
                    'precio_venta_total_ars': precio_venta_total_ars,
                    'precio_venta_total_usd': precio_venta_total_usd,
                    'envase_info_json': json.dumps({
                        'Envase_Nombre': envase_seleccionado_nombre_final,
                        'Capacidad_Litros': capacidad_litros,
                        'Unidades_Envase_Total': unidades_necesarias * cantidad_a_agregar,
                        'Precio_Envase_Unitario_ARS': precio_envase_unitario_ars,
                    })
                }

                st.session_state['simulaciones_presupuesto'].append(simulacion_data)
                st.success(
                    f"Simulación '{receta_seleccionada_nombre}' añadida al presupuesto "
                    f"con un precio de venta de ${precio_venta_total_ars:,.2f} ARS."
                )

        st.markdown("---")


    # --------------------------------------------------------------------------------------
    # 2. VISUALIZACIÓN Y EDICIÓN DEL PRESUPUESTO ACUMULADO
    # --------------------------------------------------------------------------------------
    if st.session_state['simulaciones_presupuesto']:
        st.subheader("Presupuesto Acumulado (Edición)")
        
        # 1. Crear el DataFrame para la edición
        df_presupuesto = pd.DataFrame(st.session_state['simulaciones_presupuesto'])
        
        # --- [FIX para KeyError: 'precio_venta_total_ars'] ---
        # Manejar datos antiguos de la sesión
        if 'precio_venta_total_ars' not in df_presupuesto.columns:
            df_presupuesto['precio_venta_total_ars'] = df_presupuesto['costo_total_ars'] * 1.30 
            
        if 'precio_venta_total_usd' not in df_presupuesto.columns:
            df_presupuesto['precio_venta_total_usd'] = df_presupuesto['precio_venta_total_ars'] / cotizacion_dolar_actual
            
        if 'margen_ganancia' not in df_presupuesto.columns:
            costo = df_presupuesto['costo_total_ars']
            venta = df_presupuesto['precio_venta_total_ars']
            df_presupuesto['margen_ganancia'] = ((venta - costo) / costo) * 100
        # --- [FIN FIX para KeyError: 'precio_venta_total_ars'] ---
        
        # Se asegura que la columna de precio de venta esté correctamente calculada para la visualización inicial
        df_presupuesto['Precio_Venta_ARS'] = df_presupuesto['precio_venta_total_ars']
        df_presupuesto['Precio_Venta_USD'] = df_presupuesto['precio_venta_total_usd']
        
        # <<< CORRECCIÓN CLAVE para KeyError: 'Eliminar' >>>
        # Inicializar la columna 'Eliminar' antes de seleccionarla para la vista
        df_presupuesto['Eliminar'] = False 
        
        column_config_presupuesto = {
            'ID': st.column_config.NumberColumn(disabled=True),
            'nombre_receta': st.column_config.TextColumn("Receta", disabled=True),
            'litros': st.column_config.NumberColumn("Litros", format="%.2f L", disabled=True),
            'costo_total_ars': st.column_config.NumberColumn("Costo Total (ARS)", format="$%.2f", disabled=True, help="Costo de Producción."),
            'cantidad_tandas': st.column_config.NumberColumn("Tandas", disabled=True),
            # [MODIFICACIÓN CLAVE: HABILITAR EDICIÓN DE PRECIO]
            'Precio_Venta_ARS': st.column_config.NumberColumn(
                "Precio Venta Total (ARS)", 
                format="$%.2f", 
                help="Haga doble clic para editar el Precio de Venta (ARS)."
            ),
            # [MODIFICACIÓN CLAVE: MARGEN DE SÓLO LECTURA]
            'margen_ganancia': st.column_config.NumberColumn("Margen de Ganancia (%)", format="%.2f", disabled=True, help="Margen calculado automáticamente."),
            'Precio_Venta_USD': st.column_config.NumberColumn("Venta Total (USD)", format="$%.2f", disabled=True),
            'Eliminar': st.column_config.CheckboxColumn("Eliminar", default=False)
        }

        # Mostrar solo las columnas relevantes para la edición/visualización, incluyendo el margen y el precio editable
        df_display_presupuesto = df_presupuesto[[ 
            'ID', 'nombre_receta', 'litros', 'costo_total_ars', 'cantidad_tandas', 
            'Precio_Venta_ARS', 
            'margen_ganancia', 
            'Precio_Venta_USD', 'Eliminar' 
        ]].copy()
        
        edited_presupuesto_df = st.data_editor(
            df_display_presupuesto,
            column_config=column_config_presupuesto,
            use_container_width=True,
            hide_index=True,
            # Asegurarse que el orden de columnas incluye el precio editable
            column_order=[ 'ID', 'nombre_receta', 'litros', 'costo_total_ars', 'cantidad_tandas', 
                           'Precio_Venta_ARS', 'margen_ganancia', 'Precio_Venta_USD', 'Eliminar' ],
            key="editor_presupuesto_final"
        )
        
        # Reconstruir la lista de simulaciones aplicando los cambios (Precio Venta ARS y eliminación)
        nuevas_simulaciones = []
        for index, row in edited_presupuesto_df.iterrows():
            if not row['Eliminar']:
                # Buscar la simulación original por ID
                original_sim = df_presupuesto[df_presupuesto['ID'] == row['ID']].iloc[0].to_dict()
                
                # Obtener el costo total original (no editable)
                costo_total_ars = original_sim['costo_total_ars']
                
                # [MODIFICACIÓN CLAVE] Aplicar el nuevo precio de venta manual editado
                nuevo_precio_venta_ars = row['Precio_Venta_ARS']
                
                # 1. Actualizar el precio de venta ARS
                original_sim['precio_venta_total_ars'] = nuevo_precio_venta_ars
                
                # 2. Recalcular el margen de ganancia
                nuevo_margen_ganancia = ((nuevo_precio_venta_ars - costo_total_ars) / costo_total_ars) * 100 if costo_total_ars > 0 else 0.0
                original_sim['margen_ganancia'] = nuevo_margen_ganancia
                
                # 3. Recalcular el precio de venta USD
                original_sim['precio_venta_total_usd'] = nuevo_precio_venta_ars / cotizacion_dolar_actual
                
                nuevas_simulaciones.append(original_sim)
                
        st.session_state['simulaciones_presupuesto'] = nuevas_simulaciones
        st.markdown("---")
        if st.button("Actualizar Presupuesto (Aplicar Eliminados/Margen)"):
            st.rerun()

        # --------------------------------------------------------------------------------------
        # 3. RESUMEN FINAL Y GUARDADO
        # --------------------------------------------------------------------------------------
        # Volver a cargar el DataFrame final después de la edición/eliminación
        df_final = pd.DataFrame(st.session_state['simulaciones_presupuesto'])
        
        if not df_final.empty:
            st.subheader("Resumen Final del Presupuesto")
            litros_total_acumulado = df_final['litros'].sum()
            costo_total_acumulado = df_final['costo_total_ars'].sum()
            precio_final_ars_total = df_final['precio_venta_total_ars'].sum()
            precio_final_usd_total = precio_final_ars_total / cotizacion_dolar_actual

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("Litros Totales", f"{litros_total_acumulado:,.2f} L")
            col_b.metric("Costo Total (ARS)", f"${costo_total_acumulado:,.2f}")
            col_c.metric("Precio Final (ARS)", f"${precio_final_ars_total:,.2f}")
            col_c.metric("Precio Final (USD)", f"USD ${precio_final_usd_total:,.2f}")

            # --------------------------------------------------------------------------------------
            # 4. GUARDADO DE PRESUPUESTO
            # --------------------------------------------------------------------------------------
            st.markdown("---")
            st.subheader("Guardar y Exportar Presupuesto")
            
            cliente_nombre = st.text_input("Nombre del Cliente para guardar:", key="cliente_nombre_final")
            
            if st.button("💾 Guardar Presupuesto Final y Generar PDF"):
                if not cliente_nombre:
                    st.error("Por favor, ingrese un nombre de cliente.")
                else:
                    # 1. Obtener o crear ID del cliente
                    cliente_id = get_or_create_client(conn, cliente_nombre)

                    # Desglosar los JSON de envase para obtener Envase_Nombre, Unidades_Envase_Total y Capacidad_Litros
                    df_envase_info = df_final['envase_info_json'].apply(json.loads).apply(pd.Series)
                    # CORRECCIÓN DE ERROR: Agregar .reset_index(drop=True) para evitar 'ValueError: cannot reindex on an axis with duplicate...'
                    df_envase_info = df_envase_info.reset_index(drop=True)
                    df_final_clean = df_final.reset_index(drop=True)
                    
                    df_detalle_guardado = pd.concat([
                        df_final_clean[['nombre_receta', 'litros', 'precio_venta_total_ars', 'precio_venta_total_usd']].rename(columns={'nombre_receta': 'Receta', 'precio_venta_total_ars': 'Precio_Venta_Total_ARS', 'precio_venta_total_usd': 'Precio_Venta_Total_USD'}),
                        df_envase_info[['Envase_Nombre', 'Unidades_Envase_Total', 'Capacidad_Litros']] # <<< CORRECCIÓN: SE INCLUYE Capacidad_Litros
                    ], axis=1)
                    
                    # CORRECCIÓN DE ERROR: renombrar 'litros' para que coincida con el nombre en el JSON y la impresión
                    df_detalle_guardado.rename(columns={'litros': 'Litros'}, inplace=True) 

                    # Crear el JSON final del detalle usando los datos ya desglosados
                    detalle_simulaciones_json = df_detalle_guardado.to_json(orient='records')
                    
                    # 3. Calcular el margen de ganancia promedio para el registro principal
                    margen_ganancia_promedio = ((precio_final_ars_total - costo_total_acumulado) / costo_total_acumulado) * 100 if costo_total_acumulado > 0 else 0.0

                    # 4. Guardar el presupuesto
                    presupuesto_id = save_presupuesto(
                        conn, 
                        cliente_id, 
                        margen_ganancia_promedio, 
                        litros_total_acumulado, 
                        costo_total_acumulado, 
                        precio_final_ars_total, 
                        detalle_simulaciones_json
                    )
                    st.success(f"✅ Presupuesto N° {presupuesto_id} guardado con éxito para {cliente_nombre}!")

                    # 5. Prepara los datos para generar el PDF
                    # El DataFrame final necesita las columnas Envase_Nombre y Unidades_Envase_Total
                    df_pdf_data = df_final_clean[['nombre_receta', 'litros', 'precio_venta_total_ars', 'precio_venta_total_usd']].rename(columns={'nombre_receta': 'Receta', 'precio_venta_total_ars': 'Precio_Venta_Total_ARS', 'precio_venta_total_usd': 'Precio_Venta_Total_USD'})
                    # Asegurar la columna Capacidad_Litros
                    df_pdf_data = pd.concat([df_pdf_data, df_envase_info], axis=1)

                    # Asegurar el nombre de la columna 'Litros' para el PDF y la vista previa
                    df_pdf_data.rename(columns={'litros': 'Litros'}, inplace=True) 

                    data_pdf = {
                        'cliente_nombre': cliente_nombre,
                        'cotizacion_dolar_actual': cotizacion_dolar_actual,
                        'presupuesto_id': presupuesto_id,
                        'precio_unitario_ars_litro': precio_final_ars_total / litros_total_acumulado,
                        'precio_final_ars': precio_final_ars_total,
                        'litros_total_acumulado': litros_total_acumulado,
                        'df_detalle_final_presupuesto': df_pdf_data
                    }
                    st.session_state['presupuesto_data_for_print'] = data_pdf
                    st.rerun()

    # --------------------------------------------------------------------------------------
    # 5. VISTA DE IMPRESIÓN (PDF)
    # --------------------------------------------------------------------------------------
    if st.session_state['presupuesto_data_for_print']:
        data_pdf = st.session_state['presupuesto_data_for_print']
        st.markdown("---")
        st.subheader(f"Vista Previa del Presupuesto N° {data_pdf['presupuesto_id']}")
        
        # Muestra el detalle final de la tabla de presupuesto
        df_detalle = data_pdf['df_detalle_final_presupuesto'].copy()
        
        # <<< FIX de Robustez para KeyError: 'Litros' >>>
        # Se asegura que la columna 'Litros' tenga la 'L' mayúscula en caso de Session State desactualizado
        if 'litros' in df_detalle.columns:
            df_detalle.rename(columns={'litros': 'Litros'}, inplace=True)
            
        # Calcular el precio unitario por envase para la vista previa
        df_detalle['Precio Venta ARS/u.'] = df_detalle['Precio_Venta_Total_ARS'] / df_detalle['Unidades_Envase_Total']
        df_detalle['Precio Venta USD/u.'] = df_detalle['Precio_Venta_Total_USD'] / df_detalle['Unidades_Envase_Total']
        df_detalle.rename(columns={'Precio_Venta_Total_ARS': 'Total ARS', 'Precio_Venta_Total_USD': 'Total USD'}, inplace=True)
        
        # [MODIFICACIÓN CLAVE] Eliminar 'Envase_Nombre' de la vista previa de la tabla
        st.dataframe(
            df_detalle[['Receta', 'Litros', 'Unidades_Envase_Total', 'Capacidad_Litros', 'Precio Venta ARS/u.', 'Precio Venta USD/u.', 'Total ARS', 'Total USD']],
            use_container_width=True,
            hide_index=True,
            column_config={
                'Precio Venta ARS/u.': st.column_config.NumberColumn(format="$%.2f"),
                'Precio Venta USD/u.': st.column_config.NumberColumn(format="$%.2f"),
                'Total ARS': st.column_config.NumberColumn(format="$%.2f"),
                'Total USD': st.column_config.NumberColumn(format="$%.2f")
            }
        )
        
        # Generar PDF y botón de descarga
        pdf_output = generate_pdf_reportlab(data_pdf)
        
        st.download_button(
            label=f"⬇️ Descargar Presupuesto N° {data_pdf['presupuesto_id']} ({data_pdf['cliente_nombre']}.pdf)",
            data=pdf_output,
            file_name=f"Presupuesto_{data_pdf['presupuesto_id']}_{data_pdf['cliente_nombre'].replace(' ', '_')}.pdf",
            mime="application/pdf"
        )
        
        if st.button("🗑️ Limpiar Vista de Presupuesto"):
            st.session_state['presupuesto_data_for_print'] = {}
            st.rerun()
        
    elif st.session_state['simulaciones_presupuesto']:
        st.info("Actualice el presupuesto (botón superior) para generar el resumen final.")
        
    conn.close()

if __name__ == "__main__":
    main()