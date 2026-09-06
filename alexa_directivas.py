"""Respuestas progresivas de Alexa.

Cuando una orden va a tardar, esto hace que el Echo diga algo enseguida en
vez de quedarse mudo. Se manda a la API de Amazon con el token de la propia
peticion, en un hilo aparte para no gastar del presupuesto.

No amplia los ocho segundos: solo llena el silencio mientras tanto.
"""

import json
import logging
import random
import threading
import urllib.error
import urllib.request

log = logging.getLogger("jarvis.directivas")

# Frases de espera. Varias, y elegidas al azar, porque oir siempre la misma
# cantinela cansa mas que el silencio.
FRASES_ESPERA = [
    "Dame un segundo.",
    "Voy con ello.",
    "Un momento, lo estoy mirando.",
    "Enseguida.",
    "Déjame pensarlo.",
]


def datos_de_peticion(cuerpo: dict) -> dict | None:
    """Saca de la peticion de Alexa lo necesario para responder progresivamente."""
    try:
        sistema = cuerpo["context"]["System"]
        destino = sistema["apiEndpoint"]
        token = sistema["apiAccessToken"]
        identificador = cuerpo["request"]["requestId"]
    except (KeyError, TypeError):
        return None

    if not (destino and token and identificador):
        return None

    return {"destino": destino, "token": token, "peticion": identificador}


def _enviar(datos: dict, texto: str) -> None:
    cuerpo = json.dumps({
        "header": {"requestId": datos["peticion"]},
        "directive": {
            "type": "VoicePlayer.Speak",
            "speech": f"<speak>{texto}</speak>",
        },
    }).encode("utf-8")

    peticion = urllib.request.Request(
        f"{datos['destino'].rstrip('/')}/v1/directives",
        data=cuerpo,
        method="POST",
        headers={
            "Authorization": f"Bearer {datos['token']}",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(peticion, timeout=3) as respuesta:
        log.debug("Progresiva enviada, estado %s", respuesta.status)


def avisar_que_estamos_en_ello(datos: dict | None, texto: str | None = None) -> None:
    """Hace que Alexa diga una frase de espera sin bloquear nada."""
    if not datos:
        return

    frase = texto or random.choice(FRASES_ESPERA)

    def _hilo():
        try:
            _enviar(datos, frase)
        except urllib.error.HTTPError as e:
            log.debug("El servicio de directivas devolvió %s", e.code)
        except Exception as e:
            log.debug("No pude mandar la progresiva: %s", e)

    threading.Thread(target=_hilo, daemon=True, name="progresiva").start()
