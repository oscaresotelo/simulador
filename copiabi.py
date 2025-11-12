import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
# RE-IMPORTAR st_aggrid y sus componentes
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode 
import io 

# --- Configuración de la Base de Datos ---
DB_NAME = 'negocios_universal.db'
TABLE_FACT = 'fact_data_user'
TABLE_DIM = 'dim_entidad_user'

# --- Funciones de Base de Datos ---

def setup_database_and_load_data(df_hechos: pd.DataFrame, df_entidades: pd.DataFrame):
    """Guarda los DataFrames en SQLite manteniendo los nombres de columna originales."""
    conn = sqlite3.connect(DB_NAME)
    
    df_hechos.columns = [str(col) for col in df_hechos.columns]
    df_entidades.columns = [str(col) for col in df_entidades.columns]
    
    df_hechos.to_sql(TABLE_FACT, conn, if_exists='replace', index=False)
    df_entidades.to_sql(TABLE_DIM, conn, if_exists='replace', index=False)
    
    conn.commit()
    conn.close()
    st.info(f"Tablas `{TABLE_FACT}` y `{TABLE_DIM}` creadas/actualizadas con los nombres de columna originales.")


@st.cache_data(ttl=3600)
def load_and_model_data_from_db(map_hechos, map_dim):
    """Carga los datos modelados desde SQLite."""
    conn = sqlite3.connect(DB_NAME)
    
    # --- CONSTRUCCIÓN DINÁMICA DE LA CONSULTA ---
    
    col_id_hecho = map_hechos['ID_Hecho']
    col_fecha = map_hechos['Fecha']
    col_val1 = map_hechos['Valor_Numerico_1']
    col_val2 = map_hechos['Valor_Numerico_2']
    col_id_entidad_fact = map_hechos['ID_Entidad'] 
    
    col_id_entidad_dim = map_dim['ID_Entidad']    
    col_nombre_dim = map_dim['Nombre_Entidad']
    col_cat_dim = map_dim['Categoria_Entidad']
    
    query = f"""
    SELECT 
        F."{col_id_hecho}" AS ID_Hecho_Universal,
        F."{col_fecha}" AS Fecha_Universal,
        F."{col_val1}" AS Valor_Numerico_1_Universal,
        F."{col_val2}" AS Valor_Numerico_2_Universal,
        (CAST(F."{col_val1}" AS REAL) - CAST(F."{col_val2}" AS REAL)) AS Utilidad_Calculada,
        D."{col_nombre_dim}" AS Nombre_Entidad_Universal,
        D."{col_cat_dim}" AS Categoria_Entidad_Universal
    FROM 
        {TABLE_FACT} F
    LEFT JOIN 
        {TABLE_DIM} D ON F."{col_id_entidad_fact}" = D."{col_id_entidad_dim}";
    """
    
    df_modelo = pd.read_sql(query, conn)
    conn.close()
    
    df_modelo['Fecha_Universal'] = pd.to_datetime(df_modelo['Fecha_Universal'], errors='coerce')
    
    return df_modelo

# --- Función Auxiliar para Cargar Datos de Excel o CSV ---

@st.cache_data(show_spinner=False)
def load_excel_sheet(uploaded_file, sheet_name, header_row):
    """Carga una hoja específica de un archivo Excel o CSV."""
    uploaded_file.seek(0)
    
    if uploaded_file.name.endswith('.csv'):
        return pd.read_csv(uploaded_file, header=header_row)
    else:
        return pd.read_excel(uploaded_file, sheet_name=sheet_name, header=header_row)

# --- Lógica de la Aplicación Streamlit ---

def main_app():
    st.set_page_config(layout="wide", page_title="BI Universal con SQLite")
    st.title("⭐ Plataforma BI Universal (Se Adapta a tus Datos)")
    st.markdown("---")
    
    # --- 1. Carga de Datos y Mapeo ---
    st.header("📥 1. Carga y Configuración de Datos")
    
    col_hechos, col_dim = st.columns(2)
    
    # 1a. Cargadores de Archivos
    with col_hechos:
        st.info("Cargar **Tabla de Hechos** (Valores y transacciones)")
        file_hechos = st.file_uploader("Archivo de Hechos (Ej: INGRESOS.xlsx)", key='hechos_upload', type=['csv', 'xlsx'])
    
    with col_dim:
        st.info("Cargar **Tabla de Entidad** (Atributos categóricos)")
        file_dim = st.file_uploader("Archivo de Entidad (Ej: PRODUCTOS.xlsx)", key='dim_upload', type=['csv', 'xlsx'])

    if file_hechos and file_dim:
        
        # --- 1b. Selección de Hoja de Cálculo ---
        col_sheet_hechos, col_sheet_dim = st.columns(2)
        sheet_hechos_name = None
        sheet_dim_name = None
        
        with col_sheet_hechos:
            if file_hechos.name.endswith('.xlsx'):
                xls_hechos = pd.ExcelFile(file_hechos)
                sheet_names_hechos = xls_hechos.sheet_names
                sheet_hechos_name = st.selectbox("Selecciona la Hoja de Hechos:", sheet_names_hechos)
            
        with col_sheet_dim:
            if file_dim.name.endswith('.xlsx'):
                xls_dim = pd.ExcelFile(file_dim)
                sheet_names_dim = xls_dim.sheet_names
                sheet_dim_name = st.selectbox("Selecciona la Hoja de Entidad:", sheet_names_dim)
                
        
        # --- 1c. Configuración Avanzada de Encabezado y Mapeo ---
        with st.expander("⚙️ Configuración Avanzada (Encabezados y Mapeo)", expanded=False):
            
            # 1. Ajuste de Header
            st.subheader("Configuración de Encabezados (Header)")
            header_hechos = st.number_input("Fila de encabezado para Hechos (empezando en 0):", min_value=0, value=3)
            header_dim = st.number_input("Fila de encabezado para Entidad (empezando en 0):", min_value=0, value=0)
            
            try:
                # 2. Lectura con hoja y encabezado correctos
                df_hechos_raw = load_excel_sheet(file_hechos, sheet_hechos_name, header_hechos)
                df_dim_raw = load_excel_sheet(file_dim, sheet_dim_name, header_dim)
                
            except Exception as e:
                sheet_error = sheet_hechos_name or file_hechos.name
                st.error(f"Error al leer la hoja o archivo '{sheet_error}'. Revise la fila de encabezado. Error: {e}")
                return

            st.dataframe(df_hechos_raw.head())

            # 3. Mapeo de Columnas (Selectbox)
            st.subheader("Selecciona qué columna corresponde a la función universal")
            col_list_hechos = df_hechos_raw.columns.astype(str).tolist()
            col_list_dim = df_dim_raw.columns.astype(str).tolist()
            
            def get_default_index(col_list, index):
                return min(index, len(col_list) - 1) if col_list else 0

            # Mapeo de Hechos
            st.markdown("**Hechos (Tabla Principal de Valores):**")
            map_hechos = {
                'ID_Hecho': st.selectbox("Clave Única (PK):", col_list_hechos, index=get_default_index(col_list_hechos, 0), key='h1'),
                'Fecha': st.selectbox("Fecha de la Transacción:", col_list_hechos, index=get_default_index(col_list_hechos, 1), key='h2'),
                'ID_Entidad': st.selectbox("Clave Foránea (JOIN):", col_list_hechos, index=get_default_index(col_list_hechos, 2), key='h3'),
                'Valor_Numerico_1': st.selectbox("Valor 1 (Ej: Ingresos):", col_list_hechos, index=get_default_index(col_list_hechos, 3), key='h4'),
                'Valor_Numerico_2': st.selectbox("Valor 2 (Ej: Gastos):", col_list_hechos, index=get_default_index(col_list_hechos, 4), key='h5'),
            }
            
            # Mapeo de Dimensión
            st.markdown("**Dimensión (Tabla de Atributos):**")
            map_dim = {
                'ID_Entidad': st.selectbox("Clave Única (PK):", col_list_dim, index=get_default_index(col_list_dim, 0), key='d1'),
                'Nombre_Entidad': st.selectbox("Nombre/Descripción:", col_list_dim, index=get_default_index(col_list_dim, 1), key='d2'),
                'Categoria_Entidad': st.selectbox("Categoría/Grupo:", col_list_dim, index=get_default_index(col_list_dim, 2), key='d3'),
            }
        
        # 2. Guardar en SQLite (Usando nombres originales)
        setup_database_and_load_data(df_hechos_raw, df_dim_raw)
        
        # 3. Modelado y DAX Simulado (Construyendo SQL con Mapeo)
        st.header("⚙️ 2. Modelo de Datos y Visualización")
        try:
            df_modelo = load_and_model_data_from_db(map_hechos, map_dim)
        except Exception as e:
            st.error(f"Error al cargar/modelar desde SQLite. Revise los tipos de datos numéricos. Error: {e}")
            return
        
        st.markdown("---")
        
        # --- 4. Segmentadores Globales y KPIs ---
        
        col_kpi, col_chart = st.columns([1, 3])
        
        # Tarjeta KPI (Medida DAX Simulada)
        total_utilidad = df_modelo['Utilidad_Calculada'].sum()
        with col_kpi:
            st.metric(label="Medida DAX: Utilidad Total", value=f"${total_utilidad:,.0f}")
            
            # Filtro interactivo (Segmentador)
            categorias = df_modelo['Categoria_Entidad_Universal'].dropna().unique()
            filtro_categoria = st.multiselect("Filtrar por Categoría:", options=categorias, default=categorias, key='cat_filter')
            
        # Aplicar el filtro a los datos base
        df_filtrado_base = df_modelo[df_modelo['Categoria_Entidad_Universal'].isin(filtro_categoria)]

        # --- 5. Tabla Interactiva (Usando AgGrid para Agrupación Visual) ---
        st.subheader("Datos Modelados (Editor AG Grid - Agrupación y Filtrado) 🚀")
        st.info("Utiliza el **icono de barra lateral** (`>>` a la derecha) y arrastra la columna **'Categoría Entidad Universal'** a la sección de **Row Grouping** para agrupar los datos.")

        # --- Configuración del GridOptionsBuilder para Agrupación ---
        gb = GridOptionsBuilder.from_dataframe(df_filtrado_base)

        # 1. Configurar la columna de Categoría como columna de Agrupación por defecto
        # rowGroup=True: Inicia el grid agrupado por esta columna.
        # enableRowGroup=True: Permite al usuario arrastrarla para agrupar.
        # hide=True: Oculta la columna de la vista de detalle cuando está agrupada.
        gb.configure_column("Categoria_Entidad_Universal", 
                            rowGroup=True, 
                            hide=True, 
                            enableRowGroup=True)

        # 2. Configurar la columna de Utilidad como columna de 'Valor' para sumarse al agrupar
        # aggFunc='sum' hará la suma en los nodos agrupados.
        gb.configure_column("Utilidad_Calculada", 
                            aggFunc='sum', 
                            valueGetter="data.Utilidad_Calculada",
                            valueFormatter='Number(value).toLocaleString("es-AR", {style: "currency", currency: "ARS"})')

        # 3. Configurar columna por defecto (Solución al error enable_row_grouping)
        # Esto permite que CUALQUIER columna pueda ser usada para agrupar.
        gb.configure_default_column(groupable=True, filter=True, sortable=True) 

        # 4. Configurar la barra lateral para ver las herramientas de Columnas y Filtros
        gb.configure_side_bar(filters_panel=True, columns_panel=True, defaultToolPanel='columns')
        
        # 5. Habilitar módulos necesarios para Grouping
        gb.configure_grid_options(enableRangeSelection=True)


        gridOptions = gb.build()

        AgGrid(
            df_filtrado_base,
            gridOptions=gridOptions,
            data_return_mode='AS_INPUT', 
            update_mode='MODEL_CHANGED',
            fit_columns_on_grid_load=True, 
            allow_unsafe_jscode=True,
            enable_enterprise_modules=True, # MUY IMPORTANTE para la funcionalidad de agrupación
            height=350, 
            width='100%',
        )
        
        # --- 6. Gráfico de Visualización ---
        with col_chart:
            st.subheader("Utilidad por Entidad y Categoría")
            df_agrupado_chart = df_filtrado_base.groupby(['Categoria_Entidad_Universal', 'Nombre_Entidad_Universal'])['Utilidad_Calculada'].sum().reset_index()
            
            if not df_agrupado_chart.empty:
                fig = px.bar(
                    df_agrupado_chart, 
                    x='Nombre_Entidad_Universal', 
                    y='Utilidad_Calculada', 
                    color='Categoria_Entidad_Universal', 
                    title='Utilidad Agregada por Entidad'
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("No hay datos para mostrar en el gráfico con los filtros seleccionados.")

if __name__ == "__main__":
    main_app()