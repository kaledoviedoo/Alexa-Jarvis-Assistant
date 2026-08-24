"""
Confirmacion de las ordenes que no tienen vuelta atras.

Por que existe
--------------
Alexa oye mal a veces. Ya paso: "apague el equipo" se ejecuto tal cual y el PC
se apago en mitad de una prueba. Y el modelo, cuando no entiende algo, tiende a
llamar a la herramienta que le suene: llego a intentar CERRAR una aplicacion
inventada porque la orden decia "esta consumiendo mas recursos".

Con archivos hay red (nada se borra de verdad, va a la papelera), pero apagar,
reiniciar o cerrar todo no se deshacen. Para esas, una pregunta corta cuesta
un turno y evita un disgusto.

Como funciona
-------------
La orden peligrosa no se ejecuta: se guarda y se pregunta. El "si" del turno
siguiente la dispara. Cualquier otra cosa la descarta.

Caduca a los 45 segundos a proposito. Un "si" suelto tres minutos despues,
contestando a otra cosa, no puede apagar el equipo.
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
    """
    Guarda la accion y devuelve la pregunta que Alexa dira.

    `al_rechazar` es lo que hay que hacer si dices que no. No siempre basta
    con no hacer nada: en WhatsApp el mensaje YA esta escrito en la caja
    cuando se pregunta, asi que un "no" tiene que borrarlo. Dejarlo ahi seria
    peor que no haber empezado, porque a la siguiente pulsacion de Enter se
    manda solo.

    `pregunta` permite una frase a medida en vez de la formula generica.
    """
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
    """
    Interpreta la respuesta del usuario.

    Devuelve el resultado si habia algo que confirmar, o None si esta frase
    no tiene nada que ver y debe seguir su camino por el router.
    """
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
