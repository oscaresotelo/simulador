import streamlit as st
import google.generativeai as genai
import sqlite3
import pandas as pd
import traceback
import re
# ... (asegúrate de tener todos los imports necesarios al inicio de ai.py)

DB_PATH = "minerva.db" 

# --- FUNCIÓN 1: OBTENER ESQUEMA COMPLETO ---
@st.cache_resource
def get_db_schema():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Consulta para obtener todos los comandos CREATE TABLE
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
        
        schema_rows = cursor.fetchall()
        schema = "\n".join([row[0] for row in schema_rows if row[0] is not None])
        return schema
    except Exception as e:
        st.error(f"Error al obtener el esquema de la base de datos: {e}")
        return ""
    finally:
        if conn:
            conn.close()

# --- FUNCIÓN 2: LIMPIAR Y EXTRAER CÓDIGO ---
def clean_generated_code(response):
    # Extrae el bloque de código Python encerrado en ```python ... ```
    match = re.search(r"```python\n(.*)```", response, re.DOTALL)
    if match:
        return match.group(1).strip()
    return response.strip()

# --- FUNCIÓN 3: GENERAR CÓDIGO CON AI ---
# (Se asume la configuración inicial de genai.configure)
@st.cache_data(show_spinner="Generando código con AI...")
def generar_codigo_con_ai(prompt_usuario, db_schema):
    # Este prompt guía a la IA para generar el código correcto
    system_prompt = f"""
    Eres un asistente de programación experto en Python y SQL. Tu tarea es generar código Python completo y funcional, usando 'sqlite3' y 'pandas', para interactuar con la base de datos '{DB_PATH}'.
    
    El código debe usar la estructura de la base de datos proporcionada a continuación. Debes enfocarte en responder a la solicitud del usuario.
    
    REGLAS ESTRICTAS DE SALIDA:
    1.  El código generado debe estar *exclusivamente* en un bloque markdown de Python: \n```python\n...código aquí...\n```
    2.  Siempre usa el patrón: `conn = sqlite3.connect(DB_PATH)`, `df = pd.read_sql(sql_query, conn)`, `conn.close()`.
    3.  Muestra los resultados con `st.dataframe(df)` o `st.altair_chart(alt.Chart(...))` si se requiere un gráfico.
    
    ESQUEMA DE LA BASE DE DATOS MINERVA:
    {db_schema}
    """
    
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=system_prompt,
        config={"temperature": 0.1}
    )
    
    response = model.generate_content(f"SOLICITUD DEL USUARIO: {prompt_usuario}")
    return response.text

# --- 4. LÓGICA PRINCIPAL DE STREAMLIT ---

# Obtener el esquema de la base de datos
db_schema = get_db_schema()

# Formulario para la solicitud
with st.form("ai_copilot_form"):
    user_prompt = st.text_area("✍️ Describe el código o la consulta que necesitas:", 
                               height=150, 
                               key="user_prompt_input",
                               value="Muestrame el nombre y sueldo base de todos los empleados ordenados por sueldo de forma descendente.")

    submit_button = st.form_submit_button("🚀 Generar y Ejecutar Solución")

# Lógica de procesamiento al presionar el botón
if submit_button and user_prompt:
    st.info("🤖 AI Copilot trabajando...")
    
    try:
        # Generar el código y limpiarlo
        ai_response = generar_codigo_con_ai(user_prompt, db_schema)
        generated_code = clean_generated_code(ai_response)

        st.subheader("📊 Resultado de la Ejecución:") 

        # Preparar el entorno de ejecución
        local_vars = {
            "st": st,
            "pd": pd,
            "sqlite3": sqlite3,
            "DB_PATH": DB_PATH,
            "alt": None, # Se asigna si se importa correctamente
        }
        
        try:
            # Asegurar importación de Altair para gráficos (si el import inicial falló)
            import altair as alt
            local_vars["alt"] = alt
        except ImportError:
            pass 

        try:
            # Ejecución del código generado
            exec(generated_code, globals(), local_vars)
            
        except Exception as e:
            st.error(f"❌ Error durante la ejecución del código generado: {e}")
            st.code(traceback.format_exc(), language="text")

        st.success("✅ Solución ejecutada correctamente.")
        
        # Mostrar el código para revisión
        with st.expander("Ver Código Generado", expanded=False):
            st.code(generated_code, language="python")

    except Exception as e:
        st.error(f"❌ Error al comunicarse con AI: {e}")
        st.code(traceback.format_exc(), language="text")