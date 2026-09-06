"""Servidor HTTP de la skill de Alexa.

Recibe las peticiones de Amazon, verifica que vengan firmadas, resuelve la
orden y devuelve la respuesta en SSML. Todo lo que bloquea corre en un hilo
aparte, porque un `async def` que llama a codigo sincrono congela el bucle
de eventos entero y tumba tambien a las peticiones que iban bien.

El orden al resolver una orden es: router determinista, capa semantica, y
solo entonces el modelo.
"""

import asyncio
import logging
import sys
import logging.handlers
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import alexa_directivas
import mantener_caliente
import modes
import nlu
import ollama_client
import tareas
import voz
from config import (
    ARCHIVO_LOG,
    CARPETAS_PERMITIDAS,
    ESCRITORIO,
    MAX_CARACTERES_VOZ,
    MODO_GAMING,
    SESION_CONTINUA,
    VERIFICAR_FIRMA,
    detectar_comet,
)
from security import ErrorVerificacion, verificar_peticion
from tools import sistema

formato = logging.Formatter(
    "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s",
    datefmt="%H:%M:%S",
)

consola = logging.StreamHandler()
consola.setFormatter(formato)

# Rotación: el log no crece sin control aunque Jarvis lleve meses encendido.
archivo = logging.handlers.RotatingFileHandler(
    ARCHIVO_LOG, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
)
archivo.setFormatter(formato)

logging.basicConfig(level=logging.INFO, handlers=[consola, archivo])
log = logging.getLogger("jarvis")

app = FastAPI(title="Jarvis", version="2.0")

@app.middleware("http")
async def medir_y_registrar(request, call_next):
    """Registra cada peticion cuando la respuesta ya esta lista para salir."""
    inicio = time.perf_counter()
    try:
        respuesta = await call_next(request)
    except asyncio.CancelledError:
        ms = (time.perf_counter() - inicio) * 1000
        log.error(
            "RESPUESTA CANCELADA tras %.0f ms | %s %s | el cliente dejo de esperar. "
            "Si son pocos ms, la respuesta estaba lista y se perdio de camino a Amazon.",
            ms, request.method, request.url.path,
        )
        raise
    except Exception:
        ms = (time.perf_counter() - inicio) * 1000
        log.exception("La peticion REVENTO tras %.0f ms | %s %s",
                      ms, request.method, request.url.path)
        raise

    ms = (time.perf_counter() - inicio) * 1000

    # Solo nos interesan las de Alexa; los pings del keep-alive ensuciarian.
    if request.method == "POST":
        cliente = request.client.host if request.client else "?"
        log.info("Respuesta lista en %.0f ms | estado=%s | desde=%s",
                 ms, respuesta.status_code, cliente)

        # Amazon se rinde a los 8 segundos. Si nos acercamos, avisamos aunque
        # esta vez haya llegado a tiempo.
        if ms > 5000:
            log.warning("PELIGRO: %.0f ms es demasiado cerca del limite de 8 s de Alexa.", ms)

    return respuesta


SELLO_CODIGO = "2026-08-22-semantico-30"


@app.on_event("startup")
def al_arrancar():
    log.info("=" * 62)
    log.info("  JARVIS 2.0")
    log.info("=" * 62)
    log.info("Sello del codigo  : %s", SELLO_CODIGO)
    log.info("Sesion continua   : %s", SESION_CONTINUA)
    log.info("Modo inicial      : %s", modes.modo_actual())
    log.info("Modelo            : %s", modes.perfil_actual()["modelo"])
    log.info("Escritorio        : %s", ESCRITORIO)
    log.info("Carpetas permitidas: %s", ", ".join(p.name for p in CARPETAS_PERMITIDAS))
    log.info("Verificar firma   : %s", VERIFICAR_FIRMA)

    # Creamos el contexto al arrancar para que exista y se vea desde el primer
    # dia, no solo cuando alguna orden llegue a pasar por el modelo.
    try:
        from tools import avanzado

        ruta_contexto = avanzado.asegurar_contexto()
        log.info("Contexto          : %s", ruta_contexto)
    except Exception as e:
        log.warning("No pude preparar el contexto: %s", e)

    try:
        from tools import obsidian

        v = obsidian.vault()
        log.info("Obsidian          : %s", v or "vault no detectado")
    except Exception:
        pass

    comet = detectar_comet()
    log.info("Comet             : %s", comet or "no detectado (usaré el navegador por defecto)")

    datos = sistema.info_gpu()
    if datos.get("disponible"):
        log.info(
            "GPU               : %s (%.1f GB libres de %.1f GB)",
            datos["nombre"],
            datos["vram_libre_mb"] / 1024,
            datos["vram_total_mb"] / 1024,
        )
    else:
        log.info("GPU               : nvidia-smi no disponible")

    if ollama_client.esta_disponible():
        log.info("Ollama            : conectado")
        # Precalentar es lo que evita que la primera orden del día tarde 30 s
        # y Alexa la corte. Se hace en segundo plano para no bloquear el arranque.
        modes.precalentar_en_segundo_plano()
        log.info("Precalentando el modelo en segundo plano...")
    else:
        log.warning("Ollama            : NO responde. Solo funcionarán los comandos básicos.")

    # Mantener el camino caliente: es lo que evita que la primera orden tras
    # un rato de inactividad falle y haya que repetirla.
    if mantener_caliente.iniciar():
        log.info("Camino al túnel: se mantendrá caliente")

    log.info("=" * 62)

    _programar_trabajo_de_fondo()

    log.info("Jarvis listo y escuchando.")


SEGUNDOS_ANTES_DE_RASTREAR = 20


# Cuanta VRAM libre hace falta para considerar que hay sitio para trabajar.
# Por debajo de esto ya hay algo serio ocupando la grafica.
VRAM_LIBRE_MINIMA_GB = 1.5

# Cuanto se espera antes de volver a mirar si la maquina se ha despejado.
REINTENTO_MINUTOS = 10


def _hay_sitio_para_trabajar() -> tuple[bool, str]:
    """¿Se puede rastrear el equipo ahora sin estorbar?"""
    try:
        if modes.modo_actual() == MODO_GAMING:
            return False, "estás en modo gaming"
    except Exception:
        pass

    try:
        from tools import sistema
        datos = sistema.info_gpu()
        if datos.get("disponible"):
            libre = datos["vram_libre_mb"] / 1024
            if libre < VRAM_LIBRE_MINIMA_GB:
                return False, f"solo quedan {libre:.1f} GB de VRAM libres"
    except Exception as e:
        log.debug("No pude mirar la GPU antes de trabajar: %s", e)

    return True, ""


def _programar_trabajo_de_fondo() -> None:
    """Deja el rastreo del equipo para dentro de un rato, y en un solo hilo."""
    def trabajar():
        time.sleep(SEGUNDOS_ANTES_DE_RASTREAR)

        # Si la maquina esta ocupada, esto no es urgente: se espera. El
        # catalogo viejo sigue sirviendo y la boveda no se va a ningun sitio.
        while True:
            hay_sitio, motivo = _hay_sitio_para_trabajar()
            if hay_sitio:
                break
            log.info("Rastreo aplazado %d min: %s", REINTENTO_MINUTOS, motivo)
            time.sleep(REINTENTO_MINUTOS * 60)

        try:
            from tools import catalogo
            catalogo.cargar(forzar=True)
            log.info("Aplicaciones     : catálogo al día")
        except Exception as e:
            log.warning("No pude rastrear las aplicaciones: %s", e)

        try:
            from tools import memoria
            if memoria.modelo_disponible():
                log.info("Memoria vault    : %s", memoria.indexar())

                log.info("Memoria código   : %s", memoria.indexar_proyecto())

                from tools import intencion
                indice = intencion.cargar()
                log.info("Intenciones      : %d ejemplos listos",
                         len(indice.get("entradas") or []))
            else:
                log.info("Memoria vault    : falta %s (ollama pull %s)",
                         memoria.MODELO, memoria.MODELO)
                log.info("Intenciones      : sin vectores, se queda en el router literal")
        except Exception as e:
            log.warning("No pude poner al día la memoria semántica: %s", e)

    threading.Thread(target=trabajar, daemon=True, name="arranque-diferido").start()
    log.info("Aplicaciones     : rastreo en %d s, para no estorbar al arranque",
             SEGUNDOS_ANTES_DE_RASTREAR)


def _segunda_oportunidad(texto: str) -> tuple[str | None, str]:
    """Traduce lo que dijiste a una orden que el router SI entiende."""
    try:
        from tools import intencion
    except Exception:
        return None, ""

    if not intencion.disponible():
        return None, ""

    try:
        canonica, nota = intencion.traducir(texto)
    except Exception as e:
        # Que esto falle no puede costar la orden: se sigue al modelo, que es
        # exactamente lo que pasaba antes de que esta capa existiera.
        log.warning("La capa de intencion falló: %s", e)
        return None, ""

    if not canonica:
        return None, ""

    respuesta = nlu.enrutar(canonica)
    if respuesta is None:
        log.warning("La canónica %r ya no la reconoce el router. Al modelo.", canonica)
        return None, ""

    log.info("Resuelto por parecido (%.2f): %r -> %r", nota, texto[:40], canonica)
    return respuesta, "intención"


def procesar_comando(texto: str, datos_alexa: dict | None = None) -> str:
    """Resuelve un comando. Primero el router rápido, luego el LLM."""
    inicio = time.perf_counter()
    log.info("Comando recibido: %r", texto)

    # El presupuesto empieza a contar AQUI, no cuando llamemos al modelo.
    ollama_client.empezar_presupuesto()

    respuesta = nlu.enrutar(texto)
    origen = "router"

    if respuesta is None:
        respuesta, origen = _segunda_oportunidad(texto)

    if respuesta is None:
        log.info("Sin coincidencia local, delegando al modelo.")

        alexa_directivas.avisar_que_estamos_en_ello(datos_alexa)

        respuesta = ollama_client.procesar(texto)
        origen = "modelo"

    transcurrido = (time.perf_counter() - inicio) * 1000
    log.info("Resuelto por %s en %.0f ms: %r", origen, transcurrido, respuesta[:100])

    try:
        from tools import aprendizaje
        aprendizaje.registrar(texto, origen, transcurrido)
    except Exception:
        pass

    return respuesta


def limpiar_para_voz(texto: str) -> str:
    """Deja el texto listo para que Alexa lo pronuncie."""
    if not texto:
        return "Listo."

    # Fuera markdown y saltos: Alexa los lee literalmente y suena fatal.
    for simbolo in ("**", "*", "`", "#", "_", "```"):
        texto = texto.replace(simbolo, "")
    texto = texto.replace("\n", ". ").replace("\r", "")

    while "  " in texto:
        texto = texto.replace("  ", " ")
    while ". ." in texto:
        texto = texto.replace(". .", ".")

    texto = texto.strip()

    if len(texto) > MAX_CARACTERES_VOZ:
        recortado = texto[:MAX_CARACTERES_VOZ]
        # Cortamos en la última frase completa para que no quede a medias.
        ultimo_punto = recortado.rfind(".")
        texto = recortado[: ultimo_punto + 1] if ultimo_punto > 100 else recortado + "..."

    return texto or "Listo."


def respuesta_alexa(
    texto_voz: str,
    mantener_sesion: bool = False,
    reprompt: str | None = None,
) -> dict:
    """Construye la respuesta en el formato que exige Alexa."""
    texto_voz = limpiar_para_voz(texto_voz)

    texto_voz = voz.con_nombre(texto_voz)

    cuerpo = {
        "version": "1.0",
        "response": {
            "outputSpeech": {"type": "SSML", "ssml": voz.a_ssml(texto_voz)},
            "shouldEndSession": not mantener_sesion,
        },
    }

    if mantener_sesion:
        # Si no se indica uno, ponemos el mínimo: sin reprompt Alexa cierra.
        cuerpo["response"]["reprompt"] = {
            "outputSpeech": {"type": "SSML",
                             "ssml": voz.a_ssml(reprompt or "Te escucho.")}
        }

    log.info(
        "Respondemos: shouldEndSession=%s reprompt=%s",
        cuerpo["response"]["shouldEndSession"],
        "sí" if "reprompt" in cuerpo["response"] else "NO",
    )

    return cuerpo


@app.get("/jarvis")
def estado():
    return {
        "status": "ok",
        "mensaje": "Jarvis activo y escuchando.",
        "modo": modes.modo_actual(),
        "modelo": modes.perfil_actual()["modelo"],
    }


@app.get("/")
def estado_raiz():
    return estado()


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow:\n"


@app.get("/salud")
def salud():
    """Diagnóstico completo. Abre esto en el navegador si algo no funciona."""
    datos_gpu = sistema.info_gpu()
    perfil = modes.perfil_actual()

    return {
        "servidor": "ok",
        "sello": SELLO_CODIGO,
        "arranque": " ".join(sys.argv),
        "sesion_continua": SESION_CONTINUA,
        "modo": modes.modo_actual(),
        "perfil": {
            "modelo": perfil["modelo"],
            "num_gpu": perfil["num_gpu"],
            "keep_alive": perfil["keep_alive"],
        },
        "ollama": {
            "conectado": ollama_client.esta_disponible(),
            "modelos_instalados": ollama_client.modelos_instalados(),
        },
        "gpu": datos_gpu,
        "seguridad": {
            "verificar_firma": VERIFICAR_FIRMA,
            "skill_id_configurado": bool(__import__("config").ALEXA_SKILL_ID),
        },
        "rutas": {
            "escritorio": str(ESCRITORIO),
            "escritorio_existe": ESCRITORIO.is_dir(),
            "comet": detectar_comet() or None,
        },
        "tareas_en_curso": tareas.hay_tareas_en_curso(),
        "mantener_caliente": mantener_caliente.estadisticas,
    }


@app.post("/probar")
async def probar(cuerpo: dict):
    """Prueba local sin pasar por Alexa."""
    comando = (cuerpo or {}).get("comando", "")
    if not comando:
        return {"error": "Falta el campo 'comando'."}

    inicio = time.perf_counter()
    respuesta = await asyncio.to_thread(procesar_comando, comando)

    return {
        "comando": comando,
        "respuesta": respuesta,
        "voz": limpiar_para_voz(respuesta),
        "milisegundos": round((time.perf_counter() - inicio) * 1000),
        "resuelto_por": "router" if nlu.enrutar(comando) is not None else "modelo",
    }


@app.post("/")
@app.post("/jarvis")
async def endpoint_alexa(request: Request):
    """Endpoint principal de la skill de Alexa."""
    log.info("POST recibido en la ruta: %s", request.url.path)
    cuerpo_crudo = await request.body()

    try:
        import json

        cuerpo = json.loads(cuerpo_crudo)
    except Exception:
        log.warning("Petición con JSON inválido.")
        return JSONResponse(status_code=400, content={"error": "JSON inválido"})

    # ---- Verificación de seguridad ----
    try:
        # A un hilo: la verificacion descarga la cadena de certificados de
        # Amazon por HTTP la primera vez, y eso bloquea el bucle.
        await asyncio.to_thread(
            verificar_peticion, cuerpo_crudo, cuerpo, request.headers
        )
    except ErrorVerificacion as e:
        log.warning("PETICIÓN RECHAZADA: %s", e)
        # 400 y sin detalles: no le damos pistas a quien esté sondeando.
        return JSONResponse(status_code=400, content={"error": "Petición no autorizada"})
    except Exception as e:
        log.exception("Error inesperado verificando la petición")
        return JSONResponse(status_code=400, content={"error": "Error de verificación"})

    tipo = cuerpo.get("request", {}).get("type")

    # Estas usando el asistente. El keep-warm lo mira para decidir si mantener
    # el modelo en la VRAM o dejarlo caer y liberar la grafica.
    mantener_caliente.marcar_actividad()

    sesion = cuerpo.get("session", {}) or {}
    id_sesion = (sesion.get("sessionId") or "")[-12:]
    nueva = sesion.get("new")

    log.info(
        "Petición de Alexa: %s | sesión=%s nueva=%s", tipo, id_sesion or "?", nueva
    )

    # ---- Apertura de la skill ----
    if tipo == "LaunchRequest":
        perfil = modes.perfil_actual()
        saludo = voz.saludo_inicial(perfil["nombre_hablado"], SESION_CONTINUA)

        try:
            modes.precalentar_en_segundo_plano()
        except Exception as e:
            log.debug("No pude precalentar al abrir: %s", e)

        return respuesta_alexa(saludo, mantener_sesion=True, reprompt="Te escucho.")

    # ---- Comando ----
    if tipo == "IntentRequest":
        intent = cuerpo["request"].get("intent", {}) or {}
        nombre_intent = intent.get("name", "")
        slots = intent.get("slots", {}) or {}

        # Este log es tu mejor herramienta de diagnóstico: muestra EXACTAMENTE
        # qué entendió Alexa antes de que Jarvis haga nada.
        log.info("Intent: %s | Slots: %s", nombre_intent, {
            k: (v or {}).get("value") for k, v in slots.items()
        })

        if nombre_intent in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
            return respuesta_alexa(
                "Hasta luego. Alexa vuelve a estar disponible.", mantener_sesion=False
            )

        if nombre_intent == "AMAZON.HelpIntent":
            return respuesta_alexa(
                "Puedo crear, leer y editar archivos, buscar dentro de ellos, abrir "
                "y cerrar programas, apuntar en Obsidian, darte informes del equipo "
                "y cambiar entre modo normal, dedicado y gaming. Di pausa para salir.",
                mantener_sesion=SESION_CONTINUA,
            )

        if nombre_intent in ("AMAZON.YesIntent", "AMAZON.NoIntent"):
            palabra = "sí" if nombre_intent == "AMAZON.YesIntent" else "no"
            log.info("Respuesta de sí o no: %s", palabra)

            respuesta = await asyncio.to_thread(
                procesar_comando, palabra,
                alexa_directivas.datos_de_peticion(cuerpo),
            )
            return respuesta_alexa(respuesta, mantener_sesion=SESION_CONTINUA)

        if nombre_intent == "MensajeIntent":
            dictado = (slots.get("texto") or {}).get("value", "")
            log.info("Dictado libre: %r", dictado)

            if not dictado:
                return respuesta_alexa("No capté el texto. ¿Me lo repites?",
                                       mantener_sesion=SESION_CONTINUA)

            # Se reinyecta con el "que" delante, que es la forma que espera el
            # router para el texto pendiente de un mensaje.
            respuesta = await asyncio.to_thread(
                procesar_comando, f"que {dictado}",
                alexa_directivas.datos_de_peticion(cuerpo),
            )
            return respuesta_alexa(respuesta, mantener_sesion=SESION_CONTINUA)

        if nombre_intent == "AMAZON.FallbackIntent":
            import confirmaciones
            import foco

            if confirmaciones.hay_pendiente():
                return respuesta_alexa(
                    "No te entendí. Dime sí para confirmar, o no para cancelar.",
                    mantener_sesion=SESION_CONTINUA,
                )

            pendiente = foco.actual("destinatario")
            if pendiente:
                # Refrescamos para que el reintento cuente como turno nuevo:
                # si no, el destinatario caducaria en el siguiente intento.
                foco.recordar("destinatario", pendiente["valor"])
                return respuesta_alexa(
                    f"No capté el mensaje para {pendiente['valor']}. "
                    "Repítelo empezando por 'que', por ejemplo: que llego en diez minutos.",
                    mantener_sesion=SESION_CONTINUA,
                )

            return respuesta_alexa(
                "No entendí eso. ¿Puedes repetirlo?", mantener_sesion=SESION_CONTINUA
            )

        # Buscamos el comando en el slot esperado y, si no, en cualquier slot
        # con valor. Esto salva el caso de un slot mal nombrado en la consola.
        comando = (slots.get("comando") or {}).get("value", "")
        if not comando:
            for datos_slot in slots.values():
                valor = (datos_slot or {}).get("value")
                if valor:
                    comando = valor
                    log.info("Comando tomado de un slot alternativo: %r", valor)
                    break

        if not comando:
            log.warning("IntentRequest sin ningún slot con valor. Revisa el modelo de interacción.")
            return respuesta_alexa(
                "No capté el comando. ¿Me lo repites?", mantener_sesion=SESION_CONTINUA
            )

        if nlu.es_despedida(comando):
            log.info("Despedida detectada, cierro la sesión.")
            return respuesta_alexa(
                "Hasta luego. Alexa vuelve a estar disponible.",
                mantener_sesion=False,
            )

        try:
            respuesta = await asyncio.to_thread(
                procesar_comando, comando, alexa_directivas.datos_de_peticion(cuerpo)
            )
        except Exception as e:
            log.exception("Error procesando el comando")
            respuesta = f"Tuve un problema con esa orden: {e}"

        return respuesta_alexa(respuesta, mantener_sesion=SESION_CONTINUA)

    # ---- Cierre de sesión ----
    if tipo == "SessionEndedRequest":
        peticion = cuerpo.get("request", {}) or {}
        motivo = peticion.get("reason", "")

        error = peticion.get("error") or {}

        if motivo == "ERROR" or error:
            log.error(
                "Alexa cerró la sesión por ERROR | tipo=%s | mensaje=%s",
                error.get("type", "sin tipo"),
                error.get("message", "sin mensaje"),
            )
            # Volcamos la petición entera: si Amazon añade campos nuevos, los
            # veremos aquí en vez de perderlos.
            log.error("Petición completa del error: %s", peticion)
        else:
            log.info("Sesión terminada: %s", motivo)

        return respuesta_alexa("", mantener_sesion=False)

    return respuesta_alexa("No supe interpretar esa petición.", mantener_sesion=False)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
