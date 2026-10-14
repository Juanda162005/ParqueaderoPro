# Instalar ParqueaderoPRO como una aplicación de Windows

La opción recomendada es distribuir un instalador `.exe` creado con Inno
Setup. El instalador copia la aplicación, crea accesos directos, configura
el cliente OCR y arranca Docker Compose.

## Importante

Docker Desktop y Python 3.11 son requisitos externos. No se incluyen dentro
del instalador porque Docker Desktop necesita su propia instalación,
permisos y licencia. Deben instalarse una sola vez en el PC destino.

## Preparar el PC destino

1. Instalar Docker Desktop para Windows y abrirlo.
2. Activar el backend WSL 2 si el instalador lo solicita.
3. Instalar Python 3.11 de 64 bits desde:
   `https://www.python.org/downloads/`
4. Instalar VLC para probar las cámaras RTSP.
5. Conectar el PC y las cámaras a la misma red.

## Crear el instalador

En el PC de desarrollo:

1. Descargar e instalar Inno Setup desde:
   `https://jrsoftware.org/isinfo.php`
2. Abrir:
   `instalador\ParqueaderoPRO.iss`
3. Pulsar **Compile**.
4. El resultado será:

```text
instalador\ParqueaderoPRO-Setup.exe
```

No incluir `.env.local`, `.venv` ni `.venv_lpr` en el instalador. El script
los crea en el PC destino.

## Instalar en otro PC

1. Copiar `ParqueaderoPRO-Setup.exe` al nuevo PC.
2. Ejecutarlo como administrador.
3. El instalador copiará la aplicación a:

```text
C:\Program Files\ParqueaderoPRO
```

4. El asistente pedirá la contraseña inicial del administrador.
5. Creará un secreto JWT nuevo.
6. Creará el entorno Python `.venv_lpr`.
7. Instalará OpenCV, EasyOCR, Torch y las dependencias necesarias.
8. Construirá e iniciará los contenedores Docker.
9. Creará accesos directos en el escritorio y menú Inicio.

La primera instalación OCR puede tardar y descargar varios cientos de MB.
Debe mantenerse Internet disponible durante ese paso.

## Configurar las cámaras

Después de instalar:

1. Editar `LPR_Cliente\Iniciar_LPR_Entrada.bat`.
2. Configurar la IP, usuario y contraseña de la cámara de entrada.
3. Editar `LPR_Cliente\Iniciar_LPR_Salida.bat`.
4. Configurar la IP, usuario y contraseña de la cámara de salida.
5. Probar ambos RTSP en VLC.
6. Ejecutar los accesos directos **ParqueaderoPRO - Entrada** y
   **ParqueaderoPRO - Salida**.

## Uso diario

- **ParqueaderoPRO**: inicia Docker y abre el panel web.
- **ParqueaderoPRO - Entrada**: inicia el OCR de entrada.
- **ParqueaderoPRO - Salida**: inicia el OCR de salida.
- `Detener_ParqueaderoPRO.bat`: detiene los contenedores.

La aplicación web estará en:

```text
http://localhost:8080
```

## Qué queda instalado

```text
ParqueaderoPRO
├── backend
├── frontend
├── LPR_Cliente
├── .env.local
├── .venv_lpr
└── docker-compose.local.yml
```

La base de datos queda en el volumen Docker `parqueadero_local_data`, por lo
que una actualización de archivos no debe borrar los registros. No ejecutar
`docker compose down -v` en una instalación con datos.

## Recomendación para actualizaciones

1. Detener los clientes OCR.
2. Hacer copia de seguridad.
3. Ejecutar el nuevo instalador sobre la misma carpeta.
4. No eliminar el volumen Docker.
5. Revisar las URLs RTSP y volver a iniciar los clientes.

## Límites de esta modalidad

- Docker Desktop debe estar instalado y ejecutándose.
- La instalación necesita permisos de administrador.
- La primera preparación necesita Internet.
- Las credenciales de cámaras deben configurarse manualmente.
- El sistema funciona como aplicación local; no instala automáticamente
  dominio, DNS ni acceso remoto.
