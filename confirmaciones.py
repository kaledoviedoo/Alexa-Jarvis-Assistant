"""Las ordenes sin vuelta atras se ejecutan en dos turnos.

Apagar el equipo o borrar varios archivos primero preguntan, y solo el "si"
del turno siguiente dispara la accion. Existe porque Alexa oye mal de vez en
cuando, y equivocarse aqui no se puede deshacer.
"""

import logging
import re
import threading
import time

log = logging.getLogger("jarvis.confirmar")

SEGUNDOS_VIDA = 45

_candado = threading.Lock()
_pendiente: dict = {}

_SI = re.compile(r"^\s*(?:s[ií]|sip|claro|dale|hazlo|h[aá]zlo|adelante|confirmo|"
                 r"correcto|exacto|eso\s+es|por\s+supuesto|venga|ok|okay|vale)\s*$",
                 re.IGNORECASE)

_NO = re.compile(r"^\s*(?:no|nop|nada|d[eé]jalo|cancela|cancelar|olvidalo|"
                 r"olv[ií]dalo|mejor\s+no|para|espera)\s*$", re.IGNORECASE)


def pedir(descripcion: str, accion, al_rechazar=None, pregunta: str = "") -> str:
    """Guarda la accion y devuelve la pregunta que Alexa dira."""
    with _candado:
        _pendiente.clear()
        _pendiente.update({
            "descripcion": descripcion,
            "accion": accion,
            "al_rechazar": al_rechazar,
            "momento": time.monotonic(),
        })
    log.info("Pendiente de confirmar: %s", descripcion)
    return pregunta or f"¿Confirmas que quieres {descripcion}? Di sí o no."


def hay_pendiente() -> bool:
    with _candado:
        if not _pendiente:
            return False
        if time.monotonic() - _pendiente["momento"] > SEGUNDOS_VIDA:
            log.info("La confirmación caducó: %s", _pendiente["descripcion"])
            _pendiente.clear()
            return False
        return True


def resolver(texto: str) -> str | None:
    """Interpreta la respuesta del usuario."""
    if not hay_pendiente():
        return None

    if _NO.match(texto or ""):
        with _candado:
            descripcion = _pendiente.get("descripcion", "eso")
            deshacer = _pendiente.get("al_rechazar")
            _pendiente.clear()

        log.info("Confirmación rechazada: %s", descripcion)

        if deshacer is not None:
            try:
                aviso = deshacer()
                if aviso:
                    return aviso
            except Exception as e:
                log.exception("Falló al deshacer")
                return f"Lo cancelé, pero no pude deshacerlo del todo: {e}"

        return "Vale, lo dejo."

    if not _SI.match(texto or ""):
        # Ni si ni no: el usuario cambio de tema. Descartamos lo pendiente en
        # vez de dejarlo armado esperando un "si" que llegue por otra cosa.
        with _candado:
            descripcion = _pendiente.get("descripcion", "eso")
            deshacer = _pendiente.get("al_rechazar")
            _pendiente.clear()

        log.info("Confirmación descartada al cambiar de tema: %s", descripcion)

        # Igual que con un "no": lo que quedo a medias hay que deshacerlo.
        if deshacer is not None:
            try:
                deshacer()
            except Exception:
                log.exception("Falló al deshacer tras cambiar de tema")

        return None

    with _candado:
        accion = _pendiente.get("accion")
        descripcion = _pendiente.get("descripcion", "eso")
        _pendiente.clear()

    log.info("Confirmado: %s", descripcion)
    try:
        return accion()
    except Exception as e:
        log.exception("Falló la acción confirmada")
        return f"Lo intenté pero falló: {e}"


def olvidar() -> None:
    with _candado:
        _pendiente.clear()
