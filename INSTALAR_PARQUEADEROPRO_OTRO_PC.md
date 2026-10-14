# Instalar ParqueaderoPRO en otro PC

Este instructivo instala el sistema completo en un PC con Windows:

- API y base de datos mediante Docker.
- Interfaz web local.
- Cliente OCR/LPR en Python 3.11.
- Cámara de entrada.
- Cámara de salida.

El PC debe permanecer encendido para registrar ingresos y salidas
automáticamente.

## 1. Requisitos del PC

Recomendado:

- Windows 10/11 de 64 bits.
- Procesador Intel Core i5/Ryzen 5 o superior.
- 8 GB de RAM como mínimo; 16 GB recomendado para dos clientes OCR.
- 20 GB libres en disco.
- Conexión Ethernet al router o switch.
- Docker Desktop.
- Python 3.11 de 64 bits.
- VLC para probar los streams RTSP.

No es necesario instalar PostgreSQL, MySQL, Nginx ni un servidor web
adicional. Docker instala y ejecuta los servicios de la aplicación.

## 2. Copiar el proyecto

Copiar la carpeta completa del proyecto al nuevo PC, por ejemplo:

```text
C:\ParqueaderoPRO
```

No copiar estos elementos si existen:

- `.venv`
- `.venv_lpr`
- `__pycache__`
- bases de datos de pruebas
- archivos `.env` con contraseñas de otro equipo

Las dependencias se reinstalan en el PC nuevo. La base de datos de
producción debe restaurarse únicamente si se necesita conservar el
historial anterior.

## 3. Instalar Docker Desktop

1. Descargar Docker Desktop desde `https://www.docker.com/products/docker-desktop/`.
2. Instalarlo con el backend WSL 2 recomendado.
3. Reiniciar Windows si el instalador lo solicita.
4. Abrir Docker Desktop.
5. Esperar hasta que indique que Docker está ejecutándose.

Abrir PowerShell y verificar:

```powershell
docker --version
docker compose version
```

Si alguno de los comandos no existe, cerrar y abrir PowerShell después
de instalar Docker Desktop.

## 4. Configurar la aplicación web

Abrir PowerShell en la carpeta del proyecto:

```powershell
cd C:\ParqueaderoPRO
Copy-Item .env.local.example .env.local
```

Abrir `.env.local` y cambiar, como mínimo:

```dotenv
JWT_SECRET_KEY=una-clave-larga-y-privada-de-32-caracteres-o-mas
INITIAL_ADMIN_PASSWORD=una-clave-administrador-segura
```

No reutilizar la clave del ejemplo en una instalación real. Guardar una
copia segura de estos valores, porque serán necesarios para iniciar sesión.

Iniciar los servicios:

```powershell
docker compose -f docker-compose.local.yml --env-file .env.local up -d --build
```

Comprobar el estado:

```powershell
docker compose -f docker-compose.local.yml --env-file .env.local ps
Invoke-WebRequest http://localhost:8000/health
```

Abrir en el navegador:

```text
http://localhost:8080
```

El primer usuario es `admin`. La contraseña es el valor definido en
`INITIAL_ADMIN_PASSWORD`.

## 5. Conectar las cámaras

La topología recomendada es:

```text
Cámara entrada ── PoE 1
Cámara salida  ── PoE 2
PC              ── Uplink
Router          ── Uplink
```

Si se usa alimentación independiente, cada cámara debe tener su adaptador
de corriente. El PC y las cámaras deben estar en la misma red.

Ejemplo de direcciones:

```text
PC              192.168.1.10
Cámara entrada  192.168.1.101
Cámara salida   192.168.1.102
```

Usar IP fija o reserva DHCP. No dejar que la IP de una cámara cambie después
de configurar el OCR.

## 6. Probar cada cámara antes del OCR

Instalar VLC desde `https://www.videolan.org/vlc/`.

Para Dahua, la ruta suele parecerse a:

```text
rtsp://usuario:clave@192.168.1.101:554/cam/realmonitor?channel=1&subtype=0
```

Para Hikvision, suele parecerse a:

```text
rtsp://usuario:clave@192.168.1.101:554/Streaming/Channels/101
```

La ruta exacta depende del modelo. En VLC:

1. Abrir **Medio > Abrir ubicación de red**.
2. Pegar la URL RTSP.
3. Confirmar que se vea video estable durante varios minutos.
4. Repetir con la cámara de salida.

No continuar con el OCR si VLC no muestra video. Primero corregir IP,
credenciales, cableado, RTSP o firewall.

## 7. Instalar Python y el cliente LPR

Instalar Python 3.11 de 64 bits desde `https://www.python.org/downloads/`.
Durante la instalación activar **Add Python to PATH**.

En PowerShell:

```powershell
cd C:\ParqueaderoPRO
py -3.11 -m venv .venv_lpr
.\.venv_lpr\Scripts\python.exe -m pip install --upgrade pip
.\.venv_lpr\Scripts\python.exe -m pip install -r LPR_Cliente\requirements.txt
```

La primera carga de EasyOCR puede descargar modelos y tardar varios minutos.
Debe existir conexión a Internet durante esa primera instalación.

## 8. Configurar la cámara de entrada

Editar:

```text
C:\ParqueaderoPRO\LPR_Cliente\Iniciar_LPR_Entrada.bat
```

Cambiar estos valores:

```bat
set "CAMERA_URL=rtsp://192.168.1.101:554/cam/realmonitor?channel=1&subtype=0"
set "CAMERA_USER=admin"
set "CAMERA_PASS=CLAVE_REAL_DE_ENTRADA"
set "BACKEND_URL=http://127.0.0.1:8000"
set "LPR_USERNAME=admin"
set "LPR_PASSWORD=CLAVE_DE_INITIAL_ADMIN_PASSWORD"
```

El archivo ya configura:

```bat
set "CAMERA_ROLE=entrada"
```

Una placa confirmada desde esta cámara se registra como ingreso.

## 9. Configurar la cámara de salida

Editar:

```text
C:\ParqueaderoPRO\LPR_Cliente\Iniciar_LPR_Salida.bat
```

Cambiar:

```bat
set "CAMERA_URL=rtsp://192.168.1.102:554/cam/realmonitor?channel=1&subtype=0"
set "CAMERA_USER=admin"
set "CAMERA_PASS=CLAVE_REAL_DE_SALIDA"
set "BACKEND_URL=http://127.0.0.1:8000"
set "LPR_USERNAME=admin"
set "LPR_PASSWORD=CLAVE_DE_INITIAL_ADMIN_PASSWORD"
```

El archivo ya configura:

```bat
set "CAMERA_ROLE=salida"
```

Una placa confirmada desde esta cámara se registra como salida.

No dejar `CAMBIA_ESTA_CLAVE` ni contraseñas reales dentro de documentos que
se compartan por correo o mensajería.

## 10. Ejecutar el sistema

Con Docker Desktop abierto:

1. Ejecutar `LPR_Cliente\Iniciar_LPR_Entrada.bat`.
2. Ejecutar `LPR_Cliente\Iniciar_LPR_Salida.bat`.
3. Mantener abiertas ambas ventanas.
4. Abrir `http://localhost:8080` para revisar los registros.

Cada ventana debe mostrar:

```text
Rol de cámara: entrada
```

o:

```text
Rol de cámara: salida
```

El cliente LPR reconecta el stream IP cuando se pierde el video.

## 11. Prueba de aceptación

Realizar estas pruebas en orden:

1. Entrar con un vehículo de prueba.
2. Confirmar que aparece un ingreso en el panel.
3. Revisar que la placa quede en vehículos activos.
4. Sacar el mismo vehículo.
5. Confirmar que aparece una salida y se calcula el cobro.
6. Repetir de noche.
7. Repetir con el lente ligeramente sucio para verificar el mantenimiento.
8. Confirmar que ninguna cámara registra la acción equivocada.

Si la placa no se lee, primero revisar enfoque, ángulo, iluminación, velocidad
del vehículo y limpieza del lente. Después revisar la URL RTSP y el OCR.

## 12. Iniciar Docker automáticamente

En Docker Desktop activar **Start Docker Desktop when you sign in**.

Los servicios tienen `restart: unless-stopped`, por lo que volverán a iniciar
cuando Docker se ejecute. El cliente LPR debe iniciarse manualmente o mediante
un acceso directo a cada archivo `.bat`.

Para crear accesos directos:

1. Clic derecho en `Iniciar_LPR_Entrada.bat`.
2. Seleccionar **Enviar a > Escritorio**.
3. Repetir con `Iniciar_LPR_Salida.bat`.

No ejecutar dos veces el mismo archivo porque se registrarían lecturas
duplicadas.

## 13. Copias de seguridad

La base de datos está en un volumen Docker. Consultar el volumen:

```powershell
docker volume ls
```

Para una copia operativa, detener temporalmente el servicio y conservar el
volumen o exportar la base de datos desde el contenedor. No borrar el volumen
sin una copia previa:

```powershell
docker compose -f docker-compose.local.yml --env-file .env.local down
```

No usar `down -v` en una instalación con datos, porque elimina el volumen de
la base de datos.

## 14. Problemas frecuentes

### Docker no inicia

Abrir Docker Desktop, esperar a que esté listo y ejecutar:

```powershell
docker compose -f docker-compose.local.yml --env-file .env.local ps
```

### La página no abre

Comprobar:

```text
http://localhost:8080
http://localhost:8000/health
```

### El LPR muestra 401

Revisar que `LPR_PASSWORD` sea exactamente igual a
`INITIAL_ADMIN_PASSWORD` de `.env.local`.

### La cámara no abre

Probar primero su RTSP en VLC. Luego revisar que el PC pueda hacer ping a la
IP de la cámara y que ningún firewall bloquee el tráfico local.

### El video tiene mucho retraso

Usar conexión cableada, H.264, el stream principal, buffer bajo y evitar WiFi
para el PC.

### No lee de noche

Revisar exposición, WDR, IR/luz cálida, reflejos de los faros, limpieza,
parasol y velocidad del vehículo. Una cámara de seguridad no sustituye a una
cámara ANPR profesional en condiciones extremas.

## 15. Seguridad mínima

- Cambiar las contraseñas predeterminadas de las cámaras.
- Usar una contraseña fuerte para el administrador.
- No publicar RTSP en Internet.
- Mantener cámaras y PC dentro de la red local.
- Actualizar Docker, Windows y el firmware de las cámaras.
- No compartir `.env.local` ni los archivos `.bat` con contraseñas reales.
- Hacer copias periódicas de los datos.
