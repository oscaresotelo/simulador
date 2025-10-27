import streamlit as st
import sqlite3
import pandas as pd

DB_PATH = 'minerva.db'

st.set_page_config(layout="wide")
st.title("Gestión de Recetas")

# --- Sección de Dar de Alta Nueva Receta ---
st.header("➕ Dar de Alta Nueva Receta")

# Cargar clientes para el selector
clientes_df = pd.DataFrame()
try:
    with sqlite3.connect(DB_PATH) as conn:
        clientes_df = pd.read_sql_query("SELECT id, nombre FROM clientes", conn)
except sqlite3.Error as e:
    st.error(f"Error al cargar clientes: {e}")

cliente_nombres_add = clientes_df['nombre'].tolist() if not clientes_df.empty else ["No hay clientes registrados"]

with st.form("add_receta_form", clear_on_submit=True):
    nombre_nueva = st.text_input("Nombre de la Receta", key="add_nombre_receta")
    selected_cliente_nombre_add = st.selectbox("Cliente Asociado", cliente_nombres_add, key="add_cliente_select")
    uso_nueva = st.text_input("Uso (ej. Shampoo, Acondicionador)", key="add_uso")
    linea_nueva = st.text_input("Línea de Producto (ej. Reparación, Hidratación)", key="add_linea")

    submitted_add = st.form_submit_button("Guardar Receta")

    if submitted_add:
        cliente_id_nueva = None
        if selected_cliente_nombre_add != "No hay clientes registrados":
            cliente_id_nueva = clientes_df[clientes_df['nombre'] == selected_cliente_nombre_add]['id'].iloc[0]

        if nombre_nueva and cliente_id_nueva is not None:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO recetas (nombre, cliente_id, uso, linea) VALUES (?, ?, ?, ?)",
                        (nombre_nueva, cliente_id_nueva, uso_nueva, linea_nueva)
                    )
                    conn.commit()
                st.success(f"Receta '{nombre_nueva}' agregada exitosamente.")
                st.experimental_rerun() # Recargar para actualizar la lista de recetas
            except sqlite3.IntegrityError:
                st.error("Error: Ya existe una receta con este nombre o el cliente no es válido.")
            except sqlite3.Error as e:
                st.error(f"Error al guardar la receta: {e}")
        else:
            st.warning("Por favor, ingresa el nombre de la receta y selecciona un cliente válido.")

# --- Sección de Recetas Existentes ---
st.header("📝 Recetas Existentes")

recetas_df = pd.DataFrame()
try:
    with sqlite3.connect(DB_PATH) as conn:
        recetas_df = pd.read_sql_query("""
            SELECT 
                r.id, 
                r.nombre, 
                c.nombre AS cliente_nombre, 
                r.uso, 
                r.linea 
            FROM recetas r
            LEFT JOIN clientes c ON r.cliente_id = c.id
            ORDER BY r.nombre
        """, conn)
except sqlite3.Error as e:
    st.error(f"Error al cargar recetas: {e}")

if not recetas_df.empty:
    st.dataframe(recetas_df.set_index('id'))
else:
    st.info("No hay recetas registradas todavía.")


# --- Sección de Modificar Receta ---
st.header("✏️ Modificar Receta")

receta_to_modify_id = None
receta_nombres_for_select_mod = ["Seleccionar Receta"] + recetas_df['nombre'].tolist()
selected_receta_name_mod = st.selectbox(
    "Selecciona la receta a modificar",
    receta_nombres_for_select_mod,
    key="mod_receta_select"
)

selected_receta_data = None
if selected_receta_name_mod != "Seleccionar Receta" and not recetas_df.empty:
    selected_receta_data = recetas_df[recetas_df['nombre'] == selected_receta_name_mod].iloc[0]
    receta_to_modify_id = selected_receta_data['id']

if selected_receta_data is not None:
    with st.form("update_receta_form"):
        st.write(f"Modificando Receta ID: {selected_receta_data['id']}")
        
        update_nombre = st.text_input("Nombre de la Receta", value=selected_receta_data['nombre'], key="mod_nombre")
        
        cliente_nombres_mod = clientes_df['nombre'].tolist() if not clientes_df.empty else ["No hay clientes registrados"]
        current_client_name_mod = selected_receta_data['cliente_nombre'] if 'cliente_nombre' in selected_receta_data else cliente_nombres_mod[0]
        
        initial_client_index_mod = cliente_nombres_mod.index(current_client_name_mod) if current_client_name_mod in cliente_nombres_mod else 0
        selected_cliente_nombre_mod = st.selectbox("Cliente Asociado", cliente_nombres_mod, index=initial_client_index_mod, key="mod_cliente_select")
        
        cliente_id_mod = None
        if selected_cliente_nombre_mod != "No hay clientes registrados":
            cliente_id_mod = clientes_df[clientes_df['nombre'] == selected_cliente_nombre_mod]['id'].iloc[0]

        update_uso = st.text_input("Uso", value=selected_receta_data['uso'], key="mod_uso")
        update_linea = st.text_input("Línea", value=selected_receta_data['linea'], key="mod_linea")

        submitted_update = st.form_submit_button("Actualizar Receta")

        if submitted_update:
            if update_nombre and cliente_id_mod is not None:
                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE recetas SET nombre=?, cliente_id=?, uso=?, linea=? WHERE id=?",
                            (update_nombre, cliente_id_mod, update_uso, update_linea, receta_to_modify_id)
                        )
                        conn.commit()
                    st.success(f"Receta '{update_nombre}' (ID: {receta_to_modify_id}) actualizada exitosamente.")
                    st.experimental_rerun() 
                except sqlite3.Error as e:
                    st.error(f"Error al actualizar la receta: {e}")
            else:
                st.warning("Por favor, ingresa el nombre de la receta y selecciona un cliente válido para actualizar.")

# --- Sección de Eliminar Receta ---
st.header("🗑️ Eliminar Receta")

receta_to_delete_id = None
receta_nombres_for_delete = ["Seleccionar Receta"] + recetas_df['nombre'].tolist()
selected_receta_name_del = st.selectbox(
    "Selecciona la receta a eliminar",
    receta_nombres_for_delete,
    key="del_receta_select"
)

if selected_receta_name_del != "Seleccionar Receta" and not recetas_df.empty:
    receta_to_delete_id = recetas_df[recetas_df['nombre'] == selected_receta_name_del]['id'].iloc[0]
    st.warning(f"Estás a punto de eliminar la receta: **{selected_receta_name_del}** (ID: {receta_to_delete_id}). Esta acción es irreversible.")
    
    if st.button("Confirmar Eliminación", key="confirm_delete_receta"):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                # Verificar si existen ingredientes asociados a esta receta
                cursor.execute("SELECT COUNT(*) FROM receta_ingredientes WHERE receta_id = ?", (receta_to_delete_id,))
                if cursor.fetchone()[0] > 0:
                    st.error("No se puede eliminar esta receta porque tiene ingredientes asociados. Elimina los ingredientes de la receta primero.")
                else:
                    cursor.execute("DELETE FROM recetas WHERE id=?", (receta_to_delete_id,))
                    conn.commit()
                    st.success(f"Receta '{selected_receta_name_del}' eliminada exitosamente.")
                    st.experimental_rerun()
        except sqlite3.Error as e:
            st.error(f"Error al eliminar la receta: {e}")