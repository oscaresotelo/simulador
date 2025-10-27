import streamlit as st
import google.generativeai as genai
import sqlite3
import pandas as pd
import traceback
import os
import io
import re

# Intenta importar altair para gráficos
try:
    import altair as alt
except ImportError:
    alt = None

# --- 1. CONFIGURACIÓN ---
API_KEY = "AIzaSyABFNeXQMQNy-MFlPf9818zmFn5wnuFZHc"  # Clave fija
DB_PATH = "minerva.db"  # Base de datos SQLite
SAVE_DIR = "aplicaciones" # Directorio donde se guardará el código

# Configuración de Gemini
try:
    genai.configure(api_key=API_KEY)
except Exception as e:
    st.error(f"Error al configurar la API: {e}")
    st.stop()

# Verificación del archivo de base de datos
if not os.path.exists(DB_PATH):
    st.error(f"❌ No se encontró el archivo '{DB_PATH}'. Colócalo en el mismo directorio.")
    st.stop()

# Asegura que la carpeta de aplicaciones exista
os.makedirs(SAVE_DIR, exist_ok=True)


st.set_page_config(layout="wide")
st.title("💡 Copiloto de Soluciones MINERVA")

# --- 2. FUNCIÓN DEL ESQUEMA REAL ---

@st.cache_resource
def get_db_schema():
    """Esquema real de minerva.db"""
    schema = """
-- Esquema real basado en minerva.db (Fabricación Capilar)
-- Úsalo ESTRICTAMENTE para evitar errores de columna.

CREATE TABLE clientes (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    contacto TEXT
);

CREATE TABLE recetas (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    cliente_id INTEGER,
    uso TEXT,
    linea TEXT,
    FOREIGN KEY(cliente_id) REFERENCES clientes(id)
);

CREATE TABLE materias_primas (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    unidad TEXT NOT NULL,
    UNIQUE(nombre, unidad)
);

CREATE TABLE receta_ingredientes (
    id INTEGER PRIMARY KEY,
    receta_id INTEGER NOT NULL,
    materia_prima_id INTEGER NOT NULL,
    cantidad REAL NOT NULL,
    unidad TEXT NOT NULL,
    FOREIGN KEY(receta_id) REFERENCES recetas(id),
    FOREIGN KEY(materia_prima_id) REFERENCES materias_primas(id)
);

CREATE TABLE stock_materias_primas (
    materia_prima_id INTEGER PRIMARY KEY,
    cantidad REAL NOT NULL,
    FOREIGN KEY(materia_prima_id) REFERENCES materias_primas(id)
);

CREATE TABLE movimientos_materia_prima (
    id INTEGER PRIMARY KEY,
    materia_prima_id INTEGER,
    fecha TEXT,
    cantidad REAL,
    tipo_movimiento TEXT,
    destino TEXT,
    costo_flete REAL,
    otros_costos REAL,
    lote TEXT,
    FOREIGN KEY(materia_prima_id) REFERENCES materias_primas(id)
);
"""
    return schema


# --- 3. PROMPT DEL AGENTE GEMINI (MÁXIMA PUREZA DE CÓDIGO) ---

SYSTEM_PROMPT_DB_AGENT = f"""
### 🤖 Agente SQL & Streamlit EXPERTO — Producción de Código Puro
    
**Rol:** Eres un programador senior experto en Streamlit, SQLite y Pandas, especializado en Fabricación Capilar. Tu misión es generar un **BLOQUE ÚNICO DE CÓDIGO PYTHON AUTOSUFICIENTE** que funcione correctamente y **NUNCA DEBE CAUSAR SyntaxError o NameError**.

**REGLA DE ORO DE SALIDA (Obligatoria para evitar SyntaxError):**
1.  **SOLO CÓDIGO PYTHON, SIN EXPLICACIONES.**
2.  **CERO INTRODUCCIONES/EXPLICACIONES.** El código debe empezar con la primera línea de código necesaria.

**Restricciones de Código IMPERATIVAS (Para evitar errores de ejecución y advertencias de Streamlit):**
* **PROHIBICIÓN ABSOLUTA DE FUNCIONES EXTERNAS:** NO DEFINAS NI LLAMES a funciones (`def`) como `run_query` o `connect_db`. Toda la lógica debe ser secuencial y directa.
* **MANEJO DE FORMULARIOS:** Si el código usa `st.form`, **OBLIGATORIAMENTE** debe incluir `st.form_submit_button("Enviar/Guardar")` dentro del bloque `with st.form(...)`.
* **CONEXIÓN DIRECTA Y ÚNICA:** Usa `with sqlite3.connect(DB_PATH) as conn:`.
* **VERIFICACIÓN DE ESQUEMA:** Utiliza **SOLO** las columnas declaradas en el esquema.
* **DISPLAY:** Usa `st.dataframe(df)` o `st.altair_chart(...)`.

**Esquema de la Base de Datos (ÚSALO ESTRICTAMENTE):**
{get_db_schema()}
"""

# --- 4. PERSISTENCIA DEL CÓDIGO GENERADO ---

# Inicializa el estado
if "response_code" not in st.session_state:
    st.session_state.response_code = ""

# --- 5. INTERFAZ PRINCIPAL ---

user_prompt = st.text_input(
    "💬 Ingresa tu consulta o pedido (ej. 'Listar las recetas con sus ingredientes', 'Mostrar el stock de materias primas').",
    key="user_input"
)

col1, col2 = st.columns([1, 4])
with col1:
    execute_button = st.button("Generar Solucion", type="primary")

# --- 6. FUNCIONALIDAD DE GUARDADO Y EJECUCIÓN DE CÓDIGOS GUARDADOS ---

def save_code_to_file(code_content, file_name_base):
    """Guarda el código generado en un archivo .py con el nombre proporcionado."""
    
    # Limpia el nombre del archivo de caracteres inválidos
    safe_name = re.sub(r'[^\w\-]', '', file_name_base.replace(' ', '_')).lower()
    
    if not safe_name:
        safe_name = "codigo_generado"
    
    file_path = os.path.join(SAVE_DIR, f"{safe_name}.py")
    
    # Manejar si el archivo ya existe (añade contador)
    counter = 1
    while os.path.exists(file_path):
        file_path = os.path.join(SAVE_DIR, f"{safe_name}_{counter}.py")
        counter += 1
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code_content)
        st.success(f"💾 Código guardado con éxito en: **{file_path}**")
        return True
    except Exception as e:
        st.error(f"❌ Error al guardar el archivo: {e}")
        return False

def get_saved_files():
    """Retorna una lista de archivos .py guardados, ordenados por fecha."""
    try:
        files = [f for f in os.listdir(SAVE_DIR) if f.endswith(".py")]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(SAVE_DIR, x)), reverse=True)
        return files
    except Exception:
        return []


st.sidebar.markdown("---")
st.sidebar.markdown("### Acciones Rápidas")

# Lógica del formulario de guardado
if st.session_state.response_code:
    
    with st.sidebar.form("form_guardar_codigo"):
        # Usar el prompt como nombre sugerido
        default_name = st.session_state.user_input.replace(' ', '_')[:25] if 'user_input' in st.session_state and st.session_state.user_input else "reporte_personalizado"
        
        file_name_input = st.text_input(
            "Nombre del archivo (sin .py):",
            value=default_name,
            key="file_name_key"
        )
        
        save_button = st.form_submit_button("💾 Guardar Código", type="primary")

        if save_button:
            if save_code_to_file(st.session_state.response_code, file_name_input):
                # No modificamos st.session_state.response_code aquí. st.rerun() ya actualiza la lista.
                st.rerun()

else:
    st.sidebar.info("Genera solucion primero para activar el formulario de Guardar.")


if st.sidebar.button("🧹 Limpiar solucion "):
    st.session_state.response_code = ""
    st.rerun() 

# --- Códigos Guardados ---

st.sidebar.markdown("---")
st.sidebar.markdown("### 📄 Aplicaciones Guardadas")

saved_files = get_saved_files()

if saved_files:
    # Mostrar cada archivo como un botón ejecutable
    for file_name in saved_files:
        # Usamos un botón para ejecutar al hacer click
        if st.sidebar.button(file_name, key=f"run_{file_name}"):
            file_path = os.path.join(SAVE_DIR, file_name)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    code_content = f.read()
                
                # Cargar el código en la sesión para que se ejecute en la Sección 7
                st.session_state.response_code = code_content
                
                # ¡CORRECCIÓN APLICADA AQUÍ!
                # Quitamos la línea st.session_state.user_input = "" para evitar el error.
                
                st.rerun()
                
            except Exception as e:
                # Mantener el error en la barra lateral para no interrumpir la aplicación principal
                st.sidebar.error(f"Error al cargar/ejecutar el archivo: {e}") 
                st.session_state.response_code = ""
else:
    st.sidebar.info("No hay apps guardados en la carpeta 'aplicaciones/'.")


# --- 7. MOSTRAR Y EJECUTAR CÓDIGO ACTUAL (Solo resultado) ---
if st.session_state.response_code and not execute_button:
    # Ocultar código, solo mostrar el resultado
    with st.expander("📊 Resultado del Análisis Activo", expanded=True):
        local_vars = {
            "st": st,
            "pd": pd,
            "sqlite3": sqlite3,
            "DB_PATH": DB_PATH,
            "alt": alt,
        }
        try:
            exec(st.session_state.response_code, globals(), local_vars)
        except Exception as e:
            st.error(f"❌ Error al re-ejecutar el código: {e}") 
            st.code(traceback.format_exc(), language="text") 

# --- 8. GENERAR NUEVO CÓDIGO CON GEMINI ---

if execute_button:
    if not user_prompt:
        st.warning("Por favor, escribe una petición antes de ejecutar.")
        st.stop()

    st.info("💡 Generando solucion espere...")

    try:
        # Inicialización del modelo con el prompt mejorado
        chat_session = genai.GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=SYSTEM_PROMPT_DB_AGENT
        )

        response = chat_session.generate_content(user_prompt)
        generated_code = response.text.strip()

        # Limpieza final del bloque Markdown (doble seguridad)
        if generated_code.startswith("```python"):
            generated_code = generated_code.replace("```python", "").strip()
        if generated_code.endswith("```"):
            generated_code = generated_code.rstrip("```").strip()

        # Guarda el código en la sesión para persistencia
        st.session_state.response_code = generated_code

        st.subheader("📊 Resultado de la Ejecución:") # Título del resultado

        local_vars = {
            "st": st,
            "pd": pd,
            "sqlite3": sqlite3,
            "DB_PATH": DB_PATH,
            "alt": alt,
        }

        try:
            # Ejecución del código generado
            exec(generated_code, globals(), local_vars)
        except Exception as e:
            st.error(f"❌ Error durante la ejecución: {e}")
            st.code(traceback.format_exc(), language="text")

        st.success("✅ Solucion ejectuda correctamente.")
        
        # Opcional: Mostrar el código en un expander para debugging si es necesario
        with st.expander("Ver Código Generado (Debugging)", expanded=False):
            st.code(generated_code, language="python")
        
        # Forzar el rerun para actualizar la interfaz (mostrar el formulario de guardado, etc.)
        st.rerun()

    except Exception as e:
        st.error(f"❌ Error al comunicarse con ai: {e}")
        st.code(traceback.format_exc(), language="text")


# --- 9. SIDEBAR INFORMATIVA ---
st.sidebar.markdown("---")
st.sidebar.markdown("### Información")
#st.sidebar.markdown(f"**Archivo de DB:** `{DB_PATH}`")
st.sidebar.markdown(f"La carpeta de guardado es: **`{SAVE_DIR}`**")
#st.sidebar.markdown("El Agente experto evita errores de sintaxis y manejo de formularios.")