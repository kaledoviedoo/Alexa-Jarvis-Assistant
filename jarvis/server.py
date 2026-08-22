"""
Jarvis — servidor FastAPI para la skill de Alexa.

Arranque:
    py -m uvicorn server:app --host 0.0.0.0 --port 8000

Endpoints:
    GET  /jarvis      estado del servicio (para comprobar que está vivo)
    POST /jarvis      endpoint de Alexa (verificado criptográficamente)
    GET  /salud       diagnóstico completo: Ollama, modelos, GPU, modo
    POST /probar      pruebas locales sin Alexa:  {"comando": "..."}

Flujo de una orden
------------------
    Alexa -> ngrok -> POST /jarvis
        1. Verificación de firma de Amazon           (security.py)
        2. Router determinista, milisegundos         (nlu.py)
        3. Si no coincide -> Ollama con presupuesto  (ollama_client.py)
        4. Respuesta en formato Alexa
"""

import asyncio
import logging
import sys
import logging.handlers
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
    SESION_CONTINUA,
    VERIFICAR_FIRMA,
    detectar_comet,
)
from security import ErrorVerificacion, verificar_peticion
from tools import sistema

# -------------------------------------------------------------------------
# LOGGING
# -------------------------------------------------------------------------
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
    """
    Registra cada peticion cuando la respuesta ya esta lista para salir.

    Hace falta por un motivo muy concreto. El log "Respondemos: ..." se escribe
    cuando CONSTRUIMOS el diccionario, no cuando los bytes salen por el cable.
    Si la respuesta se construye en 4 ms pero tarda en llegar (o no llega),
    el registro dice que todo fue bien mientras Amazon nos da por muertos a los
    8 segundos y manda INVALID_RESPONSE.
    Con esto queda escrito el tiempo real de extremo a extremo, el estado HTTP
    y de que IP vino. Si el tiempo es de milisegundos y Amazon sigue diciendo
    que no respondimos, el problema no esta en este equipo.
    """
    inicio = time.perf_counter()
    try:
        respuesta = await call_next(request)
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


# Sello del codigo. Sirve para una sola cosa, pero es la que mas falta hace:
# comprobar desde fuera QUE VERSION esta corriendo de verdad. Si el proceso
# viejo sigue vivo y agarrado al puerto, el nuevo no arranca y tu crees que si.
# Sube este numero cada vez que cambie algo que se note desde Alexa.
SELLO_CODIGO = "2026-08-22-mi-asistente-28"


# -------------------------------------------------------------------------
# ARRANQUE
# -------------------------------------------------------------------------
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
    # Catalogo de aplicaciones en segundo plano. La primera vez tarda unos
    # segundos rastreando menu Inicio, registro y Store; despues se lee del
    # disco. Va aparte para no retrasar el arranque.
    try:
        from tools import catalogo
        catalogo.refrescar_en_segundo_plano()
        log.info("Aplicaciones     : rastreando en segundo plano")
    except Exception as e:
        log.warning("No pude arrancar el catálogo de aplicaciones: %s", e)

    # Memoria semantica de la boveda. Incremental: si ya esta indexada, esto
    # termina en un suspiro; la primera vez tarda segun lo grande que sea.
    try:
        from tools import memoria
        if memoria.modelo_disponible():
            memoria.indexar_en_segundo_plano()
            log.info("Memoria vault    : poniéndose al día en segundo plano")
        else:
            log.info("Memoria vault    : falta %s (ollama pull %s)",
                     memoria.MODELO, memoria.MODELO)
    except Exception as e:
        log.warning("No pude arrancar la memoria semántica: %s", e)

    log.info("Jarvis listo y escuchando.")


# -------------------------------------------------------------------------
# NÚCLEO: procesar un comando
# -------------------------------------------------------------------------
def procesar_comando(texto: str, datos_alexa: dict | None = None) -> str:
    """
    Resuelve un comando. Primero el router rápido, luego el LLM.

    Esta separación es lo que mantiene a Jarvis dentro del límite de tiempo
    de Alexa en la gran mayoría de las órdenes.

    `datos_alexa` sirve para las respuestas progresivas: permite que el Echo
    diga "dame un segundo" mientras el modelo trabaja, en vez de quedarse mudo
    durante seis segundos sin que sepas si te oyó.
    """
    inicio = time.perf_counter()
    log.info("Comando recibido: %r", texto)

    # El presupuesto empieza a contar AQUI, no cuando llamemos al modelo.
    ollama_client.empezar_presupuesto()

    respuesta = nlu.enrutar(texto)
    origen = "router"

    if respuesta is None:
        log.info("Sin coincidencia local, delegando al modelo.")

        # Solo entretenemos cuando de verdad vamos a tardar. Las órdenes que
        # el router resuelve en milisegundos no necesitan que nadie las
        # distraiga: un "dame un segundo" antes de una respuesta instantánea
        # queda peor que el silencio.
        alexa_directivas.avisar_que_estamos_en_ello(datos_alexa)

        respuesta = ollama_client.procesar(texto)
        origen = "modelo"

    transcurrido = (time.perf_counter() - inicio) * 1000
    log.info("Resuelto por %s en %.0f ms: %r", origen, transcurrido, respuesta[:100])

    # Telemetria para que Jarvis aprenda que se le atraganta. Va aqui porque es
    # el unico punto por el que pasan TODAS las ordenes, y nunca puede romper
    # una: si falla el registro, la respuesta sale igual.
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


# -------------------------------------------------------------------------
# RESPUESTAS DE ALEXA
# -------------------------------------------------------------------------
def respuesta_alexa(
    texto_voz: str,
    mantener_sesion: bool = False,
    reprompt: str | None = None,
) -> dict:
    """
    Construye la respuesta en el formato que exige Alexa.

    Sobre `reprompt`: es OBLIGATORIO siempre que se deje la sesión abierta.

    Amazon lo documenta y cuesta creerlo hasta que se ve: si devuelves
    shouldEndSession=false SIN reprompt, Alexa cierra la sesión igualmente.
    El reprompt es precisamente lo que sostiene el micrófono abierto durante
    el silencio. Sin él, el dispositivo dice tu respuesta y se apaga.

    Lo dejamos muy corto ("Te escucho") porque solo suena cuando te quedas
    callado unos segundos; si encadenas órdenes seguidas no lo oirás nunca.
    """
    texto_voz = limpiar_para_voz(texto_voz)

    # Un solo sitio para esto: aqui pasa TODA respuesta hablada, venga del
    # router, del modelo o de un error. Meterlo en cada handler seria
    # repetirlo cuarenta veces y olvidarlo en la mitad.
    texto_voz = voz.con_nombre(texto_voz)

    # SSML en vez de texto plano: es la unica forma de que Alexa cambie de
    # fonetica para las palabras inglesas. Con PlainText leia "github" como
    # "guitub" y "Downloads" como "dowloads", porque el motor es español y
    # aplica sus reglas a todo lo que le llega.
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

    # Dejamos constancia de lo que SALE, no solo de lo que entra. Sin esto no
    # se puede distinguir "el servidor pidió cerrar" de "Alexa cerró por su
    # cuenta", que son problemas totalmente distintos.
    log.info(
        "Respondemos: shouldEndSession=%s reprompt=%s",
        cuerpo["response"]["shouldEndSession"],
        "sí" if "reprompt" in cuerpo["response"] else "NO",
    )

    return cuerpo


# -------------------------------------------------------------------------
# ENDPOINTS
# -------------------------------------------------------------------------
@app.get("/jarvis")
def estado():
    return {
        "status": "ok",
        "mensaje": "Jarvis activo y escuchando.",
        "modo": modes.modo_actual(),
        "modelo": modes.perfil_actual()["modelo"],
    }


# La raíz responde igual que /jarvis. Es una red de seguridad: si en la consola
# de Alexa se guarda el endpoint sin la ruta /jarvis, Amazon golpearía aquí y
# recibiría un 404 sin ninguna pista de por qué. Así funciona igual.
@app.get("/")
def estado_raiz():
    return estado()


# Este endpoint es público, así que conviene servir un robots.txt en condiciones.
# Además, varias herramientas de comprobación externas lo piden antes de mirar
# nada más y se niegan a seguir si reciben un 404.
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
        # Como se lanzo el proceso. Sirve para comprobar desde fuera que
        # uvicorn lleva --timeout-keep-alive: sin ese argumento, Amazon pierde
        # las ordenes que llegan mas de 5 segundos despues de la anterior.
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
    """
    Prueba local sin pasar por Alexa.

    Ejemplo:
      curl -X POST http://localhost:8000/probar ^
           -H "Content-Type: application/json" ^
           -d "{\\"comando\\": \\"crea un archivo llamado prueba punto py con el codigo print hola\\"}"
    """
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
    """
    Endpoint principal de la skill de Alexa.

    Atiende tanto /jarvis como la raíz, para que un endpoint mal copiado en la
    consola de Amazon no se traduzca en un 404 silencioso.
    """
    log.info("POST recibido en la ruta: %s", request.url.path)
    # El cuerpo CRUDO es imprescindible: la firma se calcula sobre los bytes
    # exactos que envió Amazon. Si dejamos que FastAPI parsee el JSON primero,
    # perdemos el formato original y la firma nunca validaría.
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

    # El estado de la sesión es el dato que zanja el diagnóstico de la sesión
    # continua. Si dos órdenes seguidas llegan con el MISMO sessionId y
    # nueva=False, la sesión se está manteniendo y Alexa nos escucha. Si cada
    # orden trae un sessionId distinto y nueva=True, no se mantiene nada:
    # el dispositivo abre una sesión nueva cada vez.
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

        # MensajeIntent: dictado libre con AMAZON.SearchQuery.
        #
        # Existe porque el slot personalizado no reconoce una frase cualquiera
        # ("llego en diez minutos" no se parece a ninguna orden) y Alexa la
        # mandaba al Fallback. Y el Fallback NO TRAE EL TEXTO: solo dice que no
        # entendio. El mensaje se perdia y la conversacion se rompia justo
        # despues de preguntar "¿que le digo?".
        # Si y no: intents integrados de Amazon.
        #
        # Sin esto, un "si" suelto no se parecia a nada del slot personalizado
        # y caia al Fallback una y otra vez. En el registro se ve el bucle:
        # cuatro AMAZON.FallbackIntent seguidos mientras un mensaje esperaba
        # confirmacion, hasta que el usuario dijo "pausa" y lo dejo.
        #
        # Se enrutan por el mismo camino que el texto libre, asi que la logica
        # de confirmaciones no cambia: solo se le entrega la palabra que
        # esperaba.
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
            # Si habia un mensaje a medias, esto es lo que acaba de pasar:
            # dictaste el texto y Alexa no supo encajarlo. Decir solo "no
            # entendi" deja al usuario sin saber que la conversacion sigue
            # viva y que basta con empezar por "que".
            import confirmaciones
            import foco

            # Lo mas urgente: si hay algo esperando un si o un no, decir solo
            # "no entendi" deja al usuario dando vueltas sin saber que la
            # pregunta sigue en pie.
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

        # ¿Nos está devolviendo el micrófono? Mientras la sesión de la skill
        # está abierta, Alexa no atiende sus propios servicios, así que esta
        # salida tiene que ser fácil y natural.
        if nlu.es_despedida(comando):
            log.info("Despedida detectada, cierro la sesión.")
            return respuesta_alexa(
                "Hasta luego. Alexa vuelve a estar disponible.",
                mantener_sesion=False,
            )

        try:
            # A un hilo tambien, y por la misma razon: aqui dentro se espera
            # al modelo hasta 6,5 segundos. Bloquear el bucle ese rato deja
            # sordo al servidor justo cuando mas ocupado esta.
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

        # Cuando el motivo es ERROR, Amazon adjunta el tipo y el mensaje. Sin
        # registrarlos, "Sesión terminada: ERROR" no dice nada y el problema es
        # imposible de diagnosticar: es la diferencia entre saber que algo
        # falló y saber POR QUÉ falló.
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
