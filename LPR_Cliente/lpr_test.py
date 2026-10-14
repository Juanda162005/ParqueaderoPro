import cv2
import easyocr
import os
import re
import requests
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlsplit, urlunsplit
import warnings
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings("ignore", category=UserWarning)

# Detectar automáticamente el puerto del backend
def detectar_backend():
    for puerto in [8000, 8001, 8002, 8003]:
        try:
            r = requests.get(f"http://127.0.0.1:{puerto}/health", timeout=1)
            if r.status_code == 200:
                return f"http://127.0.0.1:{puerto}"
        except Exception:
            continue
    return "http://127.0.0.1:8000"  # fallback

URL_BACKEND = os.getenv("BACKEND_URL", detectar_backend())
print(f"Conectando al backend en: {URL_BACKEND}")
print("Cargando modelo de IA...")
reader = easyocr.Reader(['en', 'es'], gpu=False)

sesion_backend = requests.Session()
procesando_ia = False
ultima_placa_detectada = ""
tiempo_ultima_captura = 0
ultimo_tiempo_procesamiento = 0
COOLDOWN_PROCESO_SEGUNDOS = 0.3
INTERVALO_PROCESAMIENTO_SEGUNDOS = 1.0
UMBRAL_MOVIMIENTO = 800
vehiculos_activos_cache = set()
ultima_actualizacion_parqueados = 0.0
CACHE_PARQUEADOS_SEGUNDOS = 3.0
ultima_placa_confianza = None
historial_lecturas = []
MAX_HISTORIAL_LECTURAS = 4          # 4 muestras para evaluar rápido
CONSENSO_MINIMO = 3                 # 3 de 4 coincidencias (75%) — alta certeza, rápida detección
CONFIANZA_MINIMA_PORCENTAJE = 0.6   # Al menos 60% de coincidencia
CAMARA_IP_POR_DEFECTO = "rtsp://192.168.1.100:554/Streaming/Channels/101"

FORMATOS_PLACA = (
    ("automovil", re.compile(r"^[A-Z]{3}[0-9]{3}$")),
    ("moto", re.compile(r"^[A-Z]{3}[0-9]{2}[A-Z]?$")),
    ("remolque", re.compile(r"^[RS][0-9]{5}$")),
    ("diplomatica", re.compile(r"^[A-Z]{2}[0-9]{4}$")),
)
_CONFUSIONES_LETRA = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B"})
_CONFUSIONES_NUMERO = str.maketrans({
    "O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
    "Z": "2", "S": "5", "B": "8", "G": "6",
})


# Configuración de autenticación LPR (usar variables de entorno o defaults)
LPR_USERNAME = os.getenv("LPR_USERNAME", "admin")
LPR_PASSWORD = os.getenv("LPR_PASSWORD", "admin123")
CODIGO_AUTENTICACION_FALLIDA = 2
CAMERA_ROLE = os.getenv("CAMERA_ROLE", "auto").strip().lower()
LPR_DEBUG = os.getenv("LPR_DEBUG", "0").strip().lower() in {"1", "true", "si", "sí"}
if CAMERA_ROLE not in {"entrada", "salida", "auto"}:
    raise ValueError("CAMERA_ROLE debe ser entrada, salida o auto")
# Si quieres que el LPR registre en un tenant específico, usa: LPR_USERNAME=admin@parqueadero-norte


def autenticar_backend():
    try:
        respuesta = sesion_backend.post(
            f"{URL_BACKEND}/login/",
            json={"username": LPR_USERNAME, "password": LPR_PASSWORD},
            timeout=3,
        )
        if respuesta.ok:
            datos = respuesta.json()
            token = datos.get("access_token")
            if token:
                sesion_backend.headers["Authorization"] = f"Bearer {token}"
            print(f"✅ LPR conectado → {datos.get('tenant_nombre', 'Desconocido')} (usuario: {datos.get('username')})")
            return True
        if respuesta.status_code == 401:
            print("❌ Credenciales LPR rechazadas (401). Verifica el usuario y la contraseña del administrador en Render.")
        elif respuesta.status_code >= 500:
            print(f"⚠️ Backend no disponible temporalmente ({respuesta.status_code}).")
        else:
            print(f"⚠️ No se pudo autenticar el backend LPR: {respuesta.status_code}")
        return False
    except Exception as e:
        print(f"❌ Error autenticando el backend LPR: {e}")
        return False


def hacer_peticion(method, url, **kwargs):
    if not sesion_backend.headers.get("Authorization"):
        autenticar_backend()

    try:
        respuesta = getattr(sesion_backend, method.lower())(url, **kwargs)
        if respuesta.status_code == 401:
            autenticar_backend()
            respuesta = getattr(sesion_backend, method.lower())(url, **kwargs)
        return respuesta
    except Exception as e:
        print(f"❌ Error de red en la petición {method} {url}: {e}")
        return None

def normalizar_placa(placa: str) -> str:
    """Limpia el texto: mayúsculas, sin espacios, solo letras y números"""
    return re.sub(r'[^A-Z0-9]', '', placa.upper().strip())


def extraer_placa_colombiana(texto: str) -> Optional[str]:
    """
    Extrae la placa colombiana de un texto OCR.
    Las placas colombianas tienen formatos:
    - ABC 123 (carro particular, 3 letras + 3 números)
    - ABC 12D (carro particular nuevo, 3 letras + 2 números + 1 letra)
    - ABC 123 (automóvil y servicio público)
    - ABC 12 / ABC 12D (motocicleta)
    - R12345 / S12345 (remolque)
    - AB1234 (diplomática o consular)

    El OCR suele leer la ciudad debajo de la placa (ej: "ABC 123 BOGOTÁ"),
    así que buscamos el patrón de placa DENTRO del texto, no el texto completo.
    """
    texto_limpio = normalizar_placa(texto)

    candidatos = []
    for inicio in range(len(texto_limpio)):
        for longitud in (5, 6, 7):
            fragmento = texto_limpio[inicio:inicio + longitud]
            if len(fragmento) >= 5:
                candidatos.extend(generar_candidatos_placa(fragmento))
    return max(candidatos, key=calidad_placa)[0] if candidatos else None


def textos_ocr_a_candidatos(resultados) -> List[Tuple[str, float]]:
    """Combina fragmentos OCR de una misma línea antes de validar la placa."""
    lecturas = []
    for caja, texto, confianza in resultados:
        texto_limpio = normalizar_placa(texto)
        if not texto_limpio:
            continue
        x = min(punto[0] for punto in caja)
        lecturas.append((x, texto_limpio, float(confianza)))

    lecturas.sort(key=lambda item: item[0])
    candidatos = []
    if lecturas:
        texto_completo = "".join(item[1] for item in lecturas)
        confianza_media = sum(item[2] for item in lecturas) / len(lecturas)
        candidatos.append((texto_completo, confianza_media))
        candidatos.extend((texto, confianza) for _, texto, confianza in lecturas)
    return candidatos


def generar_candidatos_placa(texto: str) -> List[Tuple[str, str]]:
    """Corrige confusiones OCR solo cuando la posición exige letra o número."""
    texto = normalizar_placa(texto)
    candidatos = []
    for tipo, patron in FORMATOS_PLACA:
        if len(texto) not in (5, 6, 7):
            continue
        # ABC12D pertenece al formato de motocicleta; no lo conviertas
        # indiscriminadamente a ABC120 por una corrección de OCR.
        if tipo == "automovil" and len(texto) == 6 and texto[-1].isalpha():
            continue
        normalizado = []
        for indice, caracter in enumerate(texto):
            espera_numero = (
                (tipo == "automovil" and indice >= 3)
                or (tipo == "moto" and 3 <= indice <= 4)
                or (tipo == "remolque" and indice >= 1)
                or (tipo == "diplomatica" and indice >= 2)
            )
            if espera_numero:
                caracter = caracter.translate(_CONFUSIONES_NUMERO)
            elif tipo != "remolque" or indice == 0:
                caracter = caracter.translate(_CONFUSIONES_LETRA)
            normalizado.append(caracter)
        candidato = "".join(normalizado)
        if patron.fullmatch(candidato):
            candidatos.append((candidato, tipo))
    return candidatos


def calidad_placa(item: Tuple[str, str]) -> Tuple[int, int]:
    placa, tipo = item
    return ({"automovil": 4, "moto": 3, "diplomatica": 2, "remolque": 1}[tipo], len(placa))


def limpiar_texto_ocr(texto: str) -> str:
    """
    Limpia el texto del OCR eliminando la ciudad y caracteres no deseados.
    Las ciudades comunes en placas colombianas: BOGOTA, MEDELLIN, CALI, etc.
    """
    texto = texto.upper().strip()
    # Eliminar nombres de ciudades comunes que el OCR puede leer
    ciudades = ['BOGOTA', 'MEDELLIN', 'CALI', 'BARRANQUILLA', 'CARTAGENA',
                'CUCUTA', 'BUCARAMANGA', 'PEREIRA', 'MANIZALES', 'IBAGUE',
                'SANTA MARTA', 'VILLAVICENCIO', 'PASTO', 'POPAYAN', 'TUNJA']
    for ciudad in ciudades:
        texto = texto.replace(ciudad, '')
    return texto


def preprocesar_para_ocr(frame):
    """
    Preprocesamiento rápido y efectivo para placas colombianas.
    Estrategia: reducir resolución -> CLAHE -> umbral -> ampliar
    """
    alto, ancho = frame.shape[:2]

    # Recorte generoso de la zona central
    inicio_y, fin_y = int(alto * 0.05), int(alto * 0.95)
    inicio_x, fin_x = int(ancho * 0.05), int(ancho * 0.95)
    recorte = frame[inicio_y:fin_y, inicio_x:fin_x]

    if recorte is None or recorte.size == 0:
        return ()

    gris = cv2.cvtColor(recorte, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gris)
    suavizado = cv2.GaussianBlur(clahe, (3, 3), 0)
    _, otsu = cv2.threshold(suavizado, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptativo = cv2.adaptiveThreshold(
        suavizado, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 7,
    )
    return tuple(
        cv2.resize(imagen, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        for imagen in (gris, otsu, adaptativo)
    )


def extraer_regiones_placa(frame) -> List:
    """Encuentra recortes con proporción de placa y conserva el frame completo."""
    gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bordes = cv2.Canny(cv2.GaussianBlur(gris, (5, 5), 0), 80, 180)
    contornos, _ = cv2.findContours(bordes, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    regiones = [(frame, 0.0)]
    alto, ancho = frame.shape[:2]
    for contorno in sorted(contornos, key=cv2.contourArea, reverse=True):
        x, y, w, h = cv2.boundingRect(contorno)
        area = w * h
        proporcion = w / max(h, 1)
        if area < ancho * alto * 0.002 or not 2.0 <= proporcion <= 6.5:
            continue
        margen_x, margen_y = int(w * 0.12), int(h * 0.35)
        x1, y1 = max(0, x - margen_x), max(0, y - margen_y)
        x2, y2 = min(ancho, x + w + margen_x), min(alto, y + h + margen_y)
        regiones.append((frame[y1:y2, x1:x2], float(area)))
        if len(regiones) == 5:
            break
    return [region for region, _ in sorted(regiones, key=lambda item: item[1], reverse=True)]


def procesar_imagen_silencioso(frame):
    """
    OCR optimizado para placas colombianas con ciudad debajo.
    """
    if frame is None or getattr(frame, "size", 0) == 0:
        return None

    votos: Dict[str, float] = defaultdict(float)
    for region in extraer_regiones_placa(frame):
        if region is None or getattr(region, "size", 0) == 0:
            continue
        for imagen in preprocesar_para_ocr(region):
            if imagen is None or getattr(imagen, "size", 0) == 0:
                continue
            try:
                resultados = reader.readtext(
                    imagen,
                    detail=1,
                    allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ",
                    text_threshold=0.25,
                    low_text=0.15,
                    link_threshold=0.15,
                    paragraph=False,
                    mag_ratio=1.5,
                )
            except cv2.error as error:
                print(f"⚠️ EasyOCR descartó una región inválida: {error}")
                continue
            for texto, confianza in textos_ocr_a_candidatos(resultados):
                if LPR_DEBUG:
                    print(f"🔎 OCR: {texto!r} ({confianza:.0%})")
                if confianza < 0.20:
                    continue
                placa = extraer_placa_colombiana(limpiar_texto_ocr(texto))
                if placa:
                    # Las regiones geométricas son más fiables que el frame completo.
                    peso_region = 1.25 if region is not frame else 1.0
                    votos[placa] += float(confianza) * peso_region
    return max(votos, key=votos.get) if votos else None


def validar_formato_placa_colombiana(placa: str) -> bool:
    """
    Valida que la placa cumpla con los formatos oficiales colombianos:
    - ABC123 (carro particular)
    - ABC12D (carro particular nuevo)
    - T1234 / TA1234 (taxi)
    - A12345 (moto)
    - ABC1234 (nuevo formato 2024+)
    Mínimo 5 caracteres, máximo 7.
    """
    placa = normalizar_placa(placa)
    return any(patron.fullmatch(placa) for _, patron in FORMATOS_PLACA)
def validar_consenso_placa(placa_nueva: str):
    """
    Sistema de consenso estricto para evitar falsos positivos:
    1. Acumula hasta 6 lecturas del mismo vehículo
    2. Exige que al menos 4 de 6 (66%) coincidan exactamente
    3. Valida que la placa tenga formato colombiano válido
    4. Si hay empate, gana la placa con formato más válido
    """
    global historial_lecturas, ultima_placa_confianza

    if not placa_nueva:
        return None

    # Validar formato colombiano antes de acumular
    if not validar_formato_placa_colombiana(placa_nueva):
        return None

    historial_lecturas.append(placa_nueva)
    if len(historial_lecturas) > MAX_HISTORIAL_LECTURAS:
        historial_lecturas.pop(0)

    # Necesitamos al menos 3 muestras para empezar a evaluar
    if len(historial_lecturas) < 3:
        return None

    # Contar ocurrencias
    contador = {}
    for lectura in historial_lecturas:
        contador[lectura] = contador.get(lectura, 0) + 1

    # Ordenar por: más coincidencias, luego formato más válido, luego más larga (más info)
    def puntuar(placa):
        formato_ok = 1 if validar_formato_placa_colombiana(placa) else 0
        return (contador[placa], formato_ok, len(placa))

    placa_consenso = max(contador.items(), key=lambda item: puntuar(item[0]))[0]
    coincidencias = contador[placa_consenso]
    total = len(historial_lecturas)

    # Exigir al menos CONSENSO_MINIMO coincidencias y el 66% del total
    if coincidencias >= CONSENSO_MINIMO and (coincidencias / total) >= CONFIANZA_MINIMA_PORCENTAJE:
        ultima_placa_confianza = placa_consenso
        print(f"   🔒 Consenso: {placa_consenso} ({coincidencias}/{total} = {coincidencias/total:.0%})")
        return placa_consenso

    return None


def limpiar_historial_lecturas():
    global historial_lecturas
    historial_lecturas = []


def construir_url_camara(url: str, usuario: str = "", contrasena: str = "") -> str:
    """Agrega credenciales a una URL RTSP/HTTP sin romper caracteres especiales."""
    if not usuario or not contrasena or "://" not in url or "@" in url:
        return url
    partes = urlsplit(url)
    autoridad = f"{quote(usuario, safe='')}:{quote(contrasena, safe='')}@{partes.netloc}"
    return urlunsplit((partes.scheme, autoridad, partes.path, partes.query, partes.fragment))


def abrir_camara(url: str):
    """Abre USB o IP con poco buffer para reducir el retraso del video."""
    if url.isdigit():
        indice = int(url)
        # DirectShow suele ser más compatible con webcams USB en Windows.
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
            captura = cv2.VideoCapture(indice, backend)
            if captura.isOpened():
                captura.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                return captura
            captura.release()
        return cv2.VideoCapture()

    captura = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    captura.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return captura


def leer_frame_inicial(captura, intentos: int = 8):
    """Da tiempo a MSMF/DirectShow para entregar el primer frame."""
    for intento in range(intentos):
        ret, frame = captura.read()
        if ret and frame is not None and frame.size:
            return True, frame
        if intento < intentos - 1:
            time.sleep(0.25)
    return False, None

def registrar_en_backend(placa, accion):
    try:
        url = f"{URL_BACKEND}/ingreso/" if accion == "ingreso" else f"{URL_BACKEND}/salida/"
        respuesta = hacer_peticion("post", url, json={"placa": placa}, timeout=3)
        if respuesta is None:
            return

        datos = respuesta.json() if respuesta.content else {}

        if respuesta.status_code == 200:
            print(f"📡 Backend confirmó {accion.upper()} para {placa}")
            if accion == "ingreso":
                print(f"✅ INGRESO REGISTRADO: {datos.get('placa')} - Cliente: {datos.get('tipo_cliente', 'General')}")
            else:
                print(f"✅ SALIDA REGISTRADA: {datos.get('placa')} - Cobro: {datos.get('monto_cobrado', 0)}")
        else:
            print(f"⚠️ AVISO BACKEND: {datos.get('detail', 'Error')}")
    except Exception as e:
        print(f"❌ Error de conexión al registrar: {e}")

def obtener_vehiculos_activos():
    global vehiculos_activos_cache, ultima_actualizacion_parqueados
    ahora = time.time()

    if (ahora - ultima_actualizacion_parqueados) < CACHE_PARQUEADOS_SEGUNDOS and vehiculos_activos_cache:
        return set(vehiculos_activos_cache)

    try:
        res = hacer_peticion("get", f"{URL_BACKEND}/parqueados/", timeout=1.5)
        if res is not None and res.status_code == 200:
            datos = res.json()
            if isinstance(datos, list):
                activos = [normalizar_placa(v.get('placa', '')) for v in datos if v.get('placa')]
            else:
                activos = [normalizar_placa(v.get('placa', '')) for v in datos.get('vehiculos_activos', []) if v.get('placa')]
            vehiculos_activos_cache = {placa for placa in activos if placa}
            ultima_actualizacion_parqueados = ahora
            return set(vehiculos_activos_cache)
    except Exception:
        pass

    return set(vehiculos_activos_cache)


def tarea_ia_en_segundo_plano(frame_capturado):
    global procesando_ia, ultima_placa_detectada, tiempo_ultima_captura

    try:
        placa = procesar_imagen_silencioso(frame_capturado)

        if placa:
            placa = normalizar_placa(placa)
            placa_confirmada = validar_consenso_placa(placa)

            if placa_confirmada is None:
                return

            tiempo_actual = time.time()
            # Bloqueo de 45 segundos: no registrar la misma placa dos veces seguidas
            # y 45 segundos mínimo entre cualquier registro para evitar falsos dobles
            if placa_confirmada == ultima_placa_detectada and (tiempo_actual - tiempo_ultima_captura) < 45:
                print(f"   ⏳ Placa {placa_confirmada} ya registrada recientemente. Ignorando...")
                return

            if placa_confirmada != ultima_placa_detectada or (tiempo_actual - tiempo_ultima_captura > 45):
                print(f"\n🎯 [LECTURA CONFIRMADA] Placa: {placa_confirmada}. Verificando estado...")

                if CAMERA_ROLE in {"entrada", "salida"}:
                    accion = CAMERA_ROLE
                    print(f"📍 Cámara {CAMERA_ROLE}: generando {accion.upper()}...")
                else:
                    activos = obtener_vehiculos_activos()
                    if placa_confirmada in activos:
                        print(f"🚗 El vehículo {placa_confirmada} ya está adentro. Generando SALIDA...")
                        accion = "salida"
                    else:
                        print(f"🚙 El vehículo {placa_confirmada} no está adentro. Generando INGRESO...")
                        accion = "ingreso"

                registrar_en_backend(placa_confirmada, accion)

                ultima_placa_detectada = placa_confirmada
                tiempo_ultima_captura = tiempo_actual
        else:
            limpiar_historial_lecturas()
    except (cv2.error, requests.RequestException) as error:
        print(f"⚠️ Error recuperable durante el procesamiento LPR: {error}")
    finally:
        procesando_ia = False

def iniciar_sistema():
    global procesando_ia, ultimo_tiempo_procesamiento
    if not autenticar_backend():
        print("🛑 LPR detenido: no se iniciará la cámara sin autenticación válida.")
        return CODIGO_AUTENTICACION_FALLIDA

    # ── CONFIGURACIÓN DE CÁMARA ──
    # CAMERA_URL puede ser:
    #   - "0" o "1" para webcam USB
    #   - "rtsp://usuario:contraseña@ip:554/stream" para cámara IP
    #   - "http://ip:8080/video" para cámara IP con stream HTTP
    camera_url = os.getenv("CAMERA_URL", CAMARA_IP_POR_DEFECTO).strip()
    camera_user = os.getenv("CAMERA_USER", "")
    camera_pass = os.getenv("CAMERA_PASS", "")
    print(f"🎛️ Rol de cámara: {CAMERA_ROLE}")

    # Si es un número (0, 1, etc.), usar webcam USB
    if camera_url.isdigit():
        camera_idx = int(camera_url)
        cap = abrir_camara(camera_url)
        print(f"📷 Usando cámara USB (índice {camera_idx})")
    else:
        # Si hay credenciales separadas, armarlas en la URL
        if camera_user and camera_pass and "://" in camera_url:
            camera_url = construir_url_camara(camera_url, camera_user, camera_pass)
            print("🔐 Credenciales de cámara configuradas")

        cap = abrir_camara(camera_url)
        print(f"📷 Conectando a cámara IP: {camera_url.split('@')[-1] if '@' in camera_url else camera_url}")

    if not cap.isOpened():
        print("❌ Error: No se pudo abrir la cámara.")
        if camera_url.isdigit():
            print("   Probando índice 1...")
            cap = abrir_camara("1")
            if not cap.isOpened():
                print("❌ Error: No se encontró ninguna cámara. Conecta una cámara e inténtalo de nuevo.")
                return 1
        else:
            print("   Verifica que la URL RTSP/HTTP sea correcta y la cámara esté encendida.")
            print("   Ejemplo: CAMERA_URL=rtsp://admin:12345@192.168.1.100:554/stream")
            return 1

    print("\n" + "="*50)
    print("SISTEMA LPR AUTOMÁTICO INICIADO")
    print("="*50)

    ret, prev_frame = leer_frame_inicial(cap)
    if not ret:
        print("⚠️ No se pudo leer el primer frame. Reintentando en 3s...")
        time.sleep(3)
        ret, prev_frame = leer_frame_inicial(cap)
        if not ret:
            print("❌ Error: No se pudo obtener video de la cámara.")
            return 1

    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    prev_gray = cv2.GaussianBlur(prev_gray, (9, 9), 0)
    prev_gray = cv2.resize(prev_gray, (320, 180), interpolation=cv2.INTER_AREA)

    contador_frames = 0
    reconexiones = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            reconexiones += 1
            print(f"⚠️ Frame perdido (reconexión #{reconexiones}). Reintentando...")
            time.sleep(1)
            # Reabrir cámara si es IP
            if not camera_url.isdigit():
                cap.release()
                time.sleep(2)
                cap = abrir_camara(camera_url)
                if not cap.isOpened():
                    print("⚠️ La cámara IP sigue sin responder; se reintentará en el siguiente ciclo.")
            continue
        reconexiones = 0  # reset

        contador_frames += 1
        if contador_frames % 2 != 0:
            continue

        frame_resized = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2GRAY)
        gray_blur = cv2.GaussianBlur(gray, (9, 9), 0)
        frame_diff = cv2.absdiff(prev_gray, gray_blur)
        thresh = cv2.threshold(frame_diff, 20, 255, cv2.THRESH_BINARY)[1]
        movimiento = cv2.countNonZero(thresh)
        prev_gray = gray_blur

        ahora = time.time()
        debe_procesar = (
            movimiento > UMBRAL_MOVIMIENTO
            or (ahora - ultimo_tiempo_procesamiento) > INTERVALO_PROCESAMIENTO_SEGUNDOS
        )
        if debe_procesar and not procesando_ia and (ahora - ultimo_tiempo_procesamiento) > COOLDOWN_PROCESO_SEGUNDOS:
            ultimo_tiempo_procesamiento = ahora
            procesando_ia = True
            threading.Thread(target=tarea_ia_en_segundo_plano, args=(frame.copy(),), daemon=True).start()

        # --- CAMBIO 1 (Visual): Dibujar el nuevo recuadro más grande ---
        alto, ancho = frame.shape[:2]
        x1, y1 = int(ancho * 0.1), int(alto * 0.1)
        x2, y2 = int(ancho * 0.9), int(alto * 0.9)
        
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(frame, "LPR AUTOMATICO", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        cv2.imshow("ParqueaderoPRO - LPR Automatico", frame)

        # Ignorar TODAS las teclas. Solo se detiene si la ventana es destruida
        cv2.waitKey(1)

    cap.release()
    cv2.destroyAllWindows()


def ejecutar_con_watchdog():
    """
    Ejecuta el LPR en un proceso hijo. Si el hijo muere (administrador de tareas),
    el padre lo reinicia automáticamente.
    """
    while True:
        print("🟢 Iniciando LPR...")
        proceso = subprocess.Popen(
            [sys.executable, "-X", "utf8", __file__, "--lpr-child"],
            env={**os.environ, "LPR_CHILD": "1"},
        )
        proceso.wait()
        if proceso.returncode == CODIGO_AUTENTICACION_FALLIDA:
            print("🛑 LPR detenido por credenciales inválidas. Corrige LPR_PASSWORD y vuelve a ejecutar el lanzador.")
            return
        print(f"⚠️  LPR detenido (codigo {proceso.returncode}). Reiniciando en 3 segundos...")
        time.sleep(3)


if __name__ == "__main__":
    if os.getenv("LPR_CHILD") == "1":
        # Soy el proceso hijo: ejecutar LPR normalmente
        codigo_salida = iniciar_sistema()
        raise SystemExit(codigo_salida or 0)
    else:
        # Soy el padre: ejecutar con watchdog
        ejecutar_con_watchdog()