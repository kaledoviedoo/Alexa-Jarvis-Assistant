"""
Gestión de los tres modos de Jarvis y control de la VRAM de la RTX 3050.

  MODO NORMAL    modelo ligero (3B) residente en GPU. Respuestas en 1-2 s.
  MODO DEDICADO  modelo grande (7B) en GPU. Razona mejor, ocupa ~4.7 GB.
  MODO GAMING    num_gpu=0 y descarga total de VRAM. La 3050 queda para el juego.

Cómo se libera la VRAM de verdad
--------------------------------
Ollama mantiene el modelo cargado según `keep_alive`. Enviarle una petición
con keep_alive=0 lo descarga de inmediato. Eso es lo que hace `descargar_modelos`
al entrar en modo gaming: no es un truco, es la forma documentada de liberar
la memoria de video sin matar el proceso de Ollama.

Sobre los "gráficos integrados"
-------------------------------
Windows asigna la GPU por aplicación (Configuración > Pantalla > Gráficos).
Ningún proceso puede reasignar por la fuerza la GPU de otro programa. Lo que
Jarvis controla es su propio consumo: en modo gaming se quita por completo de
la RTX 3050 y corre en CPU, dejando los 6 GB enteros para el juego.
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


# -------------------------------------------------------------------------
# Estado persistente
# -------------------------------------------------------------------------
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


# -------------------------------------------------------------------------
# Consulta
# -------------------------------------------------------------------------
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


# -------------------------------------------------------------------------
# Control de VRAM vía Ollama
# -------------------------------------------------------------------------
def descargar_modelos() -> int:
    """
    Descarga de la VRAM todos los modelos de Jarvis.

    Devuelve cuántos se descargaron. Se usa al entrar en modo gaming.
    """
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
    """
    Carga el modelo en memoria por adelantado.

    Esto es lo que evita que la PRIMERA orden del día tarde 30 segundos y Alexa
    corte la sesión con 'hubo un problema con la respuesta de la skill'.
    """
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


# -------------------------------------------------------------------------
# Cambio de modo
# -------------------------------------------------------------------------
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
        # Si ya estabamos aqui, no se recarga nada: repetir la orden hacia que
        # el aviso saliera cada vez peor, porque medía la VRAM que el propio
        # modelo acababa de ocupar.
        if anterior == MODO_DEDICADO:
            return f"Ya estabas en modo dedicado con {perfil['modelo']}."

        # Soltamos NUESTRO modelo anterior antes de medir. Sin esto, el 3B del
        # modo normal seguia ocupando su par de gigas y contaban como "no
        # disponibles", asi que Jarvis te pedia cerrar programas para hacer
        # sitio a un espacio que se estaba ocupando el solo.
        descargar_modelos()
        time.sleep(0.6)          # a Ollama le cuesta un instante soltarla

        sistema.cambiar_plan_energia("alto")
        precalentar_en_segundo_plano()

        datos = sistema.info_gpu()
        if not datos.get("disponible"):
            return f"Modo dedicado activado. Cargando {perfil['modelo']}."

        libre_gb = datos["vram_libre_mb"] / 1024

        # Cuanto necesita de verdad el 7B cuantizado a 4 bits: unos 4,4 GB de
        # pesos mas el contexto. El umbral anterior era 5,0 GB, inalcanzable en
        # una tarjeta de 6 GB donde Windows y el escritorio ya se quedan medio
        # giga largo. Por eso te decia que cerraras cosas con todo cerrado.
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
    """
    Opciones de inferencia que corresponden al modo actual.

    num_predict y stop existen por el limite de Alexa. Una respuesta hablada
    nunca deberia pasar de dos frases; sin tope, el modelo se enrolla, tarda
    varios segundos de mas en generar texto que Alexa iba a recortar igual, y
    el presupuesto se agota generando algo que nadie va a oir entero.
    """
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
