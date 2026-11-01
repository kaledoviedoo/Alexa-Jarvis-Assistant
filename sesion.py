"""Memoria de la conversacion en curso.

Antes cada orden que llegaba al modelo empezaba de cero: "busca el informe
de calculo" funcionaba, pero "resumelo" ya no sabia que era "lo". Aqui se
guarda de que se esta hablando, y lo comparten el router y el modelo.

Hay una sola conversacion porque Jarvis tiene un solo usuario: la de Alexa,
la de /probar y la de cualquier canal futuro son la misma persona. Se olvida
sola tras unos minutos sin hablar, para que "abrelo" no acabe aplicado a
algo de hace una hora.
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.sesion")

TURNOS = 6
SEGUNDOS_VIDA = 600
CARACTERES_POR_RESPUESTA = 300

# Lo que se sabe que significa "esta en ello": el turno se completa cuando
# termina la tarea de fondo.
_EN_CURSO = ("Estoy trabajando en eso", "Eso lleva su tiempo, lo dejo trabajando")


@dataclass
class Turno:
    orden: str
    respuesta: str
    origen: str
    momento: float = field(default_factory=time.monotonic)


_candado = threading.Lock()
_turnos: deque = deque(maxlen=TURNOS)
_ultima_actividad = 0.0


def _caducada() -> bool:
    return bool(_turnos) and time.monotonic() - _ultima_actividad > SEGUNDOS_VIDA


def _limpiar_si_caduco() -> None:
    if _caducada():
        log.info("La conversacion lleva %d s parada: empiezo de cero.", SEGUNDOS_VIDA)
        _turnos.clear()


def agregar(orden: str, respuesta: str, origen: str) -> None:
    """Apunta un turno. Lo llama el servidor con cada orden resuelta."""
    global _ultima_actividad
    with _candado:
        _limpiar_si_caduco()
        _turnos.append(Turno((orden or "").strip(), (respuesta or "").strip(), origen))
        _ultima_actividad = time.monotonic()


def completar(orden: str, resultado: str) -> None:
    """Pone el resultado real en el turno que se quedo en "estoy en ello"."""
    with _candado:
        for turno in reversed(_turnos):
            if turno.orden == (orden or "").strip() and turno.respuesta.startswith(_EN_CURSO):
                turno.respuesta = (resultado or "").strip()
                return


# Respuestas que no son contenido: avisos de "estoy en ello" del router y
# del modelo, y la coletilla que añade "como quedo lo ultimo".
_AVISO = ("pregúntame cómo quedó", "preguntame como quedo", "estoy trabajando en eso",
          "lo dejo trabajando")


def ultima_respuesta_util() -> str:
    """Lo ultimo que Jarvis dijo con contenido, para resolver "apuntalo"."""
    import re
    for turno in reversed(turnos()):
        texto = turno.respuesta or ""
        if not texto or any(a in texto.lower() for a in _AVISO):
            continue
        return re.sub(r"\s*\((?:termin[oó]|hace)[^)]*\)\.?\s*$", "", texto).strip()
    return ""


def olvidar() -> None:
    with _candado:
        _turnos.clear()


def turnos() -> list[Turno]:
    with _candado:
        _limpiar_si_caduco()
        return list(_turnos)


def _recortar(texto: str, largo: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= largo else texto[: largo - 3].rstrip() + "..."


def estado_actual() -> list[str]:
    """Lo que hay entre manos ahora mismo, en frases cortas."""
    lineas = []
    try:
        import foco
        datos = foco.actual()
        if datos:
            nombres = {"archivo": "Archivo en foco", "app": "Aplicacion en foco",
                       "destinatario": "Destinatario en foco", "envio": "Mensaje preparado para"}
            lineas.append(f"{nombres.get(datos['tipo'], datos['tipo'])}: {datos['valor']}")
            if len(datos.get("lista") or []) > 1:
                lista = ", ".join(f"{i}. {v}" for i, v in enumerate(datos["lista"][:6], 1))
                lineas.append(f"Ultima lista mostrada: {lista}")
    except Exception as e:
        log.debug("No pude leer el foco: %s", e)

    try:
        from tools import seleccion
        elegidos = seleccion.seleccionados()
        if elegidos:
            nombres = ", ".join(r.name for r in elegidos[:5])
            mas = f" y {len(elegidos) - 5} mas" if len(elegidos) > 5 else ""
            lineas.append(f"Seleccionados en {seleccion.carpeta_actual().name}: {nombres}{mas}")
    except Exception as e:
        log.debug("No pude leer la seleccion: %s", e)

    try:
        import tareas
        if tareas.hay_tareas_en_curso():
            lineas.append("Hay una tarea larga trabajando en segundo plano.")
    except Exception as e:
        log.debug("No pude leer las tareas: %s", e)

    return lineas


def para_el_modelo(max_caracteres: int = 2800) -> str:
    """El bloque que se añade al prompt: lo hablado y lo que hay entre manos.

    Va como texto en el prompt del sistema y no como mensajes previos: un
    modelo pequeño que ve sus propias respuestas viejas tiende a repetirlas.
    """
    partes = []
    estado = estado_actual()
    if estado:
        partes.append("--- LO QUE HAY ENTRE MANOS ---\n" + "\n".join(estado))

    lineas, usados = [], 0
    for turno in reversed(turnos()):
        linea = (f"El usuario dijo: {_recortar(turno.orden, 160)}\n"
                 f"Jarvis respondio: {_recortar(turno.respuesta, CARACTERES_POR_RESPUESTA)}")
        if usados + len(linea) > max_caracteres:
            break
        lineas.insert(0, linea)
        usados += len(linea)
    if lineas:
        partes.append("--- LO QUE LLEVAMOS HABLADO (de mas antiguo a mas reciente) ---\n"
                      + "\n".join(lineas))

    return "\n\n".join(partes)
