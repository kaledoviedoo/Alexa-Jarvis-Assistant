"""Cliente de Ollama con function calling y presupuesto de tiempo.

Aqui caen las ordenes que el router no supo resolver: razonar, redactar,
encadenar pasos. Como Alexa corta a los ocho segundos y el modelo puede
tardar mas, la respuesta y la ejecucion van separadas: si no termina a
tiempo, Alexa contesta que esta en ello y el trabajo sigue en un hilo, con
el resultado guardado en tareas.py.

Incluye el freno de mano: las herramientas que tocan el equipo solo se
ejecutan si la frase original de verdad las pedia.
"""

import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as TimeoutFuturo

import modes
import tareas
from config import MAX_PASOS_TOOLS, NOMBRE_USUARIO, PRESUPUESTO_SEGUNDOS
import registro
from tools import avanzado, obsidian

log = logging.getLogger("jarvis.ollama")

_ejecutor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="jarvis-llm")


_PATRON_LLAMADA_TEXTO = re.compile(
    r'\{\s*"(?:name|function|tool)"\s*:\s*"(?P<nombre>\w+)"\s*,\s*'
    r'"(?:parameters|arguments|params)"\s*:\s*(?P<args>\{.*?\})\s*\}',
    re.DOTALL,
)


def _rescatar_llamadas_en_texto(texto: str) -> str | None:
    """Si el modelo escribio llamadas a herramientas como texto plano, las ejecuta."""
    coincidencias = list(_PATRON_LLAMADA_TEXTO.finditer(texto or ""))
    if not coincidencias:
        return None

    log.warning(
        "El modelo escribio %d llamada(s) como texto en vez de invocarlas. Las ejecuto yo.",
        len(coincidencias),
    )

    resultados = []
    for m in coincidencias[:5]:
        nombre = m.group("nombre")
        try:
            argumentos = json.loads(m.group("args"))
        except Exception:
            argumentos = {}
        resultados.append(_ejecutar_herramienta(nombre, argumentos))

    if not resultados:
        return None

    if len(resultados) == 1:
        return resultados[0]
    return " ".join(resultados[:3])


def _parece_json(texto: str) -> bool:
    """Detecta restos de JSON o codigo que no deberian leerse en voz alta."""
    t = (texto or "").strip()
    if not t:
        return False
    if t.startswith("{") or t.startswith("["):
        return True
    # Muchas llaves y comillas juntas: casi seguro es estructura, no lenguaje.
    señales = t.count('":') + t.count('"name"') + t.count("parameters")
    return señales >= 2


_PREFIJOS_PARASITOS = re.compile(
    r'^\s*(?:alexa\s+(?:lee|leera|dice|dira)\s+en\s+voz\s+alta|'
    r'respuesta\s+final|respuesta|jarvis\s+responde|tu\s+respuesta|'
    r'respondo|dir[ií]a|salida)\s*[:\-]\s*',
    re.IGNORECASE,
)


_NOMBRE_SUELTO = re.compile(
    r'^\s*(?:llamada\s*[:\-]?\s*)?'
    r'(?P<nombre>[a-z][a-z0-9]*(?:_[a-z0-9]+)+)'      # tiene que llevar guion bajo
    r'\s*(?:\([^)]*\))?\s*[:\-]?\s*(?:\n|\s)+',
    re.IGNORECASE,
)


def _quitar_nombre_de_herramienta(texto: str) -> str:
    """Quita el nombre de herramienta que el modelo a veces pega delante."""
    limpio = (texto or "").strip()
    for _ in range(2):
        m = _NOMBRE_SUELTO.match(limpio)
        if not m:
            break
        if m.group("nombre").lower() not in DESPACHADOR:
            break
        resto = limpio[m.end():].strip()
        if not resto:
            break                      # solo el nombre: que lo trate _parece_json
        log.info("Quito el nombre de herramienta suelto: %r", m.group("nombre"))
        limpio = resto
    return limpio


def _quitar_prefijos(texto: str) -> str:
    """Quita las muletillas que el modelo hereda del prompt del sistema."""
    limpio = (texto or "").strip()
    for _ in range(3):
        nuevo = _PREFIJOS_PARASITOS.sub("", limpio).strip()
        if nuevo == limpio:
            break
        limpio = nuevo

    # Si ademas lo envolvio en comillas, se las quitamos: Alexa las lee.
    # Tambien con el punto fuera: "No lo tengo en mis apuntes".
    m = re.fullmatch(r'["\u201c\u00ab](.+?)["\u201d\u00bb]\s*([.!?]?)', limpio, re.DOTALL)
    if m:
        limpio = (m.group(1).strip() + m.group(2)).strip()

    return limpio


_LATEX = [
    (r"\\[()\[\]]", ""),                        # \( \) \[ \]
    (r"\$\$?", ""),
    (r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"\1 sobre \2"),
    (r"\\sqrt\{([^{}]*)\}", r"raiz de \1"),
    (r"\\int", "la integral de "),
    (r"\\sum", "la suma de "),
    (r"\\cdot|\\times", " por "),
    (r"\\(?:,|;|!|quad|qquad)", " "),
    (r"\\(alpha|beta|gamma|delta|theta|lambda|pi|sigma|infty)", r"\1"),
    (r"\^\{?2\}?", " al cuadrado"),
    (r"\^\{?([^{}\s]+)\}?", r" a la \1"),
    (r"[{}]", ""),
]


def latex_a_palabras(texto: str) -> str:
    """Lo que el modelo escribe en LaTeX, dicho para el altavoz."""
    if "\\" not in texto and "$" not in texto:
        return texto
    for patron, cambio in _LATEX:
        texto = re.sub(patron, cambio, texto)
    texto = texto.replace("infty", "infinito")
    return re.sub(r"\s{2,}", " ", texto).strip()


def limpiar_respuesta_modelo(texto: str) -> str:
    """Deja la respuesta del modelo lista para pronunciarse."""
    texto = (texto or "").strip()

    texto = _quitar_prefijos(texto)
    rescatado = _rescatar_llamadas_en_texto(texto)
    if rescatado:
        return rescatado

    # Despues del rescate, no antes: si el nombre venia con argumentos era una
    # llamada de verdad y la queriamos ejecutar, no borrar.
    texto = _quitar_nombre_de_herramienta(texto)

    # Solo el nombre y nada mas: la llamada se perdio por el camino. Leerlo en
    # voz alta ("listar guion bajo archivos") es peor que reconocer el fallo.
    if texto.strip().lower() in DESPACHADOR:
        log.warning("El modelo respondio solo el nombre de la herramienta: %r", texto)
        return "No me salió esa. Repítemela de otra forma."

    if _parece_json(texto):
        log.warning("Respuesta del modelo descartada por parecer JSON: %r", texto[:160])
        return (
            "El modelo no supo responder eso correctamente. "
            "Prueba a decirlo de otra forma o cambia a modo dedicado."
        )

    # Fuera bloques de codigo: Alexa los lee caracter a caracter.
    texto = re.sub(r"```[\s\S]*?```", "", texto).strip()
    texto = latex_a_palabras(texto)

    return texto or "No supe qué hacer con esa orden."


# Las herramientas ya no se listan aqui: cada una se declara con @herramienta
# junto a su funcion (registro.py). Estas son vistas generadas, para el codigo
# que las sigue leyendo.
registro.cargar()
DESPACHADOR = registro.despachador()
HERRAMIENTAS = registro.esquemas(registro.para_el_modelo(""))   # solo el nucleo


def _extras_para(num_ctx: int) -> int:
    """Cuantas herramientas fuera del nucleo caben segun el contexto del modo."""
    # Solo el nucleo ya son ~3.000 tokens y el prompt del sistema ~1.700.
    return 8 if num_ctx >= 8192 else 2


_PREGUNTA = re.compile(
    r"^\s*(?:¿|"
    r"(?:qu[eé]|cu[aá]l(?:es)?|cu[aá]nt[oa]s?|c[oó]mo|d[oó]nde|cu[aá]ndo|qui[eé]n(?:es)?|"
    r"por\s*qu[eé]|tengo|tienes|hay|est[aá]n?|existe|sabes|dime|me\s+dices|es\s+cierto)\b)",
    re.IGNORECASE,
)


def es_pregunta(frase: str) -> bool:
    """¿La orden pide saber algo en vez de cambiar algo?"""
    f = (frase or "").strip()
    # "segun mis apuntes, que es..." sigue siendo una pregunta.
    f = re.sub(r"^\s*(?:seg[uú]n|en)\s+mis\s+\w+\s*,?\s*", "", f, flags=re.IGNORECASE)
    return f.endswith("?") or bool(_PREGUNTA.match(f))


def herramientas_para(comando: str) -> list[dict]:
    """Los esquemas que se le ofrecen al modelo para esta orden.

    Primero por significado (pocas y relevantes). Si no hay vectores, el
    nucleo de siempre mas las extras por palabras clave.
    """
    perfil = modes.perfil_actual()
    num_ctx = int(perfil.get("num_ctx", 4096))
    elegidas = registro.elegir(comando, cuantas=10 if num_ctx >= 8192 else 6)
    if elegidas is None:
        elegidas = registro.para_el_modelo(comando, _extras_para(num_ctx))
        como = "nucleo + palabras"
    else:
        como = "por significado"

    # Una pregunta se contesta mirando. Si no se le ofrece nada que cambie el
    # equipo, el modelo no puede "contestar" cerrando Steam.
    if es_pregunta(comando):
        solo_miran = [h for h in elegidas if h.riesgo == "lectura"]
        if solo_miran:
            elegidas = solo_miran
            como += ", solo lectura por ser pregunta"
    log.info("Ofrezco %d herramientas (%s): %s", len(elegidas), como,
             ", ".join(h.nombre for h in elegidas))
    return registro.esquemas(elegidas)


PROMPT_BASE = """Eres Jarvis, el asistente local que controla este equipo con Windows.

REGLAS:
1. Si la orden implica una accion en el equipo (crear, leer, editar, mover o borrar archivos, abrir o cerrar programas, buscar, escribir con el teclado, apuntar en Obsidian), invoca SIEMPRE la herramienta correspondiente. No describas lo que harias: hazlo.
2. NUNCA escribas la llamada a la herramienta como texto. Usa el mecanismo de herramientas. Si escribes JSON en tu respuesta, el usuario lo oira leido en voz alta y sera inutil.
3. Al crear codigo, escribe codigo REAL, completo y funcional, con comentarios en espanol que expliquen el porque. Nada de esqueletos ni de "aqui iria la logica".
4. Tu respuesta final la lee Alexa en voz alta: maximo dos frases cortas en espanol, sin markdown, sin listas, sin emojis y sin leer codigo.
5. Nunca inventes rutas, nombres de archivo ni datos. Si necesitas saber que hay, usa listar_archivos, explorar_carpeta o buscar_en_contenido antes de responder.
6. Si una herramienta falla, dilo con claridad. No finjas que funciono.
7. Las preguntas de conocimiento general ("que es la oferta y la demanda", "explicame X", "cuanto es 15 por 4") se contestan DIRECTAMENTE con lo que sabes. No busques en Obsidian ni en el disco: las notas del usuario son SUS apuntes, no una enciclopedia. Solo usa las herramientas de notas si la orden menciona sus notas, sus apuntes, su vault, su diario o algo que el escribio.
8. Si la orden es demasiado corta o ambigua para saber que quiere ("no", "si", "vale", "ok", "eso"), NO llames a ninguna herramienta: pregunta que necesita. Cambiar el modo del equipo porque el usuario dijo "no" es un error grave.
9. Responde solo con lo que hay que decir. No narres lo que vas a hacer, no repitas la orden y no escribas prefijos como "Alexa lee en voz alta:" ni "Respuesta:". Esas palabras acabarian sonando por el altavoz.
10. Elige la herramienta que hace EXACTAMENTE lo pedido. Si la orden es una pregunta ("tengo...", "hay...", "cual es...", "que dice..."), usa solo herramientas que miran (listar, leer, buscar, explicar); NUNCA una que cambia algo (cerrar, apuntar, mover, borrar, crear).
11. Si dice "segun mis apuntes", "en mis notas" o "en mi boveda", la respuesta sale de sus notas: usa explicar_desde_notas o encontrar_nota, no tu memoria.
12. Nada de LaTeX ni simbolos matematicos: las formulas se dicen con palabras ("la integral de u por dv es u por v menos la integral de v por du").

COMO HABLAS:
Hablas con {NOMBRE_USUARIO}, que es quien te construyo. Tuteale.

Suena a persona, no a manual. Un asistente que contesta "Operacion completada satisfactoriamente" a cada cosa cansa en dos dias. Di "listo", "hecho", "ya esta", "ahi lo tienes", y varia: repetir siempre la misma formula suena tan robotico como la formula mas formal.

Tienes sentido del humor, seco y breve. Un comentario cuando viene a cuento, NUNCA un chiste forzado ni un chiste que retrase la respuesta util. Si te pide algo a las tres de la manana puedes decirselo de pasada; si algo va mal, puedes reconocerlo sin dramatismo ("esa se me escapo"). El humor va DESPUES de la informacion, nunca en lugar de ella.

Si te habla y no te pide nada (te saluda, comenta algo, se queja del dia), contestale como contestaria una persona: breve y al grano, sin convertirlo en una orden ni preguntarle en que puedes ayudarle. No todo lo que se dice en voz alta es una peticion.

Cuando algo te salga mal, dilo claro y sin excusas largas. "No pude, el archivo no existe" vale mas que tres frases de disculpa.

Nada de emojis ni de simbolos: esto se lee en voz alta."""


def construir_prompt_sistema() -> str:
    """Monta el prompt con el contexto real del usuario y del equipo."""
    partes = [PROMPT_BASE.replace("{NOMBRE_USUARIO}", NOMBRE_USUARIO or "el usuario")]

    try:
        partes.append("\n--- ESTE EQUIPO ---\n" + avanzado.resumen_equipo_para_modelo())
    except Exception as e:
        log.debug("No pude leer el estado del equipo: %s", e)

    try:
        contexto = avanzado.leer_contexto()
        if contexto:
            partes.append("\n--- QUIEN ES EL USUARIO (lo escribio el mismo) ---\n" + contexto)
    except Exception as e:
        log.debug("No pude leer el contexto: %s", e)

    try:
        v = obsidian.vault()
        if v is not None:
            partes.append(
                f"\n--- OBSIDIAN ---\nEl usuario tiene un vault en {v}. "
                "Usa las herramientas obsidian_* para apuntar notas e ideas."
            )
    except Exception as e:
        # Si esto falla el modelo se queda sin saber que hay boveda, y luego
        # no se entiende por que no apunta nada.
        log.debug("No pude anadir el contexto de Obsidian: %s", e)

    return "\n".join(partes)


# Se mantiene el nombre antiguo por compatibilidad con codigo que lo importe.
SYSTEM_PROMPT = PROMPT_BASE


_ENVOLTORIOS = ("parameters", "arguments", "args", "params", "input", "kwargs")


def _desenvolver_argumentos(argumentos) -> dict:
    """Saca los argumentos de verdad cuando el modelo manda el sobre entero."""
    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos)
        except Exception:
            return {}

    if not isinstance(argumentos, dict):
        return {}

    # Hasta tres capas: algunos modelos anidan el sobre dentro del sobre.
    for _ in range(3):
        for llave in _ENVOLTORIOS:
            dentro = argumentos.get(llave)
            if isinstance(dentro, str):
                try:
                    dentro = json.loads(dentro)
                except Exception:
                    dentro = None
            if isinstance(dentro, dict):
                argumentos = dentro
                break
        else:
            break

    # Metadatos de la llamada que nunca son parametros de la herramienta.
    return {k: v for k, v in argumentos.items()
            if k not in ("type", "function", "name", "tool", "tool_name", "recipient_name")}


# Pistas que el freno busca en la frase, sacadas del registro. Desaparece en
# la Fase 3, cuando el freno pase a decidir por riesgo.
HERRAMIENTAS_QUE_TOCAN = registro.corroboracion()

LARGO_MAXIMO_DE_UNA_ORDEN = 70

# Lo que se le dice al modelo cuando el freno le para una accion. Antes era
# "contesta con palabras", y el modelo contestaba lo primero que se le ocurria
# ("si, Steam esta abierto") sin haber mirado.
_SIN_INVENTAR = ("Si te pregunto algo, usa una herramienta que solo mire (listar, "
                 "leer, buscar) y responde con lo que devuelva. Si no tienes el dato, "
                 "dilo. No inventes.")

# Argumentos que no son un valor: son el hueco sin rellenar. Si el modelo
# manda esto es que copio el ejemplo del esquema.
ARGUMENTOS_VACIOS = {
    "nada", "ninguno", "ninguna", "n/a", "none", "null", "texto", "mensaje",
    "archivo", "documento", "aplicacion", "aplicación", "app", "programa",
    "carpeta", "nombre", "algo", "eso", "esto", "string", "valor",
}

# Nunca, venga de donde venga. Cerrar Alexa desde Alexa deja la sesion muerta
# a media orden y no hay forma de recuperarla hablando.
NUNCA_CERRAR = {"alexa", "jarvis", "python", "pythonw", "asistente",
                "mi asistente", "explorer", "systemd", "svchost"}

_peticion = threading.local()


def recordar_peticion(texto: str) -> None:
    """Guarda la frase original para poder contrastarla con lo que se pide."""
    _peticion.texto = texto or ""


def _frase_original() -> str:
    return getattr(_peticion, "texto", "") or ""


def _valor_principal(argumentos: dict) -> str:
    for valor in argumentos.values():
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return ""


def _por_que_no(nombre: str, argumentos: dict) -> str:
    """Motivo por el que NO se ejecuta esta llamada, o cadena vacia si pasa."""
    valor = _valor_principal(argumentos)

    # 1. El hueco del ejemplo sin rellenar.
    if valor.lower() in ARGUMENTOS_VACIOS:
        return (f"No ejecuto {nombre}: '{valor}' no es un valor real, es el "
                "hueco del ejemplo. Pregúntale a qué se refiere.")

    # 2. El modelo paso el nombre de otra herramienta como si fuera un dato.
    if valor.lower() in DESPACHADOR:
        return (f"No ejecuto {nombre}: '{valor}' es el nombre de otra "
                "herramienta, no un dato. Contesta con palabras.")

    # 3. Cerrarse a si mismo.
    if nombre == "cerrar_aplicacion" and valor.lower() in NUNCA_CERRAR:
        return (f"No cierro {valor}: es parte del propio asistente y me "
                "dejaria sin poder contestarte.")

    corroboran = HERRAMIENTAS_QUE_TOCAN.get(nombre)
    if not corroboran:
        return ""            # solo mira: que pase

    frase = _frase_original().lower()
    if not frase:
        return ""            # sin frase que contrastar, no inventamos vetos

    # 4. La frase no pide esto por ninguna parte.
    if not any(pista in frase for pista in corroboran):
        return (f"No ejecuto {nombre}: en lo que dijo no hay nada que lo pida. "
                + _SIN_INVENTAR)

    if len(frase) > LARGO_MAXIMO_DE_UNA_ORDEN:
        return (f"No ejecuto {nombre}: eso parece conversacion, no una orden. "
                + _SIN_INVENTAR)

    return ""


def _ejecutar_herramienta(nombre: str, argumentos: dict) -> str:
    h = registro.obtener(nombre)
    if h is None:
        return f"No existe la herramienta {nombre}."

    # Lo externo y lo irreversible no se ejecuta desde aqui aunque el modelo
    # lo pida por su nombre: ni siquiera se le ofrece (registro.OFRECIBLES).
    if h.riesgo not in registro.OFRECIBLES:
        log.warning("FRENO: %s es de riesgo %s; el modelo no lo ejecuta.", nombre, h.riesgo)
        return (f"No ejecuto {nombre} por mi cuenta: es de riesgo {h.riesgo}. "
                "Dile que me lo pida directamente.")

    if not registro.disponible(h):
        return f"{nombre} no está disponible en este equipo ahora mismo."

    funcion = h.funcion

    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos)
        except Exception:
            argumentos = {}

    argumentos = _desenvolver_argumentos(argumentos)

    veto = _por_que_no(nombre, argumentos)
    if veto:
        log.warning("FRENO: %s con %s. %s", nombre, argumentos, veto)
        return veto

    log.info("Ejecutando herramienta %s con %s", nombre, argumentos)

    # Solo pasan los parametros que la herramienta declara para el modelo: el
    # resto (limites, zonas de pantalla) se queda con su valor por defecto.
    validas = set(h.nombres_parametros)
    sobran = [k for k in argumentos if k not in validas]
    if sobran:
        log.info("Descarto argumentos que %s no acepta: %s", nombre, sobran)
        argumentos = {k: v for k, v in argumentos.items() if k in validas}

    try:
        resultado = funcion(**argumentos)
        return resultado if isinstance(resultado, str) else str(resultado)
    except TypeError as e:
        # El modelo mandó argumentos que no encajan con la firma.
        log.warning("Argumentos inválidos para %s: %s", nombre, e)
        return f"No pude ejecutar {nombre}: los datos no eran correctos."
    except Exception as e:
        log.exception("Fallo en la herramienta %s", nombre)
        return f"La herramienta {nombre} falló: {e}"


def _conversar(comando: str) -> str:
    """Bucle de function calling. Puede tardar; se ejecuta en un hilo aparte."""
    try:
        import ollama
    except ImportError:
        return "No tengo Ollama instalado, así que no puedo razonar esa orden."

    recordar_peticion(comando)

    perfil = modes.perfil_actual()
    mensajes = [
        {"role": "system", "content": construir_prompt_sistema()},
        {"role": "user", "content": comando},
    ]

    ofrecidas = herramientas_para(comando)
    _avisar_si_no_cabe(mensajes, ofrecidas, perfil)
    insistido = False
    ultimo_resultado = ""

    for paso in range(MAX_PASOS_TOOLS):
        try:
            respuesta = ollama.chat(
                model=perfil["modelo"],
                messages=mensajes,
                tools=ofrecidas,
                keep_alive=perfil["keep_alive"],
                options=modes.opciones_ollama(),
            )
        except Exception as e:
            log.exception("Error hablando con Ollama")
            texto = str(e).lower()
            if "not found" in texto or "no such model" in texto:
                return (
                    f"El modelo {perfil['modelo']} no está descargado. "
                    f"Ejecuta ollama pull {perfil['modelo']} en el equipo."
                )
            if "connection" in texto or "refused" in texto:
                return "No pude conectar con Ollama. Revisa que el servicio esté corriendo."
            return f"Tuve un error con el modelo: {e}"

        mensaje = respuesta.get("message", {}) or {}
        llamadas = mensaje.get("tool_calls") or []

        if not llamadas:
            contenido = (mensaje.get("content") or "").strip()

            # Un modelo pequeño a veces explica que herramienta usaria, o
            # pregunta si la usa, en vez de llamarla. Se le insiste una vez.
            if not insistido and describe_en_vez_de_actuar(contenido, ofrecidas):
                insistido = True
                log.warning("El modelo describió en vez de actuar: %r. Insisto una vez.",
                            contenido[:120])
                mensajes.append({"role": "assistant", "content": contenido})
                mensajes.append({"role": "user", "content": INSISTIR})
                continue

            if insistido and describe_en_vez_de_actuar(contenido, ofrecidas):
                log.warning("Tras insistir, el modelo sigue describiendo: %r", contenido[:120])
                return ("No me salió esa. Dímela más directa, o cambia a modo "
                        "dedicado, que razona mejor.")

            # "Listo, hecha" despues de mirar las ventanas no dice nada: se
            # lee lo que devolvio la herramienta, que ya viene para la voz.
            if ultimo_resultado and es_relleno(contenido):
                log.info("Respuesta de relleno %r: leo el resultado de la herramienta.", contenido)
                return ultimo_resultado

            log.info("Ollama respondió sin herramientas (paso %d)", paso)
            return limpiar_respuesta_modelo(contenido)

        mensajes.append(mensaje)

        for llamada in llamadas:
            funcion = llamada.get("function", {}) or {}
            nombre = funcion.get("name", "")
            argumentos = funcion.get("arguments", {}) or {}

            resultado = _ejecutar_herramienta(nombre, argumentos)
            if not str(resultado).startswith(("No ejecuto", "No existe")):
                ultimo_resultado = str(resultado)

            mensajes.append({
                "role": "tool",
                "content": str(resultado),
                "name": nombre,
            })

    return "Di demasiadas vueltas con esa orden y preferí parar."


# Caracteres por token en español, tirando a la baja para no quedarse corto.
CARACTERES_POR_TOKEN = 3.5


def tokens_estimados(mensajes: list[dict], ofrecidas: list[dict]) -> int:
    texto = sum(len(str(m.get("content", ""))) for m in mensajes)
    esquemas = len(json.dumps(ofrecidas, ensure_ascii=False))
    return int((texto + esquemas) / CARACTERES_POR_TOKEN)


def _avisar_si_no_cabe(mensajes: list[dict], ofrecidas: list[dict], perfil: dict) -> None:
    """Si el prompt no cabe, Ollama recorta el principio sin decir nada."""
    estimados = tokens_estimados(mensajes, ofrecidas)
    limite = int(perfil.get("num_ctx", 4096))
    if estimados > limite * 0.85:
        log.warning("El prompt ocupa ~%d tokens y el contexto es de %d: el modelo "
                    "perderá el principio (las reglas). Sube num_ctx o quita herramientas.",
                    estimados, limite)


_RELLENO = re.compile(
    r"^\W*(?:listo|hecho|hecha|ya\s+est[aá]|ah[ií]\s+(?:lo|la)\s+tienes|perfecto|"
    r"de\s+acuerdo|ok|okay|vale|claro|entendido|completado|terminado)\b[\W\w]{0,20}$",
    re.IGNORECASE,
)


def es_relleno(texto: str) -> bool:
    """Una respuesta que no cuenta nada: "Listo, hecha.", "Ya está"."""
    t = (texto or "").strip()
    return len(t) <= 30 and bool(_RELLENO.match(t))


INSISTIR = ("No me expliques qué función usar ni me preguntes si la uso: llama tú "
            "ahora mismo a la herramienta adecuada con el mecanismo de herramientas. "
            "Si de verdad te falta un dato, pregunta solo ese dato.")

_SENALES_DE_DESCRIBIR = re.compile(
    r"\b(?:usar|utilizar|llamar a|ejecutar|invocar)\s+(?:la\s+)?(?:funci[oó]n|herramienta)\b"
    r"|\bcon el siguiente comando\b"
    r"|\bquieres que (?:busque|lo busque|abra|revise|mire|lo haga|use|lea)\b",
    re.IGNORECASE,
)


def describe_en_vez_de_actuar(contenido: str, ofrecidas: list[dict]) -> bool:
    """¿El modelo habla de una herramienta en vez de llamarla?"""
    texto = contenido or ""
    if not texto.strip():
        return False
    nombres = {e.get("function", {}).get("name", "") for e in ofrecidas}
    if any(n and n in texto for n in nombres):
        return True
    return bool(_SENALES_DE_DESCRIBIR.search(texto))


_inicio_peticion = threading.local()


def empezar_presupuesto() -> None:
    """Marca el instante en que entro la peticion de Alexa."""
    _inicio_peticion.valor = time.perf_counter()


def restante_del_presupuesto() -> float:
    """Segundos que quedan antes de que Alexa se rinda."""
    inicio = getattr(_inicio_peticion, "valor", None)
    if inicio is None:
        return PRESUPUESTO_SEGUNDOS
    return max(0.0, PRESUPUESTO_SEGUNDOS - (time.perf_counter() - inicio))


def _terminar_en_segundo_plano(identificador: str, resumen: str, comando: str) -> None:
    """Ejecuta y guarda el resultado sin que nadie espere."""
    try:
        valor = _conversar(comando)
    except Exception as e:
        valor = f"La tarea falló: {e}"
    tareas.registrar_resultado(identificador, resumen, valor)


def procesar(comando: str) -> str:
    """Procesa un comando con el LLM respetando el límite de tiempo de Alexa."""
    identificador = uuid.uuid4().hex[:8]
    resumen = comando[:60]

    restante = restante_del_presupuesto()

    if restante < 1.0:
        log.info("Quedan %.1f s: ni lo intento, lo mando al fondo.", restante)
        tareas.registrar_inicio(identificador, resumen)
        _ejecutor.submit(_terminar_en_segundo_plano, identificador, resumen, comando)
        return ("Eso lleva su tiempo, lo dejo trabajando. "
                "Pregúntame cómo quedó lo último cuando quieras.")

    tareas.registrar_inicio(identificador, resumen)
    futuro = _ejecutor.submit(_conversar, comando)

    try:
        resultado = futuro.result(timeout=restante)
        tareas.registrar_resultado(identificador, resumen, resultado)
        return resultado

    except TimeoutFuturo:
        log.info("Presupuesto agotado (%.1fs). La tarea sigue en segundo plano.", PRESUPUESTO_SEGUNDOS)

        # La tarea NO se cancela: sigue corriendo y guarda su resultado al terminar.
        def _al_terminar():
            try:
                valor = futuro.result(timeout=300)
            except Exception as e:
                valor = f"La tarea falló: {e}"
            tareas.registrar_resultado(identificador, resumen, valor)

        threading.Thread(target=_al_terminar, daemon=True, name=f"seguim-{identificador}").start()

        perfil = modes.perfil_actual()
        if perfil["num_gpu"] == 0:
            pista = " Estoy en modo gaming, así que voy en procesador y tardo más."
        else:
            pista = ""

        return f"Estoy trabajando en eso.{pista} Pregúntame cómo quedó en un momento."

    except Exception as e:
        log.exception("Error inesperado procesando el comando")
        tareas.registrar_resultado(identificador, resumen, str(e))
        return f"Tuve un problema procesando esa orden: {e}"


def esta_disponible() -> bool:
    """Comprueba si Ollama responde. Se usa en el endpoint de salud."""
    try:
        import ollama

        ollama.list()
        return True
    except Exception:
        return False


def _nombre_de_modelo(modelo) -> str:
    """El nombre de un modelo venga como venga envuelto."""
    if isinstance(modelo, dict):
        return str(modelo.get("model") or modelo.get("name") or "")
    return str(getattr(modelo, "model", "") or getattr(modelo, "name", "") or "")


def modelos_instalados() -> list[str]:
    """Lo que tienes descargado en Ollama."""
    try:
        import ollama
        datos = ollama.list()
    except Exception as e:
        log.debug("No pude listar los modelos de Ollama: %s", e)
        return []

    crudos = datos.get("models") if isinstance(datos, dict) else None
    if crudos is None:
        crudos = getattr(datos, "models", None)

    if not crudos:
        log.debug("Ollama respondio sin modelos (tipo %s)", type(datos).__name__)
        return []

    return [n for n in (_nombre_de_modelo(m) for m in crudos) if n]
