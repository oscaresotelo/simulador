import streamlit as st
import sqlite3
import pandas as pd

# =================================================================================================
# CONFIGURACIÓN Y CONSTANTES
# =================================================================================================
DB_PATH = "minerva.db"

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

def get_recetas():
    """Obtiene todas las recetas."""
    query = "SELECT id, nombre FROM recetas ORDER BY nombre"
    df = fetch_df(query)
    if df.empty:
        return {}
    return df.set_index('id')['nombre'].to_dict()

def get_materias_primas():
    """Obtiene todas las materias primas para validación y referencia."""
    query = "SELECT id, nombre, unidad FROM materias_primas ORDER BY nombre"
    df = fetch_df(query)
    if df.empty:
        return {}
    
    mp_map = df.set_index('id').apply(lambda row: f"{row['nombre']} ({row['unidad']})", axis=1).to_dict()
    return set(df['id'].tolist()), mp_map # Devolvemos el set de IDs para validación

def get_ingredientes_receta(receta_id):
    """Obtiene los ingredientes actuales de una receta en formato DataFrame editable."""
    # Nota: No seleccionamos ninguna columna que no sea estrictamente necesaria para la edición.
    query = """
        SELECT 
            ri.materia_prima_id AS ID_MP_Actual,
            mp.nombre AS Materia_Prima,
            ri.cantidad AS Cantidad,
            ri.unidad AS Unidad,
            ri.materia_prima_id AS ID_MP_Nueva -- Columna inicial de sustitución
        FROM receta_ingredientes ri
        JOIN materias_primas mp ON ri.materia_prima_id = mp.id
        WHERE ri.receta_id = ?
        ORDER BY mp.nombre
    """
    df = fetch_df(query, (receta_id,))
    # Añadir columna para eliminación
    df['Quitar'] = False
    return df

def apply_updates(receta_id, edited_df, valid_mp_ids, initial_df):
    """Aplica los cambios del data_editor a la base de datos."""
    conn = get_connection()
    cursor = conn.cursor()
    errors = []
    success_count = 0
    
    # Convertir el DataFrame inicial a un diccionario para fácil comparación, usando el índice único (Row_Key)
    initial_map = initial_df.to_dict('index')

    # 1. Eliminar filas marcadas para "Quitar"
    df_to_delete = edited_df[edited_df['Quitar'] == True]
    for row_key, row in df_to_delete.iterrows():
        try:
            # Para eliminar, usamos el ID original (ID_MP_Actual)
            cursor.execute("""
                DELETE FROM receta_ingredientes
                WHERE receta_id = ? AND materia_prima_id = ?
            """, (receta_id, int(row['ID_MP_Actual'])))
            success_count += 1
        except sqlite3.Error as e:
            errors.append(f"❌ Error al eliminar MP {row['Materia_Prima']} (ID {row['ID_MP_Actual']}): {e}")

    # 2. Actualizar/Sustituir filas restantes
    df_to_update = edited_df[edited_df['Quitar'] == False]

    for row_key, row in df_to_update.iterrows():
        old_mp_id = int(row['ID_MP_Actual'])
        new_mp_id = int(row['ID_MP_Nueva'])
        new_cantidad = float(row['Cantidad'])
        new_unidad = row['Unidad']
        
        # Obtener valores originales usando la Row_Key del DataFrame inicial
        original_row = initial_map.get(row_key, {})
        
        # Si la clave no se encuentra (posible si se agregó una fila temporalmente), asumimos que todo es diferente
        original_cantidad = original_row.get('Cantidad', new_cantidad - 1) 
        original_unidad = original_row.get('Unidad', new_unidad + "x") 
        
        # Validación de nueva MP
        if new_mp_id not in valid_mp_ids:
            errors.append(f"❌ Error: El ID de MP Nueva {new_mp_id} no existe en la base de datos.")
            continue
            
        is_mp_changed = old_mp_id != new_mp_id
        is_qty_changed = new_cantidad != original_cantidad
        is_unit_changed = new_unidad != original_unidad

        if is_mp_changed:
            # Sustitución de ID: Se trata como DELETE y luego INSERT
            try:
                # 1. Verificar si el nuevo ID ya existe en la receta (como ingrediente diferente)
                cursor.execute("SELECT COUNT(*) FROM receta_ingredientes WHERE receta_id = ? AND materia_prima_id = ?", (receta_id, new_mp_id))
                if cursor.fetchone()[0] > 0:
                    errors.append(f"❌ Error al sustituir {old_mp_id}: El ID {new_mp_id} ya está en la receta como otro ingrediente. Consolide primero.")
                    continue

                # 2. Eliminar la fila antigua (usando el ID y Cantidad/Unidad original para ser más específico si la tabla lo permite)
                # NOTA: Usamos DELETE por RECETA_ID y OLD_MP_ID. Si OLD_MP_ID no es único, podría eliminar varias filas.
                # Asumimos que la combinación (receta_id, materia_prima_id) es el identificador lógico.
                cursor.execute("""
                    DELETE FROM receta_ingredientes
                    WHERE receta_id = ? AND materia_prima_id = ?
                """, (receta_id, old_mp_id))
                
                # 3. Insertar la nueva fila con el nuevo ID (y la nueva cantidad/unidad)
                cursor.execute("""
                    INSERT INTO receta_ingredientes (receta_id, materia_prima_id, cantidad, unidad)
                    VALUES (?, ?, ?, ?)
                """, (receta_id, new_mp_id, new_cantidad, new_unidad))
                
                success_count += 1
            except sqlite3.Error as e:
                errors.append(f"❌ Error de Sustitución ({old_mp_id} -> {new_mp_id}): {e}")
        
        elif is_qty_changed or is_unit_changed:
            # Actualización simple de Cantidad o Unidad (usa el ID_MP_Actual)
            try:
                cursor.execute("""
                    UPDATE receta_ingredientes
                    SET cantidad = ?, unidad = ?
                    WHERE receta_id = ? AND materia_prima_id = ?
                """, (new_cantidad, new_unidad, receta_id, old_mp_id))
                success_count += 1
            except sqlite3.Error as e:
                errors.append(f"❌ Error al actualizar cantidad/unidad de {row['Materia_Prima']} (ID {old_mp_id}): {e}")

    conn.commit()
    conn.close()
    return success_count, errors

def add_new_ingredient(receta_id, mp_id, cantidad, unidad, valid_mp_ids):
    """Inserta un nuevo ingrediente a la receta."""
    if mp_id not in valid_mp_ids:
        return False, f"Error: El ID de Materia Prima {mp_id} no existe."
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Verificar que no exista ya
        cursor.execute("SELECT COUNT(*) FROM receta_ingredientes WHERE receta_id = ? AND materia_prima_id = ?", (receta_id, mp_id))
        if cursor.fetchone()[0] > 0:
            return False, f"La Materia Prima ID {mp_id} ya existe en esta receta. Si desea cambiar cantidad/unidad, use el editor."
            
        cursor.execute("""
            INSERT INTO receta_ingredientes (receta_id, materia_prima_id, cantidad, unidad)
            VALUES (?, ?, ?, ?)
        """, (receta_id, mp_id, cantidad, unidad))
        conn.commit()
        return True, "Ingrediente agregado exitosamente."
    except sqlite3.Error as e:
        conn.rollback()
        return False, f"Error de base de datos al agregar: {e}"
    finally:
        conn.close()


# =================================================================================================
# INTERFAZ STREAMLIT PRINCIPAL
# =================================================================================================

def main():
    st.set_page_config(layout="wide")
    st.title("🛠️ Editor Avanzado de Ingredientes de Recetas")
    st.info("Puede editar **Cantidad/Unidad**, sustituir **IDs de MP** o **eliminar** ingredientes.")

    recetas_dict = get_recetas()
    valid_mp_ids, mp_map = get_materias_primas()

    if not recetas_dict or not valid_mp_ids:
        st.error("No se encontraron recetas o materias primas en la base de datos.")
        return

    # --- SELECCIÓN DE RECETA ---
    recetas_nombres = list(recetas_dict.values())
    receta_seleccionada_nombre = st.selectbox(
        "1. Seleccione la Receta a Modificar:", 
        recetas_nombres
    )
    
    receta_id = [id for id, nombre in recetas_dict.items() if nombre == receta_seleccionada_nombre][0]
    st.markdown(f"**ID de Receta Seleccionada:** `{receta_id}`")
    st.markdown("---")

    # --- INICIO DEL EDITOR (Usa Session State para persistir el DF) ---
    st.subheader(f"2. Editar Ingredientes de '{receta_seleccionada_nombre}'")

    # Lógica para cargar el DF inicial solo una vez por cambio de receta
    if 'ingredientes_editor_df' not in st.session_state or st.session_state.get('last_receta_id_editor') != receta_id:
        initial_df_raw = get_ingredientes_receta(receta_id)
        
        # Solución al error: Crear una clave de fila única combinando el índice de Pandas y el ID
        initial_df_raw['Row_Key'] = initial_df_raw.index.astype(str) + '_' + initial_df_raw['ID_MP_Actual'].astype(str)
        initial_df_raw = initial_df_raw.set_index('Row_Key')
        
        st.session_state.ingredientes_editor_df = initial_df_raw
        st.session_state.initial_ingredientes_df = initial_df_raw.copy()
        st.session_state.last_receta_id_editor = receta_id
        st.session_state.mp_list_view = pd.DataFrame(
            mp_map.items(), columns=['ID', 'Nombre (Unidad)']
        ).sort_values(by='ID')
        

    ingredientes_df = st.session_state.ingredientes_editor_df.copy()
    initial_df = st.session_state.initial_ingredientes_df.copy()

    if ingredientes_df.empty:
        st.warning(f"La receta '{receta_seleccionada_nombre}' no tiene ingredientes cargados. Use la sección 3 para agregar.")
    
    # Configuración del Data Editor
    column_config = {
        "ID_MP_Actual": st.column_config.NumberColumn("ID MP Actual", disabled=True, help="ID original de la Materia Prima."),
        "Materia_Prima": st.column_config.TextColumn("Materia Prima", disabled=True, width="large"),
        "Cantidad": st.column_config.NumberColumn("Cantidad", format="%.4f", min_value=0.0001, width="small"),
        "Unidad": st.column_config.TextColumn("Unidad", width="small", max_chars=10),
        "ID_MP_Nueva": st.column_config.NumberColumn("ID MP Nueva (Sustituir)", min_value=1, help="Ingrese el ID de la MP por la que desea sustituir. Si es igual al actual, solo se actualizan Cantidad/Unidad."),
        "Quitar": st.column_config.CheckboxColumn("Quitar", default=False, help="Marque para eliminar este ingrediente de la receta.", width="small"),
        # La columna Row_Key (el índice) se oculta automáticamente.
    }
    
    cols_order = ["Materia_Prima", "ID_MP_Actual", "Cantidad", "Unidad", "ID_MP_Nueva", "Quitar"]

    edited_df = st.data_editor(
        ingredientes_df,
        column_config=column_config,
        column_order=cols_order,
        hide_index=True, # Oculta la columna Row_Key
        use_container_width=True,
        key="mp_editor"
    )
    
    # Almacenar el DF editado en Session State para que persista
    st.session_state.ingredientes_editor_df = edited_df.copy()


    # --- SUBMIT DE ACTUALIZACIÓN ---
    st.markdown("---")
    
    if st.button("💾 Guardar Cambios Aplicados (Actualizar/Sustituir/Quitar)", type="primary", use_container_width=True):
        st.subheader("Resultado de la Transacción:")
        
        success_count, errors = apply_updates(receta_id, edited_df, valid_mp_ids, initial_df)
        
        if success_count > 0:
            st.success(f"✅ Transacción Exitosa. {success_count} operaciones de Actualización/Sustitución/Eliminación realizadas.")
            st.balloons()
        
        for error in errors:
            st.error(error)
        
        # Forzar recarga de datos después de la actualización
        st.session_state.ingredientes_editor_df = get_ingredientes_receta(receta_id)
        # Aplicar la clave única de nuevo
        st.session_state.ingredientes_editor_df['Row_Key'] = st.session_state.ingredientes_editor_df.index.astype(str) + '_' + st.session_state.ingredientes_editor_df['ID_MP_Actual'].astype(str)
        st.session_state.ingredientes_editor_df = st.session_state.ingredientes_editor_df.set_index('Row_Key')
        
        st.session_state.initial_ingredientes_df = st.session_state.ingredientes_editor_df.copy()
        st.rerun()


    # --- AGREGAR NUEVA MATERIA PRIMA ---
    st.markdown("---")
    st.subheader("3. ➕ Agregar Nueva Materia Prima")
    
    with st.form("form_agregar_mp"):
        col_id, col_cant, col_unidad = st.columns(3)
        
        mp_id_to_add = col_id.number_input("ID de la Materia Prima a Añadir:", min_value=1, step=1, key="add_mp_id")
        cantidad_add = col_cant.number_input("Cantidad:", min_value=0.0001, value=1.0, step=0.01, format="%.4f", key="add_qty")
        unidad_add = col_unidad.text_input("Unidad (ej: KG, LITROS, CM3):", value="KG", key="add_unit")
        
        submitted_add = st.form_submit_button("Añadir Ingrediente a la Receta", use_container_width=True)

        if submitted_add:
            success, message = add_new_ingredient(receta_id, mp_id_to_add, cantidad_add, unidad_add, valid_mp_ids)
            if success:
                st.success(f"✅ {message}")
                # Forzar recarga de datos
                st.session_state.ingredientes_editor_df = get_ingredientes_receta(receta_id)
                # Aplicar la clave única de nuevo
                st.session_state.ingredientes_editor_df['Row_Key'] = st.session_state.ingredientes_editor_df.index.astype(str) + '_' + st.session_state.ingredientes_editor_df['ID_MP_Actual'].astype(str)
                st.session_state.ingredientes_editor_df = st.session_state.ingredientes_editor_df.set_index('Row_Key')
                
                st.session_state.initial_ingredientes_df = st.session_state.ingredientes_editor_df.copy()
                st.rerun()
            else:
                st.error(f"❌ {message}")

    # --- LISTA DE REFERENCIA DE MP ---
    with st.expander("Tabla de Referencia: IDs de Materias Primas"):
        st.dataframe(
            st.session_state.mp_list_view.rename(columns={'ID': 'ID MP', 'Nombre (Unidad)': 'Nombre'}),
            use_container_width=True,
            hide_index=True
        )


if __name__ == "__main__":
    main()