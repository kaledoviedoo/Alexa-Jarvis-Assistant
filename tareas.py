"""Registro de los trabajos que corren en segundo plano.

Lo que no cabe en los ocho segundos de Alexa no se pierde: sigue en un hilo
y su resultado queda aqui, para recogerlo despues con "como quedo lo
ultimo".
"""

import logging
import threading
from collections import deque
from datetime import datetime

log = logging.getLogger("jarvis.tareas")

_lock = threading.Lock()
_resultados: deque = deque(maxlen=10)
_en_curso: dict = {}


def registrar_inicio(identificador: str, descripcion: str) -> None:
    with _lock:
        _en_curso[identificador] = {
            "descripcion": descripcion,
            "inicio": datetime.now(),
        }


def registrar_resultado(identificador: str, descripcion: str, resultado: str) -> None:
    with _lock:
        _en_curso.pop(identificador, None)
        _resultados.append(
            {
                "descripcion": descripcion,
                "resultado": resultado,
                "momento": datetime.now(),
            }
        )
    log.info("Tarea en segundo plano terminada: %s", descripcion)


def hay_tareas_en_curso() -> bool:
    with _lock:
        return bool(_en_curso)


def ultimo_resultado() -> str | None:
    """Devuelve el resultado más reciente, o None si no hay ninguno."""
    with _lock:
        if not _resultados:
            return None
        entrada = _resultados[-1]

    segundos = (datetime.now() - entrada["momento"]).total_seconds()

    if segundos < 90:
        cuando = "hace un momento"
    elif segundos < 3600:
        cuando = f"hace {int(segundos // 60)} minutos"
    else:
        cuando = f"hace {int(segundos // 3600)} horas"

    return f"{entrada['resultado']} (terminó {cuando})."


def consultar_pendiente() -> str:
    """Respuesta a '¿cómo quedó lo último?'."""
    resultado = ultimo_resultado()

    if resultado:
        return resultado

    with _lock:
        pendientes = list(_en_curso.values())

    if pendientes:
        return f"Todavía estoy trabajando en: {pendientes[0]['descripcion']}."

    return "No tengo ninguna tarea pendiente ni resultados recientes."


def lanzar_en_segundo_plano(descripcion: str, funcion) -> str:
    """Ejecuta algo lento sin hacer esperar a Alexa."""
    import threading
    import uuid

    identificador = uuid.uuid4().hex[:8]
    registrar_inicio(identificador, descripcion)

    def _trabajar():
        try:
            resultado = funcion()
        except Exception as e:
            log.exception("Falló la tarea de fondo %s", descripcion)
            resultado = f"No pude {descripcion}: {e}"
        registrar_resultado(identificador, descripcion, resultado)

    threading.Thread(target=_trabajar, daemon=True,
                     name=f"fondo-{identificador}").start()
    return identificador
