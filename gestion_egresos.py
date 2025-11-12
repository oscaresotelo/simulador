import streamlit as st
import sqlite3
import pandas as pd
from datetime import date

# =================================================================================================
# CONFIG
# =================================================================================================
DB_PATH = "minerva.db"

# =================================================================================================
# UTILIDADES DB (Tomadas de sus otros archivos)
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

# =================================================================================================
# LÓGICA CORE DE GASTOS
# =================================================================================================

def crear_tabla_gastos():
    """
    Crea la tabla 'gastos' si no existe. 
    Asegura la estructura confirmada por el usuario.
    """
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS "gastos" (
                "id" INTEGER PRIMARY KEY AUTOINCREMENT,
                "fecha_factura" TEXT NOT NULL,         
                "fecha_pago" TEXT,                    
                "beneficiario_nombre" TEXT NOT NULL,  
                "categoria_id" INTEGER NOT NULL,      
                "numero_comprobante" TEXT UNIQUE,     
                "importe_total" REAL NOT NULL,        
                "moneda" TEXT DEFAULT 'ARS',
                "observaciones" TEXT,
                FOREIGN KEY("categoria_id") REFERENCES "categorias_imputacion"("id")
            );
        """)
        conn.commit()
    except sqlite3.Error as e:
        st.error(f"Error al crear la tabla de gastos: {e}")
    finally:
        conn.close()

def get_categorias_egreso():
    """Obtiene solo las categorías de tipo EGRESO para la clasificación de gastos."""
    return fetch_df("SELECT id, nombre FROM categorias_imputacion WHERE tipo = 'EGRESO' ORDER BY nombre")

def registrar_gasto(fecha_factura, fecha_pago, beneficiario, categoria_id, comprobante, importe, obs):
    """Inserta un nuevo registro de gasto en la base de datos."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO gastos (
                fecha_factura, fecha_pago, beneficiario_nombre, categoria_id, numero_comprobante, importe_total, observaciones
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fecha_factura, fecha_pago, beneficiario, categoria_id, comprobante, importe, obs))
        conn.commit()
        return True
    except sqlite3.IntegrityError as ie:
        # El error de integridad más común aquí es por número_comprobante UNIQUE
        if 'UNIQUE constraint failed: gastos.numero_comprobante' in str(ie):
             st.error("Error de Registro: El **número de comprobante** ya existe. Verifique el número de factura para evitar duplicados.")
        else:
             st.error(f"Error de Integridad: {ie}")
        return False
    except sqlite3.Error as e:
        st.error(f"Error al registrar el gasto: {e}")
        return False
    finally:
        conn.close()

def get_gastos_recientes():
    """Obtiene los 10 gastos más recientes, mostrando la categoría."""
    query = """
        SELECT 
            g.id, g.fecha_factura, g.fecha_pago, g.beneficiario_nombre, c.nombre AS categoria, 
            g.numero_comprobante, g.importe_total
        FROM gastos g
        JOIN categorias_imputacion c ON g.categoria_id = c.id
        ORDER BY g.fecha_factura DESC, g.id DESC
        LIMIT 10
    """
    return fetch_df(query)

# =================================================================================================
# INTERFAZ STREAMLIT
# =================================================================================================

def app_gestion_egresos():
    """Interfaz principal para la gestión de egresos."""
    st.title("💸 Gestión de Egresos Fijos")
    st.markdown("Utilice esta sección para registrar sus **Gastos Operativos** (EDET, Alquiler, Sueldos, etc.)")
    crear_tabla_gastos() # Asegura que la tabla 'gastos' exista
    
    # 1. Validación de Categorías de Egreso
    categorias_df = get_categorias_egreso()
    if categorias_df.empty:
        st.warning("⚠️ No hay categorías de **EGRESO** definidas. Vaya a la sección de ABM de Categorías y cree las cuentas necesarias (Ej: 'Servicio Eléctrico', 'Alquileres', etc.) con tipo **EGRESO**.")
        return

    # Preparar datos para el selectbox
    categorias_dict = dict(zip(categorias_df['nombre'], categorias_df['id']))
    nombres_categorias = list(categorias_dict.keys())

    # 2. Formulario de Registro
    st.header("1. Registrar Nuevo Gasto (Factura)")
    
    with st.form("form_registro_gasto", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            fecha_factura = st.date_input("Fecha de la Factura (Periodo del Gasto)", value=date.today())
        
        with col2:
            beneficiario = st.text_input("Beneficiario (Ej: EDET S.A., Inmobiliaria)", max_chars=100)
            
        with col3:
            categoria_seleccionada = st.selectbox("Categoría de Gasto", nombres_categorias)
        
        col4, col5 = st.columns(2)
        
        with col4:
            importe = st.number_input("Importe Total de la Factura", min_value=0.01, format="%.2f")
            
        with col5:
            comprobante = st.text_input("Nº de Comprobante / Factura", max_chars=50)

        # Fecha de Pago (Opcional, puede ser NULL si el gasto está pendiente)
        fecha_pago_input = st.date_input("Fecha de Pago Real (Opcional. Dejar vacío si está pendiente)", value=None)
        observaciones = st.text_area("Observaciones", max_chars=255)

        submitted = st.form_submit_button("✅ Registrar Gasto Fijo")

        if submitted:
            if not beneficiario or not comprobante or not importe:
                st.error("Los campos Beneficiario, Comprobante e Importe son obligatorios.")
            else:
                categoria_id = categorias_dict[categoria_seleccionada]
                
                # Formatear fechas
                fecha_pago_str = fecha_pago_input.strftime("%Y-%m-%d") if fecha_pago_input else None
                
                if registrar_gasto(
                    fecha_factura.strftime("%Y-%m-%d"), 
                    fecha_pago_str, 
                    beneficiario, 
                    categoria_id, 
                    comprobante, 
                    importe, 
                    observaciones
                ):
                    st.success("🎉 Gasto registrado con éxito!")
                    st.rerun()

    st.markdown("---")
    
    # 3. Visualización de Gastos Recientes
    st.header("2. Últimos Gastos Registrados")
    gastos_df = get_gastos_recientes()
    if not gastos_df.empty:
        gastos_df.rename(columns={
            'fecha_factura': 'Fecha Factura',
            'fecha_pago': 'Fecha Pago',
            'beneficiario_nombre': 'Beneficiario',
            'categoria': 'Categoría',
            'numero_comprobante': 'Comprobante',
            'importe_total': 'Total'
        }, inplace=True)
        st.dataframe(gastos_df[['Fecha Factura', 'Beneficiario', 'Categoría', 'Comprobante', 'Total', 'Fecha Pago']], hide_index=True)
    else:
        st.info("Aún no hay gastos registrados en la tabla.")


# =================================================================================================
# RUN (Para probar el archivo directamente)
# =================================================================================================
if __name__ == "__main__":
    st.set_page_config(layout="wide", page_title="Gestión de Egresos Fijos")
    app_gestion_egresos()