import streamlit as st
import sqlite3
import pandas as pd

DB_PATH = 'minerva.db'

st.set_page_config(layout="wide")
st.title("Gestión de Clientes")

# --- Funciones Auxiliares (Integradas en la lógica principal para evitar 'def') ---

# Asegurarse de que la tabla clientes exista
with sqlite3.connect(DB_PATH) as conn:
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL,
            contacto TEXT
        )
    """)
    conn.commit()

# --- Ver Clientes Existentes ---
st.header("Clientes Registrados")
with sqlite3.connect(DB_PATH) as conn:
    df_clientes = pd.read_sql_query("SELECT id, nombre, contacto FROM clientes", conn)
if not df_clientes.empty:
    st.dataframe(df_clientes, use_container_width=True)
else:
    st.info("No hay clientes registrados aún.")

# --- Agregar Nuevo Cliente ---
st.header("Agregar Nuevo Cliente")
with st.form("add_client_form", clear_on_submit=True):
    new_nombre = st.text_input("Nombre del Cliente", key="new_nombre")
    new_contacto = st.text_input("Contacto (email, teléfono, etc.)", key="new_contacto")
    submitted_add = st.form_submit_button("Agregar Cliente")

    if submitted_add:
        if new_nombre:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO clientes (nombre, contacto) VALUES (?, ?)",
                                   (new_nombre, new_contacto))
                    conn.commit()
                st.success(f"Cliente '{new_nombre}' agregado exitosamente.")
                st.rerun()
            except sqlite3.Error as e:
                st.error(f"Error al agregar cliente: {e}")
        else:
            st.warning("El nombre del cliente no puede estar vacío.")

# --- Actualizar Cliente Existente ---
st.header("Actualizar Cliente Existente")

with sqlite3.connect(DB_PATH) as conn:
    clientes_data = pd.read_sql_query("SELECT id, nombre FROM clientes", conn)

if not clientes_data.empty:
    clientes_dict = {row['nombre']: row['id'] for index, row in clientes_data.iterrows()}
    clientes_nombres = list(clientes_dict.keys())

    with st.form("update_client_form"):
        selected_nombre = st.selectbox("Seleccione un cliente para actualizar", clientes_nombres, key="update_select")
        
        # Obtener datos del cliente seleccionado para pre-rellenar
        selected_client_id = None
        if selected_nombre:
            selected_client_id = clientes_dict[selected_nombre]
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT nombre, contacto FROM clientes WHERE id = ?", (selected_client_id,))
                client_to_update = cursor.fetchone()
            
            if client_to_update:
                current_nombre = client_to_update[0]
                current_contacto = client_to_update[1]
            else:
                current_nombre = ""
                current_contacto = ""
        else:
            current_nombre = ""
            current_contacto = ""

        updated_nombre = st.text_input("Nuevo Nombre del Cliente", value=current_nombre, key="updated_nombre")
        updated_contacto = st.text_input("Nuevo Contacto", value=current_contacto, key="updated_contacto")
        submitted_update = st.form_submit_button("Actualizar Cliente")

        if submitted_update:
            if selected_client_id and updated_nombre:
                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE clientes SET nombre = ?, contacto = ? WHERE id = ?",
                                       (updated_nombre, updated_contacto, selected_client_id))
                        conn.commit()
                    st.success(f"Cliente '{current_nombre}' actualizado a '{updated_nombre}' exitosamente.")
                    st.rerun()
                except sqlite3.Error as e:
                    st.error(f"Error al actualizar cliente: {e}")
            elif not updated_nombre:
                st.warning("El nombre del cliente no puede estar vacío.")
            else:
                st.warning("Seleccione un cliente para actualizar.")
else:
    st.info("No hay clientes para actualizar.")

# --- Eliminar Cliente ---
st.header("Eliminar Cliente")

with sqlite3.connect(DB_PATH) as conn:
    clientes_data_delete = pd.read_sql_query("SELECT id, nombre FROM clientes", conn)

if not clientes_data_delete.empty:
    clientes_dict_delete = {row['nombre']: row['id'] for index, row in clientes_data_delete.iterrows()}
    clientes_nombres_delete = list(clientes_dict_delete.keys())

    with st.form("delete_client_form"):
        selected_nombre_delete = st.selectbox("Seleccione un cliente para eliminar", clientes_nombres_delete, key="delete_select")
        
        submitted_delete = st.form_submit_button("Eliminar Cliente")

        if submitted_delete:
            if selected_nombre_delete:
                selected_client_id_delete = clientes_dict_delete[selected_nombre_delete]
                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        cursor = conn.cursor()
                        # Verificar si el cliente tiene recetas asociadas
                        cursor.execute("SELECT COUNT(*) FROM recetas WHERE cliente_id = ?", (selected_client_id_delete,))
                        num_recetas = cursor.fetchone()[0]

                        if num_recetas > 0:
                            st.warning(f"No se puede eliminar el cliente '{selected_nombre_delete}' porque tiene {num_recetas} receta(s) asociada(s).")
                        else:
                            cursor.execute("DELETE FROM clientes WHERE id = ?", (selected_client_id_delete,))
                            conn.commit()
                            st.success(f"Cliente '{selected_nombre_delete}' eliminado exitosamente.")
                            st.rerun()
                except sqlite3.Error as e:
                    st.error(f"Error al eliminar cliente: {e}")
            else:
                st.warning("Seleccione un cliente para eliminar.")
else:
    st.info("No hay clientes para eliminar.")