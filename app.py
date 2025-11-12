# streamlit_app.py (o app.py)

import streamlit as st

def main_page():
    """Define la interfaz de la página de bienvenida."""
    
    st.set_page_config(
        page_title="Simulador de Costos y Presupuestos",
        page_icon="🧪",
        layout="wide"
    )

    st.title("🧪 Bienvenido al Simulador de Costos y Presupuestos")
    
    st.markdown("---")
    
    st.header("Propósito de la Herramienta")
    st.info("""
        Esta aplicación está diseñada para calcular de manera precisa el **costo de producción** de tus recetas, 
        aplicando todos los factores relevantes (Materia Prima, Overhead Operativo, Flete, etc.) y generar 
        un presupuesto final en **ARS** y **USD**.
        
        **Importante:** Esta es una versión web **sin base de datos persistente**. Los precios de las Materias Primas, 
        Envases y Gastos Operativos son **simulados** (mock data) o ingresados por el usuario en el momento.
    """)
    
    st.markdown("---")
    
    st.header("Instrucciones de Uso")
    
    st.markdown("""
    Para comenzar con la simulación y generación de presupuestos, por favor selecciona la opción deseada en el **menú de navegación de la izquierda** (la barra lateral):
    
    1.  **A Granel:** Utiliza esta opción si solo necesitas calcular el costo de la receta por litro o para grandes volúmenes, sin considerar los costos del envase final.
    2.  **Produccion con Envase:** Utiliza esta opción para simular un pedido específico, incluyendo el costo de la materia prima, el costo del envase y los márgenes de ganancia.
    """)
    
    st.markdown("---")
    
    st.subheader("Simulación de Costos Clave (Valores de Referencia)")
    
    st.markdown("""
    Los cálculos de Overhead se basan en los siguientes supuestos (modificables en la barra lateral de las páginas de simulación):
    
    * **Volumen Mensual de Referencia:** 32.000 Litros (asumiendo 8 recetas/día * 200 L/receta * 20 días hábiles/mes)
    * **Overhead por Litro:** El Gasto Operativo Mensual Total (ingresado por el usuario) dividido por los 32.000 Litros de referencia.
    * **Recargo por Materia Prima:** 3% fijo sobre el costo base de la MP para cubrir fletes y otros costos indirectos de importación/logística.
    """)

if __name__ == "__main__":
    main_page()