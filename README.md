# Parqueadero Pro

Aplicación de gestión de parqueadero con backend FastAPI, frontend estático y flujo LPR.

## Despliegue con Docker

Para instalar el sistema completo en otro PC con Windows, consulta
[INSTALAR_PARQUEADEROPRO_OTRO_PC.md](INSTALAR_PARQUEADEROPRO_OTRO_PC.md).
Para distribuirlo como una aplicación con instalador `.exe`, consulta
[INSTALAR_PARQUEADEROPRO_COMO_APLICACION.md](INSTALAR_PARQUEADEROPRO_COMO_APLICACION.md).
Para la instalación y orientación de las cámaras, consulta
[INSTALACION_CAMARAS_IP.md](INSTALACION_CAMARAS_IP.md).

### Prueba web gratuita con Supabase, Render y Vercel

El backend admite dos modos:

- Sin `DATABASE_URL`: SQLite local, usado por Docker local.
- Con `DATABASE_URL`: PostgreSQL remoto de Supabase.

Para una prueba remota:

1. Crear un proyecto en Supabase y copiar su conexión PostgreSQL con
   `sslmode=require`.
2. Crear un Web Service en Render usando el archivo `render.yaml`.
3. Configurar en Render:
   - `DATABASE_URL`: conexión privada de Supabase.
   - `INITIAL_ADMIN_PASSWORD`: mínimo 12 caracteres.
   - `CORS_ORIGINS`: URL pública de Vercel.
4. En `frontend`, copiar `config.example.js` como `config.js` y reemplazar
   la URL por la URL pública de Render.
5. Publicar `frontend` como sitio estático en Vercel.
6. Usar en el LPR la URL HTTPS de Render como `BACKEND_URL`.

No subas `config.js` si contiene una URL privada y nunca pongas una clave
`service_role` de Supabase en el frontend o en el cliente OCR.

### Requisitos
- Docker
- Docker Compose

### Ejecutar
```bash
docker compose up --build
```

### Probar localmente con Docker

Usa la configuración local para probar la aplicación sin certificados SSL:

```powershell
Copy-Item .env.local.example .env.local
docker compose -f docker-compose.local.yml --env-file .env.local up -d --build
```

Después abre:

- Frontend: http://localhost:8080
- API: http://localhost:8000
- Documentación local: http://localhost:8000/docs

El usuario inicial local es `admin` y la contraseña se define en `.env.local`.
Para detener la prueba:

```powershell
docker compose -f docker-compose.local.yml --env-file .env.local down
```

El cliente LPR se ejecuta fuera de Docker y tiene sus propias dependencias:

```powershell
.\.venv\Scripts\python.exe -m pip install -r LPR_Cliente\requirements.txt
.\.venv\Scripts\python.exe LPR_Cliente\lpr_test.py
```

Para usar una cámara IP, edita `LPR_Cliente\Iniciar_LPR_Entrada.bat` o
`LPR_Cliente\Iniciar_LPR_Salida.bat` y cambia `CAMERA_URL` por el stream de
la cámara. Ejemplos:

```bat
set CAMERA_URL=rtsp://192.168.1.100:554/Streaming/Channels/101
set CAMERA_USER=admin
set CAMERA_PASS=tu_clave
```

También funcionan streams HTTP/MJPEG, por ejemplo
`http://192.168.1.100:8080/video`. El cliente reduce el buffer, detecta
la pérdida de frames y reabre automáticamente el stream IP. Si la cámara
usa otro perfil RTSP, reemplaza solo la ruta del ejemplo por la indicada
por el fabricante.

### Acceso
- Frontend: http://localhost:8080
- API: http://localhost:8000
- Health check: http://localhost:8000/health

### Variables de entorno
El backend acepta estas variables:
- JWT_SECRET_KEY
- JWT_ALGORITHM
- ACCESS_TOKEN_TTL_HOURS
- CORS_ORIGINS
- DB_FILE
- ENABLE_DOCS
- ENVIRONMENT
- INITIAL_ADMIN_PASSWORD

En producción, `JWT_SECRET_KEY` e `INITIAL_ADMIN_PASSWORD` son obligatorias
cuando se crea una base de datos nueva. No uses credenciales de ejemplo.
Los datos persistentes de Docker se montan en `/app/data`, para no ocultar
el código de la imagen.

## Ejecución local sin Docker
### Backend
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set ENVIRONMENT=development
set JWT_SECRET_KEY=local-development-secret
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### Frontend
Sirve la carpeta frontend con cualquier servidor estático, por ejemplo:
```bash
cd frontend
python -m http.server 8080
```
Y accede a http://localhost:8080
