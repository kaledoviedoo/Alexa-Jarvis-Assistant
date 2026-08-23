"""
Herramientas de navegador, orientadas a Comet (el navegador de Perplexity).

Si Comet no está instalado o no se detecta, cae automáticamente al navegador
predeterminado del sistema, así que nada se rompe.
"""

import logging
import os
import re
import subprocess
import urllib.parse
import webbrowser

from config import MOTOR_BUSQUEDA, detectar_comet

log = logging.getLogger("jarvis.navegador")

# Sitios frecuentes por nombre hablado.
SITIOS_CONOCIDOS = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "correo": "https://mail.google.com",
    "drive": "https://drive.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
    "perplexity": "https://www.perplexity.ai",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "twitch": "https://www.twitch.tv",
    "whatsapp": "https://web.whatsapp.com",
    "traductor": "https://translate.google.com",
    "maps": "https://maps.google.com",
    "mapas": "https://maps.google.com",
    "calendario": "https://calendar.google.com",
    "wikipedia": "https://es.wikipedia.org",
    "twitter": "https://x.com",
    "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com",
    "amazon": "https://www.amazon.com",
    "steam": "https://store.steampowered.com",
}


def _abrir_url(url: str) -> bool:
    """Abre una URL en Comet si está disponible; si no, en el navegador por defecto."""
    comet = detectar_comet()

    if comet:
        try:
            # Las mismas banderas que en WhatsApp: sin ellas, al abrir Comet
            # en frio sale la barra de "¿restaurar paginas?", se queda encima
            # y roba el foco. Aqui importa menos que al teclear un mensaje,
            # pero tampoco hace ninguna falta.
            from tools.whatsapp import BANDERAS_COMET

            if os.name == "nt":
                subprocess.Popen(
                    [comet, *BANDERAS_COMET, url],
                    creationflags=subprocess.DETACHED_PROCESS,
                    close_fds=True,
                )
            else:
                subprocess.Popen([comet, *BANDERAS_COMET, url], start_new_session=True)
            log.info("Abierto en Comet: %s", url)
            return True
        except Exception as e:
            log.warning("Falló abrir Comet (%s), uso el navegador por defecto.", e)

    try:
        webbrowser.open(url)
        log.info("Abierto en navegador por defecto: %s", url)
        return True
    except Exception as e:
        log.error("No se pudo abrir el navegador: %s", e)
        return False


def buscar_en_navegador(consulta: str) -> str:
    """Hace una búsqueda web en Comet."""
    consulta = (consulta or "").strip()
    if not consulta:
        return "¿Qué quieres que busque?"

    url = MOTOR_BUSQUEDA.format(q=urllib.parse.quote_plus(consulta))

    if _abrir_url(url):
        destino = "Comet" if detectar_comet() else "el navegador"
        return f"Buscando {consulta} en {destino}."
    return "No pude abrir el navegador."


def abrir_sitio(nombre: str) -> str:
    """Abre una página web por nombre ('youtube') o por dominio ('ejemplo.com')."""
    limpio = (nombre or "").strip().lower()
    if not limpio:
        return "¿Qué página quieres abrir?"

    # Alexa dicta los dominios como "ejemplo punto com".
    limpio = re.sub(r"\s+punto\s+", ".", limpio)
    limpio = limpio.replace(" ", "")

    if limpio in SITIOS_CONOCIDOS:
        url = SITIOS_CONOCIDOS[limpio]
        etiqueta = limpio
    elif re.match(r"^https?://", limpio):
        url, etiqueta = limpio, limpio
    elif re.match(r"^[\w.-]+\.[a-z]{2,}$", limpio):
        url, etiqueta = f"https://{limpio}", limpio
    else:
        # No parece una URL: lo tratamos como búsqueda.
        return buscar_en_navegador(nombre)

    if _abrir_url(url):
        return f"Abriendo {etiqueta}."
    return "No pude abrir el navegador."


def abrir_navegador() -> str:
    """Abre Comet en blanco."""
    comet = detectar_comet()
    if comet:
        try:
            if os.name == "nt":
                subprocess.Popen([comet], creationflags=subprocess.DETACHED_PROCESS)
            else:
                subprocess.Popen([comet], start_new_session=True)
            return "Abriendo Comet."
        except Exception as e:
            log.warning("No pude abrir Comet: %s", e)

    if _abrir_url("about:blank"):
        return "Abriendo el navegador."
    return "No encontré Comet instalado."


def reproducir_en_youtube(consulta: str) -> str:
    """Busca algo en YouTube y abre los resultados."""
    consulta = (consulta or "").strip()
    if not consulta:
        return "¿Qué quieres que reproduzca?"

    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(consulta)}"
    if _abrir_url(url):
        return f"Buscando {consulta} en YouTube."
    return "No pude abrir YouTube."
