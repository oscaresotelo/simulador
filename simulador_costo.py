import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime

# --- CONFIGURACIÓN DE LA BASE DE DATOS ---
DB_NAME = "minerva.db"
BASE_LITROS = 200.0 # Cantidad base de la receta original

def conectar_db(db_name=DB_NAME):
    """Establece la conexión a la base de datos."""
    conn = sqlite3.connect(db_name)
    conn.row_factory = sqlite3.Row
    return conn

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
        # Nota: precio_unitario guarda el precio final en ARS con el que se registró la compra
        return precio['precio_unitario'], precio['costo_flete'], precio['otros_costos'], precio['cotizacion_usd']
    else:
        return 0.0, 0.0, 0.0, 1.0

def calcular_costo_total(ingredientes_df, cotizacion_dolar_actual, conn):
    """
    Calcula el costo total de la receta en ARS, aplicando la cotización del dólar actual.
    """
    detalle_costo = []
    
    for index, ingrediente in ingredientes_df.iterrows():
        materia_prima_id = ingrediente["materia_prima_id"]
        cantidad_usada = ingrediente["cantidad_simulada"]
        nombre_mp = ingrediente["Materia Prima"]
        
        # 1. Obtener precios: prioridad del precio manual de la tabla
        # ATENCIÓN: Para MPs Temporales Nuevas, el 'precio_unitario_manual' ahora es el precio en USD.
        precio_manual = ingrediente.get('precio_unitario_manual', 0.0)
        cotizacion_manual = ingrediente.get('cotizacion_usd_manual', 1.0)
        es_temporal_nueva = ingrediente.get('Temporal', False) and materia_prima_id == -1

        precio_unitario_reg = 0.0
        cotizacion_usd_reg = 1.0
        costo_flete = 0.0 
        otros_costos = 0.0 

        if es_temporal_nueva and precio_manual > 0.0:
            # Caso 1a: MP Temporal NUEVA (Ingresada como USD en el formulario)
            precio_base_usd = precio_manual 
            cotizacion_usd_reg = cotizacion_manual
            precio_unitario_reg = precio_base_usd * cotizacion_usd_reg # Se convierte a ARS de registro
            
        elif precio_manual > 0.0 and not es_temporal_nueva:
            # Caso 1b: MP Existente con PRECIO MANUAL sobrescrito (Debe ser en ARS)
            precio_unitario_reg = precio_manual
            cotizacion_usd_reg = cotizacion_manual
            
        elif materia_prima_id != -1:
            # Caso 2: MP existente (base o temporal sin precio manual). Usamos la BD.
            precio_unitario_reg, costo_flete, otros_costos, cotizacion_usd_reg = \
                obtener_precio_actual_materia_prima(conn, materia_prima_id)
        else:
             # Caso 3: MP Temporal nueva sin precio USD (> 0)
            precio_unitario_reg, costo_flete, otros_costos, cotizacion_usd_reg = 0.0, 0.0, 0.0, 1.0

        # 2. Lógica de Dolarización
        costo_unitario_ars = 0.0
        
        if cotizacion_usd_reg > 1.0:
            # El precio registrado/calculado está dolarizado. Lo actualizamos con el dólar del día.
            # 1. Volver a USD (Precio Base de Compra)
            precio_base_usd = precio_unitario_reg / cotizacion_usd_reg
            # 2. Convertir al ARS del día de la simulación
            costo_unitario_ars = precio_base_usd * cotizacion_dolar_actual
            moneda_origen = f'USD ({cotizacion_usd_reg:.2f})'
        else:
            # Precio fijo en ARS
            costo_unitario_ars = precio_unitario_reg
            moneda_origen = 'ARS (Fijo)'

        # 3. Sumar costos asociados
        costo_unitario_final_ars = costo_unitario_ars + costo_flete + otros_costos
        costo_ingrediente_total = cantidad_usada * costo_unitario_final_ars
        
        detalle_costo.append({
            "Materia Prima": nombre_mp,
            "Unidad": ingrediente["Unidad"],
            "Cantidad (Simulada)": cantidad_usada,
            "Moneda Origen": moneda_origen,
            "Costo Unit. ARS (Base)": costo_unitario_ars,
            "Flete / Otros": costo_flete + otros_costos,
            "Costo Unit. ARS (Total)": costo_unitario_final_ars,
            "Costo Total ARS": costo_ingrediente_total
        })

    detalle_df = pd.DataFrame(detalle_costo)
    costo_total_ars = detalle_df["Costo Total ARS"].sum()
    
    return costo_total_ars, detalle_df

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

    conn = conectar_db()
    cursor = conn.cursor()

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

    # --- SELECCIÓN DE RECETA ---
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
    col_base.info(f"Receta Base: {BASE_LITROS} L")
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
            'cotizacion_usd_manual': 1.0, # 0.0 y 1.0 fuerzan la búsqueda en la BD para ingredientes base
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
            # Estos valores son los que se usan para MPs Temporales (precio_unitario_manual es USD para MPs Nuevas)
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
        "Cantidad Base (200L)": st.column_config.NumberColumn(
            help="Cantidad requerida para la base (200L).",
            format="%.4f",
        ),
        "cantidad_simulada": st.column_config.NumberColumn(
            "Cantidad (Total L)",
            help=f"Cantidad total requerida para {cantidad_litros:.2f}L.",
            format="%.4f",
            disabled=True,
        ),
        "Quitar": st.column_config.CheckboxColumn(
            help="Marque para excluir de la simulación.",
        ),
        "Temporal": st.column_config.CheckboxColumn(disabled=True),
        "precio_unitario_manual": st.column_config.NumberColumn(
            "Precio Manual Unit. (ARS/USD)",
            help="Precio que se usará. Para MP Existente, sobrescribe el precio ARS. Para MP Nueva, es el precio USD.",
            format="%.4f",
        ),
        "cotizacion_usd_manual": st.column_config.NumberColumn(
            "Cot. USD (Manual)",
            help="Cotización USD correspondiente al precio manual. Si es MP de BD, sobrescribe la cotización. Si es 1.0, usa la de BD.",
            format="%.2f",
            min_value=1.0,
        ),
    }
    
    # Mostrar solo las columnas relevantes al usuario
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
    costo_total, detalle_costo_df = calcular_costo_total(
        ingredientes_a_calcular, 
        cotizacion_dolar_actual, 
        conn
    )
    
    st.session_state['costo_total'] = costo_total
    st.session_state['detalle_costo'] = detalle_costo_df
    st.session_state['litros'] = cantidad_litros
    
    st.subheader(f"✅ Resultado del Costo en Vivo para {st.session_state['litros']:.2f} Litros (Dólar: ${st.session_state['dolar']:.2f})")
    
    col_res1, col_res2 = st.columns(2)
    col_res1.metric(
        "Costo Total de Materia Prima",
        f"${st.session_state['costo_total']:,.2f} ARS"
    )
    col_res2.metric(
        "Costo por Litro",
        f"${st.session_state['costo_total'] / st.session_state['litros']:,.2f} ARS/Litro"
    )
    
    with st.expander("Ver Detalle de Costo por Ingrediente 🔎"):
        st.dataframe(st.session_state['detalle_costo'], use_container_width=True)

    
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

    # ----------------------------------------------------
    # TAB 1: MP EXISTENTE (Usa precios de la base de datos)
    # ----------------------------------------------------
    with tab_existente:
        st.markdown("**Seleccione una MP existente y la cantidad base. El precio se tomará automáticamente del último registro de la BD.**")
        with st.form("form_agregar_mp_existente"):
            
            mp_existente_nombre = st.selectbox(
                "Materia Prima:",
                mp_nombres,
                key="mp_existente_select_key" 
            )
            
            # Obtener y mostrar precios de BD al seleccionar
            mp_info = mp_map[mp_existente_nombre]
            mp_id = mp_info['id']
            precio_unitario_ars, _, _, cotizacion_usd_reg_bd = obtener_precio_actual_materia_prima(conn, mp_id)
            
            # Mostrar solo el precio, ocultando la cotización registrada
            col_info_precio1, col_info_precio2 = st.columns(2)
            if cotizacion_usd_reg_bd > 1.0:
                precio_base_usd = precio_unitario_ars / cotizacion_usd_reg_bd
                col_info_precio1.metric("Precio Registrado (USD)", f"${precio_base_usd:.4f} USD")
                # col_info_precio2 (Cot. Reg. ARS) ahora está vacío
            else:
                col_info_precio1.metric("Precio Registrado (ARS)", f"${precio_unitario_ars:,.2f} ARS")
                # col_info_precio2 (Cot. Reg. ARS) ahora está vacío

            st.markdown("---")
            
            # Cantidad y Unidad
            col_cant, col_unidad_display = st.columns(2)
            cantidad_base = col_cant.number_input(
                "Cantidad Base (para 200L):",
                min_value=0.0001,
                value=1.0,
                step=0.01,
                format="%.4f",
                key="temp_cantidad_existente"
            )
            col_unidad_display.text_input("Unidad de Medida:", value=mp_info['unidad'], disabled=True)

            if st.form_submit_button("Agregar MP Existente"):
                
                # Se agrega con precio_unitario=0.0 y cotizacion_usd=1.0 para forzar el uso de la BD en el cálculo
                st.session_state.ingredientes_temporales.append({
                    'nombre': mp_info['nombre'],
                    'unidad': mp_info['unidad'],
                    'cantidad_base': cantidad_base,
                    'precio_unitario': 0.0, 
                    'cotizacion_usd': 1.0,
                    'materia_prima_id': mp_info['id'] 
                })
                st.success(f"Materia Prima '{mp_info['nombre']}' agregada con precio de la BD. Recalculando...")
                st.rerun()

    # ----------------------------------------------------
    # TAB 2: MP NUEVA (Requiere precio manual en USD)
    # ----------------------------------------------------
    with tab_nueva:
        st.markdown("**Ingrese una MP que NO está en la BD. Debe especificar el precio unitario en USD.**")
        with st.form("form_agregar_mp_nueva"):
            col_nombre, col_unidad = st.columns(2)
            mp_nombre = col_nombre.text_input("Nombre de la Materia Prima:", key="temp_nombre_nueva")
            mp_unidad = col_unidad.text_input("Unidad de Medida (kg, L, gr):", value="kg", key="temp_unidad_nueva")
            
            col_cant, col_precio, col_cot = st.columns(3)
            cantidad_base = col_cant.number_input(
                "Cantidad Base (para 200L):",
                min_value=0.0001,
                value=1.0,
                step=0.01,
                format="%.4f",
                key="temp_cantidad_nueva"
            )
            
            # Precio Unitario Manual en USD
            precio_unitario_usd = col_precio.number_input(
                "Precio Unitario Manual (USD):",
                min_value=0.01,
                value=1.0,
                step=0.01,
                format="%.4f",
                key="temp_precio_usd_nueva"
            )
            
            cotizacion_usd = col_cot.number_input(
                "Cotización USD de Compra:",
                min_value=1.0,
                value=cotizacion_dolar_actual,
                step=0.1,
                format="%.2f",
                help="Cotización del dólar con la que se 'registró' esta compra (ARS/USD).",
                key="temp_cot_usd_nueva"
            )
            
            if st.form_submit_button("Agregar MP Nueva"):
                if mp_nombre and cantidad_base > 0 and precio_unitario_usd > 0:
                    st.session_state.ingredientes_temporales.append({
                        'nombre': mp_nombre,
                        'unidad': mp_unidad,
                        'cantidad_base': cantidad_base,
                        # Guardamos el precio USD en el campo 'precio_unitario'
                        'precio_unitario': precio_unitario_usd, 
                        'cotizacion_usd': cotizacion_usd,
                        'materia_prima_id': -1 # ID temporal para nueva MP
                    })
                    st.success(f"Materia Prima Temporal '{mp_nombre}' agregada (Precio USD). Recalculando...")
                    st.rerun()
                else:
                    st.error("Por favor, complete todos los campos (nombre, cantidad base > 0, precio USD > 0).")

    conn.close()
    
if __name__ == "__main__":
    main()