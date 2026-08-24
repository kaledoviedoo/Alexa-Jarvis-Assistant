"""
Configuración central de Jarvis.

Todo lo ajustable vive aquí. Si algo no te funciona, es muy probable que
la solución sea cambiar un valor de este archivo y no tocar el resto.

Las variables se pueden sobrescribir desde un archivo .env (ver .env.example).
"""

import os
import re
import shutil
from pathlib import Path

# -------------------------------------------------------------------------
# Carga de .env (sin dependencias externas)
# -------------------------------------------------------------------------
_ENV_PATH = Path(__file__).parent / ".env"
if _ENV_PATH.exists():
    for _linea in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _linea = _linea.strip()
        if not _linea or _linea.startswith("#") or "=" not in _linea:
            continue
        _k, _v = _linea.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


def _env(nombre: str, defecto: str = "") -> str:
    return os.environ.get(nombre, defecto)


def _env_bool(nombre: str, defecto: bool) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in ("1", "true", "si", "sí", "yes", "on")


# -------------------------------------------------------------------------
# RUTAS
# -------------------------------------------------------------------------
HOME = Path.home()

# OneDrive suele "secuestrar" el Escritorio en Windows. Probamos ambas rutas.
_CANDIDATOS_ESCRITORIO = [
    HOME / "Desktop",
    HOME / "Escritorio",
    HOME / "OneDrive" / "Desktop",
    HOME / "OneDrive" / "Escritorio",
]


# Nombres de las carpetas conocidas en el registro de Windows.
_CLAVES_REGISTRO = {
    "escritorio": "Desktop",
    "documentos": "Personal",
    "descargas": "{374DE290-123F-4565-9164-39C4925E467B}",
}


def _carpeta_por_registro(cual: str) -> Path | None:
    """
    Pregunta a Windows dónde está de verdad una carpeta conocida.

    Es la única forma fiable: cuando OneDrive hace copia de seguridad del
    Escritorio o los Documentos, los redirige a OneDrive\\... pero deja las
    carpetas antiguas en su sitio. Probar rutas a ojo acierta la equivocada
    la mitad de las veces; el registro guarda la que Windows considera buena.
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        clave = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
            valor, _ = winreg.QueryValueEx(k, _CLAVES_REGISTRO[cual])

        # El valor suele venir con variables sin expandir (%USERPROFILE%).
        ruta = Path(os.path.expandvars(valor))
        return ruta if ruta.is_dir() else None
    except Exception:
        return None


def _detectar_escritorio() -> Path:
    forzado = _env("JARVIS_ESCRITORIO")
    if forzado:
        return Path(forzado)

    del_registro = _carpeta_por_registro("escritorio")
    if del_registro is not None:
        return del_registro

    for ruta in _CANDIDATOS_ESCRITORIO:
        if ruta.is_dir():
            return ruta
    return HOME / "Desktop"


ESCRITORIO = _detectar_escritorio()
DESCARGAS = _carpeta_por_registro("descargas") or (HOME / "Downloads")
DOCUMENTOS = _carpeta_por_registro("documentos") or (HOME / "Documents")

# Carpetas donde Jarvis tiene permitido leer y escribir. Cualquier ruta fuera
# de estas se rechaza. Esto es lo que impide que un comando mal interpretado
# (o alguien que descubra tu túnel) toque C:\Windows.
# Incluimos TODOS los escritorios que existan, no solo el principal. Con
# OneDrive es habitual tener dos, y así Jarvis puede leer archivos de
# cualquiera de los dos aunque escriba siempre en el que marca el registro.
_TODAS = [ESCRITORIO, DESCARGAS, DOCUMENTOS, HOME / "Jarvis"] + _CANDIDATOS_ESCRITORIO + [
    HOME / "OneDrive" / "Documents",
    HOME / "OneDrive" / "Documentos",
]

CARPETAS_PERMITIDAS = []
for _p in _TODAS:
    if _p is None:
        continue
    try:
        _r = _p.resolve()
    except OSError:
        continue
    if _r not in CARPETAS_PERMITIDAS and (_r.is_dir() or _r == ESCRITORIO.resolve()):
        CARPETAS_PERMITIDAS.append(_r)

# Carpeta de trabajo interna: logs, estado, resultados en segundo plano.
CARPETA_DATOS = Path(_env("JARVIS_DATOS", str(HOME / ".jarvis")))
CARPETA_DATOS.mkdir(parents=True, exist_ok=True)
ARCHIVO_LOG = CARPETA_DATOS / "jarvis.log"
ARCHIVO_ESTADO = CARPETA_DATOS / "estado.json"

# Papelera propia: Jarvis nunca borra de verdad, mueve aquí.
PAPELERA = CARPETA_DATOS / "papelera"
PAPELERA.mkdir(parents=True, exist_ok=True)


# -------------------------------------------------------------------------
# MODOS DE OPERACIÓN
# -------------------------------------------------------------------------
# Perfilado para una RTX 3050 de 6 GB (PC de torre).
#
#   num_gpu = -1  -> Ollama sube todas las capas que quepan a la GPU
#   num_gpu = 0   -> inferencia 100% en CPU, la VRAM queda libre para juegos
#
# keep_alive define cuánto tiempo se queda el modelo cargado en VRAM. Es la
# variable MÁS importante para la velocidad: si el modelo se descarga, la
# siguiente orden tarda 20-40s en arrancar en frío y Alexa corta la sesión.

MODO_NORMAL = "normal"
MODO_DEDICADO = "dedicado"
MODO_GAMING = "gaming"

PERFILES = {
    MODO_NORMAL: {
        "nombre_hablado": "normal",
        "modelo": _env("JARVIS_MODELO_NORMAL", "llama3.2:3b"),
        "num_gpu": -1,
        "num_ctx": 4096,
        # ~2 GB en VRAM. Deja la GPU casi libre y responde en 1-2 segundos.
        "keep_alive": "30m",
        "temperatura": 0.3,
        "descripcion": "Modelo ligero siempre caliente. Respuestas casi instantáneas.",
    },
    MODO_DEDICADO: {
        "nombre_hablado": "dedicado",
        "modelo": _env("JARVIS_MODELO_DEDICADO", "qwen2.5:7b-instruct-q4_K_M"),
        "num_gpu": -1,
        "num_ctx": 8192,
        # ~4.7 GB en VRAM: entra en 6 GB con margen si no hay un juego abierto.
        "keep_alive": "60m",
        "temperatura": 0.4,
        "descripcion": "Modelo grande en la RTX 3050. Razona mejor, tarda más.",
    },
    MODO_GAMING: {
        "nombre_hablado": "gaming",
        "modelo": _env("JARVIS_MODELO_GAMING", "llama3.2:3b"),
        "num_gpu": 0,  # <- la clave: cero capas en GPU, VRAM libre para el juego
        "num_ctx": 2048,
        "keep_alive": "10m",
        "temperatura": 0.3,
        "descripcion": "Suelta la RTX 3050 por completo. Jarvis corre en CPU.",
    },
}

MODO_INICIAL = _env("JARVIS_MODO_INICIAL", MODO_NORMAL)

# Procesos que se cierran al entrar en modo gaming (liberan RAM y VRAM).
APPS_A_CERRAR_EN_GAMING = [
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "opera.exe",
    "Comet.exe",
    "Discord.exe",
    "Spotify.exe",
    "Teams.exe",
]

# Nunca se cierran, pase lo que pase.
PROCESOS_PROTEGIDOS = {
    "explorer.exe",
    "svchost.exe",
    "system",
    "system idle process",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "dwm.exe",
    "python.exe",
    "pythonw.exe",
    "py.exe",
    "uvicorn.exe",
    "ollama.exe",
    "ollama app.exe",
    "ngrok.exe",
    "tailscale.exe",
    "tailscaled.exe",
    "nvcontainer.exe",
    "nvdisplay.container.exe",
}


# -------------------------------------------------------------------------
# OLLAMA
# -------------------------------------------------------------------------
OLLAMA_HOST = _env("OLLAMA_HOST", "http://127.0.0.1:11434")

# Máximo de vueltas del bucle de herramientas (evita bucles infinitos).
MAX_PASOS_TOOLS = 4


# -------------------------------------------------------------------------
# PRESUPUESTO DE TIEMPO DE ALEXA  (¡crítico!)
# -------------------------------------------------------------------------
# Amazon corta la skill si el endpoint no responde en ~8 segundos. Reservamos
# margen para la red y el túnel: si el LLM no terminó en este tiempo, Jarvis
# contesta "lo estoy procesando" PERO la tarea sigue ejecutándose en segundo
# plano y el resultado queda guardado para la siguiente pregunta.
PRESUPUESTO_SEGUNDOS = float(_env("JARVIS_PRESUPUESTO_SEGUNDOS", "6.5"))

# Longitud máxima de lo que Alexa va a pronunciar.
MAX_CARACTERES_VOZ = 600

# -------------------------------------------------------------------------
# SESIÓN CONTINUA
# -------------------------------------------------------------------------
# Con esto en True, tras cada orden Alexa deja el micrófono abierto y puedes
# encadenar comandos sin repetir "Alexa, dile a mi asistente que...".
#
# Se cierra diciendo una despedida ("pausa", "gracias", "hasta luego") o
# quedándote callado unos segundos: Alexa cierra la sesión sola y vuelve a
# estar disponible para sus propios servicios.
SESION_CONTINUA = _env_bool("JARVIS_SESION_CONTINUA", True)


# -------------------------------------------------------------------------
# MANTENER EL CAMINO CALIENTE
# -------------------------------------------------------------------------
# Sintoma que resuelve: la primera orden tras un rato falla y al repetirla
# funciona. No es el servidor (el router responde en menos de 1 s siempre),
# sino el tunel, que tras estar ocioso tiene que rehacer la conexion y se come
# los 8 segundos que Alexa concede.
#
# Con esto, Jarvis se pide a si mismo su URL publica cada pocos minutos, de
# forma que DNS, TLS y la ruta del tunel nunca se enfrian.
MANTENER_CALIENTE = _env_bool("JARVIS_MANTENER_CALIENTE", True)

# Cada cuantos segundos. 240 (4 minutos) mantiene la ruta viva sin gastar nada:
# son unos 360 pings al dia de menos de 1 KB cada uno.
# 90 segundos: ahora el ping recorre de verdad el camino publico (ver
# mantener_caliente.py), asi que cada uno cuesta un saludo TLS. Sale barato
# comparado con que Alexa se rinda a los 8 segundos en la primera orden.
INTERVALO_CALIENTE = int(_env("JARVIS_INTERVALO_CALIENTE", "90"))

# URL publica: sirve tanto la de Tailscale como la de ngrok, la que este puesta.
TUNEL_URL = (_env("JARVIS_TUNEL_URL") or _env("JARVIS_NGROK_URL") or "").strip().rstrip("/")
if TUNEL_URL and not TUNEL_URL.startswith(("http://", "https://")):
    TUNEL_URL = "https://" + TUNEL_URL


# -------------------------------------------------------------------------
# SEGURIDAD
# -------------------------------------------------------------------------
# ID de tu skill. Lo copias de la consola de desarrollador de Alexa.
# Formato: amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ALEXA_SKILL_ID = _env("ALEXA_SKILL_ID", "")

# Nombre de invocación de la skill, tal cual está en la consola.
#
# Amazon no admite nombres de una sola palabra salvo que sean marca propia,
# así que tiene que ser de dos o más ("jarvis local", "mi asistente"...).
# Jarvis lo usa para recortarlo del comando: Alexa a veces lo deja dentro
# del slot y sin quitarlo el router no reconocería la orden.
ALEXA_INVOCATION_NAME = _env("ALEXA_INVOCATION_NAME", "jarvis")

# Verificación criptográfica de la firma de Amazon. Déjalo en True en producción.
# Ponlo en False SOLO para pruebas locales con curl.
VERIFICAR_FIRMA = _env_bool("JARVIS_VERIFICAR_FIRMA", True)

# Tolerancia del timestamp: Amazon exige 150 segundos.
TOLERANCIA_TIMESTAMP_SEGUNDOS = 150


# -------------------------------------------------------------------------
# NAVEGADOR (Comet)
# -------------------------------------------------------------------------
_LOCAL = Path(_env("LOCALAPPDATA", str(HOME / "AppData" / "Local")))
_PROGRAMAS = Path(_env("PROGRAMFILES", r"C:\Program Files"))
_PROGRAMAS86 = Path(_env("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))

# Comet es de Perplexity y se instala en sitios distintos segun la version.
# Aqui van las rutas conocidas; si ninguna acierta, mas abajo se busca de
# verdad en el disco y en el registro.
_CANDIDATOS_COMET = [
    _LOCAL / "Perplexity" / "Comet" / "Application" / "Comet.exe",
    _LOCAL / "Perplexity" / "Comet" / "Application" / "comet.exe",
    _LOCAL / "Programs" / "Comet" / "Comet.exe",
    _LOCAL / "Comet" / "Application" / "Comet.exe",
    _LOCAL / "Programs" / "Perplexity" / "Comet.exe",
    _PROGRAMAS / "Perplexity" / "Comet" / "Application" / "Comet.exe",
    _PROGRAMAS / "Comet" / "Application" / "Comet.exe",
    _PROGRAMAS86 / "Perplexity" / "Comet" / "Application" / "Comet.exe",
    _PROGRAMAS86 / "Comet" / "Application" / "Comet.exe",
]


def _exe_por_registro(nombre_exe: str) -> str:
    """
    Busca un ejecutable en "App Paths" del registro.

    Windows mantiene ahi la ruta de casi todo lo que se instala. Es mas fiable
    que adivinar carpetas, porque lo escribe el propio instalador.
    """
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""

    clave = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{nombre_exe}"
    for raiz in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(raiz, clave) as k:
                valor, _ = winreg.QueryValueEx(k, "")
            ruta = Path(os.path.expandvars(valor.strip('"')))
            if ruta.is_file():
                return str(ruta)
        except OSError:
            continue
    return ""


def _exe_por_acceso_directo(nombre: str) -> str:
    """
    Busca el .lnk del menu Inicio y lee a donde apunta.

    Si el programa sale en el menu Inicio, existe. Leemos el destino del
    acceso directo sin depender de librerias externas: el .lnk guarda la ruta
    en texto plano (UTF-16) y basta con pescarla.
    """
    if os.name != "nt":
        return ""

    menus = [
        Path(_env("APPDATA", str(HOME / "AppData" / "Roaming"))) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(_env("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]

    for menu in menus:
        if not menu.is_dir():
            continue
        try:
            enlaces = list(menu.rglob(f"*{nombre}*.lnk"))
        except OSError:
            continue
        for enlace in enlaces:
            try:
                crudo = enlace.read_bytes().decode("utf-16-le", errors="ignore")
            except OSError:
                continue
            for trozo in re.findall(r"[A-Za-z]:\\[^\x00]{3,240}?\.exe", crudo):
                ruta = Path(trozo)
                if ruta.is_file():
                    return str(ruta)
    return ""


def _exe_buscando_en_disco(nombre_exe: str) -> str:
    """Ultimo recurso: rastrea las carpetas donde se instalan los programas."""
    if os.name != "nt":
        return ""
    for raiz in (_LOCAL / "Programs", _LOCAL, _PROGRAMAS, _PROGRAMAS86):
        if not raiz.is_dir():
            continue
        try:
            # Profundidad acotada: rglob sobre C:\ entero tardaria minutos.
            for candidato in raiz.glob(f"*/*/{nombre_exe}"):
                if candidato.is_file():
                    return str(candidato)
            for candidato in raiz.glob(f"*/*/*/{nombre_exe}"):
                if candidato.is_file():
                    return str(candidato)
        except OSError:
            continue
    return ""


def localizar_ejecutable(nombre_exe: str, candidatos: list | None = None) -> str:
    """
    Encuentra un .exe probandolo todo, de lo barato a lo caro.

    Existe porque "start comet" no abre nada: `start` solo funciona con lo que
    esta en el PATH o registrado como protocolo, y los navegadores modernos
    se instalan en la carpeta del usuario sin tocar ninguna de las dos cosas.
    Windows respondia con su sonido de error mientras Jarvis decia "Abriendo
    comet" tan tranquilo.
    """
    for ruta in (candidatos or []):
        if Path(ruta).is_file():
            return str(ruta)

    encontrado = _exe_por_registro(nombre_exe)
    if encontrado:
        return encontrado

    encontrado = shutil.which(nombre_exe) or shutil.which(nombre_exe.removesuffix(".exe"))
    if encontrado:
        return encontrado

    encontrado = _exe_por_acceso_directo(nombre_exe.removesuffix(".exe"))
    if encontrado:
        return encontrado

    return _exe_buscando_en_disco(nombre_exe)


def detectar_comet() -> str:
    """Devuelve la ruta de Comet, o cadena vacia si no se encuentra."""
    forzado = _env("JARVIS_COMET_EXE")
    if forzado and Path(forzado).is_file():
        return forzado
    return localizar_ejecutable("Comet.exe", _CANDIDATOS_COMET)


MOTOR_BUSQUEDA = _env("JARVIS_MOTOR_BUSQUEDA", "https://www.perplexity.ai/search?q={q}")


# -------------------------------------------------------------------------
# QUIEN ERES
# -------------------------------------------------------------------------
NOMBRE_USUARIO = _env("JARVIS_NOMBRE_USUARIO", "Kaled")

# Cada cuantas respuestas se usa el nombre, aproximadamente. Uno de cada
# cuatro: llamarte por tu nombre en cada frase suena a teleoperador, y no
# hacerlo nunca suena a maquina. El termino medio es lo natural.
FRECUENCIA_NOMBRE = int(_env("JARVIS_FRECUENCIA_NOMBRE", "4"))


# -------------------------------------------------------------------------
# CORREO (Outlook de escritorio, en local)
# -------------------------------------------------------------------------
# Sin nube y sin OAuth: se habla con el Outlook que ya tienes abierto a traves
# de COM. Solo lectura.
CORREO_MAXIMO = int(_env("JARVIS_CORREO_MAXIMO", "5"))
CORREO_CARACTERES = int(_env("JARVIS_CORREO_CARACTERES", "300"))


# -------------------------------------------------------------------------
# PANTALLA
# -------------------------------------------------------------------------
# Ruta de Tesseract. Si esta en el PATH no hace falta tocar nada.
TESSERACT_EXE = _env("JARVIS_TESSERACT_EXE", "")
IDIOMA_OCR = _env("JARVIS_IDIOMA_OCR", "spa+eng")

# Modelo de vision para "describe la pantalla". Va SIEMPRE en segundo plano:
# en una RTX 3050 de 6 GB tarda mas de los 8 segundos que da Alexa.
MODELO_VISION = _env("JARVIS_MODELO_VISION", "llava:7b")


# -------------------------------------------------------------------------
# OBSIDIAN
# -------------------------------------------------------------------------
# Ruta del vault. Si se deja vacia, Jarvis lo busca solo por la carpeta
# oculta .obsidian, que es la firma de todo vault.
OBSIDIAN_VAULT = _env("JARVIS_OBSIDIAN_VAULT", "")


# -------------------------------------------------------------------------
# CONTROL DE TECLADO Y MOUSE (modo acotado)
# -------------------------------------------------------------------------
# Solo se permiten estos atajos. No hay clics a coordenadas ciegas.
ATAJOS_PERMITIDOS = {
    "copiar": ["ctrl", "c"],
    "pegar": ["ctrl", "v"],
    "cortar": ["ctrl", "x"],
    "deshacer": ["ctrl", "z"],
    "rehacer": ["ctrl", "y"],
    "guardar": ["ctrl", "s"],
    "seleccionar todo": ["ctrl", "a"],
    "buscar": ["ctrl", "f"],
    "imprimir": ["ctrl", "p"],
    "cerrar pestaña": ["ctrl", "w"],
    "nueva pestaña": ["ctrl", "t"],
    "reabrir pestaña": ["ctrl", "shift", "t"],
    "cambiar ventana": ["alt", "tab"],
    "cerrar ventana": ["alt", "f4"],
    "minimizar todo": ["win", "d"],
    "mostrar escritorio": ["win", "d"],
    "bloquear equipo": ["win", "l"],
    "explorador de archivos": ["win", "e"],
    "administrador de tareas": ["ctrl", "shift", "escape"],
    "escritorio virtual derecha": ["ctrl", "win", "right"],
    "escritorio virtual izquierda": ["ctrl", "win", "left"],
    "captura de pantalla": ["win", "shift", "s"],
    "subir volumen": ["volumeup"],
    "bajar volumen": ["volumedown"],
    "silenciar": ["volumemute"],
    "reproducir": ["playpause"],
    "pausar": ["playpause"],
    "siguiente canción": ["nexttrack"],
    "canción anterior": ["prevtrack"],
}

# Longitud máxima de texto que Jarvis puede escribir de un tirón.
MAX_CARACTERES_ESCRITURA = 2000
