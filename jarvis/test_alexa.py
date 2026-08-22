"""
Prueba de extremo a extremo: simula peticiones reales de Alexa contra el servidor.

Levanta el servidor en memoria (sin puerto ni túnel) y le manda el mismo JSON
que enviaría Amazon, para comprobar que el formato de respuesta es válido.

Ejecutar:  py test_alexa.py
"""

import json
import sys
import time
from datetime import datetime, timezone

# Estas pruebas no pasan por la firma real de Amazon, así que la desactivamos
# ANTES de importar la configuración.
import os

os.environ["JARVIS_VERIFICAR_FIRMA"] = "false"
os.environ["ALEXA_SKILL_ID"] = ""

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

cliente = TestClient(server.app)

SKILL_ID = "amzn1.ask.skill.prueba-local"


def _sobre(cuerpo_peticion: dict) -> dict:
    """Envuelve una request en la estructura completa que manda Alexa."""
    return {
        "version": "1.0",
        "session": {
            "new": True,
            "sessionId": "amzn1.echo-api.session.prueba",
            "application": {"applicationId": SKILL_ID},
            "user": {"userId": "amzn1.ask.account.prueba"},
        },
        "context": {
            "System": {
                "application": {"applicationId": SKILL_ID},
                "user": {"userId": "amzn1.ask.account.prueba"},
            }
        },
        "request": cuerpo_peticion,
    }


def peticion_lanzamiento() -> dict:
    return _sobre({
        "type": "LaunchRequest",
        "requestId": "amzn1.echo-api.request.1",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "locale": "es-ES",
    })


def peticion_comando(texto: str) -> dict:
    return _sobre({
        "type": "IntentRequest",
        "requestId": "amzn1.echo-api.request.2",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "locale": "es-ES",
        "intent": {
            "name": "ComandoIntent",
            "confirmationStatus": "NONE",
            "slots": {
                "comando": {
                    "name": "comando",
                    "value": texto,
                    "confirmationStatus": "NONE",
                    "source": "USER",
                }
            },
        },
    })


def peticion_intent_amazon(nombre: str) -> dict:
    return _sobre({
        "type": "IntentRequest",
        "requestId": "amzn1.echo-api.request.3",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "locale": "es-ES",
        "intent": {"name": nombre, "confirmationStatus": "NONE", "slots": {}},
    })


def texto_hablado(datos: dict) -> str:
    """Lo que Alexa va a decir, venga como texto plano o como SSML."""
    voz = datos.get("response", {}).get("outputSpeech", {}) or {}
    if voz.get("type") == "SSML":
        import re
        # Fuera las etiquetas: para comprobar el contenido nos da igual como
        # esta marcado el idioma.
        return re.sub(r"<[^>]+>", "", voz.get("ssml", "")).strip()
    return voz.get("text", "")


def validar_respuesta(datos: dict) -> list[str]:
    """Comprueba que la respuesta cumpla el esquema que exige Alexa."""
    errores = []

    if datos.get("version") != "1.0":
        errores.append("falta o es incorrecto el campo 'version'")

    respuesta = datos.get("response")
    if not isinstance(respuesta, dict):
        errores.append("falta el objeto 'response'")
        return errores

    if "shouldEndSession" not in respuesta:
        errores.append("falta 'shouldEndSession'")

    # REGLA CRITICA: si la sesion queda abierta, TIENE que haber reprompt.
    # Amazon cierra la sesion cuando shouldEndSession=false llega sin el, y el
    # sintoma es desconcertante: Alexa contesta y se apaga, como si el servidor
    # hubiera pedido cerrar. Esta comprobacion existe porque paso de verdad.
    if respuesta.get("shouldEndSession") is False and "reprompt" not in respuesta:
        errores.append(
            "sesion abierta SIN reprompt: Alexa la cerrara igualmente"
        )

    voz = respuesta.get("outputSpeech")
    if not isinstance(voz, dict):
        errores.append("falta 'outputSpeech'")
    else:
        if voz.get("type") not in ("PlainText", "SSML"):
            errores.append(f"tipo de voz inválido: {voz.get('type')}")

        texto = voz.get("text", "") or voz.get("ssml", "")

        # El SSML tiene que ser XML valido y estar envuelto en <speak>. Si se
        # cuela un & sin escapar, Alexa rechaza la respuesta entera y el
        # sintoma es el mismo "la skill no respondio correctamente" que nos
        # costo dias localizar por otras causas.
        if voz.get("type") == "SSML":
            ssml = voz.get("ssml", "")
            if not ssml.startswith("<speak>") or not ssml.endswith("</speak>"):
                errores.append("el SSML no viene envuelto en <speak>")
            try:
                import xml.etree.ElementTree as ET
                ET.fromstring(ssml)
            except Exception as e:
                errores.append(f"el SSML no es XML válido: {e}")
        if len(texto) > 8000:
            errores.append(f"el texto supera el límite de Alexa ({len(texto)} caracteres)")
        # Alexa lee el markdown literalmente y suena mal.
        for simbolo in ("**", "```", "##"):
            if simbolo in texto:
                errores.append(f"quedó markdown sin limpiar: {simbolo!r}")

    return errores


# Con la sesion continua, estas deben devolver la sesion ABIERTA y con reprompt.
COMANDOS = [
    "cuánto uso de cpu tengo",
    "cómo está la memoria ram",
    "dame el estado general del equipo",
    "en qué modo estás",
    "qué archivos hay en el escritorio",
    "ayuda",
    "cómo quedó lo último",
]


def peticion_dictado(texto: str) -> dict:
    """MensajeIntent con AMAZON.SearchQuery: el dictado libre."""
    return _sobre({
        "type": "IntentRequest",
        "requestId": "amzn1.echo-api.request.5",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "locale": "es-MX",
        "intent": {
            "name": "MensajeIntent",
            "confirmationStatus": "NONE",
            "slots": {
                "texto": {"name": "texto", "value": texto,
                          "confirmationStatus": "NONE", "source": "USER"}
            },
        },
    })


def probar_si_y_no() -> list[str]:
    """
    Un "si" tiene que llegar como intent, no como Fallback.

    Capturado del registro: con un mensaje esperando confirmacion, dijiste
    "si" y salieron CUATRO AMAZON.FallbackIntent seguidos hasta que te
    rendiste con un "pausa". Una palabra de dos letras no se parece a nada
    del slot personalizado, asi que Alexa no supo encaminarla.

    Amazon tiene YesIntent y NoIntent justo para esto.
    """
    import confirmaciones
    fallos = []

    print()
    print("SI Y NO")

    hechos = []
    confirmaciones.olvidar()
    confirmaciones.pedir("enviar el mensaje", lambda: hechos.append("hecho") or "Enviado.")

    respuesta = cliente.post("/jarvis", json=peticion_intent_amazon("AMAZON.YesIntent"))
    if respuesta.status_code != 200:
        fallos.append(f"YesIntent devolvió {respuesta.status_code}")
        print(f"  FALLO  YesIntent -> HTTP {respuesta.status_code}")
    elif not hechos:
        fallos.append("el YesIntent no confirmó la acción pendiente")
        print(f"  FALLO  YesIntent no confirmó -> {texto_hablado(respuesta.json())[:44]!r}")
    else:
        print(f"  OK     YesIntent confirma    -> {texto_hablado(respuesta.json())[:44]!r}")

    # Y el "no" cancela.
    hechos.clear()
    confirmaciones.olvidar()
    confirmaciones.pedir("enviar el mensaje", lambda: hechos.append("hecho") or "Enviado.")
    respuesta = cliente.post("/jarvis", json=peticion_intent_amazon("AMAZON.NoIntent"))
    if hechos:
        fallos.append("GRAVE: el NoIntent ejecutó la acción igualmente")
        print("  FALLO  GRAVE: el NoIntent ejecutó la acción")
    else:
        print(f"  OK     NoIntent cancela      -> {texto_hablado(respuesta.json())[:44]!r}")

    # Y si aun asi cae al Fallback, tiene que decir como salir del bucle.
    confirmaciones.olvidar()
    confirmaciones.pedir("enviar el mensaje", lambda: "Enviado.")
    respuesta = cliente.post("/jarvis", json=peticion_intent_amazon("AMAZON.FallbackIntent"))
    dicho = texto_hablado(respuesta.json()).lower()
    if "sí" in dicho or "si " in dicho:
        print(f"  OK     Fallback con confirmación pendiente explica cómo salir")
    else:
        fallos.append("el Fallback no explica cómo responder a la confirmación")
        print(f"  FALLO  Fallback -> {dicho[:50]!r}")
    confirmaciones.olvidar()

    return fallos


def probar_modelo_de_interaccion() -> list[str]:
    """
    Valida el JSON del modelo ANTES de que lo rechace la consola de Amazon.

    Existe porque escribi una muestra con corchetes -- "d[i]le que {texto}",
    intentando marcar una letra opcional con una sintaxis que Alexa no tiene --
    y el Build entero fallo. Amazon solo admite letras, espacios, puntos de
    abreviatura, guiones bajos, apostrofes y guiones.

    Ese viaje es caro: editar, pegar en la consola, Save, Build, leer el error.
    Aqui cuesta un segundo.
    """
    import json
    import re
    from pathlib import Path

    fallos = []
    print()
    print("MODELO DE INTERACCION")

    ruta = Path(__file__).parent / "alexa" / "interaction_model.json"
    if not ruta.exists():
        print("  (no encuentro el modelo, me lo salto)")
        return fallos

    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as e:
        fallos.append(f"el modelo de interacción no es JSON válido: {e}")
        print(f"  FALLO  JSON inválido: {e}")
        return fallos

    lm = datos["interactionModel"]["languageModel"]

    # 1. Caracteres prohibidos en las muestras.
    for intent in lm["intents"]:
        for muestra in intent.get("samples", []):
            sin_slots = re.sub(r"\{\w+\}", "SLOT", muestra)
            malos = sorted({c for c in sin_slots if not (c.isalpha() or c in " ._'-")})
            if malos:
                fallos.append(f"{intent['name']}: {muestra!r} tiene {malos}")
                print(f"  FALLO  {intent['name']}: {muestra!r} -> caracteres {malos}")

    # 2. Los slots que se usan tienen que estar declarados.
    for intent in lm["intents"]:
        declarados = {s["name"] for s in intent.get("slots", [])}
        for muestra in intent.get("samples", []):
            for usado in re.findall(r"\{(\w+)\}", muestra):
                if usado not in declarados:
                    fallos.append(f"{intent['name']}: {muestra!r} usa un slot no declarado")
                    print(f"  FALLO  {intent['name']}: {{{usado}}} no está declarado")

    # 3. AMAZON.SearchQuery NO admite una muestra que sea solo el slot.
    for intent in lm["intents"]:
        tipos = {s["name"]: s.get("type") for s in intent.get("slots", [])}
        for muestra in intent.get("samples", []):
            solo = re.fullmatch(r"\{(\w+)\}", muestra.strip())
            if solo and tipos.get(solo.group(1)) == "AMAZON.SearchQuery":
                fallos.append(f"{intent['name']}: SearchQuery no admite la muestra {muestra!r}")
                print(f"  FALLO  SearchQuery a solas en {intent['name']}")

    # 4. fallbackIntentSensitivity solo existe en ingles y aleman: en es-MX
    #    tumba el Build entero con "Unsupported model configuration".
    if "modelConfiguration" in lm or "modelConfiguration" in datos["interactionModel"]:
        fallos.append("modelConfiguration no está permitido en español")
        print("  FALLO  modelConfiguration presente (solo vale en inglés y alemán)")

    if not fallos:
        total = sum(len(i.get("samples", [])) for i in lm["intents"])
        valores = sum(len(t["values"]) for t in lm.get("types", []))
        print(f"  OK     {len(lm['intents'])} intents, {total} muestras, {valores} valores del slot")

    return fallos


def probar_dictado_libre() -> list[str]:
    """
    El texto de un mensaje llega por MensajeIntent, no por ComandoIntent.

    Capturado del registro: se pregunto "¿que le digo?" y la respuesta se fue
    a AMAZON.FallbackIntent. El Fallback NO TRAE EL TEXTO -- Amazon solo dice
    que no entendio -- asi que el mensaje se perdia y la conversacion moria
    ahi. De cara al usuario parecia que Jarvis se colgaba.
    """
    fallos = []
    print()
    print("DICTADO LIBRE")

    respuesta = cliente.post("/jarvis", json=peticion_dictado("llego en diez minutos"))
    if respuesta.status_code != 200:
        fallos.append(f"MensajeIntent devolvió {respuesta.status_code}")
        print(f"  FALLO  MensajeIntent -> HTTP {respuesta.status_code}")
        return fallos

    errores = validar_respuesta(respuesta.json())
    if errores:
        fallos.extend(errores)
        print(f"  FALLO  MensajeIntent -> {errores}")
    else:
        print(f"  OK     MensajeIntent con texto  -> {texto_hablado(respuesta.json())[:50]!r}")

    # Sin texto no puede reventar.
    respuesta = cliente.post("/jarvis", json=peticion_dictado(""))
    if respuesta.status_code == 200 and not validar_respuesta(respuesta.json()):
        print("  OK     MensajeIntent vacío      -> respondió sin romperse")
    else:
        fallos.append("MensajeIntent sin texto rompió el servidor")
        print("  FALLO  MensajeIntent vacío")

    # Y el Fallback, con un mensaje a medias, tiene que explicar como seguir
    # en vez de dejarte colgado con un "no entendi".
    import foco
    foco.recordar("destinatario", "familia")
    respuesta = cliente.post("/jarvis", json=peticion_intent_amazon("AMAZON.FallbackIntent"))
    dicho = texto_hablado(respuesta.json())
    if "familia" in dicho.lower() and "que" in dicho.lower():
        print(f"  OK     Fallback con mensaje a medias -> {dicho[:56]!r}")
    else:
        fallos.append("el Fallback no recupera el mensaje a medias")
        print(f"  FALLO  Fallback con mensaje a medias -> {dicho[:56]!r}")
    foco.olvidar()

    return fallos


def probar_que_no_se_bloquea() -> list[str]:
    """
    Una orden lenta NO puede dejar sordo al servidor.

    Esta prueba existe por un fallo concreto y caro de encontrar. El endpoint
    era `async def` pero por dentro llamaba a codigo que bloquea: subprocess,
    psutil, la descarga del certificado de Amazon y, sobre todo, la espera al
    modelo de hasta 6,5 segundos. En un servidor asincrono eso paraliza el
    bucle de eventos entero: mientras una orden se cocina, uvicorn no puede
    aceptar conexiones nuevas ni completar saludos TLS.

    El sintoma era desconcertante. Todo iba bien y cada cierto comando Alexa
    decia que la skill no respondia, sin que la peticion apareciera en el
    registro. No aparecia porque nunca llegaba a entrar.

    Aqui se lanzan dos ordenes a la vez, una lenta y una rapida. Si la rapida
    tiene que esperar a la lenta, el bucle esta bloqueado otra vez.
    """
    import asyncio
    import httpx

    fallos = []
    print()
    print("CONCURRENCIA")

    original = server.procesar_comando

    def lento_o_rapido(texto, datos_alexa=None):
        if texto == "LENTO":
            time.sleep(2.0)          # imita la espera al modelo
            return "tarde pero llegue"
        return original(texto, datos_alexa)

    server.procesar_comando = lento_o_rapido

    async def correr():
        transporte = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://x") as cli:
            # create_task, no solo llamar: una corrutina sin agendar no
            # empieza a correr, y la prueba pasaria sin haber probado nada.
            # (Me paso: daba OK con el fallo reintroducido a proposito.)
            # El cronometro arranca ANTES de lanzar la lenta, no despues.
            # Si se mide solo la duracion de la rapida, el bloqueo no se ve:
            # con el bucle parado, ni el `await asyncio.sleep` de abajo avanza,
            # asi que la rapida "empieza" cuando la lenta ya termino y sale un
            # tiempo estupendo. Me paso: la prueba daba OK con el fallo puesto
            # a proposito. Lo que importa es el reloj de pared total.
            inicio = time.perf_counter()

            # create_task, no solo llamar: una corrutina sin agendar no
            # empieza a correr, y no se estaria probando nada.
            lenta = asyncio.create_task(
                cli.post("/jarvis", json=peticion_comando("LENTO"))
            )
            await asyncio.sleep(0.3)          # damos tiempo a que entre

            rapida = await cli.post("/jarvis", json=peticion_comando("en qué modo estás"))
            demora = time.perf_counter() - inicio

            await lenta
            return demora, rapida.status_code

    try:
        demora, estado = asyncio.run(correr())
    finally:
        server.procesar_comando = original

    if estado != 200:
        fallos.append(f"la orden rápida devolvió {estado} mientras otra iba lenta")
        print(f"  FALLO  la rápida devolvió {estado}")
    elif demora > 1.5:
        fallos.append(
            f"la orden rápida no llegó hasta los {demora:.1f}s: "
            "el bucle de eventos está bloqueado"
        )
        print(f"  FALLO  la rápida no llegó hasta los {demora:.1f}s -> bucle bloqueado")
    else:
        print(f"  OK     una orden lenta (2 s) no bloquea a las demás "
              f"(la rápida contestó a los {demora * 1000:.0f} ms)")

    return fallos


def main() -> int:
    fallos = []
    print("=" * 70)
    print("  PRUEBA DE EXTREMO A EXTREMO CONTRA EL SERVIDOR")
    print("=" * 70)
    print()

    # ---- Endpoints de estado ----
    print("ENDPOINTS DE ESTADO")
    respuesta = cliente.get("/jarvis")
    if respuesta.status_code == 200:
        print(f"  OK     GET /jarvis  -> {respuesta.json().get('mensaje')}")
    else:
        fallos.append(f"GET /jarvis devolvió {respuesta.status_code}")
        print(f"  FALLO  GET /jarvis  -> {respuesta.status_code}")

    respuesta = cliente.get("/salud")
    if respuesta.status_code == 200:
        salud = respuesta.json()
        print(f"  OK     GET /salud   -> modo={salud['modo']} ollama={salud['ollama']['conectado']}")
    else:
        fallos.append(f"GET /salud devolvió {respuesta.status_code}")
        print(f"  FALLO  GET /salud   -> {respuesta.status_code}")

    # ---- LaunchRequest ----
    print()
    print("APERTURA DE LA SKILL")
    respuesta = cliente.post("/jarvis", json=peticion_lanzamiento())
    if respuesta.status_code == 200:
        datos = respuesta.json()
        errores = validar_respuesta(datos)
        texto = texto_hablado(datos)
        abierta = datos["response"]["shouldEndSession"] is False
        if errores:
            fallos.extend(errores)
            print(f"  FALLO  LaunchRequest -> {errores}")
        elif not abierta:
            fallos.append("LaunchRequest debería mantener la sesión abierta")
            print("  FALLO  LaunchRequest -> cerró la sesión")
        else:
            print(f"  OK     LaunchRequest -> {texto!r}")
    else:
        fallos.append(f"LaunchRequest devolvió {respuesta.status_code}")
        print(f"  FALLO  LaunchRequest -> {respuesta.status_code}")

    # ---- Comandos ----
    print()
    print("COMANDOS DE VOZ")
    for comando in COMANDOS:
        respuesta = cliente.post("/jarvis", json=peticion_comando(comando))

        if respuesta.status_code != 200:
            fallos.append(f"{comando!r} devolvió {respuesta.status_code}")
            print(f"  FALLO  {comando[:38]:<38} -> HTTP {respuesta.status_code}")
            continue

        datos = respuesta.json()
        errores = validar_respuesta(datos)

        if errores:
            fallos.extend(f"{comando!r}: {e}" for e in errores)
            print(f"  FALLO  {comando[:38]:<38} -> {errores}")
            continue

        texto = texto_hablado(datos)
        print(f"  OK     {comando[:38]:<38} -> {texto[:52]!r}")

    # ---- Intents integrados de Amazon ----
    print()
    print("INTENTS DE AMAZON")
    for nombre in ("AMAZON.HelpIntent", "AMAZON.StopIntent", "AMAZON.FallbackIntent"):
        respuesta = cliente.post("/jarvis", json=peticion_intent_amazon(nombre))
        if respuesta.status_code == 200 and not validar_respuesta(respuesta.json()):
            texto = texto_hablado(respuesta.json())
            print(f"  OK     {nombre:<28} -> {texto[:36]!r}")
        else:
            fallos.append(f"{nombre} falló")
            print(f"  FALLO  {nombre}")

    # ---- Casos límite ----
    print()
    print("CASOS LÍMITE")

    respuesta = cliente.post("/jarvis", json=peticion_comando(""))
    if respuesta.status_code == 200:
        print("  OK     comando vacío                          -> respondió sin romperse")
    else:
        fallos.append("el comando vacío rompió el servidor")
        print(f"  FALLO  comando vacío -> {respuesta.status_code}")

    respuesta = cliente.post("/jarvis", content=b"esto no es json")
    if respuesta.status_code == 400:
        print("  OK     JSON inválido                          -> rechazado con 400")
    else:
        fallos.append(f"el JSON inválido devolvió {respuesta.status_code} en vez de 400")
        print(f"  FALLO  JSON inválido -> {respuesta.status_code}")

    respuesta = cliente.post("/jarvis", json={"version": "1.0"})
    if respuesta.status_code == 200:
        print("  OK     petición sin 'request'                 -> respondió sin romperse")
    else:
        fallos.append("la petición sin request rompió el servidor")
        print(f"  FALLO  petición sin request -> {respuesta.status_code}")

    fallos.extend(probar_modelo_de_interaccion())
    fallos.extend(probar_si_y_no())
    fallos.extend(probar_dictado_libre())
    fallos.extend(probar_que_no_se_bloquea())

    # ---- Resultado ----
    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS")
        print("=" * 70)
        for fallo in fallos:
            print("   -", fallo)
        return 1

    print("  TODO CORRECTO. El servidor responde en formato Alexa válido.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
