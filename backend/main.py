import json
import math
import os
import re
import shutil
import sqlite3
import threading
import time as time_module
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

try:
    from .database import DB_FILE, hash_password, iniciar_base_datos, verificar_password
    from .db import DATABASE_URL, connect_db
except ImportError:  # pragma: no cover - permite ejecutar main.py directamente
    from database import DB_FILE, hash_password, iniciar_base_datos, verificar_password
    from db import DATABASE_URL, connect_db

app = FastAPI(
    title="Sistema de Parqueadero LPR Pro",
    version="1.0.0",
    docs_url=None if os.getenv("ENABLE_DOCS", "false").lower() == "false" else "/docs",
    redoc_url=None if os.getenv("ENABLE_DOCS", "false").lower() == "false" else "/redoc",
)

security = HTTPBearer(auto_error=False)
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if not SECRET_KEY:
    if ENVIRONMENT == "production":
        raise RuntimeError("JWT_SECRET_KEY es obligatoria en producción")
    SECRET_KEY = "development-only-insecure-key-32-bytes"
elif ENVIRONMENT == "production" and (
    len(SECRET_KEY) < 32 or SECRET_KEY.startswith("REPLACE_WITH_")
):
    raise RuntimeError("JWT_SECRET_KEY debe ser un secreto real de al menos 32 caracteres")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_TTL_HOURS = int(os.getenv("ACCESS_TOKEN_TTL_HOURS", "8"))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:8080,http://localhost:8080",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

iniciar_base_datos()


# ═══════════════════════════════════════════════════════════════
# SEGURIDAD: Rate Limiting, Bloqueo de cuenta, Auditoría, Backup
# ═══════════════════════════════════════════════════════════════

intentos_fallidos: Dict[str, list] = defaultdict(list)
cuentas_bloqueadas: Dict[str, float] = {}
MAX_INTENTOS = 5
VENTANA_INTENTOS = 300  # 5 minutos
BLOQUEO_SEGUNDOS = 900   # 15 minutos

def verificar_rate_limit(ip: str):
    """Bloquea IPs con más de 100 peticiones por minuto"""
    ahora = time_module.time()
    intentos_fallidos[f"rate_{ip}"] = [t for t in intentos_fallidos.get(f"rate_{ip}", []) if ahora - t < 60]
    intentos_fallidos[f"rate_{ip}"].append(ahora)
    if len(intentos_fallidos[f"rate_{ip}"]) > 100:
        raise HTTPException(status_code=429, detail="Demasiadas peticiones. Espera un minuto.")

def verificar_intentos(username: str, ip: str):
    """Verifica si la cuenta o IP está bloqueada por intentos fallidos"""
    ahora = time_module.time()
    # Bloqueo por cuenta
    if username in cuentas_bloqueadas:
        if ahora - cuentas_bloqueadas[username] < BLOQUEO_SEGUNDOS:
            raise HTTPException(status_code=423, detail="Cuenta bloqueada por múltiples intentos. Espera 15 minutos.")
        del cuentas_bloqueadas[username]
    # Bloqueo por IP
    if ip in cuentas_bloqueadas:
        if ahora - cuentas_bloqueadas[ip] < BLOQUEO_SEGUNDOS:
            raise HTTPException(status_code=423, detail="Demasiados intentos desde esta IP. Espera 15 minutos.")
        del cuentas_bloqueadas[ip]

def registrar_intento_fallido(username: str, ip: str):
    """Registra un intento fallido y bloquea si excede el límite"""
    ahora = time_module.time()
    key = f"login_{username}_{ip}"
    intentos_fallidos[key] = [t for t in intentos_fallidos.get(key, []) if ahora - t < VENTANA_INTENTOS]
    intentos_fallidos[key].append(ahora)
    if len(intentos_fallidos[key]) >= MAX_INTENTOS:
        cuentas_bloqueadas[username] = ahora
        cuentas_bloqueadas[ip] = ahora

def auditar(accion: str, usuario: str = "anonimo", tenant_id: int = 0, detalle: str = ""):
    """Registra acción en log de auditoría"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        linea = f"[{ts}] {accion} | user={usuario} | tenant={tenant_id} | {detalle}\n"
        with open(os.path.join(os.path.dirname(DB_FILE), "auditoria.log"), "a", encoding="utf-8") as f:
            f.write(linea)
    except Exception:
        pass

def backup_automatico():
    """Crea backup de la BD cada hora (máximo 24 backups)"""
    try:
        backup_dir = os.path.join(os.path.dirname(DB_FILE), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        hora = datetime.now().strftime("%H")
        backup_file = os.path.join(backup_dir, f"parqueadero_backup_{hora}h.db")
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, backup_file)
        # Limpiar backups viejos (>7 días)
        for f in os.listdir(backup_dir):
            fp = os.path.join(backup_dir, f)
            if os.path.getmtime(fp) < time_module.time() - 604800:
                os.remove(fp)
    except Exception:
        pass

# Ejecutar backup automático cada hora en un hilo separado
def _backup_loop():
    while True:
        time_module.sleep(3600)
        backup_automatico()
threading.Thread(target=_backup_loop, daemon=True).start()


class LoginModel(BaseModel):
    username: str
    password: str


class RegistroIngreso(BaseModel):
    placa: str
    tipo_cliente: str = "Diario/Horas"
    tipo_vehiculo: str = "Automovil"


class RegistroSalida(BaseModel):
    placa: str


class ClienteMensual(BaseModel):
    nombre: str
    telefono: str
    placa_vehiculo: str
    tipo_vehiculo: str = "Automovil"
    valor_mensualidad: float = 0.0
    fecha_inicio: str
    fecha_fin: str


class ClienteMensualUpdate(BaseModel):
    nombre: str
    telefono: str
    tipo_vehiculo: str = "Automovil"
    valor_mensualidad: float = 0.0
    fecha_inicio: str
    fecha_fin: str


class UsuarioCreate(BaseModel):
    username: str
    password: str
    nombre: str
    rol: str = "Operador"
    permisos: Optional[Dict[str, Any]] = None


class UsuarioUpdate(BaseModel):
    username: Optional[str] = None
    nombre: Optional[str] = None
    rol: Optional[str] = None
    password: Optional[str] = None
    permisos: Optional[Dict[str, Any]] = None


class PermisosUpdate(BaseModel):
    permisos: Dict[str, Any]


class TenantCreate(BaseModel):
    nombre: str
    slug: str


class ClienteCreate(BaseModel):
    nombre: str
    slug: str
    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_nombre: str = "Administrador"
    parqueadero_nombre: str = ""  # Si no se especifica, usa el nombre del cliente


class ClienteUpdate(BaseModel):
    nombre: Optional[str] = None
    slug: Optional[str] = None


class MiPerfilUpdate(BaseModel):
    """Edición del propio perfil: nombre, username, contraseña, y datos del cliente"""
    nombre: Optional[str] = None           # nombre personal del admin
    username: Optional[str] = None         # nuevo username
    password: Optional[str] = None         # nueva contraseña (vacía = no cambia)
    cliente_nombre: Optional[str] = None   # nombre de la organización
    cliente_slug: Optional[str] = None     # slug de la organización


class ConvertirMensual(BaseModel):
    """Convierte un vehículo activo diario a cliente mensual"""
    placa: str
    nombre: str
    telefono: str
    tipo_vehiculo: str = "Automovil"
    valor_mensualidad: float = 0.0
    fecha_inicio: str
    fecha_fin: str


class GastoCreate(BaseModel):
    descripcion: str
    monto: float
    categoria: str = "General"


class TenantUserCreate(BaseModel):
    username: str
    password: str
    nombre: str
    rol: str = "Operador"
    permisos: Optional[Dict[str, Any]] = None


def get_db():
    conexion = connect_db()
    return conexion


def parse_permisos(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def normalizar_username(username: str) -> str:
    return username.strip().lower()


def normalizar_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def crear_tenant(nombre: str, slug: Optional[str] = None, cliente_id: int = 1) -> Dict[str, Any]:
    db = get_db()
    cursor = db.cursor()
    tenant_slug = normalizar_slug(slug or nombre)
    cursor.execute("SELECT id FROM tenants WHERE slug = ?", (tenant_slug,))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="El slug ya existe")
    cursor.execute("INSERT INTO tenants (nombre, slug, cliente_id) VALUES (?, ?, ?)", (nombre, tenant_slug, cliente_id))
    tenant_id = cursor.lastrowid
    db.commit()
    db.close()
    return {"id": tenant_id, "nombre": nombre, "slug": tenant_slug}


def crear_usuario_para_tenant(tenant_id: int, username: str, password: str, nombre: str, rol: str, permisos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM usuarios WHERE lower(username) = ? AND tenant_id = ?", (normalizar_username(username), tenant_id))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="El nombre de usuario ya existe en este parqueadero")
    cursor.execute(
        "INSERT INTO usuarios (username, password, nombre, rol, permisos, tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
        (normalizar_username(username), hash_password(password), nombre, rol, json.dumps(permisos or {}), tenant_id),
    )
    user_id = cursor.lastrowid
    db.commit()
    db.close()
    return {"id": user_id, "username": normalizar_username(username), "tenant_id": tenant_id}


def autenticar_usuario(username: str, password: str, tenant_slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
    db = get_db()
    cursor = db.cursor()
    if tenant_slug:
        cursor.execute(
            "SELECT u.id, u.username, u.password, u.nombre, u.rol, u.permisos, u.tenant_id, t.slug as tenant_slug, t.nombre as tenant_nombre, t.cliente_id FROM usuarios u JOIN tenants t ON t.id = u.tenant_id WHERE lower(u.username) = ? AND t.slug = ?",
            (normalizar_username(username), normalizar_slug(tenant_slug)),
        )
    else:
        cursor.execute(
            "SELECT u.id, u.username, u.password, u.nombre, u.rol, u.permisos, u.tenant_id, t.slug as tenant_slug, t.nombre as tenant_nombre, t.cliente_id FROM usuarios u JOIN tenants t ON t.id = u.tenant_id WHERE lower(u.username) = ?",
            (normalizar_username(username),),
        )
    u = cursor.fetchone()
    db.close()
    if not u or not verificar_password(password, u["password"]):
        return None
    return {
        "id": u["id"],
        "username": u["username"],
        "nombre": u["nombre"],
        "rol": u["rol"],
        "permisos": parse_permisos(u["permisos"]),
        "tenant_id": u["tenant_id"],
        "tenant_slug": u["tenant_slug"],
        "tenant_nombre": u["tenant_nombre"],
        "cliente_id": u["cliente_id"],
    }


def crear_token(usuario_id: int, username: str, tenant_slug: Optional[str] = None) -> str:
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(usuario_id),
        "username": username,
        "tenant_slug": tenant_slug or "",
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(hours=ACCESS_TOKEN_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decodificar_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    token = None
    if credentials and credentials.credentials:
        token = credentials.credentials
    else:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=401, detail="No hay sesión activa")

    try:
        payload = decodificar_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")

    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT u.id, u.username, u.nombre, u.rol, u.permisos, u.tenant_id, t.slug as tenant_slug, t.nombre as tenant_nombre, t.cliente_id FROM usuarios u JOIN tenants t ON t.id = u.tenant_id WHERE u.id = ?",
        (int(payload.get("sub", 0)),),
    )
    row = cursor.fetchone()
    db.close()

    if not row:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")

    return {
        "id": row["id"],
        "username": row["username"],
        "nombre": row["nombre"],
        "rol": row["rol"],
        "permisos": parse_permisos(row["permisos"]),
        "tenant_id": row["tenant_id"],
        "tenant_slug": row["tenant_slug"],
        "tenant_nombre": row["tenant_nombre"],
        "cliente_id": row["cliente_id"],
    }


def require_admin(current_user: dict = Depends(get_current_user)):
    if current_user.get("rol") != "Admin":
        raise HTTPException(status_code=403, detail="Acceso restringido al administrador")
    return current_user


@app.get("/")
def root():
    return {"mensaje": "API de parqueadero lista", "health": "/health"}


@app.get("/admin/backup")
def backup_manual(current_user: dict = Depends(get_current_user)):
    """Backup manual de la BD (solo superadmin)"""
    if current_user.get("cliente_id") != 1:
        raise HTTPException(status_code=403, detail="Solo superadmin")
    backup_automatico()
    auditar("BACKUP_MANUAL", current_user["username"], current_user.get("tenant_id", 0))
    return {"mensaje": "Backup creado correctamente"}


@app.get("/admin/auditoria")
def ver_auditoria(current_user: dict = Depends(get_current_user)):
    """Ver log de auditoría (solo superadmin)"""
    if current_user.get("cliente_id") != 1:
        raise HTTPException(status_code=403, detail="Solo superadmin")
    log_file = os.path.join(os.path.dirname(DB_FILE), "auditoria.log")
    if not os.path.exists(log_file):
        return {"logs": []}
    with open(log_file, "r", encoding="utf-8") as f:
        lineas = f.readlines()[-200:]  # Últimas 200 líneas
    return {"logs": [l.strip() for l in lineas]} 


# ═══════════════════════════════════════════════════════════════
# TARIFAS CONFIGURABLES POR CLIENTE
# ═══════════════════════════════════════════════════════════════

@app.get("/admin/tarifas/{cliente_id}")
def obtener_tarifas_cliente(cliente_id: int, current_user: dict = Depends(get_current_user)):
    """Obtener tarifas configuradas para un cliente (superadmin o el propio cliente)"""
    if current_user.get("cliente_id") != 1 and current_user.get("cliente_id") != cliente_id:
        raise HTTPException(status_code=403, detail="Acceso restringido")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, tipo, valor_hora, valor_mensualidad FROM tarifas_config WHERE cliente_id = ? ORDER BY id", (cliente_id,))
    tarifas = cursor.fetchall()
    db.close()
    return [dict(t) for t in tarifas]


@app.put("/admin/tarifas/{cliente_id}")
def actualizar_tarifas_cliente(cliente_id: int, payload: dict, current_user: dict = Depends(get_current_user)):
    """Actualizar tarifas de un cliente. Superadmin puede todos, admin solo el suyo."""
    if current_user.get("cliente_id") != 1 and current_user.get("cliente_id") != cliente_id:
        raise HTTPException(status_code=403, detail="No tienes permiso para modificar estas tarifas")
    db = get_db()
    cursor = db.cursor()
    tarifas = payload.get("tarifas", [])
    for t in tarifas:
        cursor.execute("UPDATE tarifas_config SET valor_hora = ?, valor_mensualidad = ? WHERE id = ? AND cliente_id = ?",
                       (t.get("valor_hora", 0), t.get("valor_mensualidad", 0), t.get("id"), cliente_id))
    db.commit()
    db.close()
    auditar("TARIFAS_ACTUALIZADAS", current_user["username"], 0, f"cliente={cliente_id}")
    return {"mensaje": "Tarifas actualizadas correctamente"}


@app.get("/mis-tarifas/")
def obtener_mis_tarifas(current_user: dict = Depends(get_current_user)):
    """El cliente ve sus propias tarifas"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT tipo, valor_hora, valor_mensualidad FROM tarifas_config WHERE cliente_id = ? ORDER BY id", (current_user.get("cliente_id"),))
    tarifas = cursor.fetchall()
    db.close()
    return [dict(t) for t in tarifas]


@app.get("/health")
def health():
    return {"status": "ok", "service": "parqueadero-api"}


@app.get("/me/")
def obtener_me(current_user: dict = Depends(get_current_user)):
    # Agregar features del cliente
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT features FROM clientes WHERE id = ?", (current_user.get("cliente_id"),))
    row = cursor.fetchone()
    db.close()
    result = dict(current_user)
    result["cliente_features"] = parse_permisos(row["features"]) if row else {}
    return result


# ── Endpoints de Clientes (Organizaciones) ──

@app.get("/clientes/")
def listar_clientes(current_user: dict = Depends(get_current_user)):
    if current_user.get("cliente_id") != 1:
        raise HTTPException(status_code=403, detail="Solo el administrador principal puede gestionar clientes")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, nombre, slug, activo, features FROM clientes ORDER BY id")
    clientes = cursor.fetchall()
    db.close()
    return [dict(c) for c in clientes]


@app.post("/clientes/")
def crear_cliente(cliente: ClienteCreate, current_user: dict = Depends(get_current_user)):
    if current_user.get("cliente_id") != 1:
        raise HTTPException(status_code=403, detail="Solo el administrador principal puede crear clientes")
    db = get_db()
    cursor = db.cursor()
    slug = normalizar_slug(cliente.slug or cliente.nombre)
    cursor.execute("SELECT id FROM clientes WHERE slug = ?", (slug,))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="El slug ya existe")

    # 1. Crear el cliente (organización)
    cursor.execute("INSERT INTO clientes (nombre, slug) VALUES (?, ?)", (cliente.nombre, slug))
    cliente_id = cursor.lastrowid

    # 2. Crear parqueadero por defecto para este cliente
    parqueadero_nombre = cliente.parqueadero_nombre or cliente.nombre
    parqueadero_slug = normalizar_slug(parqueadero_nombre)
    cursor.execute("INSERT INTO tenants (nombre, slug, cliente_id) VALUES (?, ?, ?)", (parqueadero_nombre, parqueadero_slug, cliente_id))
    tenant_id = cursor.lastrowid

    # 3. Crear usuario admin para este cliente (username único global)
    admin_username = normalizar_username(cliente.admin_username)
    # Si el username ya existe, agregar el slug como prefijo
    cursor.execute("SELECT id FROM usuarios WHERE lower(username) = ?", (admin_username,))
    if cursor.fetchone():
        admin_username = f"{admin_username}_{slug}"
    cursor.execute(
        "INSERT INTO usuarios (username, password, nombre, rol, permisos, tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
        (admin_username, hash_password(cliente.admin_password), cliente.admin_nombre, "Admin", "{}", tenant_id),
    )

    # 4. Crear tarifa por defecto
    cursor.execute("INSERT INTO tarifas (tipo, valor, tenant_id) VALUES (?, ?, ?)", ("hora", 2000.0, tenant_id))

    # 5. Copiar tarifas_config desde el cliente principal (cliente_id=1) como plantilla
    cursor.execute("SELECT tipo, valor_hora, valor_mensualidad FROM tarifas_config WHERE cliente_id = 1")
    tarifas_default = cursor.fetchall()
    for t in tarifas_default:
        cursor.execute("INSERT INTO tarifas_config (cliente_id, tipo, valor_hora, valor_mensualidad) VALUES (?, ?, ?, ?)",
                       (cliente_id, t["tipo"], t["valor_hora"], t["valor_mensualidad"]))

    db.commit()
    db.close()
    return {
        "id": cliente_id,
        "nombre": cliente.nombre,
        "slug": slug,
        "tenant_id": tenant_id,
        "tenant_slug": parqueadero_slug,
        "admin_username": admin_username,
    }


@app.delete("/clientes/{cliente_id}")
def eliminar_cliente(cliente_id: int, current_user: dict = Depends(get_current_user)):
    if current_user.get("cliente_id") != 1:
        raise HTTPException(status_code=403, detail="Solo el superadmin puede eliminar clientes")
    if cliente_id == 1:
        raise HTTPException(status_code=400, detail="No puedes eliminar el cliente principal")
    db = get_db()
    cursor = db.cursor()
    # Desactivar cliente y todos sus tenants — los registros históricos NUNCA se borran
    cursor.execute("UPDATE tenants SET activo = 0 WHERE cliente_id = ?", (cliente_id,))
    cursor.execute("UPDATE clientes SET activo = 0 WHERE id = ?", (cliente_id,))
    db.commit()
    db.close()
    return {"mensaje": "Cliente desactivado correctamente (todos los registros históricos se conservan)"}


@app.get("/clientes/{cliente_id}/features")
def obtener_features_cliente(cliente_id: int, current_user: dict = Depends(get_current_user)):
    """Devuelve las features personalizadas de un cliente"""
    if current_user.get("cliente_id") != 1 and current_user.get("cliente_id") != cliente_id:
        raise HTTPException(status_code=403, detail="Acceso restringido")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT features FROM clientes WHERE id = ?", (cliente_id,))
    row = cursor.fetchone()
    db.close()
    if not row:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return {"cliente_id": cliente_id, "features": parse_permisos(row["features"])}


@app.put("/clientes/{cliente_id}/features")
def actualizar_features_cliente(cliente_id: int, payload: dict, current_user: dict = Depends(get_current_user)):
    """Actualiza las features personalizadas de un cliente (solo superadmin)"""
    if current_user.get("cliente_id") != 1:
        raise HTTPException(status_code=403, detail="Solo el superadmin puede modificar features")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE clientes SET features = ? WHERE id = ?",
                   (json.dumps(payload.get("features", {})), cliente_id))
    db.commit()
    db.close()
    return {"mensaje": "Features actualizadas correctamente"}


@app.put("/clientes/{cliente_id}")
def editar_cliente(cliente_id: int, data: ClienteUpdate, current_user: dict = Depends(get_current_user)):
    """Editar datos de un cliente. Superadmin puede editar cualquiera; admin solo el suyo."""
    # Superadmin (cliente_id=1) puede editar cualquier cliente
    # Admin normal solo puede editar su propio cliente
    if current_user.get("cliente_id") != 1 and current_user.get("cliente_id") != cliente_id:
        raise HTTPException(status_code=403, detail="No tienes permiso para editar este cliente")

    db = get_db()
    cursor = db.cursor()

    # Verificar que el cliente existe
    cursor.execute("SELECT id, nombre, slug FROM clientes WHERE id = ?", (cliente_id,))
    row = cursor.fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Cliente no encontrado")

    campos = []
    valores = []
    if data.nombre is not None and data.nombre.strip():
        campos.append("nombre = ?")
        valores.append(data.nombre.strip())
    if data.slug is not None and data.slug.strip():
        slug = normalizar_slug(data.slug)
        # Verificar que el slug no lo tenga otro cliente
        cursor.execute("SELECT id FROM clientes WHERE slug = ? AND id != ?", (slug, cliente_id))
        if cursor.fetchone():
            db.close()
            raise HTTPException(status_code=400, detail="El slug ya está en uso por otro cliente")
        campos.append("slug = ?")
        valores.append(slug)

    if not campos:
        db.close()
        return {"mensaje": "Sin cambios"}

    valores.append(cliente_id)
    cursor.execute(f"UPDATE clientes SET {', '.join(campos)} WHERE id = ?", valores)
    db.commit()
    db.close()
    auditar("CLIENTE_EDITADO", current_user["username"], 0, f"cliente={cliente_id}")
    return {"mensaje": "Cliente actualizado correctamente"}


@app.get("/mi-cliente/")
def obtener_mi_cliente(current_user: dict = Depends(get_current_user)):
    """Devuelve los datos del cliente al que pertenece el usuario actual"""
    cliente_id = current_user.get("cliente_id")
    if not cliente_id:
        raise HTTPException(status_code=404, detail="No perteneces a ningún cliente")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, nombre, slug, features, creado_en FROM clientes WHERE id = ?", (cliente_id,))
    row = cursor.fetchone()
    db.close()
    if not row:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return {
        "id": row["id"],
        "nombre": row["nombre"],
        "slug": row["slug"],
        "features": parse_permisos(row["features"]),
        "creado_en": row["creado_en"],
    }


@app.put("/mi-perfil/")
def editar_mi_perfil(data: MiPerfilUpdate, current_user: dict = Depends(get_current_user)):
    """Permite al admin editar su propio perfil y los datos de su organización"""
    if current_user.get("rol") != "Admin":
        raise HTTPException(status_code=403, detail="Solo el administrador puede editar el perfil")

    db = get_db()
    cursor = db.cursor()
    user_id = current_user["id"]
    cliente_id = current_user.get("cliente_id")

    # --- Actualizar datos del usuario ---
    campos_user = []
    valores_user = []

    if data.nombre is not None and data.nombre.strip():
        campos_user.append("nombre = ?")
        valores_user.append(data.nombre.strip())

    if data.username is not None and data.username.strip():
        nuevo_username = normalizar_username(data.username)
        # Verificar que no exista otro usuario con ese username en el mismo tenant
        cursor.execute("SELECT id FROM usuarios WHERE lower(username) = ? AND tenant_id = ? AND id != ?",
                       (nuevo_username, current_user.get("tenant_id"), user_id))
        if cursor.fetchone():
            db.close()
            raise HTTPException(status_code=400, detail="El nombre de usuario ya está en uso")
        campos_user.append("username = ?")
        valores_user.append(nuevo_username)

    if data.password is not None and data.password.strip():
        campos_user.append("password = ?")
        valores_user.append(hash_password(data.password.strip()))

    if campos_user:
        valores_user.append(user_id)
        cursor.execute(f"UPDATE usuarios SET {', '.join(campos_user)} WHERE id = ?", valores_user)

    # --- Actualizar datos del cliente/organización ---
    if cliente_id and cliente_id != 1:  # El superadmin no edita "Cliente Principal"
        campos_cliente = []
        valores_cliente = []

        if data.cliente_nombre is not None and data.cliente_nombre.strip():
            campos_cliente.append("nombre = ?")
            valores_cliente.append(data.cliente_nombre.strip())

        if data.cliente_slug is not None and data.cliente_slug.strip():
            slug = normalizar_slug(data.cliente_slug)
            cursor.execute("SELECT id FROM clientes WHERE slug = ? AND id != ?", (slug, cliente_id))
            if cursor.fetchone():
                db.close()
                raise HTTPException(status_code=400, detail="El slug ya está en uso por otro cliente")
            campos_cliente.append("slug = ?")
            valores_cliente.append(slug)

        if campos_cliente:
            valores_cliente.append(cliente_id)
            cursor.execute(f"UPDATE clientes SET {', '.join(campos_cliente)} WHERE id = ?", valores_cliente)

    db.commit()
    db.close()

    auditar("PERFIL_EDITADO", current_user["username"], 0, f"cliente={cliente_id}")
    return {
        "mensaje": "Perfil actualizado correctamente",
        "username": data.username if data.username else current_user["username"],
        "cliente_nombre": data.cliente_nombre,
    }


# ── Endpoints de Tenants (Parqueaderos) ──

@app.get("/tenants/")
def listar_tenants(current_user: dict = Depends(get_current_user)):
    if current_user.get("rol") != "Admin":
        raise HTTPException(status_code=403, detail="Acceso restringido")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, nombre, slug, activo FROM tenants WHERE cliente_id = ? ORDER BY id", (current_user.get("cliente_id"),))
    tenants = cursor.fetchall()
    db.close()
    return [dict(t) for t in tenants]


@app.post("/tenants/")
def crear_tenant_api(tenant: TenantCreate, current_user: dict = Depends(get_current_user)):
    if not isinstance(current_user, dict) or current_user.get("rol") != "Admin":
        raise HTTPException(status_code=403, detail="Acceso restringido")
    return crear_tenant(tenant.nombre, tenant.slug, current_user.get("cliente_id"))


@app.post("/tenants/{tenant_id}/usuarios/")
def crear_usuario_en_tenant(tenant_id: int, usuario: TenantUserCreate, current_user: dict = Depends(get_current_user)):
    if current_user.get("rol") != "Admin":
        raise HTTPException(status_code=403, detail="Acceso restringido")
    # Verificar que el tenant pertenezca al mismo cliente
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT cliente_id FROM tenants WHERE id = ?", (tenant_id,))
    row = cursor.fetchone()
    db.close()
    if not row or row["cliente_id"] != current_user.get("cliente_id"):
        raise HTTPException(status_code=403, detail="No tienes acceso a este parqueadero")
    return crear_usuario_para_tenant(tenant_id, usuario.username, usuario.password, usuario.nombre, usuario.rol, usuario.permisos)


@app.delete("/tenants/{tenant_id}")
def eliminar_tenant(tenant_id: int, current_user: dict = Depends(get_current_user)):
    if current_user.get("rol") != "Admin":
        raise HTTPException(status_code=403, detail="Acceso restringido")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT cliente_id FROM tenants WHERE id = ?", (tenant_id,))
    row = cursor.fetchone()
    if not row or row["cliente_id"] != current_user.get("cliente_id"):
        db.close()
        raise HTTPException(status_code=403, detail="No tienes acceso a este parqueadero")
    cursor.execute("SELECT tenant_id FROM usuarios WHERE id = ?", (current_user["id"],))
    mi_tenant = cursor.fetchone()
    if mi_tenant and mi_tenant["tenant_id"] == tenant_id:
        db.close()
        raise HTTPException(status_code=400, detail="No puedes eliminar tu propio parqueadero")
    # Desactivar en vez de borrar — los registros históricos se conservan
    cursor.execute("UPDATE tenants SET activo = 0 WHERE id = ?", (tenant_id,))
    db.commit()
    db.close()
    return {"mensaje": "Parqueadero desactivado correctamente (registros conservados)"}


@app.post("/login/")
def login(user: LoginModel, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    username = normalizar_username(user.username)
    password = user.password.strip()

    # Rate limiting + bloqueo
    verificar_rate_limit(ip)
    verificar_intentos(username, ip)

    tenant_slug = None
    if "@" in user.username:
        username, tenant_slug = user.username.split("@", 1)
        username = normalizar_username(username)
        tenant_slug = normalizar_slug(tenant_slug)

    usuario = autenticar_usuario(username, password, tenant_slug)
    if not usuario:
        registrar_intento_fallido(username, ip)
        auditar("LOGIN_FALLIDO", username, 0, f"IP={ip}")
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")

    auditar("LOGIN_EXITOSO", usuario["username"], usuario.get("tenant_id", 0), f"IP={ip}")

    token = crear_token(usuario["id"], usuario["username"], usuario["tenant_slug"])
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=ENVIRONMENT == "production" or os.getenv("HTTPS", "") == "1",
        path="/",
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "id": usuario["id"],
        "username": usuario["username"],
        "nombre": usuario["nombre"],
        "rol": usuario["rol"],
        "permisos": usuario["permisos"],
        "tenant_id": usuario["tenant_id"],
        "tenant_slug": usuario["tenant_slug"],
        "tenant_nombre": usuario["tenant_nombre"],
        "cliente_id": usuario["cliente_id"],
    }


@app.post("/logout/")
def logout(response: Response):
    response.delete_cookie(key="access_token", path="/")
    return {"mensaje": "Sesión cerrada"}


@app.get("/usuarios/", dependencies=[Depends(require_admin)])
def listar_usuarios(current_user: dict = Depends(get_current_user)):
    tenant_id = current_user.get("tenant_id")
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, username, nombre, rol, permisos FROM usuarios WHERE tenant_id = ? ORDER BY id", (tenant_id,))
    usuarios = cursor.fetchall()
    db.close()
    return [
        {
            "id": u["id"],
            "username": u["username"],
            "nombre": u["nombre"],
            "rol": u["rol"],
            "permisos": parse_permisos(u["permisos"]),
        }
        for u in usuarios
    ]


@app.post("/usuarios/", dependencies=[Depends(require_admin)])
def crear_usuario(usuario: UsuarioCreate, current_user: dict = Depends(get_current_user)):
    username = normalizar_username(usuario.username)
    password = usuario.password.strip()
    if not password:
        raise HTTPException(status_code=400, detail="La contraseña es obligatoria para crear un usuario")

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM usuarios WHERE lower(username) = ? AND tenant_id = ?", (username, current_user.get("tenant_id")))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="El nombre de usuario ya existe")

    permisos = json.dumps(usuario.permisos or {})
    cursor.execute(
        "INSERT INTO usuarios (username, password, nombre, rol, permisos, tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
        (username, hash_password(password), usuario.nombre, usuario.rol, permisos, current_user.get("tenant_id")),
    )
    db.commit()
    db.close()
    return {"mensaje": "Usuario creado correctamente"}


@app.put("/usuarios/{usuario_id}", dependencies=[Depends(require_admin)])
def actualizar_usuario(usuario_id: int, usuario: UsuarioUpdate, current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()

    campos = []
    valores = []
    if usuario.username is not None:
        campos.append("username = ?")
        valores.append(normalizar_username(usuario.username))
    if usuario.nombre is not None:
        campos.append("nombre = ?")
        valores.append(usuario.nombre)
    if usuario.rol is not None:
        campos.append("rol = ?")
        valores.append(usuario.rol)
    if usuario.password is not None:
        password = usuario.password.strip()
        if password:
            campos.append("password = ?")
            valores.append(hash_password(password))
        # Si la contraseña llega vacía no se modifica
    if usuario.permisos is not None:
        campos.append("permisos = ?")
        valores.append(json.dumps(usuario.permisos))

    if not campos:
        db.close()
        raise HTTPException(status_code=400, detail="No hay campos para actualizar")

    valores.extend([usuario_id, current_user.get("tenant_id")])
    cursor.execute(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = ? AND tenant_id = ?", valores)
    if cursor.rowcount == 0:
        db.close()
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    db.commit()
    db.close()
    return {"mensaje": "Usuario actualizado correctamente"}


@app.put("/usuarios/{usuario_id}/permisos/", dependencies=[Depends(require_admin)])
def actualizar_permisos_usuario(usuario_id: int, payload: PermisosUpdate, current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("UPDATE usuarios SET permisos = ? WHERE id = ? AND tenant_id = ?", (json.dumps(payload.permisos), usuario_id, current_user.get("tenant_id")))
    if cursor.rowcount == 0:
        db.close()
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    db.commit()
    db.close()
    return {"mensaje": "Permisos actualizados correctamente"}


@app.delete("/usuarios/{usuario_id}", dependencies=[Depends(require_admin)])
def eliminar_usuario(usuario_id: int, current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM usuarios WHERE id = ? AND tenant_id = ?", (usuario_id, current_user.get("tenant_id")))
    if cursor.rowcount == 0:
        db.close()
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    db.commit()
    db.close()
    return {"mensaje": "Usuario eliminado correctamente"}


@app.post("/clientes-mensuales/")
def registrar_cliente_mensual(cliente: ClienteMensual, current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()

    try:
        placa = cliente.placa_vehiculo.upper().strip()
        # Obtener valor de mensualidad desde tarifas_config si no se envió
        valor = cliente.valor_mensualidad
        if valor <= 0:
            cursor.execute("SELECT valor_mensualidad FROM tarifas_config WHERE cliente_id = ? AND tipo = ?",
                           (current_user.get("cliente_id"), cliente.tipo_vehiculo))
            row = cursor.fetchone()
            if row:
                valor = row["valor_mensualidad"]

        cursor.execute('''
            INSERT INTO clientes_mensuales (nombre, telefono, placa, tipo_vehiculo, valor_mensualidad, fecha_inicio, fecha_fin, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (cliente.nombre, cliente.telefono, placa, cliente.tipo_vehiculo, valor, cliente.fecha_inicio, cliente.fecha_fin, current_user.get("tenant_id")))
        db.commit()
        return {"mensaje": "Cliente mensual registrado correctamente", "placa": placa, "valor": valor}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Esta placa ya está registrada como cliente mensual.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/clientes-mensuales/")
def listar_clientes_mensuales(current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM clientes_mensuales WHERE tenant_id = ? ORDER BY id DESC", (current_user.get("tenant_id"),))
    clientes = cursor.fetchall()
    db.close()
    return [dict(c) for c in clientes]


@app.put("/clientes-mensuales/{placa}")
def editar_cliente_mensual(placa: str, cliente: ClienteMensualUpdate, current_user: dict = Depends(get_current_user)):
    placa_normalizada = placa.upper().strip()
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        UPDATE clientes_mensuales
        SET nombre = ?, telefono = ?, tipo_vehiculo = ?, valor_mensualidad = ?, fecha_inicio = ?, fecha_fin = ?
        WHERE placa = ? AND tenant_id = ?
    ''', (cliente.nombre, cliente.telefono, cliente.tipo_vehiculo, cliente.valor_mensualidad, cliente.fecha_inicio, cliente.fecha_fin, placa_normalizada, current_user.get("tenant_id")))

    if cursor.rowcount == 0:
        db.close()
        raise HTTPException(status_code=404, detail="Cliente mensual no encontrado")

    db.commit()
    db.close()
    return {"mensaje": "Cliente mensual actualizado correctamente", "placa": placa_normalizada}


@app.delete("/clientes-mensuales/{placa}")
def eliminar_cliente_mensual(placa: str, current_user: dict = Depends(get_current_user)):
    placa_normalizada = placa.upper().strip()
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM clientes_mensuales WHERE placa = ? AND tenant_id = ?", (placa_normalizada, current_user.get("tenant_id")))

    if cursor.rowcount == 0:
        db.close()
        raise HTTPException(status_code=404, detail="Cliente mensual no encontrado")

    db.commit()
    db.close()
    return {"mensaje": "Cliente mensual eliminado correctamente", "placa": placa_normalizada}


@app.post("/convertir-mensual/")
def convertir_a_mensual(data: ConvertirMensual, current_user: dict = Depends(get_current_user)):
    """Convierte un vehículo diario activo a cliente mensual"""
    placa = data.placa.strip().upper()
    db = get_db()
    cursor = db.cursor()

    # Verificar que el vehículo está activo
    cursor.execute("SELECT id, tipo_cliente FROM registros WHERE placa = ? AND estado = 'Activo' AND tenant_id = ?",
                   (placa, current_user.get("tenant_id")))
    registro = cursor.fetchone()
    if not registro:
        db.close()
        raise HTTPException(status_code=404, detail="Vehículo no encontrado como activo")

    # Crear o actualizar cliente mensual
    valor = data.valor_mensualidad
    if valor <= 0:
        cursor.execute("SELECT valor_mensualidad FROM tarifas_config WHERE cliente_id = ? AND tipo = ?",
                       (current_user.get("cliente_id"), data.tipo_vehiculo))
        row = cursor.fetchone()
        if row:
            valor = row["valor_mensualidad"]

    cursor.execute("SELECT id FROM clientes_mensuales WHERE placa = ? AND tenant_id = ?",
                   (placa, current_user.get("tenant_id")))
    if cursor.fetchone():
        cursor.execute("UPDATE clientes_mensuales SET nombre=?, telefono=?, tipo_vehiculo=?, valor_mensualidad=?, fecha_inicio=?, fecha_fin=? WHERE placa=? AND tenant_id=?",
                       (data.nombre, data.telefono, data.tipo_vehiculo, valor, data.fecha_inicio, data.fecha_fin, placa, current_user.get("tenant_id")))
    else:
        cursor.execute("INSERT INTO clientes_mensuales (nombre, telefono, placa, tipo_vehiculo, valor_mensualidad, fecha_inicio, fecha_fin, tenant_id) VALUES (?,?,?,?,?,?,?,?)",
                       (data.nombre, data.telefono, placa, data.tipo_vehiculo, valor, data.fecha_inicio, data.fecha_fin, current_user.get("tenant_id")))

    # Actualizar el registro activo a tipo Mensual
    cursor.execute("UPDATE registros SET tipo_cliente = 'Mensual' WHERE id = ?", (registro["id"],))
    db.commit()
    db.close()
    return {"mensaje": f"Vehículo {placa} convertido a cliente mensual", "placa": placa, "valor": valor}


@app.post("/gastos/")
def registrar_gasto(data: GastoCreate, current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("INSERT INTO gastos (descripcion, monto, categoria, tenant_id, fecha) VALUES (?,?,?,?, datetime('now','localtime'))",
                   (data.descripcion, data.monto, data.categoria, current_user.get("tenant_id")))
    db.commit()
    gasto_id = cursor.lastrowid
    db.close()
    return {"id": gasto_id, "mensaje": "Gasto registrado"}


@app.get("/gastos/")
def listar_gastos(current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM gastos WHERE tenant_id = ? ORDER BY fecha DESC", (current_user.get("tenant_id"),))
    gastos = [dict(g) for g in cursor.fetchall()]
    db.close()
    return gastos


@app.delete("/gastos/{gasto_id}")
def eliminar_gasto(gasto_id: int, current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("DELETE FROM gastos WHERE id = ? AND tenant_id = ?", (gasto_id, current_user.get("tenant_id")))
    db.commit()
    db.close()
    return {"mensaje": "Gasto eliminado"}


@app.get("/contabilidad/")
def resumen_contabilidad(current_user: dict = Depends(get_current_user)):
    """Resumen de ingresos vs gastos para el tenant actual"""
    db = get_db()
    cursor = db.cursor()
    tid = current_user.get("tenant_id")

    # Ingresos: suma de total_pagado en registros finalizados
    cursor.execute("SELECT COALESCE(SUM(total_pagado),0) FROM registros WHERE tenant_id=? AND estado='Finalizado'", (tid,))
    ingresos = cursor.fetchone()[0]

    # Ingresos por mensualidades (suma de valor_mensualidad de clientes activos)
    cursor.execute("SELECT COALESCE(SUM(valor_mensualidad),0) FROM clientes_mensuales WHERE tenant_id=?", (tid,))
    ingresos_mensuales = cursor.fetchone()[0]

    # Gastos
    cursor.execute("SELECT COALESCE(SUM(monto),0) FROM gastos WHERE tenant_id=?", (tid,))
    gastos = cursor.fetchone()[0]

    db.close()
    return {
        "ingresos_parqueadero": ingresos,
        "ingresos_mensualidades": ingresos_mensuales,
        "ingresos_totales": ingresos + ingresos_mensuales,
        "gastos": gastos,
        "balance": (ingresos + ingresos_mensuales) - gastos,
    }


def detectar_tipo_vehiculo(placa: str) -> str:
    """
    Detecta si una placa es de moto o carro según formato colombiano.
    - Moto: 3 letras + 2 números + 1 letra (ABC12D) o 2 letras + 4 números (AB1234) o 1 letra + 5 números (A12345)
    - Carro: 3 letras + 3 números (ABC123) o 3 letras + 4 números (ABC1234)
    - Si no se puede determinar, asume Automovil
    """
    placa = re.sub(r'[^A-Z0-9]', '', placa.upper().strip())
    if not placa:
        return "Automovil"

    # Moto: 2 letras + 4 números (AB1234) - formato más común de moto
    if re.match(r'^[A-Z]{2}[0-9]{4}$', placa):
        return "Moto"
    # Moto: 1 letra + 5 números (A12345) - moto antigua
    if re.match(r'^[A-Z][0-9]{5}$', placa):
        return "Moto"
    # Moto: 3 letras + 2 números + 1 letra (ABC12D) - nuevo formato
    if re.match(r'^[A-Z]{3}[0-9]{2}[A-Z]$', placa):
        return "Moto"
    # Carro: 3 letras + 3 números (ABC123)
    if re.match(r'^[A-Z]{3}[0-9]{3}$', placa):
        return "Automovil"
    # Carro: 3 letras + 4 números (ABC1234) - nuevo formato
    if re.match(r'^[A-Z]{3}[0-9]{4}$', placa):
        return "Automovil"

    return "Automovil"  # default


@app.post("/ingreso/")
def registrar_ingreso(data: RegistroIngreso, current_user: dict = Depends(get_current_user)):
    placa = data.placa.strip().upper()
    hora_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id FROM registros WHERE placa = ? AND estado = 'Activo' AND tenant_id = ?", (placa, current_user.get("tenant_id")))
    if cursor.fetchone():
        db.close()
        raise HTTPException(status_code=400, detail="El vehículo ya está dentro")

    cursor.execute("SELECT * FROM clientes_mensuales WHERE placa = ? AND fecha_inicio <= ? AND fecha_fin >= ? AND tenant_id = ?", (placa, fecha_hoy, fecha_hoy, current_user.get("tenant_id")))
    mensual = cursor.fetchone()
    tipo_cliente = "Mensual" if mensual else data.tipo_cliente

    # Detectar tipo de vehículo por formato de placa
    if mensual:
        tipo_vehiculo = mensual["tipo_vehiculo"]
    elif data.tipo_vehiculo and data.tipo_vehiculo != "Automovil":
        tipo_vehiculo = data.tipo_vehiculo  # El usuario ya especificó
    else:
        tipo_vehiculo = detectar_tipo_vehiculo(placa)

    cursor.execute("INSERT INTO registros (placa, hora_ingreso, tipo_cliente, estado, tenant_id, tipo_vehiculo) VALUES (?, ?, ?, 'Activo', ?, ?)", (placa, hora_actual, tipo_cliente, current_user.get("tenant_id"), tipo_vehiculo))
    registro_id = cursor.lastrowid
    db.commit()
    db.close()

    # Datos para el ticket de ingreso
    return {
        "mensaje": "Ingreso registrado",
        "placa": placa,
        "hora_ingreso": hora_actual,
        "tipo_cliente": tipo_cliente,
        "tipo_vehiculo": tipo_vehiculo,
        "id": registro_id,
        "es_mensual": tipo_cliente == "Mensual",
    }


@app.post("/salida/")
def registrar_salida(data: RegistroSalida, current_user: dict = Depends(get_current_user)):
    placa = data.placa.strip().upper()
    hora_salida_dt = datetime.now()
    hora_salida_str = hora_salida_dt.strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT * FROM registros WHERE placa = ? AND estado = 'Activo' AND tenant_id = ?", (placa, current_user.get("tenant_id")))
    registro = cursor.fetchone()
    if not registro:
        db.close()
        raise HTTPException(status_code=404, detail="Vehículo no registrado adentro")

    hora_ingreso_dt = datetime.strptime(registro["hora_ingreso"], "%Y-%m-%d %H:%M:%S")
    diferencia = hora_salida_dt - hora_ingreso_dt
    minutos_totales = math.ceil(diferencia.total_seconds() / 60)
    horas_a_cobrar = math.ceil(minutos_totales / 60)
    if horas_a_cobrar == 0:
        horas_a_cobrar = 1

    total_pagado = 0.0
    if registro["tipo_cliente"] != "Mensual":
        # Usar tarifa por defecto (Automovil) del cliente
        cursor.execute("SELECT valor_hora FROM tarifas_config WHERE cliente_id = ? AND tipo = 'Automovil'",
                       (current_user.get("cliente_id"),))
        tarifa = cursor.fetchone()
        valor_hora = tarifa["valor_hora"] if tarifa else 2000.0
        total_pagado = horas_a_cobrar * valor_hora

    cursor.execute("UPDATE registros SET hora_salida = ?, total_pagado = ?, estado = 'Finalizado' WHERE id = ? AND tenant_id = ?", (hora_salida_str, total_pagado, registro["id"], current_user.get("tenant_id")))
    db.commit()
    db.close()

    return {
        "placa": placa,
        "hora_ingreso": registro["hora_ingreso"],
        "hora_salida": hora_salida_str,
        "tiempo_permanencia_minutos": minutos_totales,
        "horas_transcurridas": horas_a_cobrar,
        "monto_cobrado": "Mensual" if registro["tipo_cliente"] == "Mensual" else total_pagado,
        "tipo_cliente": registro["tipo_cliente"],
    }


@app.get("/parqueados/")
def obtener_parqueados(current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT r.*, t.nombre as parqueadero_nombre FROM registros r JOIN tenants t ON t.id = r.tenant_id WHERE r.estado = 'Activo' AND r.tenant_id = ? ORDER BY r.id DESC",
        (current_user.get("tenant_id"),),
    )
    vehiculos = cursor.fetchall()
    db.close()
    return [dict(v) for v in vehiculos]


@app.get("/estadisticas/")
def obtener_estadisticas(current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("SELECT COUNT(*) FROM registros WHERE hora_ingreso LIKE ? AND tenant_id = ?", (f"{fecha_hoy}%", current_user.get("tenant_id")))
    total_hoy = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(total_pagado) FROM registros WHERE hora_salida LIKE ? AND tenant_id = ?", (f"{fecha_hoy}%", current_user.get("tenant_id")))
    recaudo_hoy = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT COUNT(*), SUM(total_pagado) FROM registros WHERE estado = 'Finalizado' AND tenant_id = ?", (current_user.get("tenant_id"),))
    historico = cursor.fetchone()

    cursor.execute("SELECT SUM(valor_mensualidad) FROM clientes_mensuales WHERE tenant_id = ?", (current_user.get("tenant_id"),))
    recaudo_mensualidades = cursor.fetchone()[0] or 0.0

    # Obtener nombre del parqueadero
    cursor.execute("SELECT nombre FROM tenants WHERE id = ?", (current_user.get("tenant_id"),))
    tenant_row = cursor.fetchone()

    db.close()
    return {
        "total_vehiculos_hoy": total_hoy,
        "recaudo_hoy": recaudo_hoy,
        "total_vehiculos_historico": historico[0] or 0,
        "recaudo_historico": (historico[1] or 0.0) + recaudo_mensualidades,
        "parqueadero_nombre": tenant_row["nombre"] if tenant_row else "",
    }


@app.get("/historial/")
def obtener_historial(fecha: str = None, tenant_id: int = None, current_user: dict = Depends(get_current_user)):
    db = get_db()
    cursor = db.cursor()
    if not fecha:
        fecha = datetime.now().strftime("%Y-%m-%d")

    # Si se especifica un tenant_id, verificar que pertenezca al mismo cliente
    filtro_tenant = current_user.get("tenant_id")
    if tenant_id:
        cursor.execute("SELECT cliente_id FROM tenants WHERE id = ?", (tenant_id,))
        row = cursor.fetchone()
        if row and row["cliente_id"] == current_user.get("cliente_id"):
            filtro_tenant = tenant_id

    cursor.execute(
        "SELECT r.*, t.nombre as parqueadero_nombre FROM registros r JOIN tenants t ON t.id = r.tenant_id WHERE r.tenant_id = ? AND (r.hora_ingreso LIKE ? OR r.hora_salida LIKE ?) ORDER BY r.id DESC",
        (filtro_tenant, f"{fecha}%", f"{fecha}%"),
    )
    registros = cursor.fetchall()
    db.close()
    return [dict(r) for r in registros]


@app.get("/mis-parqueaderos/")
def obtener_mis_parqueaderos(current_user: dict = Depends(get_current_user)):
    """Devuelve los parqueaderos del cliente actual para filtrar en el historial"""
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, nombre, slug FROM tenants WHERE cliente_id = ? ORDER BY id", (current_user.get("cliente_id"),))
    tenants = cursor.fetchall()
    db.close()
    return [dict(t) for t in tenants]


# ── Exportación a Excel ──

@app.get("/historial/exportar/")
def exportar_historial_excel(fecha: str = None, tenant_id: int = None, current_user: dict = Depends(get_current_user)):
    """Exporta el historial de registros a un archivo Excel (.xlsx)"""
    import io
    import openpyxl

    db = get_db()
    cursor = db.cursor()
    if not fecha:
        fecha = datetime.now().strftime("%Y-%m-%d")

    filtro_tenant = current_user.get("tenant_id")
    if tenant_id:
        cursor.execute("SELECT cliente_id FROM tenants WHERE id = ?", (tenant_id,))
        row = cursor.fetchone()
        if row and row["cliente_id"] == current_user.get("cliente_id"):
            filtro_tenant = tenant_id

    cursor.execute(
        "SELECT r.placa, r.hora_ingreso, r.hora_salida, r.tipo_cliente, r.estado, r.total_pagado, t.nombre as parqueadero "
        "FROM registros r JOIN tenants t ON t.id = r.tenant_id "
        "WHERE r.tenant_id = ? AND (r.hora_ingreso LIKE ? OR r.hora_salida LIKE ?) "
        "ORDER BY r.id DESC",
        (filtro_tenant, f"{fecha}%", f"{fecha}%"),
    )
    registros = cursor.fetchall()
    db.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Historial"
    ws.append(["Placa", "Ingreso", "Salida", "Tipo", "Estado", "Total Pagado", "Parqueadero"])

    for r in registros:
        ws.append([r["placa"], r["hora_ingreso"], r["hora_salida"] or "",
                    r["tipo_cliente"], r["estado"], r["total_pagado"] or 0, r["parqueadero"]])

    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 30)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=historial_{fecha}.xlsx"},
    )


# --- FRONTEND ESTÁTICO ---
# Servir archivos del frontend como rutas normales (más compatible que StaticFiles mount)
_frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
_frontend_dir = os.path.normpath(_frontend_dir)

@app.get("/app/")
@app.get("/app/{filename:path}")
async def servir_frontend(filename: str = "index.html"):
    file_path = os.path.join(_frontend_dir, filename)
    # Prevenir directory traversal
    if not os.path.normpath(file_path).startswith(os.path.normpath(_frontend_dir)):
        raise HTTPException(status_code=404)
    if not os.path.isfile(file_path):
        # Para rutas SPA, devolver index.html
        file_path = os.path.join(_frontend_dir, "index.html")
    content_type = "text/html"
    if filename.endswith(".css"):
        content_type = "text/css"
    elif filename.endswith(".js"):
        content_type = "application/javascript"
    elif filename.endswith(".json"):
        content_type = "application/json"
    elif filename.endswith(".png"):
        content_type = "image/png"
    elif filename.endswith(".ico"):
        content_type = "image/x-icon"
    try:
        with open(file_path, "rb") as f:
            return Response(content=f.read(), media_type=content_type)
    except FileNotFoundError:
        raise HTTPException(status_code=404)