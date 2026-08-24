"""
Microsoft Teams sin API ni permisos de administrador.

Lo que se puede y lo que no, sin adornos
----------------------------------------
Teams no guarda los mensajes en tu disco de forma legible: viven en el
servidor de Microsoft. Para leerlos "de verdad" (historico, busqueda) haria
falta Microsoft Graph, y eso exige registrar una app en Azure y consentimiento
de un administrador que, en una cuenta de trabajo o de universidad, casi nunca
llega.

Asi que aqui se hace lo que si se puede hacer hoy, sin pedirle permiso a nadie:

- ABRIR Teams y NAVEGAR con enlaces msteams:, que es el mecanismo oficial de
  la propia aplicacion.
- LEER lo que este en pantalla, con el OCR de tools/pantalla.py.

O sea: Jarvis lee lo que TU verias mirando. Ni mas ni menos. Si el canal esta
abierto y hay mensajes a la vista, los lee. Lo que no se ve, no existe para el.
Es una limitacion real y no la disimulamos en las respuestas.
"""

import logging
import subprocess
import time
import urllib.parse

from tools import pantalla

log = logging.getLogger("jarvis.teams")

# Secciones de Teams a las que se llega por enlace, sin tocar la interfaz.
SECCIONES = {
    "chat": "msteams:/l/chat/0/0",
    "chats": "msteams:/l/chat/0/0",
    "mensajes": "msteams:/l/chat/0/0",
    "equipos": "msteams:/l/team/0/0",
    "canales": "msteams:/l/team/0/0",
    "calendario": "msteams:/l/meeting/0/0",
    "reuniones": "msteams:/l/meeting/0/0",
    "actividad": "msteams:/l/activity",
    "notificaciones": "msteams:/l/activity",
    "llamadas": "msteams:/l/call/0/0",
}


def _lanzar(url: str) -> bool:
    try:
        subprocess.Popen(f'start "" "{url}"', shell=True)
        return True
    except Exception as e:
        log.warning("No pude lanzar %s: %s", url, e)
        return False


def abrir(seccion: str = "") -> str:
    """Abre Teams, opcionalmente en una seccion concreta."""
    clave = (seccion or "").strip().lower()

    if not clave:
        from tools import sistema
        resultado = sistema.abrir_aplicacion("teams")
        if "No conozco" in resultado or "No pude" in resultado:
            # Reserva: el protocolo funciona aunque no encontremos el .exe.
            if _lanzar("msteams:"):
                return "Abriendo Teams."
        return resultado

    for nombre, url in SECCIONES.items():
        if nombre in clave:
            if _lanzar(url):
                return f"Abriendo {nombre} en Teams."
            return "No pude abrir Teams."

    return abrir_canal(clave)


def abrir_canal(nombre: str) -> str:
    """
    Abre la busqueda de Teams con el nombre del canal escrito.

    No se puede saltar directo a un canal por su nombre: el enlace msteams:
    de canal necesita identificadores internos que solo da la API. Lo que si
    funciona, y es lo que hace un humano, es abrir la busqueda con el texto
    ya puesto para que este a un Enter de distancia.
    """
    texto = (nombre or "").strip()
    if not texto:
        return "¿Qué canal quieres abrir?"

    url = "msteams:/l/search?q=" + urllib.parse.quote(texto)
    if not _lanzar(url):
        return "No pude abrir Teams."

    return (f"Abrí la búsqueda de Teams con {texto}. "
            "Dime 'lee la pantalla' cuando cargue y te cuento lo que hay.")


def leer_lo_visible(cuantas_lineas: int = 10) -> str:
    """
    Lee los mensajes que esten a la vista en Teams.

    Damos un momento a que la ventana termine de pintar: si se lee demasiado
    pronto, el OCR pilla la pantalla a medio cargar y devuelve trozos sueltos
    que suenan a galimatias.
    """
    time.sleep(1.2)

    # Recortamos la barra lateral izquierda y la de titulo. Ahi solo hay
    # nombres de menu y de equipos sueltos que, mezclados con los mensajes,
    # convierten la lectura en una lista de palabras sin sentido. Los mensajes
    # viven en la franja central-derecha.
    texto = pantalla.leer_pantalla(maximo_lineas=cuantas_lineas,
                                   zona=(0.22, 0.08, 1.0, 0.94))
    if texto.startswith("No pude") or texto.startswith("No veo"):
        return texto

    return texto.replace("En la pantalla leo:", "En Teams veo:", 1)


def resumen_actividad() -> str:
    """Abre la pestaña de Actividad y lee lo que aparezca."""
    if not _lanzar(SECCIONES["actividad"]):
        return "No pude abrir Teams."

    # Mas margen que en leer_lo_visible: cambiar de pestaña y traer la lista
    # del servidor tarda mas que repintar lo que ya estaba.
    time.sleep(2.0)

    # La lista de actividad ocupa la columna de la izquierda, no el centro.
    texto = pantalla.leer_pantalla(maximo_lineas=8, zona=(0.05, 0.08, 0.62, 0.94))
    if texto.startswith("No pude") or texto.startswith("No veo"):
        return "Abrí la actividad de Teams, pero no consigo leer la pantalla."

    return texto.replace("En la pantalla leo:", "En tu actividad de Teams:", 1)
