"""
Cambiar de ventana por voz.

Se apoya en pygetwindow, que ya viene con pyautogui, asi que no hay nada
nuevo que instalar.

Detalle que parece tonto y no lo es: Windows se niega a que un proceso
cualquiera ponga otra ventana en primer plano. Es una proteccion contra
programas que se cuelan encima de lo que estas haciendo. `activate()` falla
con un error de acceso denegado bastante a menudo, asi que aqui se intenta
primero minimizar y restaurar, que es el truco que si funciona: al restaurar,
Windows le concede el foco.
"""

import difflib
import logging
import time

log = logging.getLogger("jarvis.ventanas")

# Ventanas del sistema que nunca son lo que el usuario quiere.
_INVISIBLES = {
    "", "Program Manager", "Windows Input Experience", "Configuración",
    "Microsoft Text Input Application", "NVIDIA GeForce Overlay",
    "Windows Shell Experience Host", "Search", "Buscar",
}


def _pygetwindow():
    try:
        import pygetwindow
        return pygetwindow
    except ImportError:
        log.warning("Falta pygetwindow (viene con pyautogui).")
        return None


def _ventanas_reales(gw) -> list:
    salida = []
    for v in gw.getAllWindows():
        try:
            titulo = (v.title or "").strip()
            if titulo in _INVISIBLES or not titulo:
                continue
            # Las de tamaño cero existen pero no se ven.
            if v.width < 50 or v.height < 50:
                continue
            salida.append(v)
        except Exception:
            continue
    return salida


def listar() -> str:
    """Que ventanas hay abiertas."""
    gw = _pygetwindow()
    if gw is None:
        return "No puedo ver las ventanas, falta pygetwindow."

    ventanas = _ventanas_reales(gw)
    if not ventanas:
        return "No veo ninguna ventana abierta."

    titulos = []
    for v in ventanas[:8]:
        # Los titulos largos traen la ruta entera del archivo y el nombre del
        # programa; para escuchar, con el trozo util basta.
        titulo = v.title.split(" - ")[0].strip()
        if titulo and titulo not in titulos:
            titulos.append(titulo[:40])

    return f"Tienes {len(ventanas)} ventanas. Las principales: " + ", ".join(titulos[:6])


def _buscar(gw, nombre: str):
    """La ventana que mejor encaja con lo que se dijo."""
    objetivo = nombre.strip().lower()
    ventanas = _ventanas_reales(gw)

    # Por contenido del titulo, que es lo mas natural: "cambia a chrome"
    # tiene que encontrar "Gmail - Google Chrome".
    for v in ventanas:
        if objetivo in v.title.lower():
            return v

    # Y si no, por parecido, que Alexa transcribe los nombres como quiere.
    titulos = [v.title.lower() for v in ventanas]
    parecidos = difflib.get_close_matches(objetivo, titulos, n=1, cutoff=0.6)
    if parecidos:
        return ventanas[titulos.index(parecidos[0])]

    return None


def cambiar_a(nombre: str) -> str:
    """Pone una ventana en primer plano."""
    gw = _pygetwindow()
    if gw is None:
        return "No puedo cambiar de ventana, falta pygetwindow."

    objetivo = (nombre or "").strip()
    if not objetivo:
        return "¿A qué ventana quieres que cambie?"

    ventana = _buscar(gw, objetivo)
    if ventana is None:
        return f"No encuentro ninguna ventana de {objetivo}."

    titulo = ventana.title.split(" - ")[0].strip()[:40]

    try:
        if ventana.isMinimized:
            ventana.restore()
        else:
            # El rodeo que hace falta: Windows no deja robar el foco de forma
            # directa, pero si devolverselo a una ventana que se restaura.
            ventana.minimize()
            time.sleep(0.15)
            ventana.restore()
        time.sleep(0.1)
        ventana.activate()
    except Exception as e:
        # activate() falla a menudo aunque la ventana YA se haya puesto
        # delante con el rodeo anterior. Comprobamos el resultado real en vez
        # de fiarnos del error.
        log.debug("activate() se quejó: %s", e)
        try:
            if gw.getActiveWindow() and gw.getActiveWindow().title == ventana.title:
                return f"Ahí tienes {titulo}."
        except Exception:
            pass
        return f"Intenté cambiar a {titulo} pero Windows no me dejó traerla al frente."

    log.info("Ventana al frente: %s", titulo)
    return f"Ahí tienes {titulo}."


def minimizar_todo() -> str:
    """Enseña el escritorio."""
    try:
        import pyautogui
        pyautogui.hotkey("win", "d")
        return "Escritorio a la vista."
    except Exception as e:
        return f"No pude minimizar: {e}"


def maximizar_actual() -> str:
    gw = _pygetwindow()
    if gw is None:
        return "Falta pygetwindow."
    try:
        activa = gw.getActiveWindow()
        if activa is None:
            return "No hay ninguna ventana activa."
        activa.maximize()
        return f"Maximicé {activa.title.split(' - ')[0][:40]}."
    except Exception as e:
        return f"No pude maximizar: {e}"
