BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS "asistencia" (
	"id"	INTEGER,
	"empleado_id"	INTEGER NOT NULL,
	"fecha"	TEXT NOT NULL,
	"hora_entrada_real"	TEXT,
	"hora_salida_real"	TEXT,
	"es_feriado"	BOOLEAN DEFAULT 0,
	"ausente"	BOOLEAN DEFAULT 0,
	"justificado"	BOOLEAN DEFAULT 0,
	"justificacion_path"	TEXT,
	"observacion"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("empleado_id") REFERENCES "empleados"("id")
);
CREATE TABLE IF NOT EXISTS "categorias_imputacion" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL UNIQUE,
	"tipo"	TEXT NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "clientes" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL,
	"contacto"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "composicion_colorantes" (
	"colorante_combinado_id"	INTEGER,
	"colorante_primario_id"	INTEGER,
	"proporcion"	REAL,
	FOREIGN KEY("colorante_combinado_id") REFERENCES "materias_primas"("id"),
	FOREIGN KEY("colorante_primario_id") REFERENCES "materias_primas"("id")
);
CREATE TABLE IF NOT EXISTS "compras_materia_prima" (
	"id"	INTEGER,
	"proveedor_id"	INTEGER NOT NULL,
	"materia_prima_id"	INTEGER NOT NULL,
	"fecha"	TEXT NOT NULL,
	"cantidad"	REAL NOT NULL,
	"precio_unitario"	REAL NOT NULL,
	"costo_total"	REAL NOT NULL,
	"costo_flete"	REAL DEFAULT 0,
	"otros_costos"	REAL DEFAULT 0,
	"moneda"	TEXT DEFAULT 'ARS',
	"cotizacion_usd"	REAL DEFAULT NULL,
	"observaciones"	TEXT,
	"numero_comprobante"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id"),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id"),
	FOREIGN KEY("proveedor_id") REFERENCES "proveedores"("id"),
	FOREIGN KEY("proveedor_id") REFERENCES "proveedores"("id")
);
CREATE TABLE IF NOT EXISTS "costos_asociados" (
	"id"	INTEGER,
	"materia_prima_id"	INTEGER NOT NULL,
	"tipo_costo"	TEXT NOT NULL,
	"monto"	REAL NOT NULL,
	"fecha"	TEXT NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id")
);
CREATE TABLE IF NOT EXISTS "cotizacion_dolar" (
	"id"	INTEGER,
	"fecha_hora"	TEXT NOT NULL,
	"compra"	REAL NOT NULL,
	"venta"	REAL NOT NULL,
	"fecha"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "detalle_orden_compra" (
	"id"	INTEGER,
	"orden_compra_id"	INTEGER NOT NULL,
	"materia_prima_id"	INTEGER NOT NULL,
	"cantidad"	REAL NOT NULL,
	"precio_unitario"	REAL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id"),
	FOREIGN KEY("orden_compra_id") REFERENCES "ordenes_de_compra"("id")
);
CREATE TABLE IF NOT EXISTS "detalle_venta" (
	"id"	INTEGER,
	"venta_id"	INTEGER NOT NULL,
	"stock_id"	INTEGER NOT NULL,
	"cantidad"	INTEGER NOT NULL,
	"precio_unitario"	REAL NOT NULL,
	"subtotal"	REAL NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("stock_id") REFERENCES "stock_productos_envasados"("id"),
	FOREIGN KEY("venta_id") REFERENCES "ventas"("id")
);
CREATE TABLE IF NOT EXISTS "empleados" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL,
	"sueldo_base"	REAL NOT NULL,
	"horas_jornada_diaria"	REAL NOT NULL,
	"horario_entrada"	TEXT NOT NULL,
	"horario_salida"	TEXT NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "entradas_envases" (
	"id"	INTEGER,
	"proveedor_id"	INTEGER NOT NULL,
	"fecha_ingreso"	TEXT NOT NULL,
	"numero_comprobante"	TEXT NOT NULL,
	"envase_id"	INTEGER NOT NULL,
	"cantidad_ingresada"	INTEGER NOT NULL,
	"precio_unitario"	REAL,
	"cliente_asignado_id"	INTEGER,
	"producto_asignado_id"	INTEGER,
	"lote"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_asignado_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id"),
	FOREIGN KEY("producto_asignado_id") REFERENCES "recetas"("id"),
	FOREIGN KEY("proveedor_id") REFERENCES "proveedores"("id")
);
CREATE TABLE IF NOT EXISTS "envases" (
	"id"	INTEGER,
	"descripcion"	TEXT NOT NULL,
	"unidad"	TEXT,
	"cliente_id"	INTEGER NOT NULL DEFAULT 6,
	"capacidad_litros"	REAL,
	UNIQUE("descripcion","cliente_id"),
	PRIMARY KEY("id"),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id")
);
CREATE TABLE IF NOT EXISTS "gastos" (
	"id"	INTEGER,
	"fecha_factura"	TEXT NOT NULL,
	"fecha_pago"	TEXT,
	"beneficiario_nombre"	TEXT NOT NULL,
	"categoria_id"	INTEGER NOT NULL,
	"numero_comprobante"	TEXT UNIQUE,
	"importe_total"	REAL NOT NULL,
	"moneda"	TEXT DEFAULT 'ARS',
	"observaciones"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("categoria_id") REFERENCES "categorias_imputacion"("id")
);
CREATE TABLE IF NOT EXISTS "historial_cambios_dolar" (
	"id"	INTEGER,
	"fecha_cambio"	TEXT NOT NULL,
	"tipo_cambio"	TEXT NOT NULL,
	"valor_anterior"	REAL,
	"valor_nuevo"	REAL NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "historial_cotizaciones_orden" (
	"id"	INTEGER,
	"detalle_orden_compra_id"	INTEGER NOT NULL,
	"precio_unitario"	REAL,
	"fecha_cotizacion"	TEXT,
	"moneda"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("detalle_orden_compra_id") REFERENCES "detalle_orden_compra"("id")
);
CREATE TABLE IF NOT EXISTS "imputaciones_pago" (
	"id"	INTEGER,
	"pago_comprobante_id"	INTEGER NOT NULL,
	"categoria_id"	INTEGER NOT NULL,
	"monto_imputado"	REAL NOT NULL,
	"referencia_egreso"	TEXT,
	"fecha_imputacion"	TEXT NOT NULL,
	"observaciones"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("categoria_id") REFERENCES "categorias_imputacion"("id"),
	FOREIGN KEY("pago_comprobante_id") REFERENCES "pagos_comprobantes"("id")
);
CREATE TABLE IF NOT EXISTS "lineas" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL UNIQUE,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "liquidaciones" (
	"id"	INTEGER,
	"empleado_id"	INTEGER NOT NULL,
	"periodo"	TEXT NOT NULL,
	"dias_habiles_mes"	INTEGER NOT NULL,
	"dias_pagados"	INTEGER NOT NULL,
	"horas_extras"	REAL DEFAULT 0,
	"sueldo_bruto"	REAL NOT NULL,
	"bono_objetivo"	REAL DEFAULT 0,
	"bono_presentismo"	REAL DEFAULT 0,
	"descuento_falta"	REAL DEFAULT 0,
	"total_a_cobrar"	REAL NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("empleado_id") REFERENCES "empleados"("id")
);
CREATE TABLE IF NOT EXISTS "lotes" (
	"id"	INTEGER,
	"numero"	TEXT NOT NULL UNIQUE,
	"fecha"	TEXT NOT NULL,
	"cliente_id"	INTEGER,
	"receta_id"	INTEGER,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("receta_id") REFERENCES "recetas"("id")
);
CREATE TABLE IF NOT EXISTS "materias_primas" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL,
	"unidad"	TEXT NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	UNIQUE("nombre","unidad")
);
CREATE TABLE IF NOT EXISTS "movimientos_cajas" (
	"id"	INTEGER,
	"fecha"	TEXT NOT NULL,
	"tipo_caja"	TEXT NOT NULL,
	"cantidad"	INTEGER NOT NULL,
	"tipo_movimiento"	TEXT NOT NULL CHECK("tipo_movimiento" IN ('Ingreso', 'Egreso')),
	"orden_envasado_id"	INTEGER,
	"observacion"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "movimientos_envases" (
	"id"	INTEGER,
	"envase_id"	INTEGER NOT NULL,
	"fecha"	TEXT NOT NULL,
	"cantidad"	REAL NOT NULL,
	"tipo_movimiento"	TEXT NOT NULL CHECK("tipo_movimiento" IN ('Ingreso', 'Egreso')),
	"observacion"	TEXT,
	"lote"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id")
);
CREATE TABLE IF NOT EXISTS "movimientos_materia_prima" (
	"id"	INTEGER,
	"materia_prima_id"	INTEGER NOT NULL,
	"lote"	TEXT NOT NULL,
	"fecha"	TEXT NOT NULL,
	"cantidad"	REAL NOT NULL,
	"tipo_movimiento"	TEXT NOT NULL CHECK("tipo_movimiento" IN ('ingreso', 'egreso')),
	"destino"	TEXT NOT NULL CHECK("destino" IN ('produccion', 'laboratorio', 'otro')),
	"costo_flete"	REAL DEFAULT 0,
	"otros_costos"	REAL DEFAULT 0,
	"precio_unitario"	REAL DEFAULT 0,
	"compra_id"	INTEGER,
	"precio_unitario_total"	REAL DEFAULT 0,
	"reactor"	INTEGER,
	"observaciones"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("compra_id") REFERENCES "compras_materia_prima"("id"),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id")
);
CREATE TABLE IF NOT EXISTS "movimientos_orden_compra" (
	"id"	INTEGER,
	"orden_compra_id"	INTEGER NOT NULL,
	"fecha_movimiento"	TEXT NOT NULL,
	"estado_anterior"	TEXT,
	"estado_nuevo"	TEXT,
	"usuario_id"	INTEGER,
	"descripcion"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("orden_compra_id") REFERENCES "ordenes_de_compra"("id"),
	FOREIGN KEY("usuario_id") REFERENCES "usuarios"("id")
);
CREATE TABLE IF NOT EXISTS "movimientos_productos_envasados" (
	"id"	INTEGER,
	"stock_id"	INTEGER NOT NULL,
	"fecha"	TEXT NOT NULL,
	"cantidad"	REAL NOT NULL,
	"tipo_movimiento"	TEXT NOT NULL CHECK("tipo_movimiento" IN ('Ingreso', 'Egreso')),
	"destino"	TEXT,
	"observacion"	TEXT,
	"numero_comprobante"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("stock_id") REFERENCES "stock_productos_envasados"("id")
);
CREATE TABLE IF NOT EXISTS "ordenes_de_compra" (
	"id"	INTEGER,
	"proveedor_id"	INTEGER NOT NULL,
	"fecha_orden"	TEXT NOT NULL,
	"fecha_entrega"	TEXT,
	"estado"	TEXT DEFAULT 'pendiente',
	"observaciones"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("proveedor_id") REFERENCES "proveedores"("id")
);
CREATE TABLE IF NOT EXISTS "ordenes_envasado" (
	"id"	INTEGER,
	"produccion_id"	INTEGER NOT NULL,
	"fecha"	TEXT NOT NULL,
	"producto_cliente_id"	INTEGER NOT NULL,
	"envase_id"	INTEGER NOT NULL,
	"cantidad_unidades"	REAL NOT NULL,
	"estado"	TEXT DEFAULT 'pendiente',
	"envasadora_id"	INTEGER,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id"),
	FOREIGN KEY("produccion_id") REFERENCES "producciones_temp"("id"),
	FOREIGN KEY("producto_cliente_id") REFERENCES "productos_clientes"("id")
);
CREATE TABLE IF NOT EXISTS "pagos_comprobantes" (
	"id"	INTEGER,
	"venta_id"	INTEGER NOT NULL,
	"fecha_pago"	TEXT NOT NULL,
	"importe_pagado"	REAL NOT NULL,
	"observaciones"	TEXT,
	"estado_imputacion"	TEXT DEFAULT 'Pendiente',
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("venta_id") REFERENCES "ventas"("id")
);
CREATE TABLE IF NOT EXISTS "pagos_materia_prima" (
	"id"	INTEGER,
	"compra_id"	INTEGER NOT NULL,
	"importe"	REAL NOT NULL,
	"fecha"	TEXT NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("compra_id") REFERENCES "compras_materia_prima"("id")
);
CREATE TABLE IF NOT EXISTS "pedidos_produccion" (
	"id"	INTEGER,
	"cliente_id"	INTEGER,
	"receta_id"	INTEGER,
	"cantidad"	REAL,
	"numero_pedido"	TEXT,
	"fecha"	TEXT,
	"estado"	TEXT CHECK("estado" IN ('pendiente', 'en_proceso', 'producido', 'anulado', 'Vendido')),
	"lote"	TEXT,
	PRIMARY KEY("id"),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("receta_id") REFERENCES "recetas"("id")
);
CREATE TABLE IF NOT EXISTS "precios_materias_primas" (
	"id"	INTEGER,
	"materia_prima_id"	INTEGER NOT NULL,
	"precio_unitario"	REAL NOT NULL,
	"fecha"	TEXT NOT NULL,
	"costo_flete"	REAL DEFAULT 0,
	"otros_costos"	REAL DEFAULT 0,
	"cotizacion_usd"	REAL NOT NULL DEFAULT 1,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id")
);
CREATE TABLE IF NOT EXISTS "precios_productos_envasados" (
	"id"	INTEGER,
	"cliente_id"	INTEGER NOT NULL,
	"producto_id"	INTEGER NOT NULL,
	"envase_id"	INTEGER NOT NULL,
	"precio_unitario"	REAL NOT NULL,
	"fecha_desde"	TEXT NOT NULL,
	"fecha_hasta"	TEXT,
	UNIQUE("cliente_id","producto_id","envase_id","fecha_desde"),
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id"),
	FOREIGN KEY("producto_id") REFERENCES "productos"("id")
);
CREATE TABLE IF NOT EXISTS "presupuestos" (
	"id"	INTEGER,
	"cliente_id"	INTEGER NOT NULL,
	"fecha"	TEXT NOT NULL,
	"porcentaje_ganancia"	REAL NOT NULL,
	"volumen_total_litros"	REAL NOT NULL,
	"costo_total_ars"	REAL NOT NULL,
	"precio_final_ars"	REAL NOT NULL,
	"detalle_simulaciones_json"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id")
);
CREATE TABLE IF NOT EXISTS "produccion_materia_prima" (
	"id"	INTEGER,
	"produccion_id"	INTEGER NOT NULL,
	"materia_prima_id"	INTEGER NOT NULL,
	"lote"	TEXT NOT NULL,
	"cantidad_usada"	REAL NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id"),
	FOREIGN KEY("produccion_id") REFERENCES "producciones_temp"("id")
);
CREATE TABLE IF NOT EXISTS "producciones" (
	"id"	INTEGER,
	"pedido_id"	INTEGER NOT NULL,
	"fecha"	TEXT NOT NULL,
	"lote"	TEXT NOT NULL UNIQUE,
	"estado"	TEXT DEFAULT 'pendiente',
	"cantidad_litros"	REAL DEFAULT 0,
	"litros_envasados"	REAL DEFAULT 0,
	"cantidad_real_litros"	REAL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("pedido_id") REFERENCES "pedidos_produccion"("id")
);
CREATE TABLE IF NOT EXISTS "producto_presentaciones" (
	"id"	INTEGER,
	"producto_id"	INTEGER NOT NULL,
	"envase_base_id"	INTEGER NOT NULL,
	"nombre_presentacion"	TEXT NOT NULL,
	"multiplicador_unidades_base"	INTEGER NOT NULL,
	"orden_jerarquico"	INTEGER NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	UNIQUE("producto_id","envase_base_id","nombre_presentacion"),
	FOREIGN KEY("envase_base_id") REFERENCES "envases"("id"),
	FOREIGN KEY("producto_id") REFERENCES "productos"("id")
);
CREATE TABLE IF NOT EXISTS "productos" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL,
	"linea_id"	INTEGER NOT NULL,
	"envase_id"	INTEGER,
	"cliente_id"	INTEGER NOT NULL,
	"id_receta"	INTEGER,
	"permite_reproceso"	INTEGER DEFAULT 0,
	"tipo_caja_id"	INTEGER,
	PRIMARY KEY("id" AUTOINCREMENT),
	UNIQUE("nombre","cliente_id"),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id"),
	FOREIGN KEY("linea_id") REFERENCES "lineas"("id"),
	FOREIGN KEY("tipo_caja_id") REFERENCES "tipo_cajas"("id")
);
CREATE TABLE IF NOT EXISTS "productos_clientes" (
	"id"	INTEGER,
	"cliente_id"	INTEGER NOT NULL,
	"producto_id"	INTEGER NOT NULL,
	"uso"	TEXT NOT NULL,
	"linea"	TEXT NOT NULL,
	"id_receta"	INTEGER,
	UNIQUE("cliente_id","producto_id","uso","linea"),
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("producto_id") REFERENCES "productos"("id")
);
CREATE TABLE IF NOT EXISTS "productos_clientes_envases" (
	"id"	INTEGER,
	"producto_cliente_id"	INTEGER NOT NULL,
	"envase_id"	INTEGER NOT NULL,
	"tipo_caja"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	UNIQUE("producto_cliente_id","envase_id"),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id"),
	FOREIGN KEY("producto_cliente_id") REFERENCES "productos_clientes"("id")
);
CREATE TABLE IF NOT EXISTS "productos_envases" (
	"id"	INTEGER,
	"producto_id"	INTEGER,
	"envase_id"	INTEGER,
	"tipo_caja"	TEXT,
	PRIMARY KEY("id"),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id"),
	FOREIGN KEY("producto_id") REFERENCES "productos"("id")
);
CREATE TABLE IF NOT EXISTS "promesas_pago" (
	"id"	INTEGER,
	"revendedor_id"	INTEGER NOT NULL,
	"fecha_promesa"	TEXT NOT NULL,
	"monto_prometido"	REAL NOT NULL,
	"observaciones"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("revendedor_id") REFERENCES "revendedores"("id")
);
CREATE TABLE IF NOT EXISTS "proveedores" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL UNIQUE,
	"contacto"	TEXT,
	"cuit"	TEXT,
	"direccion"	TEXT,
	"telefono"	TEXT,
	"email"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "receta_ingredientes" (
	"id"	INTEGER,
	"receta_id"	INTEGER NOT NULL,
	"materia_prima_id"	INTEGER NOT NULL,
	"cantidad"	REAL NOT NULL,
	"unidad"	TEXT NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id"),
	FOREIGN KEY("receta_id") REFERENCES "recetas"("id")
);
CREATE TABLE IF NOT EXISTS "receta_materia_prima" (
	"id"	INTEGER,
	"receta_id"	INTEGER NOT NULL,
	"materia_prima_id"	INTEGER NOT NULL,
	"cantidad"	REAL NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	UNIQUE("receta_id","materia_prima_id"),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id") ON DELETE CASCADE,
	FOREIGN KEY("receta_id") REFERENCES "recetas"("id") ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS "receta_modificada" (
	"id"	INTEGER,
	"pedido_id"	INTEGER,
	"materia_prima_id"	INTEGER,
	"cantidad"	REAL,
	"unidad"	TEXT,
	"receta_id"	INTEGER,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id"),
	FOREIGN KEY("pedido_id") REFERENCES "pedidos_produccion_old"("id")
);
CREATE TABLE IF NOT EXISTS "recetas" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL,
	"cliente_id"	INTEGER,
	"uso"	TEXT,
	"linea"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id")
);
CREATE TABLE IF NOT EXISTS "reprocesos_productos_envasados" (
	"id"	INTEGER,
	"fecha"	TEXT NOT NULL,
	"stock_id"	INTEGER NOT NULL,
	"cantidad"	REAL NOT NULL,
	"produccion_id"	INTEGER NOT NULL,
	"observacion"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("produccion_id") REFERENCES "producciones_temp"("id"),
	FOREIGN KEY("stock_id") REFERENCES "stock_productos_envasados"("id")
);
CREATE TABLE IF NOT EXISTS "revendedores" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL,
	"nombre_empresa"	TEXT,
	"contacto"	TEXT,
	"descuento"	REAL DEFAULT 0.0,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "salidas_productos_envasados" (
	"id"	INTEGER,
	"orden_envasado_id"	INTEGER NOT NULL,
	"fecha"	TEXT NOT NULL,
	"cantidad_despachada"	REAL NOT NULL,
	"observacion"	TEXT,
	"fecha_salida"	TEXT,
	"destino"	TEXT,
	"numero_comprobante"	,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("orden_envasado_id") REFERENCES "ordenes_envasado"("id")
);
CREATE TABLE IF NOT EXISTS "simulaciones" (
	"id"	INTEGER,
	"receta_id"	INTEGER NOT NULL,
	"cantidad"	REAL NOT NULL,
	"fecha"	TEXT NOT NULL,
	"cliente_id"	INTEGER,
	"resultado_json"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("receta_id") REFERENCES "recetas"("id")
);
CREATE TABLE IF NOT EXISTS "sobrantes_pagos" (
	"id"	INTEGER,
	"revendedor_id"	INTEGER NOT NULL,
	"importe_sobrante"	REAL NOT NULL,
	"fecha_sobrante"	TEXT NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("revendedor_id") REFERENCES "revendedores"("id")
);
CREATE TABLE IF NOT EXISTS "stock_cajas" (
	"id"	INTEGER,
	"tipo_caja"	TEXT NOT NULL,
	"capacidad_envase_id"	INTEGER,
	"cantidad_actual"	INTEGER NOT NULL,
	"ubicacion"	TEXT,
	"tipo_caja_id"	INTEGER,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("tipo_caja_id") REFERENCES "tipo_cajas"("id")
);
CREATE TABLE IF NOT EXISTS "stock_envases_clientes" (
	"cliente_id"	INTEGER NOT NULL,
	"envase_id"	INTEGER NOT NULL,
	"uso"	TEXT NOT NULL,
	"linea"	TEXT NOT NULL,
	"cantidad"	INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY("cliente_id","envase_id","uso","linea"),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id")
);
CREATE TABLE IF NOT EXISTS "stock_envases_clientes_lotes" (
	"id"	INTEGER,
	"cliente_id"	INTEGER NOT NULL,
	"envase_id"	INTEGER NOT NULL,
	"lote"	TEXT NOT NULL,
	"cantidad"	REAL NOT NULL,
	"fecha_ingreso"	TEXT,
	"uso"	TEXT,
	"linea"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id")
);
CREATE TABLE IF NOT EXISTS "stock_materias_primas" (
	"materia_prima_id"	INTEGER,
	"cantidad"	REAL NOT NULL,
	"cantidad_actual"	REAL DEFAULT 0,
	PRIMARY KEY("materia_prima_id"),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id")
);
CREATE TABLE IF NOT EXISTS "stock_por_lote" (
	"id"	INTEGER,
	"materia_prima_id"	INTEGER NOT NULL,
	"lote"	TEXT NOT NULL,
	"cantidad"	REAL NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("materia_prima_id") REFERENCES "materias_primas"("id")
);
CREATE TABLE IF NOT EXISTS "stock_productos_envasados" (
	"id"	INTEGER,
	"cliente_id"	INTEGER NOT NULL,
	"receta_id"	INTEGER NOT NULL,
	"envase_id"	INTEGER NOT NULL,
	"lote"	TEXT,
	"cantidad"	REAL NOT NULL,
	"fecha_ingreso"	TEXT NOT NULL,
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id"),
	FOREIGN KEY("envase_id") REFERENCES "envases"("id"),
	FOREIGN KEY("receta_id") REFERENCES "recetas"("id")
);
CREATE TABLE IF NOT EXISTS "tipo_cajas" (
	"id"	INTEGER,
	"producto"	TEXT NOT NULL,
	"linea"	TEXT,
	"unidades_por_caja"	INTEGER NOT NULL,
	"descripcion"	TEXT,
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "usuarios" (
	"id"	INTEGER,
	"nombre"	TEXT NOT NULL,
	"username"	TEXT NOT NULL UNIQUE,
	"password"	TEXT NOT NULL,
	"rol"	TEXT NOT NULL CHECK("rol" IN ('Administrador', 'Operador')),
	PRIMARY KEY("id" AUTOINCREMENT)
);
CREATE TABLE IF NOT EXISTS "ventas" (
	"id"	INTEGER,
	"cliente_id"	INTEGER NOT NULL,
	"fecha_venta"	TEXT NOT NULL,
	"numero_comprobante"	TEXT,
	"total_venta"	REAL NOT NULL,
	"observacion"	TEXT,
	"revendedor_id"	INTEGER,
	"estado_venta"	TEXT NOT NULL DEFAULT 'activa',
	PRIMARY KEY("id" AUTOINCREMENT),
	FOREIGN KEY("cliente_id") REFERENCES "clientes"("id")
);
COMMIT;
