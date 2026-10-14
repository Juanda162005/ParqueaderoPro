import os
import sqlite3
import bcrypt
try:
    from .db import connect_db
except ImportError:  # pragma: no cover - permite ejecutar database.py directamente
    from db import connect_db

DB_FILE = os.getenv("DB_FILE", os.path.join(os.path.dirname(__file__), "parqueadero.db"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
INITIAL_ADMIN_PASSWORD = os.getenv("INITIAL_ADMIN_PASSWORD")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verificar_password(password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    if isinstance(hashed_password, str) and hashed_password.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode("utf-8"), hashed_password.encode("utf-8"))
        except ValueError:
            return False
    return password == hashed_password


def iniciar_base_datos():
    if os.getenv("DATABASE_URL", "").strip():
        try:
            from .database_postgres import iniciar_base_datos_postgres
        except ImportError:  # pragma: no cover - ejecución como main.py
            from database_postgres import iniciar_base_datos_postgres
        iniciar_base_datos_postgres()
        return
    conexion = connect_db()
    cursor = conexion.cursor()

    cursor.execute("PRAGMA foreign_keys = ON")

    # ── Tablas ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1,
            features TEXT NOT NULL DEFAULT '{}',
            creado_en TEXT NOT NULL DEFAULT (datetime('now'))
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tenants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1,
            cliente_id INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            nombre TEXT NOT NULL,
            rol TEXT NOT NULL DEFAULT 'Operador',
            permisos TEXT NOT NULL DEFAULT '{}',
            tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS registros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            placa TEXT NOT NULL,
            hora_ingreso TEXT NOT NULL,
            hora_salida TEXT,
            tipo_cliente TEXT NOT NULL DEFAULT 'Diario/Horas',
            estado TEXT NOT NULL DEFAULT 'Activo',
            total_pagado REAL DEFAULT 0,
            tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tarifas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL DEFAULT 'hora',
            valor REAL NOT NULL DEFAULT 2000.0,
            tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes_mensuales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            telefono TEXT NOT NULL,
            placa TEXT NOT NULL,
            tipo_vehiculo TEXT NOT NULL DEFAULT 'Automovil',
            valor_mensualidad REAL DEFAULT 0,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tarifas_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            valor_hora REAL NOT NULL DEFAULT 2000,
            valor_mensualidad REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descripcion TEXT NOT NULL,
            monto REAL NOT NULL DEFAULT 0,
            categoria TEXT NOT NULL DEFAULT 'General',
            fecha TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            tenant_id INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    ''')

    # ── Migraciones para BD existentes ──
    migraciones = [
        ("usuarios", "permisos", "TEXT NOT NULL DEFAULT '{}'"),
        ("usuarios", "tenant_id", "INTEGER NOT NULL DEFAULT 1"),
        ("registros", "tenant_id", "INTEGER NOT NULL DEFAULT 1"),
        ("registros", "tipo_vehiculo", "TEXT NOT NULL DEFAULT 'Automovil'"),
        ("tarifas", "tenant_id", "INTEGER NOT NULL DEFAULT 1"),
        ("clientes_mensuales", "tenant_id", "INTEGER NOT NULL DEFAULT 1"),
        ("clientes_mensuales", "valor_mensualidad", "REAL DEFAULT 0"),
        ("clientes_mensuales", "tipo_vehiculo", "TEXT NOT NULL DEFAULT 'Automovil'"),
        ("tenants", "cliente_id", "INTEGER NOT NULL DEFAULT 1"),
    ]
    for tabla, columna, tipo in migraciones:
        existing_columns = {
            row[1] for row in cursor.execute(f"PRAGMA table_info({tabla})").fetchall()
        }
        if columna not in existing_columns:
            cursor.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")

    # ── Indices ──
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_tenant ON usuarios(tenant_id)")
    existing_client_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(clientes)").fetchall()
    }
    if "features" not in existing_client_columns:
        cursor.execute("ALTER TABLE clientes ADD COLUMN features TEXT NOT NULL DEFAULT '{}'")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_username ON usuarios(username)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_registros_tenant ON registros(tenant_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_registros_estado ON registros(estado)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_registros_placa ON registros(placa)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_clientes_mensuales_tenant ON clientes_mensuales(tenant_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tenants_cliente ON tenants(cliente_id)")

    # ── Datos semilla ──
    cursor.execute("SELECT COUNT(*) FROM clientes")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO clientes (nombre, slug) VALUES (?, ?)",
                       ("Cliente Principal", "cliente-principal"))

    cursor.execute("SELECT COUNT(*) FROM tenants")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO tenants (nombre, slug) VALUES (?, ?)",
                       ("Parqueadero Principal", "parqueadero-principal"))

    cursor.execute("SELECT COUNT(*) FROM usuarios")
    if cursor.fetchone()[0] == 0:
        admin_password = INITIAL_ADMIN_PASSWORD
        if not admin_password:
            if ENVIRONMENT == "production":
                raise RuntimeError(
                    "INITIAL_ADMIN_PASSWORD es obligatoria al crear la base en producción"
                )
            admin_password = "admin123"
        if ENVIRONMENT == "production" and (
            len(admin_password) < 12 or admin_password.startswith("REPLACE_WITH_")
        ):
            raise RuntimeError(
                "INITIAL_ADMIN_PASSWORD debe tener al menos 12 caracteres reales"
            )
        tenant_id = cursor.execute(
            "SELECT id FROM tenants WHERE slug = ?", ("parqueadero-principal",)
        ).fetchone()[0]
        cursor.execute(
            "INSERT INTO usuarios (username, password, nombre, rol, permisos, tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("admin", hash_password(admin_password), "Administrador", "Admin", "{}", tenant_id),
        )
        if ENVIRONMENT != "production":
            cursor.execute(
                "INSERT INTO usuarios (username, password, nombre, rol, permisos, tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
                ("user", hash_password("user123"), "Operador 1", "Operador",
                 '{"metricas":false,"mensuales":false,"historial":false}', tenant_id),
            )

    cursor.execute("SELECT COUNT(*) FROM tarifas")
    if cursor.fetchone()[0] == 0:
        tenant_id = cursor.execute(
            "SELECT id FROM tenants WHERE slug = ?", ("parqueadero-principal",)
        ).fetchone()[0]
        cursor.execute("INSERT INTO tarifas (tipo, valor, tenant_id) VALUES (?, ?, ?)",
                       ("hora", 2000.0, tenant_id))

    # Tarifas por defecto para cliente principal
    cursor.execute("SELECT COUNT(*) FROM tarifas_config")
    if cursor.fetchone()[0] == 0:
        for tipo, hora, mens in [("Moto", 1000, 50000), ("Automovil", 2000, 120000),
                                  ("Camioneta", 2500, 150000), ("Camion", 3500, 200000)]:
            cursor.execute("INSERT INTO tarifas_config (cliente_id, tipo, valor_hora, valor_mensualidad) VALUES (?,?,?,?)",
                           (1, tipo, hora, mens))

    conexion.commit()
    conexion.close()
