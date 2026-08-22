"""
Control de teclado y mouse en modo ACOTADO.

Decisión de diseño deliberada: no existe ninguna función que haga clic en una
coordenada arbitraria. Un reconocimiento de voz equivocado no puede hacer clic
en "Eliminar cuenta" porque Jarvis simplemente no sabe hacer clics ciegos.

Lo que sí puede hacer:
  - escribir texto en la ventana activa
  - ejecutar atajos de una lista blanca (config.ATAJOS_PERMITIDOS)
  - tomar capturas de pantalla
  - desplazar la rueda del mouse
  - pulsar teclas simples (enter, escape, tab, flechas)
"""

import logging
import time
from datetime import datetime
from pathlib import Path

from config import (
    ATAJOS_PERMITIDOS,
    ESCRITORIO,
    MAX_CARACTERES_ESCRITURA,
)

log = logging.getLogger("jarvis.entrada")

# pyautogui se importa de forma perezosa: en un servidor sin escritorio activo
# su import falla, y no queremos que eso tumbe todo Jarvis al arrancar.
_pyautogui = None
_intento_import = False


def _obtener_pyautogui():
    global _pyautogui, _intento_import
    if _intento_import:
        return _pyautogui

    _intento_import = True
    try:
        import pyautogui

        # Desactivamos la "failsafe" de la esquina: con control por voz, mover
        # el mouse a la esquina superior izquierda no debería abortar todo.
        pyautogui.FAILSAFE = False
        # Pausa mínima entre acciones para que las apps alcancen a procesarlas.
        pyautogui.PAUSE = 0.05
        _pyautogui = pyautogui
        log.info("pyautogui cargado correctamente.")
    except Exception as e:
        log.warning("pyautogui no disponible: %s", e)
        _pyautogui = None

    return _pyautogui


# Teclas sueltas que se pueden pulsar por nombre.
TECLAS_SIMPLES = {
    "enter": "enter",
    "entrar": "enter",
    "aceptar": "enter",
    "escape": "esc",
    "escapar": "esc",
    "tabulador": "tab",
    "tab": "tab",
    "espacio": "space",
    "borrar": "backspace",
    "suprimir": "delete",
    "arriba": "up",
    "abajo": "down",
    "izquierda": "left",
    "derecha": "right",
    "inicio": "home",
    "fin": "end",
    "página arriba": "pageup",
    "pagina arriba": "pageup",
    "página abajo": "pagedown",
    "pagina abajo": "pagedown",
}


def escribir_texto(texto: str, pulsar_enter: bool = False) -> str:
    """Escribe texto en la ventana que esté activa."""
    pyautogui = _obtener_pyautogui()
    if pyautogui is None:
        return "No tengo control del teclado. Falta instalar pyautogui."

    texto = (texto or "").strip()
    if not texto:
        return "¿Qué quieres que escriba?"

    if len(texto) > MAX_CARACTERES_ESCRITURA:
        return f"Ese texto es demasiado largo, el límite son {MAX_CARACTERES_ESCRITURA} caracteres."

    try:
        # Damos un instante para que el usuario tenga la ventana correcta al frente.
        time.sleep(0.3)
        # write() no maneja tildes ni ñ en todos los teclados; para texto en
        # español usamos el portapapeles cuando hay caracteres no ASCII.
        if any(ord(c) > 127 for c in texto):
            _escribir_via_portapapeles(texto, pyautogui)
        else:
            pyautogui.write(texto, interval=0.01)

        if pulsar_enter:
            pyautogui.press("enter")
    except Exception as e:
        log.exception("Error escribiendo texto")
        return f"No pude escribir: {e}"

    log.info("Texto escrito (%d caracteres)", len(texto))
    return f"Escribí el texto ({len(texto)} caracteres)."


def _escribir_via_portapapeles(texto: str, pyautogui) -> None:
    """Pega texto con acentos usando el portapapeles (más fiable que write)."""
    try:
        import pyperclip

        respaldo = ""
        try:
            respaldo = pyperclip.paste()
        except Exception:
            pass

        pyperclip.copy(texto)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.1)

        # Devolvemos el portapapeles a como estaba.
        if respaldo:
            try:
                pyperclip.copy(respaldo)
            except Exception:
                pass
    except ImportError:
        # Sin pyperclip: escribimos igual, aceptando que algún acento se pierda.
        pyautogui.write(texto, interval=0.01)


def ejecutar_atajo(nombre_atajo: str) -> str:
    """Ejecuta un atajo de teclado de la lista blanca."""
    pyautogui = _obtener_pyautogui()
    if pyautogui is None:
        return "No tengo control del teclado. Falta instalar pyautogui."

    clave = (nombre_atajo or "").strip().lower()
    teclas = ATAJOS_PERMITIDOS.get(clave)

    if teclas is None:
        # Coincidencia parcial: "minimiza todo" -> "minimizar todo"
        for alias, combo in ATAJOS_PERMITIDOS.items():
            if alias in clave or clave in alias:
                teclas, clave = combo, alias
                break

    if teclas is None:
        disponibles = ", ".join(list(ATAJOS_PERMITIDOS)[:6])
        return f"No conozco el atajo '{nombre_atajo}'. Puedo hacer: {disponibles}, entre otros."

    try:
        if len(teclas) == 1:
            pyautogui.press(teclas[0])
        else:
            pyautogui.hotkey(*teclas)
    except Exception as e:
        return f"No pude ejecutar el atajo: {e}"

    log.info("Atajo ejecutado: %s -> %s", clave, teclas)
    return f"Listo, {clave}."


def pulsar_tecla(nombre_tecla: str, veces: int = 1) -> str:
    """Pulsa una tecla simple (enter, escape, flechas...)."""
    pyautogui = _obtener_pyautogui()
    if pyautogui is None:
        return "No tengo control del teclado."

    clave = (nombre_tecla or "").strip().lower()
    tecla = TECLAS_SIMPLES.get(clave)

    if tecla is None:
        return f"No conozco la tecla '{nombre_tecla}'."

    veces = max(1, min(int(veces), 20))
    try:
        for _ in range(veces):
            pyautogui.press(tecla)
    except Exception as e:
        return f"No pude pulsar la tecla: {e}"

    return f"Pulsé {clave}{f' {veces} veces' if veces > 1 else ''}."


def desplazar(direccion: str = "abajo", cantidad: int = 5) -> str:
    """Desplaza la rueda del mouse."""
    pyautogui = _obtener_pyautogui()
    if pyautogui is None:
        return "No tengo control del mouse."

    cantidad = max(1, min(int(cantidad), 30))
    unidades = cantidad * 120  # una "muesca" de rueda son 120 unidades

    if (direccion or "").strip().lower() in ("arriba", "up", "subir"):
        unidades = abs(unidades)
    else:
        unidades = -abs(unidades)

    try:
        pyautogui.scroll(unidades)
    except Exception as e:
        return f"No pude desplazar: {e}"

    return f"Desplacé hacia {direccion}."


def _siguiente_nombre_captura() -> str:
    """Devuelve captura1.png, captura2.png... buscando el primer hueco libre."""
    carpeta = Path(ESCRITORIO)
    usados = set()

    try:
        for archivo in carpeta.glob("captura*.png"):
            resto = archivo.stem[len("captura"):]
            if resto.isdigit():
                usados.add(int(resto))
    except Exception:
        pass

    numero = 1
    while numero in usados:
        numero += 1
    return f"captura{numero}.png"


def captura_pantalla(nombre: str = "") -> str:
    """Toma una captura de pantalla y la guarda en el Escritorio."""
    pyautogui = _obtener_pyautogui()
    if pyautogui is None:
        return "No puedo tomar capturas. Falta instalar pyautogui y Pillow."

    if not nombre:
        # Numeracion correlativa en vez de marca de tiempo: "captura1.png" es
        # mucho mas facil de nombrar por voz despues ("abre la captura 3")
        # que "captura_20260821_143052.png".
        nombre = _siguiente_nombre_captura()
    if not nombre.lower().endswith(".png"):
        nombre += ".png"

    destino = Path(ESCRITORIO) / nombre

    try:
        imagen = pyautogui.screenshot()
        imagen.save(destino)
    except Exception as e:
        log.exception("Error tomando captura")
        return f"No pude tomar la captura: {e}"

    log.info("Captura guardada: %s", destino)
    return f"Guardé la captura como {destino.name} en el escritorio."


def escribir_y_buscar(texto: str) -> str:
    """
    Combo útil: enfoca la barra de direcciones del navegador (Ctrl+L),
    escribe la consulta y pulsa Enter.
    """
    pyautogui = _obtener_pyautogui()
    if pyautogui is None:
        return "No tengo control del teclado."

    try:
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.2)
        _escribir_via_portapapeles(texto, pyautogui) if any(
            ord(c) > 127 for c in texto
        ) else pyautogui.write(texto, interval=0.01)
        time.sleep(0.1)
        pyautogui.press("enter")
    except Exception as e:
        return f"No pude hacer la búsqueda: {e}"

    return f"Buscando {texto} en la ventana activa."
