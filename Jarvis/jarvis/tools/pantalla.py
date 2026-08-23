"""
Ver la pantalla y actuar sobre ella.

Dos caminos, y la eleccion no es de gusto: es de reloj
------------------------------------------------------
Alexa corta a los ocho segundos.

1. OCR (Tesseract). Lee el TEXTO de la pantalla en menos de un segundo, y
   sabe en que coordenadas esta cada palabra, asi que ademas puede hacer clic
   sobre lo que le digas. Cabe de sobra en el plazo. No entiende iconos ni
   imagenes: solo texto.

2. Modelo de vision (llava en Ollama). Describe la pantalla entera, iconos
   incluidos, pero en una RTX 3050 de 6 GB tarda entre diez y treinta
   segundos. NO cabe. Por eso siempre va en segundo plano y el resultado se
   recoge despues con "como quedo lo ultimo".

Por eso las ordenes rapidas ("que pone en la pantalla", "haz clic en Guardar")
usan OCR, y solo "describe la pantalla" pasa por el modelo.
"""

import base64
import difflib
import io
import logging
import re
import time

from config import IDIOMA_OCR, MODELO_VISION, TESSERACT_EXE

log = logging.getLogger("jarvis.pantalla")

# Palabras que el OCR escupe y que no aportan nada al escucharlas.
_BASURA = re.compile(r"^[\W_]+$")


def _captura(zona: tuple[float, float, float, float] | None = None):
    """
    Imagen de la pantalla principal, o None.

    `zona` recorta en proporciones (izquierda, arriba, derecha, abajo) de 0 a 1.
    Sirve para dejar fuera lo que estorba: en Teams, por ejemplo, la barra
    lateral y la de titulo aportan solo nombres de menu sueltos que luego se
    mezclan con los mensajes y hacen que la lectura suene a galimatias.
    """
    try:
        import mss
        from PIL import Image
    except ImportError:
        log.warning("Faltan mss o pillow. Ejecuta scripts/instalar_extras.ps1")
        return None

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1]        # 1 = pantalla principal
            crudo = sct.grab(monitor)
        imagen = Image.frombytes("RGB", crudo.size, crudo.bgra, "raw", "BGRX")
    except Exception as e:
        log.warning("No pude capturar la pantalla: %s", e)
        return None

    if zona:
        ancho, alto = imagen.size
        izq, arr, der, aba = zona
        imagen = imagen.crop((int(ancho * izq), int(alto * arr),
                              int(ancho * der), int(alto * aba)))
    return imagen


# Idioma que se acaba usando de verdad. Se resuelve una vez y se recuerda.
_idioma_real: str | None = None


def _tesseract():
    try:
        import pytesseract
    except ImportError:
        return None
    if TESSERACT_EXE:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE
    return pytesseract


def _idioma(pytesseract) -> str:
    """
    Devuelve el idioma que Tesseract puede usar de verdad.

    Existe porque winget instala Tesseract SOLO con el ingles: el instalador
    grafico deja marcar idiomas, pero winget lo lanza en silencio con las
    opciones por defecto y esa pantalla nunca aparece. Pedirle entonces
    "spa+eng" no degrada la calidad: falla entero con un error de que no
    encuentra el idioma, y "leer la pantalla" deja de funcionar sin que se
    entienda por que.

    Asi que preguntamos que tiene y nos quedamos con lo que haya. Con el
    ingles se leen bien las palabras; solo las tildes salen regular.
    """
    global _idioma_real
    if _idioma_real is not None:
        return _idioma_real

    try:
        disponibles = set(pytesseract.get_languages(config=""))
    except Exception as e:
        log.debug("No pude preguntar los idiomas a Tesseract: %s", e)
        _idioma_real = IDIOMA_OCR
        return _idioma_real

    pedidos = [i for i in IDIOMA_OCR.split("+") if i]
    validos = [i for i in pedidos if i in disponibles]

    if not validos:
        # Ni uno de los pedidos existe: tiramos con lo primero que haya.
        validos = [next(iter(sorted(disponibles - {"osd"})), "eng")]

    faltan = [i for i in pedidos if i not in disponibles]
    if faltan:
        log.warning(
            "A Tesseract le faltan estos idiomas: %s. Uso %s. "
            "Instala el español con scripts/idioma_ocr.ps1 para que las "
            "tildes se lean bien.",
            ", ".join(faltan), "+".join(validos),
        )

    _idioma_real = "+".join(validos)
    return _idioma_real


def _es_basura(palabra: str) -> bool:
    """
    Descarta lo que el OCR se inventa.

    En una interfaz densa como la de Teams, Tesseract escupe restos de bordes
    y de iconos: barras sueltas, letras huerfanas, cadenas sin vocales. Leidos
    en voz alta suenan a averia. Esto es lo que separa "En Teams veo: Fija
    proyectos paramanter |. HistoriacompletadeEnror" de algo escuchable.
    """
    if len(palabra) < 2:
        return True
    if _BASURA.match(palabra):
        return True
    # Sin vocales y con mas de tres letras no es una palabra de ningun idioma
    # que nos interese (las siglas cortas, MSN o CPU, se salvan por longitud).
    if len(palabra) > 3 and not re.search(r"[aeiouáéíóúüAEIOUÁÉÍÓÚÜ]", palabra):
        return True
    # Mas simbolos que letras: es un icono mal leido.
    letras = sum(c.isalnum() for c in palabra)
    return letras < len(palabra) / 2


def _palabras(zona=None, confianza_minima: float = 55.0) -> list[dict]:
    """Cada palabra visible con su posicion y su confianza."""
    imagen = _captura(zona)
    if imagen is None:
        return []

    pytesseract = _tesseract()
    if pytesseract is None:
        log.warning("Falta pytesseract.")
        return []

    try:
        datos = pytesseract.image_to_data(
            imagen, lang=_idioma(pytesseract), output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        log.warning("Tesseract falló: %s", e)
        return []

    salida = []
    for i, texto in enumerate(datos.get("text", [])):
        texto = (texto or "").strip()
        if not texto or _es_basura(texto):
            continue
        try:
            confianza = float(datos["conf"][i])
        except (ValueError, TypeError, KeyError):
            confianza = -1.0
        # Por debajo del umbral el OCR se esta inventando letras. Para hablar
        # subimos el liston mas que para hacer clic: una palabra dudosa leida
        # en voz alta molesta, pero para localizar un boton vale la pena
        # arriesgarse un poco mas.
        if confianza < confianza_minima:
            continue
        salida.append({
            "texto": texto,
            "x": datos["left"][i] + datos["width"][i] // 2,
            "y": datos["top"][i] + datos["height"][i] // 2,
            "linea": datos.get("line_num", [0] * len(datos["text"]))[i],
            "bloque": datos.get("block_num", [0] * len(datos["text"]))[i],
            "confianza": confianza,
        })
    return salida


def _lineas(palabras: list[dict]) -> list[str]:
    """Junta las palabras en lineas legibles, en su orden original."""
    agrupadas: dict = {}
    for p in palabras:
        agrupadas.setdefault((p["bloque"], p["linea"]), []).append(p)

    lineas = []
    for clave in sorted(agrupadas):
        trozo = " ".join(w["texto"] for w in agrupadas[clave]).strip()
        # Menos de dos palabras es un boton o un resto de menu, no contenido.
        if len(trozo) > 6 and trozo.count(" ") >= 1:
            lineas.append(trozo)
    return lineas


# -------------------------------------------------------------------------
# ORDENES RAPIDAS (OCR)
# -------------------------------------------------------------------------
def leer_pantalla(maximo_lineas: int = 12, zona=None) -> str:
    """Lee en voz alta lo que pone en la pantalla."""
    palabras = _palabras(zona)
    if not palabras:
        return ("No pude leer la pantalla. Comprueba que Tesseract esté instalado "
                "con scripts/instalar_extras.ps1")

    lineas = _lineas(palabras)
    if not lineas:
        return "No veo texto legible en la pantalla."

    # Las lineas mas largas suelen ser el contenido; las cortas, menus y
    # botones. Para escuchar interesa el contenido.
    utiles = sorted(lineas, key=len, reverse=True)[:maximo_lineas]
    # ...pero se leen en el orden en que aparecen, no por longitud.
    utiles = [l for l in lineas if l in utiles]

    return "En la pantalla leo: " + ". ".join(utiles)


def leer_primera_linea(zona=None) -> str:
    """
    La linea de MAS ARRIBA de una zona, no la mas larga.

    leer_pantalla ordena por longitud porque para escuchar interesa el
    contenido. Para una cabecera es justo al reves: lo que importa es lo
    primero, y lo de debajo es el subtitulo.

    Sin esto, en WhatsApp Web salio "here for group info" como si fuera el
    nombre del chat. Es el texto del subtitulo ("click here for group info"),
    no un contacto. Y el aviso de "el chat abierto no es el que pediste"
    salto por el motivo equivocado.
    """
    palabras = _palabras(zona, confianza_minima=45.0)
    if not palabras:
        return ""

    agrupadas: dict = {}
    for p in palabras:
        agrupadas.setdefault((p["bloque"], p["linea"]), []).append(p)

    lineas = []
    for grupo in agrupadas.values():
        texto = " ".join(w["texto"] for w in grupo).strip()
        if texto:
            lineas.append((min(w["y"] for w in grupo), texto))

    lineas.sort(key=lambda t: t[0])
    return lineas[0][1] if lineas else ""


def buscar_en_pantalla(texto: str) -> str:
    """¿Esta ese texto en la pantalla?"""
    objetivo = (texto or "").strip().lower()
    if not objetivo:
        return "¿Qué quieres que busque en la pantalla?"

    palabras = _palabras()
    if not palabras:
        return "No pude leer la pantalla."

    for linea in _lineas(palabras):
        if objetivo in linea.lower():
            return f"Sí, lo veo: {linea}"

    return f"No veo {texto} en la pantalla."


def _localizar(objetivo: str, palabras: list[dict]) -> dict | None:
    """Encuentra donde esta un texto, tolerando la transcripcion de voz."""
    objetivo = objetivo.strip().lower()

    # Exacto primero.
    for p in palabras:
        if p["texto"].lower() == objetivo:
            return p

    # Contenido dentro de una palabra mas larga.
    for p in palabras:
        if objetivo in p["texto"].lower():
            return p

    # Y por ultimo parecido: Alexa transcribe "guardar" como "guarda" a menudo.
    textos = [p["texto"].lower() for p in palabras]
    parecidos = difflib.get_close_matches(objetivo, textos, n=1, cutoff=0.75)
    if parecidos:
        return palabras[textos.index(parecidos[0])]

    return None


def clic_en(texto: str) -> str:
    """
    Hace clic sobre un texto de la pantalla.

    Esto es lo que convierte "ver la pantalla" en "usar la pantalla". Y es
    seguro de una forma que un clic a ciegas no lo es: solo pincha donde hay
    un texto que se ha leido de verdad. Si no lo encuentra, no pincha nada
    en vez de pinchar en cualquier sitio.
    """
    objetivo = (texto or "").strip()
    if not objetivo:
        return "¿Dónde quieres que haga clic?"

    palabras = _palabras(confianza_minima=40.0)
    if not palabras:
        return "No pude leer la pantalla, así que no voy a hacer clic a ciegas."

    encontrado = _localizar(objetivo, palabras)
    if not encontrado:
        return f"No encuentro {objetivo} en la pantalla, así que no hago clic."

    try:
        import pyautogui
        pyautogui.click(encontrado["x"], encontrado["y"])
    except ImportError:
        return "Falta pyautogui, no puedo hacer clic."
    except Exception as e:
        return f"No pude hacer clic: {e}"

    log.info("Clic en %r (%d, %d)", encontrado["texto"], encontrado["x"], encontrado["y"])
    return f"Hecho, hice clic en {encontrado['texto']}."


# -------------------------------------------------------------------------
# CAMINO LENTO (modelo de vision)
# -------------------------------------------------------------------------
def describir_pantalla(pregunta: str = "") -> str:
    """
    Describe la pantalla con un modelo de vision.

    Tarda mucho mas de lo que Alexa aguanta. Quien llama a esto tiene que
    hacerlo en segundo plano; aqui no se disimula el coste.
    """
    imagen = _captura()
    if imagen is None:
        return "No pude capturar la pantalla."

    try:
        import ollama
    except ImportError:
        return "No tengo Ollama, no puedo mirar la pantalla."

    # Reducimos antes de mandarla: una captura 4K son millones de pixeles que
    # el modelo va a convertir en tokens, y cada uno cuesta tiempo de GPU.
    imagen.thumbnail((1280, 1280))
    memoria = io.BytesIO()
    imagen.save(memoria, format="PNG", optimize=True)
    en_base64 = base64.b64encode(memoria.getvalue()).decode("ascii")

    instruccion = (pregunta or "").strip() or "Describe brevemente qué se ve en esta pantalla."

    try:
        respuesta = ollama.chat(
            model=MODELO_VISION,
            messages=[{
                "role": "user",
                "content": (
                    f"{instruccion} Responde en español, en dos frases como mucho, "
                    "sin listas ni markdown: esto se va a leer en voz alta."
                ),
                "images": [en_base64],
            }],
            options={"temperature": 0.2, "num_predict": 120},
        )
        return (respuesta.get("message", {}).get("content") or "").strip() or \
            "No supe describir lo que hay en la pantalla."
    except Exception as e:
        log.warning("El modelo de visión falló: %s", e)
        return (f"No pude mirar la pantalla: {e}. "
                f"¿Tienes el modelo {MODELO_VISION}? Instálalo con ollama pull {MODELO_VISION}")


# -------------------------------------------------------------------------
# CLIC CON REFERENCIA ESPACIAL
# -------------------------------------------------------------------------
# "haz clic en el archivo debajo del mensaje del profe Andres".
#
# Aqui no hay nada que un modelo tenga que razonar: es geometria. El OCR ya
# sabe en que coordenadas esta cada palabra, asi que localizar la referencia
# y mirar que hay justo debajo es una resta. Y sale en milisegundos, mientras
# que preguntarselo a un modelo se comeria el plazo de Alexa entero.
DIRECCIONES = {
    "debajo": (0, 1), "abajo": (0, 1), "bajo": (0, 1), "siguiente": (0, 1),
    "encima": (0, -1), "arriba": (0, -1), "sobre": (0, -1), "anterior": (0, -1),
    "derecha": (1, 0), "izquierda": (-1, 0),
    "lado": (1, 0), "junto": (1, 0),
}


def _lineas_con_posicion(palabras: list[dict]) -> list[dict]:
    """Las lineas, cada una con su caja y su texto."""
    agrupadas: dict = {}
    for p in palabras:
        agrupadas.setdefault((p["bloque"], p["linea"]), []).append(p)

    lineas = []
    for clave in sorted(agrupadas):
        grupo = agrupadas[clave]
        texto = " ".join(w["texto"] for w in grupo).strip()
        if not texto:
            continue
        lineas.append({
            "texto": texto,
            "x": sum(w["x"] for w in grupo) // len(grupo),
            "y": sum(w["y"] for w in grupo) // len(grupo),
            "x_min": min(w["x"] for w in grupo),
            "x_max": max(w["x"] for w in grupo),
        })
    return lineas


def _linea_de_referencia(referencia: str, lineas: list[dict]) -> dict | None:
    """La linea que mejor encaja con la referencia hablada."""
    objetivo = referencia.strip().lower()
    if not objetivo:
        return None

    # Coincidencia literal de la frase entera.
    for l in lineas:
        if objetivo in l["texto"].lower():
            return l

    # Si no, la linea que mas palabras comparta. Alexa transcribe los nombres
    # propios de formas creativas ("profe Andres" puede llegar como "profe
    # andrés" o "profeandres"), asi que exigir la frase exacta seria fragil.
    piezas = [p for p in re.split(r"\W+", objetivo) if len(p) > 2]
    if not piezas:
        return None

    mejor, mejor_puntos = None, 0
    for l in lineas:
        bajo = l["texto"].lower()
        puntos = sum(1 for p in piezas if p in bajo)
        if puntos > mejor_puntos:
            mejor, mejor_puntos = l, puntos

    # Al menos la mitad de las palabras: con una sola coincidencia estariamos
    # pinchando practicamente al azar.
    return mejor if mejor_puntos >= max(1, len(piezas) // 2) else None


def clic_relativo(referencia: str, direccion: str) -> str:
    """
    Hace clic en el elemento que esta en cierta direccion respecto a otro.

    Ejemplo: clic_relativo("el mensaje del profe Andres", "debajo").
    """
    dir_clave = (direccion or "").strip().lower()
    vector = None
    for nombre, v in DIRECCIONES.items():
        if nombre in dir_clave:
            vector = v
            break

    if vector is None:
        return f"No entendí la dirección '{direccion}'. Di debajo, encima, a la derecha o a la izquierda."

    palabras = _palabras(confianza_minima=40.0)
    if not palabras:
        return "No pude leer la pantalla, así que no voy a hacer clic a ciegas."

    lineas = _lineas_con_posicion(palabras)
    ancla = _linea_de_referencia(referencia, lineas)

    if ancla is None:
        return f"No encuentro {referencia} en la pantalla."

    dx, dy = vector
    candidatos = []

    for l in lineas:
        if l is ancla:
            continue

        if dy:
            # Arriba o abajo: tiene que estar en la misma columna, o no seria
            # "debajo de eso" sino "en la otra punta de la pantalla".
            if l["x_max"] < ancla["x_min"] - 60 or l["x_min"] > ancla["x_max"] + 60:
                continue
            distancia = (l["y"] - ancla["y"]) * dy
        else:
            # Izquierda o derecha: en la misma franja horizontal.
            if abs(l["y"] - ancla["y"]) > 40:
                continue
            distancia = (l["x"] - ancla["x"]) * dx

        if distancia > 0:
            candidatos.append((distancia, l))

    if not candidatos:
        return f"Encontré {ancla['texto'][:40]}, pero no veo nada {dir_clave} de eso."

    # El mas cercano en esa direccion: "debajo" significa lo siguiente, no lo
    # ultimo de la pantalla.
    candidatos.sort(key=lambda c: c[0])
    elegido = candidatos[0][1]

    try:
        import pyautogui
        pyautogui.click(elegido["x"], elegido["y"])
    except ImportError:
        return "Falta pyautogui, no puedo hacer clic."
    except Exception as e:
        return f"No pude hacer clic: {e}"

    log.info("Clic %s de %r -> %r", dir_clave, ancla["texto"][:40], elegido["texto"][:40])
    return f"Hice clic en {elegido['texto'][:60]}."


# -------------------------------------------------------------------------
# RAZONAR SOBRE LO QUE SE VE
# -------------------------------------------------------------------------
# Hasta aqui todo era "busca ESTE texto". Lo de abajo es distinto: buscar
# algo por su FUNCION, sin saber como se llama en esta app concreta.
#
# El buscador de Spotify pone "Buscar", el de Teams "Buscar" arriba del todo,
# el de Epic no pone nada y es una lupa, y Valorant no tiene. La palabra
# cambia, la intencion no. Aqui van las palabras que suele llevar cada cosa,
# ordenadas de la mas fiable a la mas dudosa: se pincha la primera que
# aparezca de verdad en la pantalla.
INTENCIONES = {
    "buscar": [
        "buscar", "búsqueda", "busqueda", "search", "buscar en", "explorar",
        "encontrar", "find", "filtrar",
    ],
    "jugar": [
        # "jugar" antes que "iniciar": en Epic el boton de la ficha del juego
        # pone JUGAR, y "iniciar" tambien aparece en textos de la interfaz
        # que no son ese boton.
        "jugar", "play", "iniciar", "launch", "continuar", "reanudar",
        "instalar", "install",
    ],
    "aceptar": [
        "aceptar", "aceptar todo", "ok", "continuar", "siguiente", "confirmar",
        "entendido", "de acuerdo", "permitir", "si", "yes",
    ],
    "cerrar": [
        "cerrar", "cancelar", "descartar", "ahora no", "mas tarde", "no gracias",
        "omitir", "saltar", "close", "cancel",
    ],
    "enviar": [
        "enviar", "send", "publicar", "mandar", "responder",
    ],
    "descargar": [
        "descargar", "download", "obtener", "guardar",
    ],
}


def encontrar_por_intencion(intencion: str, palabras=None) -> dict | None:
    """
    Localiza en pantalla el elemento que CUMPLE esa funcion.

    Devuelve el mismo dict que `_localizar` (texto, x, y) o None. Se separa
    del clic a proposito: hay sitios donde interesa saber si existe antes de
    decidir que hacer.
    """
    candidatas = INTENCIONES.get((intencion or "").strip().lower())
    if not candidatas:
        return None

    if palabras is None:
        palabras = _palabras(confianza_minima=40.0)
    if not palabras:
        return None

    # En orden de fiabilidad, no el primero que encaje por casualidad.
    for etiqueta in candidatas:
        encontrado = _localizar(etiqueta, palabras)
        if encontrado:
            log.info("Intencion %r resuelta como %r", intencion, encontrado["texto"])
            return encontrado

    return None


def clic_por_intencion(intencion: str) -> str:
    """Pincha lo que sirva para eso, se llame como se llame."""
    palabras = _palabras(confianza_minima=40.0)
    if not palabras:
        return "No pude leer la pantalla, así que no voy a hacer clic a ciegas."

    encontrado = encontrar_por_intencion(intencion, palabras)
    if not encontrado:
        visibles = ", ".join(_lineas(palabras)[:3]) or "nada legible"
        return (f"No veo nada que sirva para {intencion} en esta pantalla. "
                f"Lo que leo arriba es: {visibles}.")

    try:
        import pyautogui
        pyautogui.click(encontrado["x"], encontrado["y"])
    except ImportError:
        return "Falta pyautogui, no puedo hacer clic."
    except Exception as e:
        return f"No pude hacer clic: {e}"

    return f"Hecho, pinché en {encontrado['texto']}."


def buscar_dentro_de_lo_que_veo(consulta: str) -> str:
    """
    Encuentra el buscador de la app que tengas delante y escribe ahi.

    Es el plan B de `sistema.buscar_en_app`: cuando la app no esta en la
    tabla de atajos, en vez de rendirse se mira la pantalla, se busca algo
    que sirva para buscar, se pincha y se escribe. Es lo que harias tu.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return "¿Qué quieres que busque?"

    palabras = _palabras(confianza_minima=40.0)
    if not palabras:
        return "No pude leer la pantalla para encontrar el buscador."

    caja = encontrar_por_intencion("buscar", palabras)
    if not caja:
        return ""          # cadena vacia: que el de arriba decida el plan C

    try:
        import pyautogui
        from tools import entrada

        pyautogui.click(caja["x"], caja["y"])
        time.sleep(0.4)

        # Puede haber texto de una busqueda anterior. Seleccionar todo y
        # escribir encima lo reemplaza; escribir sin mas lo concatena y
        # buscarias "tame impalabad bunny".
        pyautogui.hotkey("ctrl", "a")
        entrada.escribir_texto(consulta, pulsar_enter=True)
    except ImportError:
        return "Falta pyautogui, no puedo escribir en el buscador."
    except Exception as e:
        return f"Encontré el buscador pero no pude escribir: {e}"

    return f"Buscando {consulta} en lo que tienes abierto."
