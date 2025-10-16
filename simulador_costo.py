import streamlit as st
import sqlite3
import pandas as pd
from datetime import date
import calendar 

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

# NOTA: get_costo_flete_ars() FUE ELIMINADA y su lógica reemplazada por input manual.

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


# =================================================================================================
# FUNCIONES EXISTENTES (De costeo de MP)
# =================================================================================================
# (Estas funciones se mantienen sin cambios, gestionan el costo directo de la Materia Prima)
# ...

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
    cursor.execute("""
        SELECT precio_unitario, costo_flete, otros_costos, cotizacion_usd
        FROM precios_materias_primas
        WHERE materia_prima_id = ?
        ORDER BY fecha DESC
        LIMIT 1
    """, (materia_prima_id,))
    
    precio = cursor.fetchone()
    if precio:
        # precio_unitario: Precio final en ARS con el que se registró la compra
        # costo_flete, otros_costos: Costos directos por unidad de MP asociados a esa compra
        return precio['precio_unitario'], precio['costo_flete'], precio['otros_costos'], precio['cotizacion_usd']
    else:
        return 0.0, 0.0, 0.0, 1.0

def calcular_costo_total(ingredientes_df, cotizacion_dolar_actual, conn):
    """
    Calcula el costo total SÓLO de la Materia Prima en ARS (incluyendo flete/otros asociados a la MP).
    """
    detalle_costo = []
    
    for index, ingrediente in ingredientes_df.iterrows():
        materia_prima_id = ingrediente["materia_prima_id"]
        cantidad_usada = ingrediente["cantidad_simulada"]
        nombre_mp = ingrediente["Materia Prima"]
        
        # 1. Obtener precios: prioridad del precio manual de la tabla
        precio_manual = ingrediente.get('precio_unitario_manual', 0.0)
        cotizacion_manual = ingrediente.get('cotizacion_usd_manual', 1.0)
        es_temporal_nueva = ingrediente.get('Temporal', False) and materia_prima_id == -1

        precio_unitario_reg = 0.0
        cotizacion_usd_reg = 1.0
        costo_flete_mp = 0.0 
        otros_costos_mp = 0.0 

        if es_temporal_nueva and precio_manual > 0.0:
            precio_base_usd = precio_manual 
            cotizacion_usd_reg = cotizacion_manual
            precio_unitario_reg = precio_base_usd * cotizacion_usd_reg 
            
        elif precio_manual > 0.0 and not es_temporal_nueva:
            precio_unitario_reg = precio_manual
            cotizacion_usd_reg = cotizacion_manual
            
        elif materia_prima_id != -1:
            precio_unitario_reg, costo_flete_mp, otros_costos_mp, cotizacion_usd_reg = \
                obtener_precio_actual_materia_prima(conn, materia_prima_id)
        else:
            precio_unitario_reg, costo_flete_mp, otros_costos_mp, cotizacion_usd_reg = 0.0, 0.0, 0.0, 1.0

        # 2. Lógica de Dolarización
        costo_unitario_ars = 0.0
        
        if cotizacion_usd_reg > 1.0:
            precio_base_usd = precio_unitario_reg / cotizacion_usd_reg
            costo_unitario_ars = precio_base_usd * cotizacion_dolar_actual
            moneda_origen = f'USD ({cotizacion_usd_reg:.2f})'
        else:
            costo_unitario_ars = precio_unitario_reg
            moneda_origen = 'ARS (Fijo)'

        # 3. Sumar costos asociados (Flete y Otros Costos de MP)
        costo_unitario_final_ars = costo_unitario_ars + costo_flete_mp + otros_costos_mp
        costo_ingrediente_total = cantidad_usada * costo_unitario_final_ars
        
        detalle_costo.append({
            "Materia Prima": nombre_mp,
            "Unidad": ingrediente["Unidad"],
            "Cantidad (Simulada)": cantidad_usada,
            "Moneda Origen": moneda_origen,
            "Costo Unit. ARS (Base)": costo_unitario_ars,
            "Flete / Otros MP": costo_flete_mp + otros_costos_mp, 
            "Costo Unit. ARS (Total)": costo_unitario_final_ars,
            "Costo Total ARS": costo_ingrediente_total
        })

    detalle_df = pd.DataFrame(detalle_costo)
    costo_total_ars = detalle_df["Costo Total ARS"].sum()
    
    return costo_total_ars, detalle_df

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
        # FLETE BASE AHORA SE INICIALIZA EN 0 Y SE INGRESA MANUALMENTE
        st.session_state['flete_base_200l'] = 5000.0 # Valor inicial sugerido

    conn = get_connection()
    
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
    # 3. VOLUMEN MENSUAL Y CÁLCULO DE OVERHEAD POR LITRO (AUTOMÁTICO)
    # -----------------------------------------------------------
    st.sidebar.subheader("Asignación de Costos por Overhead")
    
    # Cálculo automático del volumen mensual basado en las 8 recetas diarias
    volumen_mensual_litros = VOLUMEN_MENSUAL_AUTOMATICO
    
    st.sidebar.markdown(f"**Volumen Mensual de Producción (8 Recetas/Día):**")
    st.sidebar.info(f"{volumen_mensual_litros:,.0f} Litros/Mes")

    # Calcular Costo Indirecto por Litro
    if volumen_mensual_litros > 0:
        costo_indirecto_por_litro = gasto_fijo_mensual_auto / volumen_mensual_litros
    else:
        costo_indirecto_por_litro = 0.0
        
    st.sidebar.metric("Costo Indirecto Operativo por Litro", f"${costo_indirecto_por_litro:,.2f} ARS/L")

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

    # 2. Configurar el editor de datos (vista Excel)
    column_config = {
        "ID_Receta": st.column_config.TextColumn(disabled=True),
        "Materia Prima": st.column_config.TextColumn(disabled=True),
        "materia_prima_id": st.column_config.TextColumn(disabled=True),
        "Unidad": st.column_config.TextColumn(disabled=True),
        "Cantidad Base (200L)": st.column_config.NumberColumn(help="Cantidad requerida para la base (200L).", format="%.4f",),
        "cantidad_simulada": st.column_config.NumberColumn("Cantidad (Total L)", format="%.4f", disabled=True,),
        "Quitar": st.column_config.CheckboxColumn(help="Marque para excluir de la simulación.",),
        "Temporal": st.column_config.CheckboxColumn(disabled=True),
        "precio_unitario_manual": st.column_config.NumberColumn("Precio Manual Unit. (ARS/USD)", format="%.4f",),
        "cotizacion_usd_manual": st.column_config.NumberColumn("Cot. USD (Manual)", format="%.2f", min_value=1.0,),
    }
    
    cols_display = ["Materia Prima", "Unidad", "Cantidad Base (200L)", "cantidad_simulada", 
                    "precio_unitario_manual", "cotizacion_usd_manual", "Quitar"]

    edited_df = st.data_editor(
        ingredientes_df[cols_display],
        column_config=column_config,
        num_rows="fixed",
        use_container_width=True,
        key="data_editor_ingredientes"
    )

    # 3. Mapear de vuelta el DataFrame editado al original para el cálculo
    ingredientes_df['Cantidad Base (200L)'] = edited_df['Cantidad Base (200L)']
    ingredientes_df['Quitar'] = edited_df['Quitar']
    ingredientes_df['precio_unitario_manual'] = edited_df['precio_unitario_manual']
    ingredientes_df['cotizacion_usd_manual'] = edited_df['cotizacion_usd_manual']
    ingredientes_df['cantidad_simulada'] = ingredientes_df['Cantidad Base (200L)'] * factor_escala

    # --- LÓGICA DE CÁLCULO EN VIVO ---
    ingredientes_a_calcular = ingredientes_df[~ingredientes_df['Quitar']].copy()
    
    # Costo SÓLO de Materia Prima (Incluye Flete/Otros por MP)
    costo_mp_total, detalle_costo_df = calcular_costo_total(
        ingredientes_a_calcular, 
        cotizacion_dolar_actual, 
        conn
    )
    
    # --------------------------------------------------------------------------
    # CÁLCULOS DE COSTOS FIJOS (LÓGICA AUTOMÁTICA)
    # --------------------------------------------------------------------------
    
    # CÁLCULO DEL GASTO INDIRECTO (OVERHEAD)
    gasto_indirecto_tanda = costo_indirecto_por_litro * cantidad_litros
    
    # CÁLCULO DEL FLETE GENERAL (COMO COSTO DIRECTO) - USANDO VALOR MANUAL
    costo_flete_total_ars = st.session_state['flete_base_200l'] * factor_escala
    
    # CÁLCULO DEL COSTO TOTAL FINAL
    # Costo Total Final = (MP + Flete/Otros MP) + Flete General + Indirecto Operativo
    costo_total_final = costo_mp_total + costo_flete_total_ars + gasto_indirecto_tanda
    
    # Actualizar Session State (para uso futuro)
    st.session_state['costo_total_mp_y_flete_mp'] = costo_mp_total
    st.session_state['costo_flete_general_tanda'] = costo_flete_total_ars
    st.session_state['costo_total_final'] = costo_total_final
    st.session_state['litros'] = cantidad_litros
    
    st.subheader(f"✅ Resultado del Costo en Vivo para {st.session_state['litros']:.2f} Litros (Dólar: ${st.session_state['dolar']:.2f})")
    
    # MUESTRA DE RESULTADOS AMPLIADA
    col_res1, col_res2, col_res3 = st.columns(3) 
    
    col_res1.metric(
        "Costo Materia Prima (Incl. Flete/Otros MP)",
        f"${costo_mp_total:,.2f} ARS"
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

    st.markdown("---")
    st.header("COSTO TOTAL FINAL")
    col_final_1, col_final_2 = st.columns(2)
    
    col_final_1.metric(
        "Costo Total Final de la Tanda",
        f"${costo_total_final:,.2f} ARS"
    )
    
    col_final_2.metric(
        "Costo por Litro",
        f"${costo_total_final / st.session_state['litros']:,.2f} ARS/L"
    )
    
    with st.expander("Ver Detalle de Costo por Ingrediente 🔎"):
        st.dataframe(detalle_costo_df, use_container_width=True)

    
    # --- GESTIÓN DE ESTADO TEMPORAL ---
    st.header("⚙️ Guardar Estado de Simulación Temporal")
    
    if st.button("Aplicar Cambios (Cantidades y Precios) al Estado Temporal"):
        
        st.info("Actualizando estado de la simulación...")
        st.session_state.ingredientes_temporales = []
        ingredientes_temporales_actualizados = ingredientes_a_calcular[ingredientes_a_calcular['Temporal']]
        
        for index, row in ingredientes_temporales_actualizados.iterrows():
            st.session_state.ingredientes_temporales.append({
                'nombre': row['Materia Prima'],
                'unidad': row['Unidad'],
                'cantidad_base': row['Cantidad Base (200L)'],
                'precio_unitario': row['precio_unitario_manual'],
                'cotizacion_usd': row['cotizacion_usd_manual'],
                'materia_prima_id': row['materia_prima_id'],
            })
            
        st.success("Estado de simulación actualizado. Estos cambios se mantendrán hasta que cambie la receta base o reinicie la app.")
        st.rerun()

    # --- GESTIÓN DE MATERIAS PRIMAS (Agregar) ---
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
            precio_unitario_ars, _, _, cotizacion_usd_reg_bd = obtener_precio_actual_materia_prima(conn, mp_id)
            col_info_precio1, _ = st.columns(2)
            if cotizacion_usd_reg_bd > 1.0:
                precio_base_usd = precio_unitario_ars / cotizacion_usd_reg_bd
                col_info_precio1.metric("Precio Registrado (USD)", f"${precio_base_usd:.4f} USD")
            else:
                col_info_precio1.metric("Precio Registrado (ARS)", f"${precio_unitario_ars:,.2f} ARS")
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