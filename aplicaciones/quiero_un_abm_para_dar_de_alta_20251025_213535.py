import streamlit as st
import sqlite3
import pandas as pd

DB_PATH = 'minerva.db'

st.title("Gestión de Recetas")

# --- Dar de Alta Nueva Receta ---
st.header("Dar de Alta Nueva Receta")

# Obtener clientes para el selectbox
try:
    with sqlite3.connect(DB_PATH) as conn:
        clientes_df = pd.read_sql_query("SELECT id, nombre FROM clientes", conn)
    clientes_dict = {row['nombre']: row['id'] for index, row in clientes_df.iterrows()}
    clientes_nombres = list(clientes_dict.keys())
except Exception as e:
    st.error(f"Error al cargar clientes: {e}")
    clientes_df = pd.DataFrame(columns=['id', 'nombre'])
    clientes_dict = {}
    clientes_nombres = []

with st.form("form_alta_receta"):
    nombre_receta = st.text_input("Nombre de la Receta", key="nombre_receta_input")
    
    if clientes_nombres:
        cliente_seleccionado_nombre = st.selectbox("Cliente Asociado", clientes_nombres, key="cliente_receta_selectbox")
        cliente_id_seleccionado = clientes_dict.get(cliente_seleccionado_nombre)
    else:
        st.warning("No hay clientes registrados. Por favor, agregue clientes primero.")
        cliente_id_seleccionado = None
    
    uso_receta = st.text_input("Uso (e.g., Shampoo, Acondicionador)", key="uso_receta_input")
    linea_receta = st.text_input("Línea de Producto (e.g., Capilar, Corporal)", key="linea_receta_input")
    
    submit_button = st.form_submit_button("Guardar Receta")

    if submit_button:
        if not nombre_receta:
            st.error("El nombre de la receta no puede estar vacío.")
        elif cliente_id_seleccionado is None:
            st.error("Debe seleccionar un cliente para la receta.")
        else:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO recetas (nombre, cliente_id, uso, linea) VALUES (?, ?, ?, ?)",
                        (nombre_receta, cliente_id_seleccionado, uso_receta, linea_receta)
                    )
                    conn.commit()
                st.success(f"Receta '{nombre_receta}' agregada exitosamente.")
                # Clear form fields after submission
                st.session_state["nombre_receta_input"] = ""
                st.session_state["uso_receta_input"] = ""
                st.session_state["linea_receta_input"] = ""
                # Force rerun to update the displayed recipes
                st.rerun()
            except sqlite3.Error as e:
                st.error(f"Error al guardar la receta: {e}")

# --- Mostrar Recetas Existentes ---
st.header("Recetas Existentes")

try:
    with sqlite3.connect(DB_PATH) as conn:
        recetas_df = pd.read_sql_query(
            """
            SELECT
                r.id,
                r.nombre AS Receta,
                c.nombre AS Cliente,
                r.uso AS Uso,
                r.linea AS Linea
            FROM recetas r
            JOIN clientes c ON r.cliente_id = c.id
            ORDER BY r.nombre
            """,
            conn
        )
    if not recetas_df.empty:
        st.dataframe(recetas_df, use_container_width=True)
    else:
        st.info("No hay recetas registradas aún.")
except Exception as e:
    st.error(f"Error al cargar las recetas existentes: {e}")