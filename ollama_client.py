"""
Cliente de Ollama con function calling y presupuesto de tiempo.

Aquí caen las órdenes que el router determinista (nlu.py) no supo resolver:
razonar, redactar, decidir entre varias herramientas, encadenar pasos.

El problema del tiempo
----------------------
Alexa corta a los ~8 s. El modelo de 7B puede tardar más. La solución no es
rendirse, sino separar la RESPUESTA de la EJECUCIÓN:

  - Si el modelo termina dentro del presupuesto, Alexa dice el resultado real.
  - Si no, Alexa dice "lo estoy procesando" y la tarea SIGUE corriendo en un
    hilo. El resultado queda en tareas.py y se consulta con "¿cómo quedó?".

Así ninguna orden se pierde por un timeout.
"""

import inspect
import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as TimeoutFuturo

import modes
import tareas
from config import MAX_PASOS_TOOLS, PRESUPUESTO_SEGUNDOS
from tools import archivos, avanzado, entrada, navegador, obsidian, sistema

log = logging.getLogger("jarvis.ollama")

_ejecutor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="jarvis-llm")



# =========================================================================
# SANEADO DE LA RESPUESTA DEL MODELO
# =========================================================================
# Los modelos pequenos fallan de una forma concreta y muy fea: en vez de
# INVOCAR la herramienta, escriben la llamada como texto y la devuelven.
# Alexa entonces lee en voz alta algo como:
#
#   {"name":"eliminar_archivo","parameters":{"nombre_archivo":"captura1.png"}}
#
# Detectamos ese caso, ejecutamos de verdad lo que el modelo queria hacer, y
# devolvemos una frase humana. Si no se puede rescatar, al menos no leemos
# JSON en voz alta.

_PATRON_LLAMADA_TEXTO = re.compile(
    r'\{\s*"(?:name|function|tool)"\s*:\s*"(?P<nombre>\w+)"\s*,\s*'
    r'"(?:parameters|arguments|params)"\s*:\s*(?P<args>\{.*?\})\s*\}',
    re.DOTALL,
)


def _rescatar_llamadas_en_texto(texto: str) -> str | None:
    """
    Si el modelo escribio llamadas a herramientas como texto plano, las ejecuta.

    Devuelve la frase resultante, o None si el texto no contenia ninguna.
    """
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


# Los modelos pequenos copian trozos del prompt en su respuesta. Suena
# ridiculo por el altavoz: "Alexa lee en voz alta: comilla El procesador...".
# Los quitamos aqui en vez de confiar en que el modelo obedezca la regla 9.
_PREFIJOS_PARASITOS = re.compile(
    r'^\s*(?:alexa\s+(?:lee|leera|dice|dira)\s+en\s+voz\s+alta|'
    r'respuesta\s+final|respuesta|jarvis\s+responde|tu\s+respuesta|'
    r'respondo|dir[ií]a|salida)\s*[:\-]\s*',
    re.IGNORECASE,
)


# El modelo a veces escupe el nombre de la herramienta antes del resultado:
#   "listar_archivos \nEn Desktop hay 108 archivos."
# Por el altavoz suena a fallo ("listar guion bajo archivos"). No es JSON ni
# una llamada rescatable: ya se ejecuto, esto es solo ruido delante.
_NOMBRE_SUELTO = re.compile(
    r'^\s*(?:llamada\s*[:\-]?\s*)?'
    r'(?P<nombre>[a-z][a-z0-9]*(?:_[a-z0-9]+)+)'      # tiene que llevar guion bajo
    r'\s*(?:\([^)]*\))?\s*[:\-]?\s*(?:\n|\s)+',
    re.IGNORECASE,
)


def _quitar_nombre_de_herramienta(texto: str) -> str:
    """
    Si la respuesta empieza por el nombre de una herramienta y despues hay
    texto de verdad, se queda solo con el texto.

    Comprobamos contra DESPACHADOR y no contra cualquier palabra con guion
    bajo: asi una frase que empiece por un nombre de archivo real
    (`notas_clase.md esta en Documentos`) no se queda mutilada.
    """
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
    if len(limpio) > 1 and limpio[0] in '"\u201c\u00ab' and limpio[-1] in '"\u201d\u00bb':
        limpio = limpio[1:-1].strip()

    return limpio


def limpiar_respuesta_modelo(texto: str) -> str:
    """
    Deja la respuesta del modelo lista para pronunciarse.

    Primero intenta rescatar llamadas escritas como texto; si lo que queda
    sigue pareciendo estructura de datos, no lo lee: avisa.
    """
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

    return texto or "No supe qué hacer con esa orden."


# =========================================================================
# ESQUEMAS DE HERRAMIENTAS
# =========================================================================
def _herramienta(nombre: str, descripcion: str, propiedades: dict, requeridos: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": nombre,
            "description": descripcion,
            "parameters": {
                "type": "object",
                "properties": propiedades,
                "required": requeridos,
            },
        },
    }


def _texto(descripcion: str) -> dict:
    return {"type": "string", "description": descripcion}


HERRAMIENTAS = [
    _herramienta(
        "crear_archivo",
        "Crea un archivo nuevo en el equipo. Soporta .py, .txt, .md, .json, .csv, .docx y .xlsx.",
        {
            "nombre_archivo": _texto("Nombre con extensión, por ejemplo script.py o informe.docx"),
            "contenido": _texto("Contenido completo que va dentro del archivo"),
            "carpeta": _texto("Carpeta destino: escritorio, descargas o documentos. Por defecto escritorio."),
        },
        ["nombre_archivo"],
    ),
    _herramienta(
        "leer_archivo",
        "Lee el contenido de un archivo de texto existente.",
        {"nombre_archivo": _texto("Nombre del archivo con su extensión")},
        ["nombre_archivo"],
    ),
    _herramienta(
        "editar_archivo",
        "Edita un archivo de texto existente: agregar al final, anteponer, reemplazar texto o sobrescribir.",
        {
            "nombre_archivo": _texto("Nombre del archivo con su extensión"),
            "accion": _texto("Una de: agregar, anteponer, reemplazar, sobrescribir"),
            "contenido": _texto("Texto a agregar o con el que sobrescribir"),
            "buscar": _texto("Solo para accion=reemplazar: el texto que hay que encontrar"),
            "reemplazar": _texto("Solo para accion=reemplazar: el texto nuevo"),
        },
        ["nombre_archivo", "accion"],
    ),
    _herramienta(
        "mover_archivo",
        "Mueve un archivo a otra carpeta.",
        {
            "origen": _texto("Nombre del archivo a mover"),
            "destino": _texto("Carpeta destino: escritorio, descargas, documentos o el nombre de una subcarpeta"),
        },
        ["origen", "destino"],
    ),
    _herramienta(
        "buscar_archivo",
        "Busca archivos por nombre en el escritorio, descargas y documentos.",
        {"patron": _texto("Parte del nombre del archivo a buscar")},
        ["patron"],
    ),
    _herramienta(
        "listar_archivos",
        "Lista los archivos de una carpeta.",
        {"carpeta": _texto("escritorio, descargas o documentos")},
        [],
    ),
    _herramienta(
        "abrir_aplicacion",
        "Abre un programa instalado en Windows.",
        {"nombre_app": _texto("Nombre del programa: spotify, chrome, comet, steam, calculadora, vs code...")},
        ["nombre_app"],
    ),
    _herramienta(
        "cerrar_aplicacion",
        "Cierra un programa que esté en ejecución.",
        {"nombre_app": _texto("Nombre del programa a cerrar")},
        ["nombre_app"],
    ),
    _herramienta(
        "buscar_en_navegador",
        "Hace una búsqueda web en el navegador Comet.",
        {"consulta": _texto("Lo que hay que buscar")},
        ["consulta"],
    ),
    _herramienta(
        "abrir_sitio",
        "Abre una página web concreta en Comet.",
        {"nombre": _texto("Nombre conocido (youtube, gmail, github) o dominio (ejemplo.com)")},
        ["nombre"],
    ),
    _herramienta(
        "escribir_texto",
        "Escribe texto con el teclado en la ventana que esté activa en ese momento.",
        {"texto": _texto("Texto exacto a escribir")},
        ["texto"],
    ),
    _herramienta(
        "ejecutar_atajo",
        "Ejecuta un atajo de teclado permitido.",
        {"nombre_atajo": _texto("copiar, pegar, guardar, minimizar todo, cambiar ventana, subir volumen...")},
        ["nombre_atajo"],
    ),
    _herramienta(
        "captura_pantalla",
        "Toma una captura de pantalla y la guarda en el escritorio.",
        {"nombre": _texto("Nombre opcional del archivo PNG")},
        [],
    ),
    _herramienta(
        "estado_sistema",
        "Devuelve el estado de CPU, memoria y tarjeta gráfica.",
        {},
        [],
    ),
    _herramienta(
        "cambiar_modo",
        "Cambia el modo de Jarvis: normal (ligero), dedicado (modelo grande en GPU) o gaming (libera la gráfica).",
        {"modo": _texto("normal, dedicado o gaming")},
        ["modo"],
    ),

    # ---- Archivos: lo que faltaba ----
    _herramienta(
        "eliminar_archivo",
        "Manda un archivo a la papelera de Jarvis. No borra de forma definitiva: se puede recuperar.",
        {"nombre_archivo": _texto("Nombre del archivo con su extensión")},
        ["nombre_archivo"],
    ),
    _herramienta(
        "eliminar_varios",
        "Manda a la papelera todos los archivos de una carpeta cuyo nombre contenga un patrón.",
        {
            "patron": _texto("Parte del nombre, por ejemplo 'captura' para borrar todas las capturas"),
            "carpeta": _texto("escritorio, descargas o documentos"),
        },
        ["patron"],
    ),
    _herramienta(
        "copiar_archivo",
        "Copia un archivo a otra carpeta dejando el original.",
        {"origen": _texto("Archivo a copiar"), "destino": _texto("Carpeta destino")},
        ["origen", "destino"],
    ),
    _herramienta(
        "crear_carpeta",
        "Crea una carpeta nueva.",
        {"nombre": _texto("Nombre de la carpeta"), "carpeta": _texto("Dónde crearla")},
        ["nombre"],
    ),

    # ---- Busqueda profunda ----
    _herramienta(
        "buscar_en_contenido",
        "Busca una frase DENTRO de los archivos, no solo en sus nombres. Úsalo cuando el usuario no recuerde dónde guardó algo.",
        {
            "texto": _texto("La frase o palabra a buscar dentro de los archivos"),
            "carpeta": _texto("Opcional: limitar a escritorio, descargas o documentos"),
        },
        ["texto"],
    ),
    _herramienta(
        "explorar_carpeta",
        "Recorre una carpeta y sus subcarpetas y resume qué hay: cuántos archivos, de qué tipo y qué subcarpetas.",
        {"carpeta": _texto("escritorio, descargas o documentos")},
        [],
    ),
    _herramienta(
        "archivos_recientes",
        "Lista lo que se ha modificado últimamente. Útil para '¿en qué estaba trabajando?'.",
        {"dias": {"type": "integer", "description": "Cuántos días atrás mirar. Por defecto 7."}},
        [],
    ),

    # ---- Estado del equipo ----
    _herramienta(
        "informe_completo",
        "Informe detallado del equipo: CPU, memoria, discos por unidad, gráfica, red y procesos. Con guardar=true lo escribe como archivo.",
        {"guardar": {"type": "boolean", "description": "true para guardarlo como archivo markdown"}},
        [],
    ),
    _herramienta(
        "info_equipo",
        "Describe este equipo: sistema, procesador, memoria, gráfica y tiempo encendido.",
        {},
        [],
    ),

    # ---- Obsidian ----
    _herramienta(
        "obsidian_crear_nota",
        "Crea una nota nueva en el vault de Obsidian del usuario, con frontmatter y etiquetas.",
        {
            "titulo": _texto("Título de la nota"),
            "contenido": _texto("Cuerpo de la nota en markdown"),
            "etiquetas": _texto("Etiquetas separadas por comas, sin almohadilla"),
            "carpeta": _texto("Subcarpeta del vault, opcional"),
        },
        ["titulo"],
    ),
    _herramienta(
        "obsidian_agregar_a_nota",
        "Añade texto al final de una nota existente de Obsidian. Si no existe, la crea.",
        {"titulo": _texto("Título de la nota"), "contenido": _texto("Texto a añadir")},
        ["titulo", "contenido"],
    ),
    _herramienta(
        "obsidian_agregar_al_diario",
        "Añade una línea a la nota diaria de hoy en Obsidian. Úsalo para apuntes rápidos e ideas.",
        {"contenido": _texto("Lo que hay que apuntar")},
        ["contenido"],
    ),
    _herramienta(
        "obsidian_buscar",
        "Busca una frase dentro de todas las notas de Obsidian.",
        {"texto": _texto("Qué buscar en las notas")},
        ["texto"],
    ),

    # ---- Contexto personal ----
    _herramienta(
        "recordar_en_contexto",
        "Apunta un dato permanente sobre el usuario o su forma de trabajar en el contexto de Jarvis, para tenerlo en cuenta siempre.",
        {"contenido": _texto("El dato a recordar")},
        ["contenido"],
    ),
]



# Mapa nombre-de-herramienta -> función real.
DESPACHADOR = {
    "crear_archivo": archivos.crear_archivo,
    "leer_archivo": archivos.leer_archivo,
    "editar_archivo": archivos.editar_archivo,
    "mover_archivo": archivos.mover_archivo,
    "buscar_archivo": archivos.buscar_archivo,
    "listar_archivos": archivos.listar_archivos,
    "abrir_aplicacion": sistema.abrir_aplicacion,
    "cerrar_aplicacion": sistema.cerrar_aplicacion,
    "buscar_en_navegador": navegador.buscar_en_navegador,
    "abrir_sitio": navegador.abrir_sitio,
    "escribir_texto": entrada.escribir_texto,
    "ejecutar_atajo": entrada.ejecutar_atajo,
    "captura_pantalla": entrada.captura_pantalla,
    "estado_sistema": lambda: sistema.estado_general(),
    "cambiar_modo": lambda modo: modes.cambiar_modo(modo),

    "eliminar_archivo": archivos.eliminar_archivo,
    "eliminar_varios": archivos.eliminar_varios,
    "copiar_archivo": archivos.copiar_archivo,
    "crear_carpeta": archivos.crear_carpeta,

    "buscar_en_contenido": avanzado.buscar_en_contenido,
    "explorar_carpeta": avanzado.explorar_carpeta,
    "archivos_recientes": avanzado.archivos_recientes,
    "informe_completo": avanzado.informe_completo,
    "info_equipo": avanzado.info_equipo,

    "obsidian_crear_nota": obsidian.crear_nota,
    "obsidian_agregar_a_nota": obsidian.agregar_a_nota,
    "obsidian_agregar_al_diario": obsidian.agregar_al_diario,
    "obsidian_buscar": obsidian.buscar_en_vault,

    "recordar_en_contexto": avanzado.editar_contexto,
}


PROMPT_BASE = """Eres Jarvis, el asistente local que controla este equipo con Windows.

REGLAS:
1. Si la orden implica una accion en el equipo (crear, leer, editar, mover o borrar archivos, abrir o cerrar programas, buscar, escribir con el teclado, apuntar en Obsidian), invoca SIEMPRE la herramienta correspondiente. No describas lo que harias: hazlo.
2. NUNCA escribas la llamada a la herramienta como texto. Usa el mecanismo de herramientas. Si escribes JSON en tu respuesta, el usuario lo oira leido en voz alta y sera inutil.
3. Al crear codigo, escribe codigo REAL, completo y funcional, con comentarios en espanol que expliquen el porque. Nada de esqueletos ni de "aqui iria la logica".
4. Tu respuesta final la lee Alexa en voz alta: maximo dos frases cortas en espanol, sin markdown, sin listas, sin emojis y sin leer codigo.
5. Nunca inventes rutas, nombres de archivo ni datos. Si necesitas saber que hay, usa listar_archivos, explorar_carpeta o buscar_en_contenido antes de responder.
6. Si una herramienta falla, dilo con claridad. No finjas que funciono.
7. Las preguntas de conocimiento general ("que es la oferta y la demanda", "explicame X", "cuanto es 15 por 4") se contestan DIRECTAMENTE con lo que sabes. No busques en Obsidian ni en el disco: las notas del usuario son SUS apuntes, no una enciclopedia. Solo usa obsidian_buscar si la orden menciona sus notas, su vault, su diario o algo que el escribio.
8. Si la orden es demasiado corta o ambigua para saber que quiere ("no", "si", "vale", "ok", "eso"), NO llames a ninguna herramienta: pregunta que necesita. Cambiar el modo del equipo porque el usuario dijo "no" es un error grave.
9. Responde solo con lo que hay que decir. No narres lo que vas a hacer, no repitas la orden y no escribas prefijos como "Alexa lee en voz alta:" ni "Respuesta:". Esas palabras acabarian sonando por el altavoz.

COMO HABLAS:
Hablas con Kaled, que es quien te construyo. Tuteale.

Suena a persona, no a manual. Un asistente que contesta "Operacion completada satisfactoriamente" a cada cosa cansa en dos dias. Di "listo", "hecho", "ya esta", "ahi lo tienes", y varia: repetir siempre la misma formula suena tan robotico como la formula mas formal.

Tienes sentido del humor, seco y breve. Un comentario cuando viene a cuento, NUNCA un chiste forzado ni un chiste que retrase la respuesta util. Si te pide algo a las tres de la manana puedes decirselo de pasada; si algo va mal, puedes reconocerlo sin dramatismo ("esa se me escapo"). El humor va DESPUES de la informacion, nunca en lugar de ella.

Si te habla y no te pide nada (te saluda, comenta algo, se queja del dia), contestale como contestaria una persona: breve y al grano, sin convertirlo en una orden ni preguntarle en que puedes ayudarle. No todo lo que se dice en voz alta es una peticion.

Cuando algo te salga mal, dilo claro y sin excusas largas. "No pude, el archivo no existe" vale mas que tres frases de disculpa.

Nada de emojis ni de simbolos: esto se lee en voz alta."""


def construir_prompt_sistema() -> str:
    """
    Monta el prompt con el contexto real del usuario y del equipo.

    Un modelo de 3B no sabe nada de quien le habla. Sin esto responde sobre un
    usuario imaginario y con rutas inventadas. Con esto sabe donde esta, que
    hardware tiene y como quiere trabajar la persona.
    """
    partes = [PROMPT_BASE]

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
    except Exception:
        pass

    return "\n".join(partes)


# Se mantiene el nombre antiguo por compatibilidad con codigo que lo importe.
SYSTEM_PROMPT = PROMPT_BASE



# =========================================================================
# EJECUCIÓN DE HERRAMIENTAS
# =========================================================================
# Claves con las que los modelos pequenos envuelven los argumentos. En vez de
# mandar {"texto": "oferta y demanda"} devuelven el sobre entero:
#   {"type": "function", "function": "obsidian_buscar",
#    "parameters": {"texto": "oferta y demanda"}}
# ...y la llamada reventaba con "unexpected keyword argument 'type'".
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


# =========================================================================
# EL FRENO DE MANO
# =========================================================================
# Un modelo de 3B, cuando no entiende, no se calla: llama a la herramienta
# que le suene. Del registro real, hablando yo solo sin darle ninguna orden:
#
#   "estoy en la cama y no tengo ganas de levantarme..."  -> escribio texto
#                                                            en la ventana
#                                                            que hubiera al
#                                                            frente
#   "que no le puedo hablar aca porque no me ha..."       -> cerrar Alexa
#   (varias)                                              -> cerrar 'nada',
#                                                            cerrar
#                                                            'estado_sistema',
#                                                            borrar 'prueba'
#
# Contestar mal se nota y se repite. Escribir en una ventana que no mirabas o
# cerrar un programa con trabajo sin guardar, no: te enteras tarde. Asi que
# las herramientas que TOCAN el equipo llevan freno, y las que solo miran
# (leer, listar, estado) pasan sin nada: equivocarse ahi no cuesta nada.
HERRAMIENTAS_QUE_TOCAN = {
    "escribir_texto":    ("escrib", "teclea", "redacta", "pon ", "ponme",
                          "dile", "manda", "mensaje", "responde", "contesta"),
    "ejecutar_atajo":    ("atajo", "pulsa", "presiona", "tecla", "copia",
                          "pega", "guarda", "minimiza", "maximiza", "volumen"),
    "cerrar_aplicacion": ("cierra", "cerrar", "cierre", "quita", "mata",
                          "apaga", "termina", "sal de"),
    "eliminar_archivo":  ("elimina", "borra", "quita", "eliminar", "borrar"),
    "eliminar_varios":   ("elimina", "borra", "quita", "eliminar", "borrar"),
    "mover_archivo":     ("mueve", "mover", "mueva", "pasa", "lleva", "manda a"),
    "copiar_archivo":    ("copia", "copiar", "duplica"),
    "editar_archivo":    ("edita", "agrega", "añade", "reemplaza", "cambia",
                          "escrib", "modifica"),
    "crear_archivo":     ("crea", "crear", "cree", "hazme", "haz un", "nuevo"),
    "crear_carpeta":     ("crea", "crear", "cree", "carpeta", "nueva"),
    "cambiar_modo":      ("modo", "gaming", "dedicado", "normal", "juego"),
}

# Longitud a partir de la cual una frase deja de parecer una orden. Las
# ordenes de verdad son cortas y empiezan por el verbo: "cierra spotify",
# "escribe hola", "elimina prueba punto txt". Las dos frases que dispararon
# acciones sin querer tenian 73 y 139 caracteres.
LARGO_MAXIMO_DE_UNA_ORDEN = 70

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
    """
    Motivo por el que NO se ejecuta esta llamada, o cadena vacia si pasa.

    Devuelve texto y no un booleano a proposito: ese texto vuelve al modelo
    como resultado de la herramienta, asi que en vez de reintentar lo mismo
    lee por que se le paro y suele contestar con palabras.
    """
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
                "Contesta con palabras a lo que te esta contando.")

    # 5. Demasiado larga para ser una orden. Aqui es donde se cortan las dos
    #    del registro: la frase lleva la palabra suelta ("pon", "digo") pero
    #    dentro de un parrafo que era conversacion, no una orden.
    if len(frase) > LARGO_MAXIMO_DE_UNA_ORDEN:
        return (f"No ejecuto {nombre}: eso parece conversacion, no una orden. "
                "Si de verdad la quiere, que la diga corta y directa.")

    return ""


def _ejecutar_herramienta(nombre: str, argumentos: dict) -> str:
    funcion = DESPACHADOR.get(nombre)
    if funcion is None:
        return f"No existe la herramienta {nombre}."

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

    # Segunda red: descartamos las claves que la funcion no acepta. Un modelo
    # de 3B se inventa parametros con facilidad, y perder una llamada entera
    # por un campo de mas es un desperdicio: mejor ejecutar con lo que sirve.
    try:
        validas = set(inspect.signature(funcion).parameters)
        sobran = [k for k in argumentos if k not in validas]
        if sobran:
            log.info("Descarto argumentos que %s no acepta: %s", nombre, sobran)
            argumentos = {k: v for k, v in argumentos.items() if k in validas}
    except (TypeError, ValueError):
        pass

    try:
        return funcion(**argumentos)
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

    # El freno necesita la frase tal cual la dijo, para poder contrastar lo
    # que el modelo quiere ejecutar con lo que de verdad se pidio. Se guarda
    # aqui y no en el servidor porque esto ya corre en el hilo del trabajo, y
    # el almacen es por hilo.
    recordar_peticion(comando)

    perfil = modes.perfil_actual()
    mensajes = [
        {"role": "system", "content": construir_prompt_sistema()},
        {"role": "user", "content": comando},
    ]

    for paso in range(MAX_PASOS_TOOLS):
        try:
            respuesta = ollama.chat(
                model=perfil["modelo"],
                messages=mensajes,
                tools=HERRAMIENTAS,
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
            log.info("Ollama respondió sin herramientas (paso %d)", paso)
            return limpiar_respuesta_modelo(contenido)

        mensajes.append(mensaje)

        for llamada in llamadas:
            funcion = llamada.get("function", {}) or {}
            nombre = funcion.get("name", "")
            argumentos = funcion.get("arguments", {}) or {}

            resultado = _ejecutar_herramienta(nombre, argumentos)

            mensajes.append({
                "role": "tool",
                "content": str(resultado),
                "name": nombre,
            })

    return "Di demasiadas vueltas con esa orden y preferí parar."


# =========================================================================
# ENTRADA PÚBLICA CON PRESUPUESTO
# =========================================================================
# Reloj del presupuesto de la peticion en curso.
#
# Alexa concede unos 8 segundos DESDE QUE MANDA LA PETICION, no desde que
# llamamos al modelo. Todo lo que consuma el router antes hay que restarlo.
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
    """
    Procesa un comando con el LLM respetando el límite de tiempo de Alexa.

    Si el modelo no termina a tiempo, devuelve un acuse y deja la tarea
    corriendo en segundo plano.
    """
    identificador = uuid.uuid4().hex[:8]
    resumen = comando[:60]

    # El presupuesto es de la PETICION entera, no de esta llamada. Si el router
    # ya gasto 400 ms probando patrones, al modelo le quedan 6.1 s, no 6.5.
    # Usar el numero fijo aqui era la forma silenciosa de pasarse del limite de
    # Alexa justo en las ordenes mas lentas, que son las que ya iban apuradas.
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
    """
    Lo que tienes descargado en Ollama.

    Acepta las dos formas de respuesta a proposito. La biblioteca de Ollama
    devolvia un diccionario y ahora devuelve un objeto con atributos; el
    codigo viejo solo entendia el diccionario y, con la version nueva, se
    quedaba en una lista vacia SIN dar error. Y una lista vacia aqui no se
    lee como "no pude preguntar", se lee como "no tienes nada instalado":
    por eso el arranque insistia en que faltaba nomic-embed-text con el
    modelo ya descargado.
    """
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
        # Llego respuesta pero sin modelos dentro. O de verdad no hay
        # ninguno, o la forma cambio otra vez; dejamos rastro para no
        # volver a perseguir esto a ciegas.
        log.debug("Ollama respondio sin modelos (tipo %s)", type(datos).__name__)
        return []

    return [n for n in (_nombre_de_modelo(m) for m in crudos) if n]
