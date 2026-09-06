"""Tres perfiles de modelo segun lo que este haciendo la grafica.

Normal usa un modelo pequeño siempre cargado, dedicado uno grande que razona
mejor, y gaming mueve el trabajo a la CPU para dejar la VRAM libre. Con una
RTX 3050 de 6 GB no caben un modelo grande y un juego a la vez, y esta es la
forma de decidirlo sin adivinar.
"""

import json
import logging
import threading
import time
from datetime import datetime

from config import (
    APPS_A_CERRAR_EN_GAMING,
    ARCHIVO_ESTADO,
    MODO_DEDICADO,
    MODO_GAMING,
    MODO_INICIAL,
    MODO_NORMAL,
    PERFILES,
)
from tools import sistema

log = logging.getLogger("jarvis.modos")

_lock = threading.Lock()
_modo_actual = MODO_INICIAL if MODO_INICIAL in PERFILES else MODO_NORMAL


def _guardar_estado() -> None:
    try:
        ARCHIVO_ESTADO.write_text(
            json.dumps(
                {"modo": _modo_actual, "actualizado": datetime.now().isoformat()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning("No pude guardar el estado: %s", e)


def _cargar_estado() -> None:
    """Recupera el modo tras un reinicio del servidor."""
    global _modo_actual
    try:
        if ARCHIVO_ESTADO.exists():
            datos = json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8"))
            guardado = datos.get("modo")
            if guardado in PERFILES:
                _modo_actual = guardado
                log.info("Modo recuperado del estado anterior: %s", guardado)
    except Exception as e:
        log.warning("No pude leer el estado previo: %s", e)


def modo_actual() -> str:
    return _modo_actual


def perfil_actual() -> dict:
    return PERFILES[_modo_actual]


def describir_modo() -> str:
    perfil = perfil_actual()
    detalle = ""

    datos = sistema.info_gpu()
    if datos.get("disponible"):
        libre = datos["vram_libre_mb"] / 1024
        detalle = f" Quedan {libre:.1f} gigas de memoria de video libres."

    return f"Estoy en modo {perfil['nombre_hablado']} con el modelo {perfil['modelo']}.{detalle}"


def descargar_modelos() -> int:
    """Descarga de la VRAM todos los modelos de Jarvis."""
    try:
        import ollama
    except ImportError:
        log.warning("La librería ollama no está instalada; no puedo descargar modelos.")
        return 0

    descargados = 0
    modelos = {perfil["modelo"] for perfil in PERFILES.values()}

    for modelo in modelos:
        try:
            # keep_alive=0 le dice a Ollama: suéltalo de la VRAM ya mismo.
            ollama.generate(model=modelo, prompt="", keep_alive=0)
            descargados += 1
            log.info("Modelo descargado de VRAM: %s", modelo)
        except Exception as e:
            log.debug("No se pudo descargar %s (puede que no estuviera cargado): %s", modelo, e)

    return descargados


def precalentar_modelo(modelo: str | None = None) -> bool:
    """Carga el modelo en memoria por adelantado."""
    try:
        import ollama
    except ImportError:
        return False

    perfil = perfil_actual()
    modelo = modelo or perfil["modelo"]

    try:
        ollama.generate(
            model=modelo,
            prompt="",
            keep_alive=perfil["keep_alive"],
            options={"num_gpu": perfil["num_gpu"], "num_ctx": perfil["num_ctx"]},
        )
        log.info("Modelo precalentado: %s (num_gpu=%s)", modelo, perfil["num_gpu"])
        return True
    except Exception as e:
        log.warning("No pude precalentar %s: %s", modelo, e)
        return False


def precalentar_en_segundo_plano() -> None:
    """Precalienta sin bloquear el arranque del servidor."""
    hilo = threading.Thread(target=precalentar_modelo, daemon=True, name="precalentar")
    hilo.start()


def cambiar_modo(nuevo_modo: str) -> str:
    """Cambia de modo y devuelve la frase que dirá Alexa."""
    global _modo_actual

    if nuevo_modo not in PERFILES:
        return f"No conozco el modo '{nuevo_modo}'. Tengo normal, dedicado y gaming."

    with _lock:
        anterior = _modo_actual
        _modo_actual = nuevo_modo
        _guardar_estado()

    perfil = PERFILES[nuevo_modo]
    log.info("Cambio de modo: %s -> %s", anterior, nuevo_modo)

    # ---------------- MODO GAMING ----------------
    if nuevo_modo == MODO_GAMING:
        descargados = descargar_modelos()
        cerrados = sistema.cerrar_varias(APPS_A_CERRAR_EN_GAMING)
        sistema.cambiar_plan_energia("alto")

        datos = sistema.info_gpu()
        if datos.get("disponible"):
            libre = datos["vram_libre_mb"] / 1024
            return (
                f"Modo gaming activado. Solté la gráfica y cerré {cerrados} programas. "
                f"Tienes {libre:.1f} gigas de memoria de video libres."
            )

        ram = sistema.psutil.virtual_memory()
        return (
            f"Modo gaming activado. Liberé la gráfica y cerré {cerrados} programas. "
            f"Memoria disponible al {100 - ram.percent:.0f} por ciento."
        )

    # ---------------- MODO DEDICADO ----------------
    if nuevo_modo == MODO_DEDICADO:
        if anterior == MODO_DEDICADO:
            return f"Ya estabas en modo dedicado con {perfil['modelo']}."

        descargar_modelos()
        time.sleep(0.6)          # a Ollama le cuesta un instante soltarla

        sistema.cambiar_plan_energia("alto")
        precalentar_en_segundo_plano()

        datos = sistema.info_gpu()
        if not datos.get("disponible"):
            return f"Modo dedicado activado. Cargando {perfil['modelo']}."

        libre_gb = datos["vram_libre_mb"] / 1024

        NECESITA_ENTERO = 4.4
        MINIMO_UTIL = 3.0

        if libre_gb >= NECESITA_ENTERO:
            return (f"Modo dedicado activado, {perfil['modelo']} entra entero en la gráfica. "
                    f"Quedan {libre_gb:.1f} gigas libres.")

        if libre_gb >= MINIMO_UTIL:
            # Ollama reparte lo que no cabe entre GPU y CPU. Funciona, solo que
            # mas lento. Eso es informacion util, no un error.
            return (f"Modo dedicado activado. Con {libre_gb:.1f} gigas libres el modelo "
                    "no entra del todo en la gráfica y una parte irá en procesador, "
                    "así que irá algo más lento.")

        return (f"Modo dedicado activado, pero solo hay {libre_gb:.1f} gigas de video libres "
                "y el modelo grande casi no cabe. Si tienes un juego o el navegador con "
                "muchas pestañas abiertos, ciérralos y vuelve a decírmelo.")

    # ---------------- MODO NORMAL ----------------
    if anterior == MODO_GAMING:
        # Veníamos de gaming: el modelo estaba fuera de la GPU, hay que recargarlo.
        descargar_modelos()

    precalentar_en_segundo_plano()
    sistema.cambiar_plan_energia("equilibrado")
    return "Modo normal activado. Jarvis en perfil ligero y rápido."


def opciones_ollama(tokens_maximos: int = 140) -> dict:
    """Opciones de inferencia que corresponden al modo actual."""
    perfil = perfil_actual()
    return {
        "num_gpu": perfil["num_gpu"],
        "num_ctx": perfil["num_ctx"],
        "temperature": perfil["temperatura"],
        "num_predict": tokens_maximos,
        # Cortes tipicos cuando el modelo empieza a divagar o a inventarse
        # un dialogo con el usuario.
        "stop": ["\nUsuario:", "\nUser:", "\nHumano:", "\n\n\n"],
    }


# Recuperamos el modo guardado al importar el módulo.
_cargar_estado()
