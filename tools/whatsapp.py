"""Enviar mensajes de WhatsApp por la version web.

Abre web.whatsapp.com en Comet, busca el contacto, escribe el texto y
ANTES DE ENVIAR lee con OCR el nombre del chat que se abrio y lo confirma en
voz alta. Solo con el si se pulsa enviar; con el no se borra el texto.

Ese paso existe porque mandar un mensaje a la persona equivocada no se
deshace.
"""

import logging
import subprocess
import threading
import time

from config import detectar_comet
from tools import pantalla, ventanas
from registro import herramienta

log = logging.getLogger("jarvis.whatsapp")

URL = "https://web.whatsapp.com/"

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


SEGUNDOS_PARA_CARGAR = 25
SEGUNDOS_TRAS_APARECER = 4


def esperar_whatsapp() -> bool:
    """Abre WhatsApp Web y espera a que su ventana exista y cargue.

    Antes se lanzaba y se devolvia "repiteme el mensaje": el modelo lo tomaba
    como hecho y contestaba "voy a enviarlo" sin que nada pasara.
    """
    lanzar_en_segundo_plano()
    limite = time.monotonic() + SEGUNDOS_PARA_CARGAR
    while time.monotonic() < limite:
        time.sleep(1.0)
        if _ventana_de_whatsapp() is not None:
            # La ventana aparece antes de que carguen los chats.
            time.sleep(SEGUNDOS_TRAS_APARECER)
            return _enfocar()
    log.warning("WhatsApp Web no aparecio en %d s.", SEGUNDOS_PARA_CARGAR)
    return False


def esta_abierto() -> bool:
    return _ventana_de_whatsapp() is not None


BANDERAS_COMET = [
    "--disable-session-crashed-bubble",   # el globo de "se cerro inesperadamente"
    "--hide-crash-restore-bubble",        # el mismo globo en versiones nuevas
    "--no-default-browser-check",         # "¿quieres que sea tu navegador?"
    "--no-first-run",                     # la pantalla de bienvenida
    "--disable-features=InfobarUI",       # barras informativas varias
]


def descartar_avisos() -> None:
    """Cierra cualquier aviso que se haya colado encima del navegador."""
    try:
        import pyautogui
        pyautogui.press("escape")
        time.sleep(0.15)
    except Exception as e:
        log.debug("No pude descartar avisos: %s", e)


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


def _ventana_activa_es_whatsapp() -> bool:
    """¿Las teclas que se manden ahora llegan a WhatsApp?"""
    try:
        import pygetwindow as gw
        activa = gw.getActiveWindow()
        return activa is not None and "whatsapp" in (activa.title or "").lower()
    except Exception:
        return False


def _enfocar() -> bool:
    """Trae al frente WhatsApp Web y comprueba que de verdad tiene el teclado.

    Windows no siempre deja que un programa quite el foco a otro: la ventana
    sube pero las teclas siguen yendo a la de antes. Asi se escribio
    "familia" en otra aplicacion. Si tras los intentos el foco no es de
    WhatsApp, se devuelve False y nadie teclea nada.
    """
    ventana = _ventana_de_whatsapp()
    if ventana is None:
        return False

    ventanas.cambiar_a(ventana.title[:30])
    time.sleep(0.35)
    if not _ventana_activa_es_whatsapp():
        # Minimizar y restaurar es lo que Windows si respeta como "el usuario
        # quiere esta ventana".
        try:
            ventana.minimize()
            time.sleep(0.25)
            ventana.restore()
            time.sleep(0.2)
            ventana.activate()
            time.sleep(0.35)
        except Exception as e:
            log.debug("No pude reactivar WhatsApp: %s", e)
    try:
        if not ventana.isMaximized:
            ventana.maximize()
            time.sleep(0.3)
    except Exception as e:
        log.debug("No pude maximizar WhatsApp: %s", e)

    if not _ventana_activa_es_whatsapp():
        log.warning("WhatsApp esta al frente pero el teclado no es suyo: no tecleo nada.")
        return False
    return True


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


# La cabecera del chat esta justo debajo de la barra del navegador (unos
# 90 px a escala 100 %, unos 115 a 125 %) y a la derecha del panel de chats.
CABECERA_DESDE_PX = 85
CABECERA_HASTA_PX = 185


def zona_cabecera(ancho: int, alto: int) -> tuple[float, float, float, float]:
    """Donde esta el nombre del chat abierto, segun la forma de la ventana.

    El panel de chats de WhatsApp Web ocupa mas proporcion cuanto mas
    estrecha es la ventana: ~46 % en un monitor vertical de 1096 px, ~30 %
    en uno horizontal de 1920. La zona fija de antes (pensada para 1920x1080)
    caia en un monitor vertical sobre la fila de filtros.
    """
    if ancho < 1100:
        izquierda = 0.47
    elif ancho < 1441:
        izquierda = 0.41
    else:
        izquierda = 0.31
    alto = max(alto, CABECERA_HASTA_PX + 1)
    return (izquierda, CABECERA_DESDE_PX / alto, 0.98, CABECERA_HASTA_PX / alto)


def _quien_esta_abierto() -> str:
    """Lee de la pantalla el nombre del chat abierto."""
    # La primera linea, no la mas larga: el nombre va arriba y el subtitulo
    # debajo. Ordenar por longitud devolvia el subtitulo.
    # Dentro de la ventana de WhatsApp: con una zona fija de la pantalla se
    # leia lo que hubiera ahi ("Calculando", de otra aplicacion).
    ventana = _ventana_de_whatsapp()
    if ventana is None:
        return ""
    crudo = pantalla.leer_primera_linea(zona_cabecera(ventana.width, ventana.height), ventana=ventana)
    return _limpiar_nombre(crudo)


def _borrar_caja() -> str:
    """Vacia la caja de mensaje. Es el deshacer de un 'no'."""
    pg = _pyautogui()
    if pg is None:
        return "Cancelado, pero no pude borrar el texto: revísalo en WhatsApp."
    # Ctrl+A y borrar en otra ventana destrozaria lo que hubiera ahi.
    if not _enfocar() or not _ir_a_la_caja_de_mensaje(pg):
        return "Cancelado. No envié nada, pero el texto sigue escrito en WhatsApp: bórralo tú."
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
    # El "si" puede llegar desde PowerShell: el Enter tiene que ir a WhatsApp.
    if not _enfocar():
        return "No lo envié: no pude poner WhatsApp al frente. El mensaje sigue escrito, envíalo tú."
    try:
        pg.press("enter")
        log.info("Mensaje enviado.")
        return "Enviado."
    except Exception as e:
        return f"No pude enviar: {e}"


def coincide(pedido: str, real: str) -> bool:
    """¿El chat abierto es el pedido? 'familia' encaja con 'Familia Pérez Gómez'."""
    import re as _re
    pedidas = {p for p in _re.split(r"\W+", (pedido or "").lower()) if len(p) > 2}
    pedidas -= {"mis", "del", "los", "las", "grupo", "chat"}
    reales = {p for p in _re.split(r"\W+", (real or "").lower()) if len(p) > 2}
    return not pedidas or bool(pedidas & reales)


def abrir_y_comprobar(pg, destinatario: str) -> str:
    """Abre el chat y devuelve el nombre que se lee arriba.

    Si no es el pedido, repite la busqueda una vez con mas calma: lo normal
    es que WhatsApp Web acabara de cargar y la primera busqueda se perdiera.
    """
    _buscar_y_abrir_chat(pg, destinatario)
    nombre_real = _quien_esta_abierto()
    if nombre_real and coincide(destinatario, nombre_real):
        return nombre_real

    log.info("El chat abierto es %r y pedi %r: busco otra vez, mas despacio.", nombre_real, destinatario)
    time.sleep(2.0)
    _buscar_y_abrir_chat(pg, destinatario, lento=True)
    nombre_real = _quien_esta_abierto()
    if not coincide(destinatario, nombre_real):
        guardar_captura_de_diagnostico()
    return nombre_real


def guardar_captura_de_diagnostico() -> None:
    """Guarda lo que vio el OCR cuando el nombre del chat no encaja.

    Una sola copia en ~/.jarvis/capturas, que se pisa cada vez: sirve para
    ajustar la zona de la cabecera viendo la imagen en vez de adivinando.
    """
    try:
        from config import CARPETA_DATOS
        carpeta = CARPETA_DATOS / "capturas"
        carpeta.mkdir(parents=True, exist_ok=True)
        ventana = _ventana_de_whatsapp()
        entera = pantalla._captura(None, ventana)
        cabecera = (pantalla._captura(zona_cabecera(ventana.width, ventana.height), ventana)
                    if ventana is not None else None)
        if entera is not None:
            entera.save(carpeta / "whatsapp_ventana.png")
        if cabecera is not None:
            cabecera.save(carpeta / "whatsapp_cabecera.png")
        titulo = ventana.title if ventana is not None else "(sin ventana)"
        caja = (ventana.left, ventana.top, ventana.width, ventana.height) if ventana is not None else None
        log.info("Captura de diagnostico guardada en %s (ventana %r, caja %s)", carpeta, titulo, caja)
    except Exception as e:
        log.debug("No pude guardar la captura de diagnostico: %s", e)


class SinFoco(RuntimeError):
    """El teclado no es de WhatsApp: no se teclea."""


def pegar(pg, texto: str) -> None:
    """Pone el texto pegandolo, no tecleandolo.

    Teclear letra a letra respeta el Bloq Mayus ("vENTANAS ABIERTAS") y no
    sabe escribir tildes ni eñes. Pegar no tiene ninguno de los dos
    problemas. El portapapeles del usuario se deja como estaba.
    """
    try:
        import pyperclip
    except ImportError:
        pg.write(texto, interval=0.01)
        return

    respaldo = None
    try:
        respaldo = pyperclip.paste()
    except Exception as e:
        log.debug("No pude leer el portapapeles: %s", e)

    pyperclip.copy(texto)
    time.sleep(0.08)
    pg.hotkey("ctrl", "v")
    # El navegador pega de forma asincrona: devolver el portapapeles antes
    # de tiempo haria que pegara lo que el usuario tenia copiado.
    time.sleep(0.4)

    if respaldo is not None:
        try:
            pyperclip.copy(respaldo)
        except Exception as e:
            log.debug("No pude devolver el portapapeles: %s", e)


# La caja de busqueda esta a esta altura desde arriba de la ventana (a escala
# 100 %), centrada en el panel de chats.
BUSCADOR_PX = 178


def _ir_al_buscador(pg) -> bool:
    ventana = _ventana_de_whatsapp()
    if ventana is None:
        return False
    izquierda_del_chat = zona_cabecera(ventana.width, ventana.height)[0]
    x = int(ventana.left + ventana.width * izquierda_del_chat * 0.55)
    y = int(ventana.top + BUSCADOR_PX)
    pg.click(x, y)
    time.sleep(0.2)
    return _ventana_activa_es_whatsapp()


def _ir_a_la_caja_de_mensaje(pg) -> bool:
    """Clic en la caja de escribir, abajo en el panel del chat.

    Tras abrir el chat el foco suele quedar ahi, pero si la busqueda no se
    cerro bien queda en el buscador y el mensaje acababa escrito alli.
    """
    ventana = _ventana_de_whatsapp()
    if ventana is None:
        return False
    x = int(ventana.left + ventana.width * 0.62)
    y = int(ventana.top + ventana.height - 55)
    pg.click(x, y)
    time.sleep(0.25)
    return _ventana_activa_es_whatsapp()


def _buscar_y_abrir_chat(pg, nombre: str, lento: bool = False) -> None:
    """Busca el chat y lo abre. El cursor queda en la caja de mensaje."""
    if not _ventana_activa_es_whatsapp() and not _enfocar():
        raise SinFoco("el teclado no es de WhatsApp")
    pg.press("escape")
    time.sleep(0.15)
    pg.press("escape")
    time.sleep(0.2)

    # Clic en la caja de busqueda. El atajo Ctrl+Alt+/ no sirve con teclado
    # en español: la barra es Mayus+7 y Ctrl+Alt hace de AltGr.
    if not _ir_al_buscador(pg):
        raise SinFoco("no encuentro el buscador de WhatsApp")
    time.sleep(0.35)

    # Vaciar lo que quedara de una busqueda anterior: si no, la segunda
    # busqueda era "familiafamilia" y no encontraba nada.
    pg.hotkey("ctrl", "a")
    time.sleep(0.05)
    pg.press("backspace")
    time.sleep(0.1)

    pegar(pg, nombre)
    time.sleep(ESPERA_BUSQUEDA * (2.5 if lento else 1))

    pg.press("enter")
    time.sleep(ESPERA_CHAT * (2 if lento else 1))


@herramienta(
    descripcion="Envía un mensaje por WhatsApp Web, tras confirmar el nombre real del chat.",
    riesgo="externa", necesita=("windows",),
    args={"destinatario": "Contacto o grupo", "mensaje": "Texto a enviar"},
    palabras=("whatsapp", "mensaje", "manda", "dile", "escribele"),
    corroboran=("manda", "envia", "dile", "dila", "escribele", "escribe", "responde", "contesta", "avisa", "pasale", "reenvia", "comparte"),
)
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

    # Si ya hay una ventana de WhatsApp no se abre otra: antes, cada vez que
    # el foco fallaba a la primera, se abria una pestaña nueva.
    if _ventana_de_whatsapp() is None:
        if not esperar_whatsapp():
            return ("Abrí WhatsApp Web pero no terminó de cargar a tiempo. "
                    "No escribí nada; cuando lo veas cargado, repíteme el mensaje.")
    elif not _enfocar():
        return ("No pude poner WhatsApp al frente: el teclado seguía en otra ventana. "
                "No escribí nada. Haz clic en WhatsApp y repítemelo.")

    # Primero se abre el chat y se comprueba que es el pedido; solo despues se
    # escribe. Antes se escribia y luego se miraba, y si WhatsApp acababa de
    # cargar el texto quedaba en el chat que estuviera abierto.
    try:
        nombre_real = abrir_y_comprobar(pg, destinatario)
    except SinFoco:
        return ("No pude poner WhatsApp al frente: el teclado seguía en otra ventana. "
                "No escribí nada. Haz clic en WhatsApp y repítemelo.")
    except Exception as e:
        log.exception("Falló abriendo el chat")
        return f"Algo falló manejando WhatsApp: {e}"

    if not nombre_real:
        log.warning("No pude leer el nombre del chat abierto: no escribo nada.")
        return (f"Busqué {destinatario} en WhatsApp pero no consigo leer qué chat quedó abierto. "
                "No escribí nada para no mandarlo a quien no es.")

    if not coincide(destinatario, nombre_real):
        log.warning("Pedi %r y el chat abierto es %r: no escribo nada.", destinatario, nombre_real)
        return (f"No encontré el chat {destinatario}: el que quedó abierto es {nombre_real}. "
                "No escribí nada. Dime el nombre del chat tal como sale en WhatsApp.")

    if not _ventana_activa_es_whatsapp() or not _ir_a_la_caja_de_mensaje(pg):
        return ("El foco se fue de WhatsApp antes de escribir. No escribí nada. "
                "Repítemelo con WhatsApp al frente.")

    try:
        pegar(pg, mensaje)
        time.sleep(0.25)
    except Exception as e:
        log.exception("Falló escribiendo el mensaje")
        return f"Algo falló manejando WhatsApp: {e}"

    corto = mensaje if len(mensaje) <= 60 else mensaje[:60] + "..."

    if not nombre_real:
        return confirmaciones.pedir(
            f"enviar el mensaje a {destinatario}", _enviar_ahora,
            al_rechazar=_borrar_caja,
            pregunta=(f"Escribí {corto}, pero no consigo leer con quién está abierto el chat. "
                      f"Pedí buscar {destinatario}. ¿Lo envío igual? Di sí o no."),
        )

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


@herramienta(
    descripcion="Abre una conversación de WhatsApp Web sin escribir nada.",
    riesgo="escritura", necesita=("windows",),
    args={"nombre": "Nombre del contacto o del grupo"},
    # Abrir el chat es el primer paso de mandar algo: "mandale a familia"
    # tambien lo pide.
    corroboran=("abre", "chat", "whatsapp", "entra", "conversacion",
                "manda", "envia", "dile", "escribele", "avisa"),
    palabras=("whatsapp", "chat", "conversacion"),
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
        abrir_y_comprobar(pg, nombre)
    except Exception as e:
        return f"No pude abrir el chat: {e}"

    real = _quien_esta_abierto()
    return f"Abrí el chat de {real}." if real else f"Busqué {nombre} en WhatsApp."


@herramienta(
    descripcion="Lee los últimos mensajes visibles del chat de WhatsApp Web que esté abierto.",
    necesita=("windows", "tesseract"),
    palabras=("whatsapp", "chat", "mensajes", "que me escribieron", "que dicen"),
    fuente_externa=True,
)
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


@herramienta(
    descripcion="Abre WhatsApp Web.",
    riesgo="escritura", necesita=("windows",),
    corroboran=("abre", "whatsapp"), palabras=("whatsapp",),
)
def abrir_whatsapp() -> str:
    """Abre WhatsApp Web sin mas."""
    if _enfocar():
        return "Ya lo tenías abierto, te lo puse delante."
    _abrir_en_comet()
    return "Abriendo WhatsApp Web en Comet."
