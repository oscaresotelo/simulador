import streamlit as st
import sqlite3
import pandas as pd

# --- Configuración de la Base de Datos ---
# Asegúrate de que este archivo exista en el mismo directorio
DB_NAME = 'minerva.db'

def get_db_connection():
    """Establece y devuelve la conexión a la base de datos SQLite."""
    conn = sqlite3.connect(DB_NAME)
    # Permite acceder a las columnas por nombre (útil para fetchall)
    conn.row_factory = sqlite3.Row 
    return conn

# --- Funciones de Acceso a Datos (CRUD) ---

def fetch_recetas():
    """Obtiene todas las recetas para el selector."""
    conn = get_db_connection()
    recetas = conn.execute("SELECT id, nombre FROM recetas ORDER BY nombre").fetchall()
    conn.close()
    return recetas

def fetch_materias_primas():
    """Obtiene todas las materias primas para el selector de ingredientes."""
    conn = get_db_connection()
    mp = conn.execute("SELECT id, nombre, unidad FROM materias_primas ORDER BY nombre").fetchall()
    conn.close()
    return mp

def fetch_ingredientes_receta(receta_id):
    """Obtiene los ingredientes actuales de una receta específica."""
    conn = get_db_connection()
    query = """
    SELECT
        T1.id,
        T3.nombre AS ingrediente,
        T1.cantidad,
        T1.unidad,
        T1.materia_prima_id
    FROM receta_ingredientes AS T1
    INNER JOIN materias_primas AS T3 ON T1.materia_prima_id = T3.id
    WHERE T1.receta_id = ?
    """
    ingredientes = conn.execute(query, (receta_id,)).fetchall()
    conn.close()
    return ingredientes

def add_ingrediente(receta_id, materia_prima_id, cantidad, unidad):
    """Agrega un nuevo ingrediente a la receta."""
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO receta_ingredientes (receta_id, materia_prima_id, cantidad, unidad) VALUES (?, ?, ?, ?)",
            (receta_id, materia_prima_id, cantidad, unidad)
        )
        conn.commit()
        st.success("✅ Ingrediente agregado correctamente.")
    except sqlite3.IntegrityError:
        # Nota: La tabla receta_ingredientes no tiene un UNIQUE, pero si existiera
        # una restricción similar se manejaría aquí.
        st.warning("⚠️ Ocurrió un error al agregar el ingrediente. Revisa los datos ingresados.")
    finally:
        conn.close()

def update_ingrediente(receta_detalle_id, cantidad, unidad):
    """Actualiza la cantidad y unidad de un ingrediente existente por su ID de detalle."""
    conn = get_db_connection()
    conn.execute(
        "UPDATE receta_ingredientes SET cantidad = ?, unidad = ? WHERE id = ?",
        (cantidad, unidad, receta_detalle_id)
    )
    conn.commit()
    conn.close()
    st.success("🔄 Ingrediente actualizado correctamente.")

def delete_ingrediente(receta_detalle_id):
    """Elimina un ingrediente de la receta por su ID de detalle."""
    conn = get_db_connection()
    conn.execute("DELETE FROM receta_ingredientes WHERE id = ?", (receta_detalle_id,))
    conn.commit()
    conn.close()
    st.info("❌ Ingrediente eliminado correctamente.")

# --- Interfaz de Streamlit ---

st.set_page_config(page_title="Gestor de Ingredientes de Recetas", layout="wide")
st.title("🧪 Gestión de Ingredientes de Recetas")

# 1. Selector de Receta
recetas = fetch_recetas()
# Creamos un diccionario para mapear el nombre legible de la receta a su ID
receta_dict = {f"{r['nombre']} (ID: {r['id']})": r['id'] for r in recetas}
receta_seleccionada_nombre = st.selectbox(
    "Selecciona la Receta a Editar:",
    options=list(receta_dict.keys())
)

if receta_seleccionada_nombre:
    receta_id = receta_dict[receta_seleccionada_nombre]
    st.subheader(f"Composición actual de: {receta_seleccionada_nombre}")
    
    # Obtener y mostrar ingredientes actuales
    ingredientes_data = fetch_ingredientes_receta(receta_id)
    ingredientes_df = pd.DataFrame(ingredientes_data, columns=['ID Detalle', 'Ingrediente', 'Cantidad', 'Unidad', 'ID Materia Prima'])
    
    if not ingredientes_df.empty:
        # Mostramos la tabla solo con las columnas relevantes para el usuario
        st.dataframe(ingredientes_df[['ID Detalle', 'Ingrediente', 'Cantidad', 'Unidad']], use_container_width=True)
    else:
        st.info("Esta receta no tiene ingredientes. ¡Comienza agregando uno!")

    
    st.markdown("---")
    
    # 2. Tabs para Agregar y Editar/Eliminar
    tab_agregar, tab_editar_eliminar = st.tabs(["➕ Agregar Ingrediente", "✏️ Editar / Eliminar"])
    
    # --- Tab AGREGAR INGREDIENTE ---
    with tab_agregar:
        st.markdown("### Agrega un Nuevo Ingrediente")
        
        mp_options = fetch_materias_primas()
        mp_dict = {f"{mp['nombre']} ({mp['unidad']})": mp['id'] for mp in mp_options}
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            materia_prima_nombre = st.selectbox(
                "Materia Prima:",
                options=list(mp_dict.keys()),
                key="add_mp_select"
            )
        
        if materia_prima_nombre:
            materia_prima_id = mp_dict[materia_prima_nombre]
            # Extraer la unidad sugerida del nombre de la MP
            sugerencia_unidad = materia_prima_nombre.split('(')[-1].replace(')', '').strip()
            
            with col2:
                cantidad = st.number_input(
                    "Cantidad Requerida:", 
                    min_value=0.001, 
                    value=1.0, 
                    step=0.1, 
                    format="%.3f",
                    key="add_cantidad"
                )
            
            with col3:
                unidad = st.text_input(
                    "Unidad (ej: kg, L, un):", 
                    value=sugerencia_unidad, 
                    key="add_unidad"
                )
            
            if st.button("Guardar Nuevo Ingrediente", key="btn_add"):
                add_ingrediente(receta_id, materia_prima_id, cantidad, unidad)
                st.rerun() # Refrescar para ver el cambio

    # --- Tab EDITAR / ELIMINAR ---
    with tab_editar_eliminar:
        st.markdown("### Edita o Elimina un Ingrediente Existente")

        if ingredientes_data:
            # Creamos un diccionario para el selector de ingredientes por ID de Detalle
            detalle_dict = {
                f"ID {ing['id']} - {ing['ingrediente']} ({ing['cantidad']} {ing['unidad']})": ing['id']
                for ing in ingredientes_data
            }
            
            detalle_seleccionado_nombre = st.selectbox(
                "Selecciona el Ingrediente a Modificar:",
                options=list(detalle_dict.keys()),
                key="edit_detalle_select"
            )
            
            if detalle_seleccionado_nombre:
                detalle_id = detalle_dict[detalle_seleccionado_nombre]
                # Buscar los datos originales del detalle seleccionado
                datos_originales = next((ing for ing in ingredientes_data if ing['id'] == detalle_id), None)

                if datos_originales:
                    st.markdown("#### Modificar Cantidad y Unidad")
                    col_e1, col_e2, col_e3 = st.columns(3)
                    
                    with col_e1:
                        st.text_input(
                            "Ingrediente:", 
                            value=datos_originales['ingrediente'], 
                            disabled=True
                        )
                    
                    with col_e2:
                        nueva_cantidad = st.number_input(
                            "Nueva Cantidad:",
                            min_value=0.001,
                            value=datos_originales['cantidad'],
                            step=0.1,
                            format="%.3f",
                            key="edit_cantidad"
                        )
                    
                    with col_e3:
                        nueva_unidad = st.text_input(
                            "Nueva Unidad:",
                            value=datos_originales['unidad'],
                            key="edit_unidad"
                        )

                    # Botones de Acción
                    col_btn1, col_btn2 = st.columns([1, 1])
                    
                    with col_btn1:
                        if st.button("Actualizar Ingrediente", key="btn_update", use_container_width=True, type="primary"):
                            update_ingrediente(detalle_id, nueva_cantidad, nueva_unidad)
                            st.rerun()
                            
                    with col_btn2:
                        # Corregido: 'danger' no es un tipo válido. Usamos 'primary' para destacarlo.
                        if st.button("Eliminar Ingrediente", key="btn_delete", use_container_width=True, type="primary"): 
                            delete_ingrediente(detalle_id)
                            st.rerun()
        else:
            st.info("No hay ingredientes para editar o eliminar. Agrega uno primero en la pestaña 'Agregar Ingrediente'.")