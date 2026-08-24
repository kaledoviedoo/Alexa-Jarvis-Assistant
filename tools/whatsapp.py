"""
WhatsApp por la web, en Comet.

Por que NO la aplicacion de escritorio
--------------------------------------
El primer intento lanzaba el protocolo `whatsapp:`. Si la aplicacion no esta
instalada, Windows no falla: abre la Microsoft Store ofreciendola. O sea que
en vez de mandar un mensaje aparecia una tienda, y el bucle que esperaba a que
la ventana apareciera se comia DOCE SEGUNDOS antes de rendirse. Alexa concede
ocho. La sesion moria antes de que el usuario supiera que habia pasado.

Ahora se usa https://web.whatsapp.com/ en Comet, que es donde ya tienes la
sesion iniciada.

El reloj manda
--------------
Abrir el navegador y cargar WhatsApp Web en frio tarda bastante mas de lo que
Alexa aguanta. Por eso hay dos caminos:

  - Si WhatsApp Web YA esta abierto  -> se busca, se escribe y se pregunta.
    Son unos cinco segundos: entra, justo pero entra.
  - Si NO lo esta -> se abre y se contesta AL MOMENTO pidiendo que repitas la
    orden. Nada de esperar con la sesion de Alexa colgando.

La seguridad no cambia: el nombre que se te dice para confirmar sale de leer
la cabecera del chat que WhatsApp tiene abierto de verdad.
"""

import logging
import subprocess
import threading
import time

from config import detectar_comet
from tools import pantalla, ventanas

log = logging.getLogger("jarvis.whatsapp")

URL = "https://web.whatsapp.com/"

# Zona donde WhatsApp Web pinta el nombre del chat abierto. En navegador hay
# barra de pestañas y de direcciones encima, asi que la cabecera cae mas abajo
# que en la aplicacion de escritorio.
# La franja del nombre del chat. En navegador hay pestañas y barra de
# direcciones encima, asi que cae mas abajo que en una aplicacion. Estrecha a
# proposito: si se abre de mas, entra el subtitulo y se confunde con el nombre.
ZONA_CABECERA = (0.30, 0.11, 0.70, 0.175)
ZONA_CONVERSACION = (0.32, 0.20, 1.0, 0.88)

# Pausas justas: cada decima cuenta contra los ocho segundos de Alexa.
ESPERA_BUSQUEDA = 0.9
ESPERA_CHAT = 1.1

_abriendo = threading.Event()


def _pyautogui():
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        log.warning("Falta pyautogui.")
        return None


def _ventana_de_whatsapp():
    """La ventana del navegador que ya tiene WhatsApp Web abierto, o None."""
    try:
        import pygetwindow as gw
    except ImportError:
        return None

    for v in gw.getAllWindows():
        try:
            titulo = (v.title or "").lower()
            if "whatsapp" in titulo and v.width > 300:
                return v
        except Exception:
            continue
    return None


def esta_abierto() -> bool:
    return _ventana_de_whatsapp() is not None


# Banderas para que Comet no se pare a preguntar nada al arrancar en frio.
#
# El problema: si Comet estaba cerrado, al abrirlo aparece la barra de
# "¿Restaurar paginas?" (o el globo de sesion interrumpida). Se queda encima,
# roba el foco del teclado, y todo lo que Jarvis escriba despues se pierde o
# va a parar donde no debe. El flujo se quedaba a medias sin decir por que.
#
# Son banderas estandar de Chromium; Comet esta construido sobre el.
BANDERAS_COMET = [
    "--disable-session-crashed-bubble",   # el globo de "se cerro inesperadamente"
    "--hide-crash-restore-bubble",        # el mismo globo en versiones nuevas
    "--no-default-browser-check",         # "¿quieres que sea tu navegador?"
    "--no-first-run",                     # la pantalla de bienvenida
    "--disable-features=InfobarUI",       # barras informativas varias
]


def descartar_avisos() -> None:
    """
    Cierra cualquier aviso que se haya colado encima del navegador.

    Las banderas cubren el caso normal, pero no todos: una actualizacion
    reciente, un permiso pendiente o una notificacion pueden dejar algo
    delante. Escape las cierra sin tocar la pagina, y es inofensivo si no hay
    nada que cerrar.
    """
    try:
        import pyautogui
        pyautogui.press("escape")
        time.sleep(0.15)
    except Exception:
        pass


def _abrir_en_comet() -> None:
    """Abre WhatsApp Web en una pestaña nueva de Comet. No espera a nada."""
    comet = detectar_comet()
    try:
        if comet:
            # Las banderas van ANTES de la URL: Chromium interpreta lo que
            # viene despues de la direccion como mas direcciones.
            subprocess.Popen([comet, *BANDERAS_COMET, URL],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            log.info("Abriendo WhatsApp Web en Comet.")
        else:
            subprocess.Popen(f'start "" "{URL}"', shell=True)
            log.info("Comet no detectado: abro WhatsApp Web en el navegador por defecto.")
    except Exception as e:
        log.warning("No pude abrir WhatsApp Web: %s", e)


def lanzar_en_segundo_plano() -> None:
    """Abre WhatsApp Web sin esperar. Publica para que el router la use."""
    if _abriendo.is_set():
        return
    _abriendo.set()
    threading.Thread(target=_abrir_en_comet, daemon=True).start()
    # El cerrojo se suelta solo: si no, un fallo al abrir dejaria a Jarvis
    # creyendo para siempre que ya lo esta intentando.
    threading.Timer(20, _abriendo.clear).start()


def _enfocar() -> bool:
    """Trae al frente la ventana que ya tiene WhatsApp Web."""
    ventana = _ventana_de_whatsapp()
    if ventana is None:
        return False

    resultado = ventanas.cambiar_a(ventana.title[:30])
    time.sleep(0.35)
    return "Ahí tienes" in resultado or _ventana_de_whatsapp() is not None


# Texto de la interfaz que NO es el nombre de nadie. Sale en la cabecera, justo
# debajo del nombre, y el OCR lo mezcla con el. En el registro llego a
# confirmarse un envio "a here for group info", que es el subtitulo de un grupo
# en la version inglesa.
_RUIDO_CABECERA = (
    "click here", "here for", "group info", "info del grupo", "informacion del grupo",
    "haz clic", "haz click", "en linea", "en línea", "online", "last seen",
    "ultima vez", "última vez", "escribiendo", "typing", "toca aqui", "toca aquí",
    "tap here", "contact info", "datos del contacto", "participantes", "members",
)


def _limpiar_nombre(crudo: str) -> str:
    """Quita de la cabecera lo que es interfaz y no un nombre."""
    nombre = (crudo or "").strip(" .,:;-|")
    if not nombre:
        return ""

    bajo = nombre.lower()
    for ruido in _RUIDO_CABECERA:
        if ruido in bajo:
            # Nos quedamos con lo que hubiera ANTES del ruido, si es que hay
            # algo aprovechable.
            corte = bajo.index(ruido)
            nombre = nombre[:corte].strip(" .,:;-|")
            bajo = nombre.lower()
            if not nombre:
                return ""

    return nombre[:60] if len(nombre) > 1 else ""


def _quien_esta_abierto() -> str:
    """Lee de la pantalla el nombre del chat abierto."""
    # La primera linea, no la mas larga: el nombre va arriba y el subtitulo
    # debajo. Ordenar por longitud devolvia el subtitulo.
    crudo = pantalla.leer_primera_linea(ZONA_CABECERA)
    return _limpiar_nombre(crudo)


def _borrar_caja() -> str:
    """Vacia la caja de mensaje. Es el deshacer de un 'no'."""
    pg = _pyautogui()
    if pg is None:
        return "Cancelado, pero no pude borrar el texto: revísalo en WhatsApp."
    try:
        pg.hotkey("ctrl", "a")
        time.sleep(0.12)
        pg.press("backspace")
        log.info("Mensaje borrado sin enviar.")
        return "Vale, lo borré. No se envió nada."
    except Exception as e:
        return f"Cancelado, pero no pude borrar el texto: {e}"


def _enviar_ahora() -> str:
    pg = _pyautogui()
    if pg is None:
        return "No pude enviar, falta pyautogui."
    try:
        pg.press("enter")
        log.info("Mensaje enviado.")
        return "Enviado."
    except Exception as e:
        return f"No pude enviar: {e}"


def _buscar_y_abrir_chat(pg, nombre: str) -> None:
    """Busca el chat y lo abre. El cursor queda en la caja de mensaje."""
    # Doble Escape: el primero se lleva por delante cualquier aviso del
    # navegador que se haya quedado encima (restaurar sesion, permisos), el
    # segundo cierra el chat anterior y devuelve el foco a la lista.
    pg.press("escape")
    time.sleep(0.15)
    pg.press("escape")
    time.sleep(0.2)

    # Atajo de busqueda de WhatsApp Web.
    pg.hotkey("ctrl", "alt", "/")
    time.sleep(0.45)

    pg.write(nombre, interval=0.015)
    time.sleep(ESPERA_BUSQUEDA)

    pg.press("enter")
    time.sleep(ESPERA_CHAT)


def enviar_mensaje(destinatario: str, mensaje: str) -> str:
    """Prepara el mensaje y pide confirmacion con el nombre REAL del chat."""
    import confirmaciones
    import re as _re

    destinatario = (destinatario or "").strip()
    mensaje = (mensaje or "").strip()

    if not destinatario:
        return "¿A quién le mando el mensaje?"
    if not mensaje:
        return "¿Qué quieres que le diga?"

    pg = _pyautogui()
    if pg is None:
        return "No puedo manejar WhatsApp, falta pyautogui."

    # --- WhatsApp Web todavia no esta abierto ---
    # Contestamos YA. Esperar a que cargue serian mas de ocho segundos y Alexa
    # cerraria la sesion: exactamente lo que pasaba antes.
    if not _enfocar():
        lanzar_en_segundo_plano()
        return ("Estoy abriendo WhatsApp Web en Comet. "
                "Dame unos segundos y repíteme el mensaje.")

    try:
        _buscar_y_abrir_chat(pg, destinatario)
        nombre_real = _quien_esta_abierto()
        pg.write(mensaje, interval=0.008)
        time.sleep(0.25)
    except Exception as e:
        log.exception("Falló preparando el mensaje")
        return f"Algo falló manejando WhatsApp: {e}"

    corto = mensaje if len(mensaje) <= 60 else mensaje[:60] + "..."

    if not nombre_real:
        return confirmaciones.pedir(
            f"enviar el mensaje a {destinatario}", _enviar_ahora,
            al_rechazar=_borrar_caja,
            pregunta=(f"Escribí {corto}, pero no consigo leer con quién está abierto el chat. "
                      f"Pedí buscar {destinatario}. ¿Lo envío igual? Di sí o no."),
        )

    # ¿Se parece lo que abrio WhatsApp a lo que pediste? Si no comparten ni una
    # palabra, lo mas probable es que Alexa transcribiera mal el nombre y el
    # buscador abriera el primer chat que le sono. Este aviso es lo unico que
    # separa "casi lo mando a quien no era" de haberlo mandado.
    pedidas = {p for p in _re.split(r"\W+", destinatario.lower()) if len(p) > 2}
    reales = {p for p in _re.split(r"\W+", nombre_real.lower()) if len(p) > 2}

    if pedidas and not (pedidas & reales):
        return confirmaciones.pedir(
            f"enviar el mensaje a {nombre_real}", _enviar_ahora,
            al_rechazar=_borrar_caja,
            pregunta=(f"Ojo: pedí buscar {destinatario} pero el chat abierto es {nombre_real}. "
                      f"Está escrito {corto}. ¿Lo envío igual? Di sí o no."),
        )

    return confirmaciones.pedir(
        f"enviar el mensaje a {nombre_real}", _enviar_ahora,
        al_rechazar=_borrar_caja,
        pregunta=f"Tengo abierto el chat de {nombre_real} y escrito {corto}. ¿Lo envío? Di sí o no.",
    )


def abrir_chat(nombre: str) -> str:
    """Solo abre la conversacion, sin escribir nada."""
    nombre = (nombre or "").strip()
    if not nombre:
        return "¿Qué chat quieres abrir?"

    pg = _pyautogui()
    if pg is None:
        return "Falta pyautogui."

    if not _enfocar():
        lanzar_en_segundo_plano()
        return "Estoy abriendo WhatsApp Web en Comet. Dame unos segundos y repítemelo."

    try:
        _buscar_y_abrir_chat(pg, nombre)
    except Exception as e:
        return f"No pude abrir el chat: {e}"

    real = _quien_esta_abierto()
    return f"Abrí el chat de {real}." if real else f"Busqué {nombre} en WhatsApp."


def leer_chat() -> str:
    """Lee los ultimos mensajes visibles del chat abierto."""
    if not _enfocar():
        lanzar_en_segundo_plano()
        return "Estoy abriendo WhatsApp Web en Comet. Dame unos segundos y repítemelo."

    time.sleep(0.4)
    quien = _quien_esta_abierto()
    texto = pantalla.leer_pantalla(maximo_lineas=8, zona=ZONA_CONVERSACION)

    if texto.startswith("No pude") or texto.startswith("No veo"):
        return "No consigo leer la conversación."

    cuerpo = texto.replace("En la pantalla leo:", "").strip()
    return f"En el chat de {quien}: {cuerpo}" if quien else f"En WhatsApp veo: {cuerpo}"


def abrir_whatsapp() -> str:
    """Abre WhatsApp Web sin mas."""
    if _enfocar():
        return "Ya lo tenías abierto, te lo puse delante."
    _abrir_en_comet()
    return "Abriendo WhatsApp Web en Comet."
