# Instalación de cámaras IP para ParqueaderoPRO

Esta guía configura dos cámaras IP conectadas sin switch PoE:

- Cámara 1: entrada, registra únicamente ingresos.
- Cámara 2: salida, registra únicamente salidas.
- Cada cámara usa su propio adaptador de corriente.
- El video llega al PC por red mediante RTSP.

## 1. Materiales

- Dos cámaras IP exteriores con RTSP, IP67, visión nocturna y WDR.
- Dos adaptadores de corriente compatibles con las cámaras.
- Un switch Ethernet normal de 5 u 8 puertos.
- Tres cables Cat5e o Cat6:
  - uno para cada cámara;
  - uno para el PC.
- Router de la red local.
- PC donde se ejecuta ParqueaderoPRO.
- UPS recomendada para router, switch y PC.

## 2. Cableado

La conexión física debe quedar así:

```text
Cámara entrada -- cable de red -- switch normal
Cámara salida  -- cable de red -- switch normal
PC             -- cable de red -- switch normal
Router         -- cable de red -- switch normal
```

Conecta cada cámara a su adaptador de corriente. El switch normal transmite
datos, pero no alimenta las cámaras.

No conectes un adaptador de corriente si el modelo fue comprado para otra
tensión. Usa siempre el adaptador entregado por el fabricante.

## 3. Direcciones IP

Configura IP fija o reserva DHCP en el router:

```text
PC             192.168.1.10
Cámara entrada 192.168.1.101
Cámara salida  192.168.1.102
Máscara        255.255.255.0
```

Los valores son ejemplos. Usa el rango real de tu router. Las dos cámaras
deben tener IP diferentes y el PC debe estar en la misma red.

Configura primero cada cámara desde su página web y cambia la contraseña
predeterminada. Después verifica desde el PC que sus páginas abran:

```text
http://192.168.1.101
http://192.168.1.102
```

## 4. Configuración de las cámaras

En cada cámara:

1. Actualiza el firmware si el fabricante lo recomienda.
2. Cambia el usuario y la contraseña predeterminados.
3. Configura fecha, hora y zona horaria de Colombia.
4. Activa RTSP.
5. Usa H.264 si el equipo tiene problemas con H.265.
6. Configura WDR para la entrada y salida.
7. Ajusta exposición y obturador para que la placa no quede borrosa.
8. Usa el stream principal para el OCR.
9. Orienta cada cámara a un solo carril.

Rutas RTSP habituales:

```text
Hikvision:
rtsp://usuario:clave@IP:554/Streaming/Channels/101

Dahua:
rtsp://usuario:clave@IP:554/cam/realmonitor?channel=1&subtype=0
```

La ruta exacta depende del modelo. Pruébala primero en VLC desde el PC:

1. Abre VLC.
2. Selecciona **Medio > Abrir ubicación de red**.
3. Pega la URL RTSP.
4. Confirma que el video se vea sin cortes.

Si VLC no muestra video, no continúes con el OCR: revisa IP, contraseña,
RTSP, firewall y cableado.

## 5. Instalación física para placas

Para una distancia mínima de 5 metros:

- Usa un solo carril por cámara.
- Monta la cámara aproximadamente a 2-2,5 metros.
- Mantén el ángulo horizontal lo más frontal posible.
- Ajusta el lente para que la placa tenga buen tamaño, sin zoom digital.
- Evita apuntar directamente al sol o a los faros.
- Instala parasol o visera contra polvo y lluvia.
- Inclina ligeramente el frente hacia abajo para reducir acumulación.
- Limpia el lente según el nivel de polución.
- Si es posible, haz que el vehículo reduzca la velocidad.

Haz una prueba de día y otra de noche. La cámara debe verse bien antes de
ajustar el OCR.

## 6. Preparar el PC

1. Inicia Docker Desktop.
2. Abre PowerShell en la carpeta del proyecto.
3. Inicia la aplicación:

```powershell
docker compose -f docker-compose.local.yml --env-file .env.local up -d
```

4. Verifica:

```text
Frontend: http://localhost:8080
API:      http://localhost:8000/health
```

5. Instala las dependencias del cliente LPR en Python 3.11:

```powershell
C:\Users\Spike\parqueadero_app\.venv_lpr\Scripts\python.exe -m pip install -r LPR_Cliente\requirements.txt
```

## 7. Configurar los dos clientes LPR

Edita [Iniciar_LPR_Entrada.bat](LPR_Cliente/Iniciar_LPR_Entrada.bat):

```bat
set "CAMERA_URL=rtsp://192.168.1.101:554/Streaming/Channels/101"
set "CAMERA_USER=admin"
set "CAMERA_PASS=CLAVE_REAL_ENTRADA"
```

Edita [Iniciar_LPR_Salida.bat](LPR_Cliente/Iniciar_LPR_Salida.bat):

```bat
set "CAMERA_URL=rtsp://192.168.1.102:554/Streaming/Channels/101"
set "CAMERA_USER=admin"
set "CAMERA_PASS=CLAVE_REAL_SALIDA"
```

No dejes `CAMBIA_ESTA_CLAVE` en los archivos de uso real.

## 8. Arranque diario

1. Enciende router y switch.
2. Enciende las cámaras y espera a que estén disponibles.
3. Inicia Docker Desktop y los servicios:

```powershell
docker compose -f docker-compose.local.yml --env-file .env.local up -d
```

4. Ejecuta `Iniciar_LPR_Entrada.bat`.
5. Ejecuta `Iniciar_LPR_Salida.bat`.
6. Comprueba en pantalla que cada ventana indique su cámara y rol.
7. Prueba un vehículo autorizado de entrada y otro de salida.

Las dos ventanas pueden ejecutarse al mismo tiempo. La entrada solo llama
`/ingreso/` y la salida solo llama `/salida/`. Ya no dependen de adivinar
la acción consultando los vehículos activos.

## 9. Diagnóstico rápido

- **No abre la cámara:** prueba la URL en VLC.
- **VLC pide contraseña:** revisa usuario y clave; codifica caracteres
  especiales en la URL si los escribes directamente.
- **Video con retraso:** usa H.264, stream principal y red cableada.
- **Placa pequeña:** ajusta el lente o acerca la zona de lectura.
- **Placa blanca de noche:** reduce IR o usa iluminación controlada.
- **Polvo/reflejos:** limpia el lente, instala visera y evita IR excesivo.
- **401 en el backend:** revisa `LPR_USERNAME`, `LPR_PASSWORD` y `.env.local`.
- **Ingreso duplicado:** conserva la ventana de consenso y evita que el
  vehículo permanezca frente a la cámara durante mucho tiempo.

## 10. Seguridad

- No expongas RTSP a Internet.
- Cambia las contraseñas iniciales de las cámaras.
- Usa una red local separada para cámaras si el router lo permite.
- No compartas este archivo con contraseñas reales.
- Haz copias de seguridad de la carpeta de datos de ParqueaderoPRO.
