"""
Respuestas progresivas de Alexa.

El problema
-----------
Alexa corta a los ocho segundos. Cuando una orden llega al modelo, Jarvis
tarda entre dos y seis segundos, y durante todo ese rato el Echo se queda
mudo: no sabes si te oyo, si esta pensando o si se colgo. La respuesta llega
de golpe al final, o no llega.

La solucion
-----------
Amazon tiene un servicio de directivas que permite hacer hablar al Echo
MIENTRAS la peticion sigue abierta. Se manda un POST a la direccion que viene
en la propia peticion, con el token que viene en la propia peticion, y Alexa
dice esa frase al momento. Luego, cuando terminamos, contestamos normal.

Limites reales (documentacion de Amazon)
----------------------------------------
- Maximo cinco progresivas por peticion.
- NO amplian el plazo: los ocho segundos siguen contando igual. Esto compra
  paciencia, no tiempo.
- Una progresiva que llegue despues de la respuesta final no suena.
- Responde 204 sin cuerpo cuando va bien.

Por eso solo se usan cuando la orden va al modelo. Las que resuelve el router
en milisegundos no necesitan que nadie las entretenga.
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
    """
    Saca de la peticion de Alexa lo necesario para responder progresivamente.

    Devuelve None si falta algo: en las pruebas locales y en el simulador no
    siempre viene, y eso no debe romper nada.
    """
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
    """
    Hace que Alexa diga una frase de espera sin bloquear nada.

    Va en su propio hilo a proposito: si el servicio de directivas tarda o
    falla, no puede comerse ni un milisegundo del presupuesto de la orden.
    Un fallo aqui es cosmetico; perder la respuesta de verdad no lo es.
    """
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
