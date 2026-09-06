"""Como suena Jarvis.

Dos cosas. Como te llama (alterna entre tu nombre, parce y bro, siempre al
principio de la frase y solo una de cada cuatro veces) y como pronuncia lo
que esta en ingles (marca esas palabras con SSML para que el motor cambie de
fonetica y no lea "GitHub" como "guitub").
"""

import logging
import random
import re

from config import FRECUENCIA_NOMBRE, NOMBRE_USUARIO

log = logging.getLogger("jarvis.voz")

TRATAMIENTOS = [t for t in (NOMBRE_USUARIO, NOMBRE_USUARIO, "parce", "bro") if t]

# El ultimo que se uso, para no repetirlo seguido. Dos "bro" en dos frases
# cantan mucho mas que un "bro" suelto.
_ultimo_tratamiento = ""

_PLANTILLAS_INICIO = ["{n}, {t}.", "{n}, {t}.", "Mira {n}, {t}.", "{n}: {t}."]


def _sin_punto(texto: str) -> str:
    return texto.rstrip().rstrip(".").rstrip()


def _elegir_tratamiento() -> str:
    """Uno de los tres, pero nunca el mismo dos veces seguidas."""
    global _ultimo_tratamiento
    opciones = [t for t in TRATAMIENTOS if t != _ultimo_tratamiento] or TRATAMIENTOS
    elegido = random.choice(opciones)
    _ultimo_tratamiento = elegido
    return elegido


def _ya_te_nombra(texto: str) -> bool:
    """Si la frase ya te llama de alguna forma, no le pegamos otra."""
    bajo = texto.lower()
    if NOMBRE_USUARIO and NOMBRE_USUARIO.lower() in bajo:
        return True
    return any(re.search(rf"\b{t}\b", bajo) for t in ("parce", "bro"))


def con_nombre(texto: str, siempre: bool = False) -> str:
    """Te llama por tu nombre, o parce, o bro. A veces."""
    if not texto:
        return texto

    if not TRATAMIENTOS:
        return texto

    if _ya_te_nombra(texto):
        return texto

    if not siempre:
        if FRECUENCIA_NOMBRE <= 0 or random.randint(1, FRECUENCIA_NOMBRE) != 1:
            return texto

    limpio = _sin_punto(texto)
    if not limpio:
        return texto

    tratamiento = _elegir_tratamiento()

    # Las preguntas y exclamaciones no admiten plantilla: el vocativo tiene
    # que quedar FUERA de los signos y la frase entrar entera detras.
    if limpio.endswith(("?", "!")):
        # Si empieza por signo de apertura, la minuscula va DESPUES del signo:
        # "Kaled, ¿Que aplicacion..." con mayuscula queda mal.
        if limpio[0] in "¿¡" and len(limpio) > 2:
            cuerpo = limpio[0] + limpio[1].lower() + limpio[2:]
        elif limpio[:2].isupper():
            cuerpo = limpio
        else:
            cuerpo = limpio[0].lower() + limpio[1:]
        return f"{tratamiento}, {cuerpo}"

    # Minuscula inicial al encadenar, salvo que empiece por nombre propio o
    # siglas (CPU, Obsidian), que no se tocan.
    cuerpo = limpio if limpio[:2].isupper() else limpio[0].lower() + limpio[1:]
    return random.choice(_PLANTILLAS_INICIO).format(n=tratamiento, t=cuerpo)


def saludo_inicial(modo_hablado: str, continua: bool) -> str:
    """El saludo de apertura. Aqui el nombre va siempre."""
    if continua:
        return (
            f"Hola {NOMBRE_USUARIO}. Jarvis en línea en modo {modo_hablado}. "
            "Te escucho: dime órdenes seguidas sin repetir mi nombre. "
            "Di pausa cuando termines."
        )
    return f"Hola {NOMBRE_USUARIO}. Jarvis en línea en modo {modo_hablado}. ¿Qué necesitas?"


PALABRAS_INGLESAS = {
    # Programas y servicios
    "github", "gmail", "google", "chrome", "outlook", "teams", "onedrive",
    "spotify", "discord", "steam", "twitch", "youtube", "whatsapp", "telegram",
    "windows", "microsoft", "office", "excel", "word", "powerpoint",
    "obsidian", "notion", "slack", "zoom", "epic", "valorant", "riot",
    "nvidia", "geforce", "intel", "amd", "ryzen", "radeon",
    "ollama", "python", "javascript", "typescript", "node", "docker",
    "visual", "studio", "code", "cursor", "comet", "perplexity", "claude",
    "alexa", "echo", "tailscale", "ngrok", "cloudflare",
    # Carpetas y terminos que salen a diario
    "downloads", "desktop", "documents", "pictures", "videos", "music",
    "screenshot", "backup", "update", "driver", "drivers", "firmware",
    "hardware", "software", "framerate", "frames", "gaming", "streaming",
    "lag", "ping", "buffer", "cache", "log", "logs", "debug", "release",
    "commit", "push", "pull", "branch", "merge", "repo", "repository",
    "deadline", "meeting", "mail", "inbox", "spam", "thread", "chat",
}

# Extensiones: ".py" se lee "punto pi ye" si no se marca.
_EXTENSIONES = {"py", "js", "ts", "md", "txt", "json", "html", "css", "exe",
                "pdf", "docx", "xlsx", "pptx", "csv", "yaml", "yml", "sh", "ps1"}

_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+")


def _escapar(texto: str) -> str:
    """XML basico. Sin esto, un '&' en un nombre de archivo rompe el SSML."""
    return (texto.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;"))


def _es_inglesa(palabra: str) -> bool:
    limpia = palabra.lower().strip(".,;:!?¿¡()[]\"'")
    return limpia in PALABRAS_INGLESAS or limpia in _EXTENSIONES


def a_ssml(texto: str) -> str:
    """Envuelve el texto en SSML marcando las palabras inglesas."""
    if not texto:
        return "<speak></speak>"

    partes = []
    ultimo = 0
    dentro_de_ingles = False
    acumulado = []

    def _cerrar():
        nonlocal dentro_de_ingles, acumulado
        if acumulado:
            # Las palabras inglesas seguidas van en UNA sola etiqueta: "visual
            # studio code" en tres etiquetas separadas suena entrecortado.
            partes.append(f'<lang xml:lang="en-US">{" ".join(acumulado)}</lang>')
            acumulado = []
        dentro_de_ingles = False

    for coincidencia in _PALABRA.finditer(texto):
        entre = texto[ultimo:coincidencia.start()]
        palabra = coincidencia.group(0)
        ultimo = coincidencia.end()

        if _es_inglesa(palabra):
            if dentro_de_ingles and entre.strip() == "":
                acumulado.append(_escapar(palabra))
            else:
                _cerrar()
                partes.append(_escapar(entre))
                acumulado = [_escapar(palabra)]
                dentro_de_ingles = True
        else:
            _cerrar()
            partes.append(_escapar(entre))
            partes.append(_escapar(palabra))

    _cerrar()
    partes.append(_escapar(texto[ultimo:]))

    return "<speak>" + "".join(partes) + "</speak>"
