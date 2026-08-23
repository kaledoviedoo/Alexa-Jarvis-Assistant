"""
Foco de sesion: memoria muy corta de lo ultimo que se hizo.

De donde viene la idea
----------------------
Es el patron converse() de Mycroft. Cuando un skill acaba de responder, tiene
derecho de tanteo sobre la frase siguiente: la ve ANTES que el enrutado
general, y si la reconoce se la queda.

Que problema resuelve aqui
--------------------------
Las frases con pronombre no dicen de que hablan: "cierralo", "borralo", "abre
el segundo", "ese". El router de patrones no puede resolverlas porque el
sujeto esta en el turno anterior, asi que acababan en el modelo: seis segundos
y medio de presupuesto, una RTX 3050 calentandose y un 3b adivinando, para
algo que aqui se resuelve leyendo una variable.

Como funciona
-------------
Cada handler que actua sobre algo concreto deja constancia:

    foco.recordar("app", "spotify")
    foco.recordar("archivo", "notas.txt", lista=["notas.txt", "prueba.py"])

Y caduca: dos minutos o tres turnos, lo que llegue antes. Es memoria de
conversacion, no una base de datos. Un "cierralo" veinte minutos despues no
debe cerrar nada: mas vale preguntar que acertar por accidente.
"""

import logging
import threading
import time

log = logging.getLogger("jarvis.foco")

SEGUNDOS_VIDA = 120
TURNOS_VIDA = 3

_candado = threading.Lock()
_estado: dict = {}


def recordar(tipo: str, valor: str, lista: list[str] | None = None) -> None:
    """Anota sobre que se acaba de actuar."""
    with _candado:
        _estado.clear()
        _estado.update({
            "tipo": tipo,
            "valor": valor,
            "lista": list(lista or []),
            "momento": time.monotonic(),
            "turnos": 0,
        })
    log.debug("Foco: %s = %r", tipo, valor)


def _vivo() -> bool:
    if not _estado:
        return False
    if time.monotonic() - _estado["momento"] > SEGUNDOS_VIDA:
        return False
    return _estado["turnos"] <= TURNOS_VIDA


def actual(tipo: str | None = None) -> dict | None:
    """Lo que hay en foco, o None si caduco o es de otro tipo."""
    with _candado:
        if not _vivo():
            _estado.clear()
            return None
        if tipo and _estado["tipo"] != tipo:
            return None
        return dict(_estado)


def envejecer() -> None:
    """Suma un turno. Se llama una vez por orden recibida."""
    with _candado:
        if _estado:
            _estado["turnos"] += 1


def olvidar() -> None:
    with _candado:
        _estado.clear()


# -------------------------------------------------------------------------
# ORDINALES
# -------------------------------------------------------------------------
# "abre el segundo" tras haber listado archivos. Se aceptan tanto la palabra
# como el numero, porque Alexa transcribe "el 2" y "el segundo" indistintamente.
ORDINALES = {
    "primero": 0, "primera": 0, "1": 0, "uno": 0, "una": 0,
    "segundo": 1, "segunda": 1, "2": 1, "dos": 1,
    "tercero": 2, "tercera": 2, "tercer": 2, "3": 2, "tres": 2,
    "cuarto": 3, "cuarta": 3, "4": 3, "cuatro": 3,
    "quinto": 4, "quinta": 4, "5": 4, "cinco": 4,
    "ultimo": -1, "última": -1, "ultima": -1,
}


def elemento_por_ordinal(palabra: str) -> str | None:
    """Devuelve el elemento de la lista en foco que corresponde al ordinal."""
    datos = actual()
    if not datos or not datos.get("lista"):
        return None

    indice = ORDINALES.get((palabra or "").strip().lower())
    if indice is None:
        return None

    lista = datos["lista"]
    try:
        return lista[indice]
    except IndexError:
        return None
