import os
try:
    from .db import connect_db
    from .database import hash_password
except ImportError:  # pragma: no cover - ejecución como main.py
    from db import connect_db
    from database import hash_password


def iniciar_base_datos_postgres():
    conexion = connect_db()
    cursor = conexion.cursor()
    tablas = [
        """CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY, nombre TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1, features TEXT NOT NULL DEFAULT '{}',
            creado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE IF NOT EXISTS tenants (
            id SERIAL PRIMARY KEY, nombre TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1, cliente_id INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(cliente_id) REFERENCES clientes(id) ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY, username TEXT NOT NULL, password TEXT NOT NULL,
            nombre TEXT NOT NULL, rol TEXT NOT NULL DEFAULT 'Operador',
            permisos TEXT NOT NULL DEFAULT '{}', tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS registros (
            id SERIAL PRIMARY KEY, placa TEXT NOT NULL, hora_ingreso TEXT NOT NULL,
            hora_salida TEXT, tipo_cliente TEXT NOT NULL DEFAULT 'Diario/Horas',
            estado TEXT NOT NULL DEFAULT 'Activo', total_pagado DOUBLE PRECISION DEFAULT 0,
            tenant_id INTEGER NOT NULL DEFAULT 1, tipo_vehiculo TEXT NOT NULL DEFAULT 'Automovil',
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS tarifas (
            id SERIAL PRIMARY KEY, tipo TEXT NOT NULL DEFAULT 'hora',
            valor DOUBLE PRECISION NOT NULL DEFAULT 2000, tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS clientes_mensuales (
            id SERIAL PRIMARY KEY, nombre TEXT NOT NULL, telefono TEXT NOT NULL, placa TEXT NOT NULL,
            tipo_vehiculo TEXT NOT NULL DEFAULT 'Automovil', valor_mensualidad DOUBLE PRECISION DEFAULT 0,
            fecha_inicio TEXT NOT NULL, fecha_fin TEXT NOT NULL, tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS tarifas_config (
            id SERIAL PRIMARY KEY, cliente_id INTEGER NOT NULL, tipo TEXT NOT NULL,
            valor_hora DOUBLE PRECISION NOT NULL DEFAULT 2000,
            valor_mensualidad DOUBLE PRECISION NOT NULL DEFAULT 0,
            FOREIGN KEY(cliente_id) REFERENCES clientes(id) ON DELETE CASCADE)""",
        """CREATE TABLE IF NOT EXISTS gastos (
            id SERIAL PRIMARY KEY, descripcion TEXT NOT NULL, monto DOUBLE PRECISION NOT NULL DEFAULT 0,
            categoria TEXT NOT NULL DEFAULT 'General', fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE)""",
    ]
    for tabla in tablas:
        cursor.execute(tabla)
    for indice in (
        "CREATE INDEX IF NOT EXISTS idx_usuarios_tenant ON usuarios(tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_usuarios_username ON usuarios(username)",
        "CREATE INDEX IF NOT EXISTS idx_registros_tenant ON registros(tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_registros_estado ON registros(estado)",
        "CREATE INDEX IF NOT EXISTS idx_registros_placa ON registros(placa)",
        "CREATE INDEX IF NOT EXISTS idx_clientes_mensuales_tenant ON clientes_mensuales(tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_tenants_cliente ON tenants(cliente_id)",
    ):
        cursor.execute(indice)

    cursor.execute("SELECT COUNT(*) FROM clientes")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO clientes (nombre, slug) VALUES (?, ?)", ("Cliente Principal", "cliente-principal"))
    cursor.execute("SELECT COUNT(*) FROM tenants")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO tenants (nombre, slug) VALUES (?, ?)", ("Parqueadero Principal", "parqueadero-principal"))
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        password = os.getenv("INITIAL_ADMIN_PASSWORD")
        if not password or len(password) < 12:
            raise RuntimeError("INITIAL_ADMIN_PASSWORD debe tener al menos 12 caracteres")
        tenant_id = cursor.execute(
            "SELECT id FROM tenants WHERE slug = ?", ("parqueadero-principal",)
        ).fetchone()[0]
        cursor.execute(
            "INSERT INTO usuarios (username, password, nombre, rol, permisos, tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("admin", hash_password(password), "Administrador", "Admin", "{}", tenant_id),
        )
    cursor.execute("SELECT COUNT(*) FROM tarifas")
    if cursor.fetchone()[0] == 0:
        tenant_id = cursor.execute(
            "SELECT id FROM tenants WHERE slug = ?", ("parqueadero-principal",)
        ).fetchone()[0]
        cursor.execute("INSERT INTO tarifas (tipo, valor, tenant_id) VALUES (?, ?, ?)", ("hora", 2000, tenant_id))
    conexion.commit()
    conexion.close()
