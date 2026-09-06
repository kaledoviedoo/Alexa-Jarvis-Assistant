"""Memoria de una sola frase, para que los pronombres funcionen.

Guarda de que se acaba de hablar durante unos pocos turnos, y asi "cierralo"
o "el primero" saben a que se refieren. Caduca rapido a proposito: un foco
que dura demasiado acaba aplicando una orden a algo de hace cinco minutos.
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
