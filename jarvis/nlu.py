"""
Router determinista de comandos: el corazón de la velocidad de Jarvis.

Por qué existe
--------------
Alexa corta la skill si el endpoint tarda más de ~8 segundos. Un modelo de 7B
en una RTX 3050 arrancando en frío tarda 20-40 s. Si CADA orden pasara por el
LLM, la mayoría fallaría con "hubo un problema con la respuesta de la skill".

Este módulo reconoce las órdenes frecuentes con expresiones regulares y las
ejecuta en milisegundos, sin tocar el modelo. Solo lo verdaderamente abierto
("resume esto", "explícame aquello") cae al LLM.

Cómo añadir un comando nuevo
-----------------------------
Escribe una función manejadora y añade una tupla (regex, manejadora) a INTENTS.
El orden importa: lo más específico va primero.
"""

import logging
import re
import unicodedata

import modes
import tareas
from config import MODO_DEDICADO, MODO_GAMING, MODO_NORMAL
import confirmaciones
import foco
from tools import (aprendizaje, archivar, archivos, avanzado, catalogo,
                   correo, entrada, estudio, investigar, memoria, navegador,
                   obsidian, pantalla, plan, rendimiento, seleccion, sistema,
                   teams, ventanas, whatsapp)

log = logging.getLogger("jarvis.nlu")


# =========================================================================
# NORMALIZACIÓN
# =========================================================================
# Tabla de acentos que PRESERVA la longitud del texto. Esto es importante:
# gracias a ello, las posiciones que devuelve el regex sobre el texto sin
# acentos siguen siendo válidas sobre el texto original, y podemos extraer
# el contenido con sus tildes intactas.
_ACENTOS_ORIGEN = "áéíóúüàèìòùâêîôûÁÉÍÓÚÜÑñ"
_ACENTOS_DESTINO = "aeiouuaeiouaeiouAEIOUUNn"
TABLA_ACENTOS = str.maketrans(_ACENTOS_ORIGEN, _ACENTOS_DESTINO)


def sin_acentos(texto: str) -> str:
    """Quita acentos conservando la longitud exacta de la cadena."""
    return texto.translate(TABLA_ACENTOS)


# Muletillas que Alexa transcribe y que solo estorban.
MULETILLAS = [
    "por favor ",
    "porfavor ",
    "necesito que ",
    "quiero que ",
    "puedes ",
    "podrias ",
    "podrías ",
    "me gustaria que ",
    "me gustaría que ",
    "quisiera que ",
    "hazme el favor de ",
    "oye ",
]


# ---- Nombre de invocación -------------------------------------------------
# Alexa a veces deja el nombre de la skill dentro del slot ("jarvis local crea
# un archivo..."). Como el nombre lo eliges tú en la consola y Amazon exige dos
# palabras o más, no podemos codificarlo aquí: lo leemos de la configuración.

# Palabras que NUNCA se recortan sueltas, aunque formen parte del nombre de
# invocación: son verbos con los que empiezan órdenes reales, y quitarlos
# rompería el comando.
_VERBOS_INTOCABLES = {
    "abre", "abrir", "crea", "crear", "lee", "leer", "busca", "buscar",
    "cierra", "cerrar", "mueve", "mover", "copia", "copiar", "borra", "borrar",
    "elimina", "eliminar", "escribe", "edita", "editar", "pon", "pone",
    "modo", "activa", "toma", "haz", "dame", "dime", "lista", "listar",
    "agrega", "reemplaza", "presiona", "pulsa", "apaga", "bloquea",
}


def _variantes_invocacion() -> list[str]:
    """Formas del nombre de invocación que conviene recortar del comando."""
    from config import ALEXA_INVOCATION_NAME

    nombre = sin_acentos((ALEXA_INVOCATION_NAME or "").strip().lower())
    if not nombre:
        return []

    variantes = {f"{nombre} "}

    # También cada palabra por separado: el reconocimiento de voz parte el
    # nombre con frecuencia y solo llega media parte.
    for palabra in nombre.split():
        if len(palabra) > 3 and palabra not in _VERBOS_INTOCABLES:
            variantes.add(f"{palabra} ")

    # Las más largas primero, para que "jarvis local " gane a "jarvis ".
    return sorted(variantes, key=len, reverse=True)


MULETILLAS_INVOCACION = _variantes_invocacion()

# "que" solo es muletilla cuando VIENE DESPUÉS de otra ("por favor que crees...").
# Al inicio de la frase casi siempre es un "qué" interrogativo legítimo
# ("qué archivos hay en el escritorio"), y quitarlo rompía esas órdenes.
MULETILLA_ENCADENADA = "que "


def limpiar_comando(texto: str) -> str:
    """Quita muletillas del inicio y normaliza espacios."""
    texto = (texto or "").strip()
    texto = re.sub(r"\s+", " ", texto)
    texto = restaurar_articulo(texto)

    # Puede haber varias muletillas encadenadas y en cualquier orden:
    #   "jarvis local por favor crea..."  /  "por favor jarvis local crea..."
    ya_recorto = False
    for _ in range(4):
        minusculas = sin_acentos(texto.lower())
        candidatas = (
            MULETILLAS_INVOCACION
            + MULETILLAS
            + ([MULETILLA_ENCADENADA] if ya_recorto else [])
        )
        recortado = False
        for muletilla in candidatas:
            if minusculas.startswith(muletilla):
                texto = texto[len(muletilla):].strip()
                recortado = True
                ya_recorto = True
                break
        if not recortado:
            break

    return texto.rstrip(" .,¿?¡!")


# ---- Numeros que Alexa convierte a digitos --------------------------------
# El reconocimiento de voz normaliza "un" a "1", asi que "crea un archivo"
# llega como "crea 1 archivo". Lo revertimos SOLO delante de sustantivos donde
# un 1 no puede ser una cantidad real, para no estropear ordenes legitimas
# como "apaga el equipo en 5 minutos".
_SUSTANTIVOS_CONTABLES = (
    r"archivos?|documentos?|scripts?|ficheros?|notas?|carpetas?|directorios?|"
    r"folders?|capturas?|l[ií]neas?|textos?|p[aá]ginas?|im[aá]genes?|"
    r"pesta[nñ]as?|ventanas?|correos?|mensajes?|whats?apps?|chats?|"
    r"recados?|mails?|emails?|juegos?|programas?|aplicaciones?"
)


def restaurar_articulo(texto: str) -> str:
    """Devuelve el '1' a la forma 'un' cuando hace de articulo, no de cantidad."""
    return re.sub(
        rf"\b1\s+(?={_SUSTANTIVOS_CONTABLES})",
        "un ",
        texto,
        flags=re.IGNORECASE,
    )


# ---- Puntuación dictada ---------------------------------------------------
# OJO: esto se aplica SOLO a nombres de archivo, nunca al texto completo.
# Si se aplicara a todo, "el punto de vista" se convertiría en "el.de vista".
_DICTADO_ARCHIVO = [
    (r"\s+punto\s+", "."),
    (r"\s+guion\s+bajo\s+", "_"),
    (r"\s+guión\s+bajo\s+", "_"),
    (r"\s+piso\s+", "_"),
    (r"\s+guion\s+", "-"),
    (r"\s+guión\s+", "-"),
    (r"\s+raya\s+", "-"),
    (r"\s+barra\s+", "/"),
]

# Extensiones dictadas por nombre en vez de por letra.
_EXTENSIONES_HABLADAS = {
    "python": ".py",
    "py": ".py",
    "texto": ".txt",
    "txt": ".txt",
    "word": ".docx",
    "documento word": ".docx",
    "docx": ".docx",
    "excel": ".xlsx",
    "hoja de calculo": ".xlsx",
    "hoja de cálculo": ".xlsx",
    "xlsx": ".xlsx",
    "markdown": ".md",
    "md": ".md",
    "json": ".json",
    "csv": ".csv",
    "html": ".html",
    "javascript": ".js",
    "js": ".js",
}


def normalizar_nombre_archivo(nombre: str, tipo_hablado: str = "") -> str:
    """
    Convierte un nombre dictado en un nombre de archivo real.

        'prueba punto py'          -> 'prueba.py'
        'mis notas punto txt'      -> 'mis_notas.txt'
        'informe'   (tipo: word)   -> 'informe.docx'
    """
    nombre = (nombre or "").strip().strip('"').strip("'")

    for patron, reemplazo in _DICTADO_ARCHIVO:
        nombre = re.sub(patron, reemplazo, nombre, flags=re.IGNORECASE)

    nombre = nombre.strip()

    # Sin extensión: la deducimos del tipo que dijo el usuario.
    if "." not in nombre:
        clave = sin_acentos((tipo_hablado or "").strip().lower())
        extension = ""
        for alias, ext in _EXTENSIONES_HABLADAS.items():
            if sin_acentos(alias) == clave:
                extension = ext
                break
        nombre += extension or ".txt"

    # La gente dicta "punto python" o "punto texto" en vez de la extension real.
    # Sin esto se crean archivos llamados "prueba.python", que Windows no asocia
    # con nada.
    raiz_tmp, punto_tmp, ext_tmp = nombre.rpartition(".")
    if punto_tmp:
        equivalente = _EXTENSIONES_HABLADAS.get(sin_acentos(ext_tmp.strip().lower()))
        if equivalente:
            nombre = raiz_tmp + equivalente

    # Los espacios restantes se vuelven guiones bajos: "mis notas.txt" -> "mis_notas.txt"
    raiz, punto, extension = nombre.rpartition(".")
    if punto:
        raiz = raiz.replace(" ", "_")
        nombre = f"{raiz}.{extension.replace(' ', '').lower()}"

    # Quitamos caracteres que Windows no admite en nombres de archivo.
    nombre = re.sub(r'[<>:"|?*]', "", nombre)

    return nombre


def interpretar_contenido(nombre_archivo: str, contenido: str) -> str:
    """
    Convierte instrucciones habladas en contenido utilizable.

    Dictar código por voz es incómodo, así que traducimos los casos más
    comunes: 'print hola' en un .py se convierte en print("hola").
    """
    contenido = (contenido or "").strip()
    if not contenido:
        return ""

    extension = nombre_archivo.rsplit(".", 1)[-1].lower() if "." in nombre_archivo else ""
    plano = sin_acentos(contenido.lower())

    if extension == "py":
        # 'print hola'  ->  print("hola")
        coincidencia = re.match(r"^print\s+(.+)$", contenido, re.IGNORECASE)
        if coincidencia and "(" not in contenido:
            texto = coincidencia.group(1).strip().strip('"').strip("'")
            return f'print("{texto}")'

        # 'imprime hola' / 'que imprima hola'
        coincidencia = re.match(r"^(?:que\s+)?imprima?\s+(.+)$", contenido, re.IGNORECASE)
        if coincidencia:
            texto = coincidencia.group(1).strip().strip('"').strip("'")
            return f'print("{texto}")'

        # 'hola mundo' a secas en un .py: casi seguro quiere un print.
        if plano in ("hola", "hola mundo", "hello world"):
            return f'print("{contenido}")'

    # Colas que la gente añade al dictar y que no son parte del contenido:
    # "con hola escrito dentro" -> el contenido es solo "hola".
    contenido = re.sub(
        r"\s+(?:escrito|escritas?|escritos?|puesto|metido)?\s*(?:a?dentro|en\s+[eé]l)\s*$",
        "",
        contenido,
        flags=re.IGNORECASE,
    ).strip()

    # Alexa dicta los saltos de línea como "salto de linea" / "nueva linea".
    contenido = re.sub(
        r"\s*(?:salto de l[ií]nea|nueva l[ií]nea|siguiente l[ií]nea)\s*",
        "\n",
        contenido,
        flags=re.IGNORECASE,
    )

    return contenido


# =========================================================================
# MANEJADORAS
# =========================================================================
# Cada una recibe el objeto `match` y devuelve la frase que dirá Alexa.

# ---- Modos ----
def _modo_normal(m) -> str:
    return modes.cambiar_modo(MODO_NORMAL)


def _modo_dedicado(m) -> str:
    return modes.cambiar_modo(MODO_DEDICADO)


def _modo_gaming(m) -> str:
    return modes.cambiar_modo(MODO_GAMING)


def _que_modo(m) -> str:
    return modes.describir_modo()


# ---- Métricas ----
def _cpu(m) -> str:
    return sistema.uso_cpu()


def _ram(m) -> str:
    return sistema.uso_ram()


def _disco(m) -> str:
    return sistema.uso_disco()


def _gpu(m) -> str:
    return sistema.uso_gpu()


def _estado(m) -> str:
    return sistema.estado_general()


def _procesos(m) -> str:
    return sistema.procesos_pesados()


def _bateria(m) -> str:
    return sistema.bateria()


# ---- Archivos ----
def _crear_archivo(m) -> str:
    grupos = m.groupdict()
    # El tipo puede venir antes del sustantivo ("documento word llamado X")
    # o después ("archivo python llamado X"). Aceptamos ambos.
    tipo = grupos.get("tipo") or grupos.get("tipo2") or ""
    nombre = normalizar_nombre_archivo(grupos.get("nombre", ""), tipo)
    contenido = interpretar_contenido(nombre, grupos.get("contenido", "") or "")
    carpeta = (grupos.get("carpeta") or "").strip()
    return archivos.crear_archivo(nombre, contenido, carpeta)


def _crear_carpeta(m) -> str:
    nombre = (m.group("nombre") or "").strip().replace(" ", "_")
    return archivos.crear_carpeta(nombre)


def _leer_archivo(m) -> str:
    nombre = normalizar_nombre_archivo(m.group("nombre"))
    foco.recordar("archivo", nombre)
    resultado = archivos.leer_archivo(nombre)
    # Recortamos para que Alexa no lea un archivo entero en voz alta.
    if len(resultado) > 500:
        resultado = resultado[:500] + "... y sigue."
    return resultado


def _editar_agregar(m) -> str:
    nombre = normalizar_nombre_archivo(m.group("nombre"))
    contenido = interpretar_contenido(nombre, m.group("contenido"))
    return archivos.editar_archivo(nombre, "agregar", contenido)


def _editar_reemplazar(m) -> str:
    nombre = normalizar_nombre_archivo(m.group("nombre"))
    return archivos.editar_archivo(
        nombre,
        "reemplazar",
        buscar=m.group("buscar").strip(),
        reemplazar=m.group("reemplazar").strip(),
    )


def _mover_archivo(m) -> str:
    nombre = normalizar_nombre_archivo(m.group("nombre"))
    return archivos.mover_archivo(nombre, m.group("destino").strip())


def _copiar_archivo(m) -> str:
    nombre = normalizar_nombre_archivo(m.group("nombre"))
    return archivos.copiar_archivo(nombre, m.group("destino").strip())


def _eliminar_archivo(m) -> str:
    nombre = normalizar_nombre_archivo(m.group("nombre"))
    return archivos.eliminar_archivo(nombre)


def _listar_archivos(m) -> str:
    carpeta = (m.groupdict().get("carpeta") or "escritorio").strip()
    resultado = archivos.listar_archivos(carpeta)

    # Guardamos la lista en el orden en que se dijo, para que "abre el segundo"
    # signifique algo. Los nombres van tras los dos puntos, separados por comas.
    if ":" in resultado:
        nombres = [n.strip(" .") for n in resultado.split(":", 1)[1].split(",")]
        nombres = [n for n in nombres if n and not n.startswith("y sigue")]
        if nombres:
            foco.recordar("archivo", nombres[0], lista=nombres)
    return resultado


def _buscar_archivo(m) -> str:
    patron = normalizar_nombre_archivo(m.group("patron")).replace(".txt", "")
    return archivos.buscar_archivo(patron)


# ---- Aplicaciones ----
def _abrir_app(m) -> str:
    nombre = m.group("app").strip()
    resultado = sistema.abrir_aplicacion(nombre)
    # Dejamos constancia para que el siguiente "cierralo" sepa de que habla.
    if resultado.startswith("Abriendo"):
        foco.recordar("app", resultado.replace("Abriendo", "").strip(" ."))
    return resultado


def _cerrar_app(m) -> str:
    nombre = m.group("app").strip()
    resultado = sistema.cerrar_aplicacion(nombre)
    foco.olvidar()
    return resultado


# ---- Navegador ----
def _buscar_web(m) -> str:
    return navegador.buscar_en_navegador(m.group("consulta").strip())


def _abrir_sitio(m) -> str:
    return navegador.abrir_sitio(m.group("sitio").strip())


def _youtube(m) -> str:
    return navegador.reproducir_en_youtube(m.group("consulta").strip())


def _abrir_navegador(m) -> str:
    return navegador.abrir_navegador()


# ---- Teclado y mouse ----
def _escribir(m) -> str:
    return entrada.escribir_texto(m.group("texto").strip())


def _atajo(m) -> str:
    return entrada.ejecutar_atajo(m.group("atajo").strip())


def _captura(m) -> str:
    return entrada.captura_pantalla()


def _desplazar(m) -> str:
    direccion = (m.groupdict().get("direccion") or "abajo").strip()
    return entrada.desplazar(direccion)


def _pulsar(m) -> str:
    return entrada.pulsar_tecla(m.group("tecla").strip())


# ---- Energía ----
def _bloquear(m) -> str:
    return sistema.bloquear_equipo()


def _suspender(m) -> str:
    return sistema.suspender_equipo()


def _apagar(m) -> str:
    minutos = int(m.groupdict().get("minutos") or 1)
    cuando = "ahora" if minutos <= 1 else f"en {minutos} minutos"
    return confirmaciones.pedir(
        f"apagar el equipo {cuando}",
        lambda: sistema.apagar_equipo(minutos),
    )


def _cancelar_apagado(m) -> str:
    return sistema.cancelar_apagado()


def _reiniciar(m) -> str:
    return confirmaciones.pedir("reiniciar el equipo", sistema.reiniciar_equipo)


# ---- Meta ----
def _pendiente(m) -> str:
    return tareas.consultar_pendiente()


def _foco_cerrar(m) -> str:
    """'cierralo' / 'cierra eso': cierra lo ultimo que se abrio."""
    datos = foco.actual("app")
    if not datos:
        return "No sé qué quieres que cierre. Dime el nombre del programa."
    return sistema.cerrar_aplicacion(datos["valor"])


def _foco_leer(m) -> str:
    """'leelo' / 'abrelo': sobre el ultimo archivo mencionado."""
    datos = foco.actual("archivo")
    if not datos:
        return "No sé a qué archivo te refieres. Dime el nombre."
    resultado = archivos.leer_archivo(datos["valor"])
    if len(resultado) > 500:
        resultado = resultado[:500] + "... y sigue."
    return resultado


def _foco_eliminar(m) -> str:
    """'borralo': sobre el ultimo archivo. Pasa por confirmacion."""
    datos = foco.actual("archivo")
    if not datos:
        return "No sé qué archivo quieres borrar. Dime el nombre."
    return confirmaciones.pedir(
        f"borrar {datos['valor']}",
        lambda: archivos.eliminar_archivo(datos["valor"]),
    )


def _foco_ordinal(m) -> str:
    """'abre el segundo' despues de haber listado archivos."""
    elegido = foco.elemento_por_ordinal(m.group("cual"))
    if not elegido:
        return "No tengo ninguna lista reciente. Pídeme primero que liste algo."

    foco.recordar("archivo", elegido, lista=(foco.actual() or {}).get("lista"))
    resultado = archivos.leer_archivo(elegido)
    if len(resultado) > 500:
        resultado = resultado[:500] + "... y sigue."
    return f"{elegido}. {resultado}"


# ---- Memoria semantica ----
def _memoria_indexar(m) -> str:
    # El envoltorio de coincidencias no expone el texto original, asi que
    # "desde cero" se captura en el propio patron en vez de rebuscarlo.
    forzar = bool(m.groupdict().get("todo"))
    memoria.indexar_en_segundo_plano(forzar)
    return ("Estoy indexando tu bóveda. La primera vez tarda un rato; "
            "pregúntame cómo va la memoria.")


def _memoria_estado(m) -> str:
    return memoria.estado()


def _memoria_buscar(m) -> str:
    """Buscar por significado. Rapido: un vector y una comparacion."""
    g = m.groupdict()
    consulta = (g.get("q") or g.get("q2") or g.get("q3") or "").strip()
    encontradas = memoria.buscar(consulta, cuantos=4)
    if not encontradas:
        return (f"No encuentro nada parecido a {consulta}. "
                "Si acabas de escribirlo, dime indexa la bóveda.")

    mejor = encontradas[0]
    otras = [n["titulo"] for n in encontradas[1:3]]
    respuesta = f"Lo más parecido es {mejor['titulo']}."
    if otras:
        respuesta += f" También {', '.join(otras)}."
    return respuesta


# ---- Archivar en la boveda ----
def _archivar(m) -> str:
    que = (m.groupdict().get("que") or m.groupdict().get("que2") or "").strip()
    if not que:
        return "¿Qué archivo quieres que guarde?"
    return _al_fondo(
        f"archivar {que}",
        lambda: archivar.archivar(que),
        f"Voy a leer {que} y meterlo donde toca. Pregúntame cómo quedó lo último.",
    )


# ---- Planificador ----
def _planificar(m) -> str:
    g = m.groupdict()
    orden = (g.get("orden") or g.get("orden2") or "").strip()
    if not orden:
        return "¿Qué quieres que prepare?"
    return _al_fondo(
        f"plan para {orden}",
        lambda: plan.ejecutar(orden),
        f"Vale, lo desgloso en pasos y lo hago. Pregúntame cómo quedó lo último.",
    )


# ---- Aprendizaje ----
def _aprendizaje_resumen(m) -> str:
    return aprendizaje.resumen_corto()


def _aprendizaje_propuestas(m) -> str:
    return _al_fondo(
        "revisar en qué puedo mejorar",
        aprendizaje.escribir_propuestas,
        "Reviso mi historial a ver qué se me atraganta. Pregúntame cómo quedó lo último.",
    )


# ---- Catalogo de aplicaciones ----
def _catalogo_refrescar(m) -> str:
    catalogo.refrescar_en_segundo_plano()
    return ("Estoy rastreando el menú Inicio, el registro y la Store. "
            "Tarda unos segundos; pregúntame qué aplicaciones conozco.")


def _catalogo_resumen(m) -> str:
    return catalogo.resumen()


def _catalogo_tengo(m) -> str:
    """¿Esta instalado X? Sin abrir nada."""
    g = m.groupdict()
    que = (g.get("que") or g.get("que2") or "").strip()
    encontrados = catalogo.buscar(que, cuantos=4)
    if not encontrados:
        return f"No veo nada parecido a {que} instalado."

    if encontrados[0]["nota"] >= 0.85:
        return f"Sí, tienes {encontrados[0]['nombre']}."

    nombres = ", ".join(c["nombre"] for c in encontrados[:3])
    return f"Exactamente {que} no, pero tienes: {nombres}."


# ---- Estudio ----
# Todo esto lee varias notas y razona sobre ellas: son miles de tokens y
# decenas de segundos. Ni se intenta en directo, va al fondo.
def _al_fondo(descripcion: str, funcion, aviso: str) -> str:
    tareas.lanzar_en_segundo_plano(descripcion, funcion)
    return aviso


def _examen(m) -> str:
    g = m.groupdict()
    tema = (g.get("tema") or g.get("tema2") or "").strip()
    cuando = (g.get("cuando") or "").strip()
    if not tema:
        return "¿De qué es el examen?"
    return _al_fondo(
        f"preparar el examen de {tema}",
        lambda: estudio.preparar_examen(tema, cuando),
        f"Voy a revisar tus notas sobre {tema}. Tarda un poco: "
        "pregúntame cómo quedó lo último.",
    )


def _preguntas_repaso(m) -> str:
    g = m.groupdict()
    tema = (g.get("tema") or g.get("tema2") or g.get("tema3") or "").strip()
    return _al_fondo(
        f"preguntas de repaso de {tema}",
        lambda: estudio.preguntas_de_repaso(tema),
        f"Preparando preguntas de {tema}. Pregúntame cómo quedó lo último.",
    )


def _explicar_notas(m) -> str:
    g = m.groupdict()
    tema = (g.get("tema") or g.get("tema2") or "").strip()
    return _al_fondo(
        f"explicar {tema} con tus notas",
        lambda: estudio.explicar_desde_notas(tema),
        f"Miro qué tienes escrito sobre {tema}. Pregúntame cómo quedó lo último.",
    )


def _que_tengo_de(m) -> str:
    # Esta si es rapida: solo mira titulos, no llama al modelo.
    g = m.groupdict()
    return estudio.que_tengo_de((g.get("tema") or g.get("tema2") or "").strip())


# ---- Notas por relacion ----
def _nota_encontrar(m) -> str:
    g = m.groupdict()
    desc = (g.get("desc") or g.get("desc2") or "").strip()
    return estudio.encontrar_nota(desc)


def _nota_abrir(m) -> str:
    g = m.groupdict()
    return estudio.abrir_nota_relacionada((g.get("desc") or g.get("desc2") or "").strip())


def _nota_relacionar(m) -> str:
    g = m.groupdict()
    desc = (g.get("desc") or g.get("desc2") or g.get("desc3") or "").strip()
    return _al_fondo(
        f"relacionar tus notas sobre {desc}",
        lambda: estudio.relacionar(desc),
        f"Cruzando tus notas sobre {desc}. Pregúntame cómo quedó lo último.",
    )


# ---- Investigar en internet ----
def _investigar(m) -> str:
    g = m.groupdict()
    consulta = (g.get("consulta") or g.get("consulta2") or "").strip()
    return _al_fondo(
        f"investigar {consulta}",
        lambda: investigar.investigar(consulta),
        f"Buscando sobre {consulta}. Dame unos segundos y pregúntame cómo quedó lo último.",
    )


def _comparar(m) -> str:
    g = m.groupdict()
    consulta = (g.get("consulta") or g.get("consulta2") or "").strip()
    return _al_fondo(
        f"comparar {consulta}",
        lambda: investigar.comparar(consulta),
        f"Comparando opciones de {consulta}. Pregúntame cómo quedó lo último.",
    )


def _mejor_opcion(m) -> str:
    g = m.groupdict()
    consulta = (g.get("consulta") or g.get("consulta2") or "").strip()
    return _al_fondo(
        f"buscar la mejor opción de {consulta}",
        lambda: investigar.mejor_opcion(consulta),
        f"Mirando qué recomiendan para {consulta}. Pregúntame cómo quedó lo último.",
    )


# ---- WhatsApp ----
def _wa_enviar(m) -> str:
    g = m.groupdict()
    destino = (g.get("quien") or g.get("quien2") or "").strip()
    texto = (g.get("texto") or g.get("texto2") or "").strip()

    # Mismo cuidado que en el dialogo de dos turnos: si WhatsApp Web no esta
    # abierto, guardamos la orden entera antes de contestar. Si no, decir
    # "repitemelo" obliga a soltar toda la frase otra vez, y con un
    # reconocimiento de voz que ya se equivoca con los nombres, cada
    # repeticion es otra oportunidad de que salga mal.
    if not whatsapp.esta_abierto():
        foco.recordar("envio", destino, lista=[texto])
        whatsapp.lanzar_en_segundo_plano()
        return ("Estoy abriendo WhatsApp Web en Comet. "
                "Cuando cargue dime sigue, y lo escribo.")

    return whatsapp.enviar_mensaje(destino, texto)


def _wa_texto_pendiente(m) -> str:
    """
    El texto del mensaje, dicho en el turno siguiente al "¿Que le digo?".

    Este patron es MUY amplio a proposito (casi cualquier frase), asi que solo
    puede actuar si de verdad hay un destinatario esperando. Si no, devuelve
    None y el router sigue probando el resto de patrones como si nada.
    """
    datos = foco.actual("destinatario")
    if not datos:
        return None

    # SOLO el turno inmediatamente siguiente a la pregunta. El foco vive tres
    # turnos, y eso aqui seria peligroso: si preguntamos "que le digo a X" y
    # tu te pones a hablar de otra cosa, la segunda frase acabaria escrita en
    # el chat de X. Contestas o no contestas; a la tercera ya no vale.
    if datos.get("turnos", 0) > 1:
        foco.olvidar()
        return None

    destinatario = datos["valor"]
    texto = m.group("texto").strip()

    # Si WhatsApp Web no esta abierto, NO se puede borrar el foco todavia.
    # Aqui estaba el fallo: se olvidaba el destinatario, se contestaba "estoy
    # abriendo, repitemelo", y al repetir solo el TEXTO ya no habia a quien
    # mandarselo. La orden se perdia y el modelo contestaba "ya esta" sin
    # haber hecho nada.
    #
    # Guardamos los dos datos y se reanuda con un "sigue".
    if not whatsapp.esta_abierto():
        foco.recordar("envio", destinatario, lista=[texto])
        whatsapp.lanzar_en_segundo_plano()
        return ("Estoy abriendo WhatsApp Web en Comet. "
                "Cuando cargue dime sigue, y lo escribo.")

    foco.olvidar()
    return whatsapp.enviar_mensaje(destinatario, texto)


def _wa_falta_texto(m) -> str:
    """Pidio mandar un mensaje pero no dijo cual."""
    quien = m.group("quien").strip()
    foco.recordar("destinatario", quien)

    # NO repetimos el nombre transcrito. Alexa entendio "familia biogli" donde
    # dijiste "familia Oviedo Gil", y devolverte esa cadena rota solo suena a
    # que Jarvis no se entera. El nombre BUENO llega en la confirmacion, leido
    # de la cabecera del chat que WhatsApp abre de verdad, que es el unico que
    # importa. Aqui basta con pedir el texto.
    return "Vale. ¿Qué le digo? Te confirmo con quién antes de enviar."


def _wa_reanudar(m) -> str:
    """
    Retoma un envio que quedo esperando a que cargara WhatsApp Web.

    Devuelve None si no hay nada pendiente, para que "sigue" o "dale" sigan
    valiendo como lo que sean en cualquier otro contexto.
    """
    datos = foco.actual("envio")
    if not datos or not datos.get("lista"):
        return None

    destinatario, texto = datos["valor"], datos["lista"][0]

    if not whatsapp.esta_abierto():
        # Refrescamos para que no caduque mientras espera a cargar.
        foco.recordar("envio", destinatario, lista=[texto])
        return "WhatsApp Web todavía no ha cargado. Dame unos segundos más y dime sigue otra vez."

    foco.olvidar()
    return whatsapp.enviar_mensaje(destinatario, texto)


def _wa_abrir_app(m) -> str:
    return whatsapp.abrir_whatsapp()


def _wa_abrir(m) -> str:
    return whatsapp.abrir_chat(m.group("quien").strip())


def _wa_leer(m) -> str:
    return whatsapp.leer_chat()


# ---- Correo saliente ----
def _correo_enviar(m) -> str:
    g = m.groupdict()
    quien = (g.get("quien") or g.get("quien2") or "").strip()
    texto = (g.get("texto") or g.get("texto2") or "").strip()
    return correo.enviar_correo(quien, texto)


def _correo_responder(m) -> str:
    return correo.responder_ultimo(m.group("texto").strip())


# ---- Ventanas ----
def _ventana_cambiar(m) -> str:
    g = m.groupdict()
    return ventanas.cambiar_a((g.get("cual") or g.get("cual2") or "").strip())


def _ventanas_listar(m) -> str:
    return ventanas.listar()


def _ventana_minimizar(m) -> str:
    return ventanas.minimizar_todo()


def _ventana_maximizar(m) -> str:
    return ventanas.maximizar_actual()


# ---- Rendimiento ----
def _por_que_lag(m) -> str:
    return rendimiento.diagnostico()


def _cerrar_prescindibles(m) -> str:
    cerrables = rendimiento.candidatos_a_cerrar()
    if not cerrables:
        return "No hay nada prescindible abierto que pueda cerrar."
    nombres = ", ".join(p["nombre"] for p in cerrables[:4])
    return confirmaciones.pedir(
        f"cerrar {nombres}",
        rendimiento.cerrar_prescindibles,
    )


def _listo_para_jugar(m) -> str:
    return rendimiento.informe_para_jugar()


# ---- Juegos ----
def _abrir_juego(m) -> str:
    g = m.groupdict()
    return sistema.abrir_juego((g.get("juego") or g.get("juego2") or "").strip())


# ---- Almacenamiento ----
def _archivos_olvidados(m) -> str:
    return avanzado.archivos_olvidados()


# ---- Clic espacial ----
def _clic_relativo(m) -> str:
    g = m.groupdict()
    return pantalla.clic_relativo(
        (g.get("referencia") or "").strip(),
        (g.get("direccion") or "").strip(),
    )


# ---- Correo ----
def _correos_ultimos(m) -> str:
    cuantos = _numero(m.groupdict().get("cuantos"), 3)
    return correo.ultimos_correos(cuantos, con_cuerpo=False)


def _correos_leidos(m) -> str:
    cuantos = _numero(m.groupdict().get("cuantos"), 3)
    return correo.ultimos_correos(cuantos, con_cuerpo=True)


def _correos_sin_leer(m) -> str:
    return correo.correos_sin_leer()


def _correo_uno(m) -> str:
    return correo.leer_correo(_numero(m.groupdict().get("cual"), 1))


def _correos_de(m) -> str:
    g = m.groupdict()
    quien = (g.get("quien") or g.get("quien2") or "").strip()
    return correo.buscar_correos(quien)


# ---- Pantalla ----
def _pantalla_leer(m) -> str:
    return pantalla.leer_pantalla()


def _pantalla_buscar(m) -> str:
    return pantalla.buscar_en_pantalla(m.group("texto").strip())


def _pantalla_clic(m) -> str:
    g = m.groupdict()
    texto = (g.get("texto") or g.get("texto2") or "").strip()
    return pantalla.clic_en(texto)


def _pantalla_describir(m) -> str:
    """
    El modelo de vision tarda mas de lo que Alexa aguanta, asi que ni se
    intenta en directo: va al fondo y se recoge con "como quedo lo ultimo".
    """
    pregunta = (m.groupdict().get("pregunta") or "").strip()
    tareas.lanzar_en_segundo_plano(
        "describir la pantalla",
        lambda: pantalla.describir_pantalla(pregunta),
    )
    return ("Estoy mirando la pantalla. Tarda unos segundos: "
            "pregúntame cómo quedó lo último y te lo cuento.")


# ---- Teams ----
def _teams_abrir(m) -> str:
    g = m.groupdict()
    seccion = (g.get("seccion") or g.get("seccion2") or "").strip()
    return teams.abrir(seccion)


def _teams_canal(m) -> str:
    return teams.abrir_canal(m.group("canal").strip())


def _teams_leer(m) -> str:
    return teams.leer_lo_visible()


def _teams_actividad(m) -> str:
    return teams.resumen_actividad()


_NUMEROS_HABLADOS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "primer": 1, "primero": 1, "segundo": 2, "tercero": 3, "tercer": 3,
}


def _numero(crudo, defecto: int) -> int:
    """Convierte '3' o 'tres' en 3. Alexa manda ambas cosas indistintamente."""
    if not crudo:
        return defecto
    texto = str(crudo).strip().lower()
    if texto.isdigit():
        return int(texto)
    return _NUMEROS_HABLADOS.get(texto, defecto)


# ---- Razonar sobre lo que se ve ----
def _clic_intencion(m) -> str:
    """"dale a jugar", "dale al buscador": por funcion, no por nombre."""
    g = m.groupdict()
    crudo = (g.get("que") or "").strip().lower()

    # Se traduce lo que dijiste a una de las intenciones que el OCR conoce.
    # No es un diccionario de sinonimos: es que "dale a play" y "dale a
    # iniciar" quieren la misma cosa y la app decide como se llama.
    equivalencias = {
        "jugar": "jugar", "play": "jugar", "iniciar": "jugar",
        "empezar": "jugar", "comenzar": "jugar", "arrancar": "jugar",
        "buscar": "buscar", "buscador": "buscar", "el buscador": "buscar",
        "lupa": "buscar", "la lupa": "buscar",
        "busqueda": "buscar", "la busqueda": "buscar", "búsqueda": "buscar",
        "aceptar": "aceptar", "ok": "aceptar", "continuar": "aceptar",
        "siguiente": "aceptar", "permitir": "aceptar", "confirmar": "aceptar",
        "cerrar": "cerrar", "cancelar": "cerrar", "descartar": "cerrar",
        "enviar": "enviar", "mandar": "enviar", "publicar": "enviar",
        "descargar": "descargar", "bajar": "descargar",
    }
    intencion = equivalencias.get(crudo)
    if not intencion:
        for clave, valor in equivalencias.items():
            if clave in crudo:
                intencion = valor
                break

    if not intencion:
        # No es una funcion conocida: sera un texto literal de la pantalla.
        return pantalla.clic_en(crudo)

    return pantalla.clic_por_intencion(intencion)


def _arrancar_partida(m) -> str:
    juego = (m.groupdict().get("juego") or "").strip()
    return sistema.arrancar_partida(juego)


# ---- Seleccion de archivos ----
def _sel_entrar(m) -> str:
    return seleccion.entrar_en(m.group("carpeta").strip())


def _sel_coger(m) -> str:
    g = m.groupdict()
    cantidad = _numero(g.get("cantidad"), 0)
    criterio = (g.get("criterio") or g.get("criterio2") or "").strip()

    # "los 3 mas recientes" y "los 3 primeros" no son lo mismo, y la
    # diferencia importa: en Descargas el orden alfabetico y el de fecha no
    # se parecen en nada.
    recientes = bool(g.get("recientes"))

    return seleccion.seleccionar(criterio, cantidad, recientes)


def _sel_que_hay(m) -> str:
    return seleccion.que_hay()


def _sel_titulos(m) -> str:
    return seleccion.leer_titulos()


def _sel_mover(m) -> str:
    return seleccion.mover_a(m.group("destino").strip())


def _sel_soltar(m) -> str:
    seleccion.olvidar()
    return "Solté la selección."


def _sel_archivar(m) -> str:
    cuantos = seleccion.contar()
    if not cuantos:
        return "No tengo nada seleccionado que archivar."
    return _al_fondo(
        f"archivar {cuantos} archivos en la bóveda",
        seleccion.archivar_en_boveda,
        f"Voy a repartir esos {cuantos} por la bóveda, mirando uno a uno dónde "
        "encaja cada cual. Pregúntame cómo quedó lo último.",
    )


def _pide_concrecion(m) -> str:
    return "No te seguí. Dime la orden completa."


# ---- Ordenes que se entendian al reves (registro del 22 de agosto) ----
def _buscar_en_app(m):
    """
    "busca tame impala en spotify" -> dentro de Spotify, no en Google.

    Devuelve None a proposito cuando la app no tiene buscador propio. Ese
    None hace que enrutar() siga probando patrones, y el siguiente que
    encaja es el de busqueda web, que es lo correcto para "busca vuelos en
    diciembre".
    """
    g = m.groupdict()
    consulta = (g.get("consulta") or "").strip()
    app = (g.get("app") or "").strip()

    if not consulta or not app:
        return None
    if not sistema.app_tiene_buscador(app):
        return None

    return sistema.buscar_en_app(consulta, app)


def _archivos_grandes(m) -> str:
    """
    "qué archivo tiene más memoria" contestaba con el porcentaje de RAM.

    "Memoria" en boca de alguien que pregunta por un ARCHIVO es espacio en
    disco. El bloque de metricas se lo quedaba por la palabra suelta.
    """
    cantidad = _numero((m.groupdict().get("cantidad") or ""), 3)
    return sistema.archivos_mas_grandes(cantidad)


def _pestana_y_sitio(m) -> str:
    """
    "abre una pestaña y entra a claude" -> abre claude.

    Antes se tragaba la frase entera como si fuera el nombre de un programa
    y contestaba "Abriendo un pestaña y entra a cloud", que ademas de no
    hacer nada era mentira.
    """
    return navegador.abrir_sitio(m.group("sitio").strip())


def _modo_desconocido(m) -> str:
    """
    "apaga el modo voz baja" intentaba cerrar un programa llamado asi.

    Cualquier "modo X" que no reconozcamos se contesta diciendo cuales hay,
    en vez de tratarlo como una aplicacion.
    """
    return modes.describir_modo()


def _ayuda(m) -> str:
    return (
        "Puedo crear y editar archivos, abrir programas, buscar en Comet, "
        "escribir con el teclado, darte el estado del equipo y cambiar entre "
        "modo normal, dedicado y gaming. Dime qué necesitas."
    )



# ---- Avanzado ----














# ---- Obsidian ----














# =========================================================================
# TABLA DE INTENTS
# =========================================================================
# El orden importa muchísimo: lo más específico va primero. Por ejemplo,
# "busca el archivo X" tiene que ir ANTES que "busca X", o toda búsqueda de
# archivo terminaría abriendo el navegador.

_F = re.IGNORECASE


# -------------------------------------------------------------------------
# PIEZAS REUTILIZABLES
# -------------------------------------------------------------------------
# Una misma orden se dice de muchas formas. En vez de escribir a mano cada
# variante dentro de cada patron, definimos aqui los grupos de sinonimos y
# los insertamos donde hagan falta. Anadir una forma nueva es tocar una linea.
#
# Incluimos imperativo (crea), subjuntivo (cree) e infinitivo (crear) porque
# la invocacion "dile a X que ..." induce subjuntivo y "puedes ..." infinitivo.

def _alt(*formas: str) -> str:
    """Construye un grupo no capturador con todas las formas dadas."""
    return "(?:" + "|".join(formas) + ")"


# ---- Verbos ----
V_CREAR = _alt(
    "crea", "cree", "crees", "crear", "cr[eé]ame", "cr[eé]eme", "creame", "creeme",
    "h[aá]zme", "hazme", "haz", "haga", "hagas", "hacer", "h[aá]game",
    "gen[eé]rame", "generame", "gen[eé]reme", "genereme", "genera", "genere", "generar",
    "constr[uú]yeme", "construye", "construya", "construir",
    "gu[aá]rdame", "guarda", "guarde", "guardar", "nuevo", "nueva",
)
V_LEER = _alt(
    "lee", "lea", "leer", "l[eé]eme", "leeme", "l[eé]ame",
    "mu[eé]strame", "muestra", "muestre", "mostrar", "ens[eé][nñ]ame", "ensena",
    "[aá]breme", "abre", "abra", "dime", "diga", "d[ií]game",
)
V_EDITAR = _alt(
    "edita", "edite", "editar", "modifica", "modifique", "modificar",
    "actualiza", "actualice", "actualizar", "cambia", "cambie", "cambiar",
    "corrige", "corrija", "corregir",
)
V_AGREGAR = _alt(
    "agrega", "agregue", "agregar", "a[nñ]ade", "a[nñ]ada", "a[nñ]adir", "anade", "aniade", "aniada", "aniadir",
    "suma", "sume", "mete", "meta", "meter", "pon", "ponga", "poner", "escribe", "escriba",
    "incluye", "incluya",
)
V_REEMPLAZAR = _alt(
    "reemplaza", "reemplace", "reemplazar", "sustituye", "sustituya", "sustituir",
    "cambia", "cambie", "cambiar",
)
V_MOVER = _alt(
    "mueve", "mueva", "mover", "mu[eé]veme", "manda", "mande", "mandar",
    "env[ií]a", "env[ií]e", "enviar", "pasa", "pase", "pasar", "lleva", "lleve", "llevar",
)
V_COPIAR = _alt("copia", "copie", "copiar", "c[oó]piame", "duplica", "duplique", "duplicar")
V_ELIMINAR = _alt(
    "elimina", "elimine", "eliminar", "borra", "borre", "borrar",
    "quita", "quite", "quitar", "tira", "tire", "desecha", "deseche",
)
V_LISTAR = _alt(
    "lista", "liste", "listar", "mu[eé]strame", "muestra", "muestre",
    "dime", "diga", "d[ií]game", "dame", "d[eé]me", "ens[eé][nñ]ame", "ver", "veamos",
)
V_BUSCAR = _alt(
    "busca", "busque", "buscar", "b[uú]scame", "encuentra", "encuentre", "encontrar",
    "localiza", "localice", "localizar", "ubica", "ubique", "hallar", "halla",
)
V_INVESTIGAR = _alt(
    "busca", "busque", "buscar", "b[uú]scame", "investiga", "investigue", "investigar",
    "averigua", "averig[uü]e", "averiguar", "consulta", "consulte", "consultar",
    "google[ae]?", "googlea", "mira", "mire",
)
V_ABRIR = _alt(
    "abre", "abra", "abrir", "[aá]breme", "inicia", "inicie", "iniciar",
    "ejecuta", "ejecute", "ejecutar", "lanza", "lance", "lanzar",
    "arranca", "arranque", "arrancar", "pon", "ponga", "poner", "entra", "entre",
)
V_CERRAR = _alt(
    "cierra", "cierre", "cerrar", "ci[eé]rrame", "mata", "mate", "matar",
    "termina", "termine", "terminar", "finaliza", "finalice",
    "apaga", "apague", "quita", "quite", "detiene", "detenga", "det[eé]n",
)
V_ESCRIBIR = _alt(
    "escribe", "escriba", "escribir", "teclea", "teclee", "teclear",
    "redacta", "redacte", "redactar", "dicta", "dicte", "pon", "ponga",
)
V_CAPTURAR = _alt(
    "toma", "tome", "tomar", "haz", "haga", "hazme", "hacer",
    "saca", "saque", "sacar", "captura", "capture", "capturar",
)
V_PRESIONAR = _alt(
    "presiona", "presione", "presionar", "pulsa", "pulse", "pulsar",
    "oprime", "oprima", "aprieta", "apriete", "haz", "haga", "dale",
)
V_ACTIVAR = _alt(
    "activa", "active", "activar", "pon", "ponga", "poner", "cambia\\s+a", "cambie\\s+a",
    "entra\\s+en", "entre\\s+en", "pasa\\s+a", "pase\\s+a", "quiero",
)

# ---- Conectores ----
ART = r"(?:un[ao]?s?\s+|el\s+|la\s+|los\s+|las\s+|mi\s+|mis\s+)?"
NOMBRADO = r"(?:llamad[oa]s?\s+|nombrad[oa]\s+|de\s+nombre\s+|con\s+(?:el\s+)?nombre\s+|que\s+se\s+llame\s+)?"
# Al CREAR, "nota" es un sustantivo generico valido ("crea una nota").
SUST_ARCHIVO = r"(?:archivos?|documentos?|scripts?|ficheros?|notas?)"

# Al REFERIRSE a un archivo existente NO incluimos "nota": chocaria con
# nombres de archivo reales como "notas.txt", y el patron se comeria el
# nombre creyendo que era el sustantivo.
SUST_REF = r"(?:archivos?|documentos?|scripts?|ficheros?)"
CARPETA_DEST = r"(?:escritorio|descargas|documentos)"
EN_CARPETA = rf"(?:\s+(?:en|dentro\s+de|a|hacia)\s+(?:el\s+|la\s+|las\s+|los\s+)?(?:carpeta\s+)?(?P<carpeta>{CARPETA_DEST}))?"
CONTENIDO = (
    r"(?:\s*(?:con|que|y\s+que)\s+(?:el\s+|la\s+|un\s+)?"
    r"(?:c[oó]digo|texto|contenido|diga|digas|contenga|tenga|ponga|incluya)?\s*"
    r"(?P<contenido>.+?))?"
)
COLA = r"(?:\s+(?:escrit[oa]s?\s+|puesto\s+|metido\s+)?a?dentro)?$"
TIPO = r"(?:(?P<tipo>documento\s+word|hoja\s+de\s+c[aá]lculo|python|texto|word|excel|markdown|json|csv|html|javascript)\s+)?"
TIPO2 = r"(?:(?:de\s+)?(?P<tipo2>python|texto|word|excel|markdown|json|csv|html|javascript)\s+)?"



# ---- Avanzado ----
def _informe(m) -> str:
    guardar = bool(re.search(r"\bguard|\bescrib|\barchivo\b", m.group(0), re.I))
    return avanzado.informe_completo(guardar=guardar)


def _info_equipo(m) -> str:
    return avanzado.info_equipo()


def _buscar_contenido(m) -> str:
    return avanzado.buscar_en_contenido(m.group("texto").strip())


def _explorar(m) -> str:
    return avanzado.explorar_carpeta((m.groupdict().get("carpeta") or "escritorio").strip())


def _recientes(m) -> str:
    dias = m.groupdict().get("dias")
    return avanzado.archivos_recientes(int(dias) if dias else 7)


def _donde_contexto(m) -> str:
    return avanzado.donde_esta_contexto()


def _recordar(m) -> str:
    return avanzado.editar_contexto(m.group("dato").strip())


# ---- Obsidian ----
def _obs_diario(m) -> str:
    return obsidian.agregar_al_diario(m.group("contenido").strip())


def _obs_nota(m) -> str:
    g = m.groupdict()
    return obsidian.crear_nota(g.get("titulo", "").strip(), (g.get("contenido") or "").strip())


def _obs_agregar(m) -> str:
    return obsidian.agregar_a_nota(m.group("titulo").strip(), m.group("contenido").strip())


def _obs_buscar(m) -> str:
    # El texto puede venir por cualquiera de las dos formas de decirlo:
    # "busca X en mis notas" o "busca en mis notas X".
    g = m.groupdict()
    texto = (g.get("texto") or g.get("texto2") or "").strip()
    return obsidian.buscar_en_vault(texto)


def _obs_estado(m) -> str:
    return obsidian.estado_vault()


def _eliminar_varios(m) -> str:
    g = m.groupdict()
    return archivos.eliminar_varios(
        g.get("patron", "").strip(),
        (g.get("carpeta") or "").strip(),
    )


def _cerrar_todo(m) -> str:
    return confirmaciones.pedir("cerrar todos los programas", sistema.cerrar_todo)


# =========================================================================
# TABLA DE INTENTS
# =========================================================================
# El orden importa muchisimo: lo mas especifico va primero. Por ejemplo,
# "busca el archivo X" tiene que ir ANTES que "busca X", o toda busqueda de
# archivo terminaria abriendo el navegador.

# Formas de introducir el CONTENIDO de un mensaje. La lista es larga porque
# aqui cada variante que falte no degrada la orden: la manda entera al bloque
# de archivos, que interpreta "manda un mensaje a familia" como mover un
# fichero llamado "un mensaje" a una carpeta llamada "familia".
CONECTOR_TEXTO = r"(?:que|qu[eé]\s+diga|dici[eé]ndole(?:\s+que)?|diciendo(?:\s+que)?|escribiendo(?:\s+que)?|escr[ií]bele(?:\s+que)?|con\s+el\s+texto|con\s+el\s+mensaje|el\s+texto|para\s+decirle(?:\s+que)?|av[ií]sale(?:\s+que)?|:)"


# Prefijo para los informes del equipo. Sin el, hablar de un producto por su
# nombre disparaba el informe del componente propio: "compara rtx 3050 y rtx
# 4060" contestaba con la temperatura de TU grafica.
SOBRE_MI_EQUIPO = (
    r"^(?!.*\b(?:compara|comparativa|comparar|precios?|cu[aá]nto\s+cuesta|"
    r"cu[aá]l\s+es\s+mejor|mejor\s+opci[oó]n|recomiendas|investiga|"
    r"vale\s+la\s+pena|deber[ií]a\s+comprar)\b)[\s\S]*"
)


INTENTS: list[tuple[re.Pattern, callable]] = [
    # ------------------------------------------------------------------
    # ORDENES VACIAS  (lo primero de todo)
    # ------------------------------------------------------------------
    # Un "no" suelto no es una orden. Antes iba al modelo, y el modelo de 3B
    # se sentia obligado a llamar a alguna herramienta: contesto un "no" con
    # cambiar_modo("normal"). Cortarlo aqui cuesta un microsegundo y evita
    # que Jarvis toque el equipo por un monosilabo.
    (
        re.compile(r"^\s*(?:no|s[ií]|ok|okay|vale|bueno|claro|eso|ese|esa|"
                   r"aja|ah[aá]|mmm|este|pues|ya|nada|que|c[oó]mo|eh)\s*$", _F),
        _pide_concrecion,
    ),

    # ------------------------------------------------------------------
    # FOCO DE SESION  (pronombres: dependen del turno anterior)
    # ------------------------------------------------------------------
    # Van pronto a proposito. "cierralo" no encaja en ningun patron de app
    # porque no nombra ninguna, asi que sin esto acababa en el modelo: seis
    # segundos y medio para resolver algo que esta en una variable.
    (
        re.compile(r"^\s*(?:ci[eé]rr(?:a|e)(?:lo|la|los|las|melo)?|"
                   r"c[eé]rrame\s+(?:eso|ese|esa)|"
                   r"(?:ci[eé]rra|cierre)\s+(?:eso|ese|esa|el\s+de\s+antes))\s*$", _F),
        _foco_cerrar,
    ),
    (
        re.compile(r"^\s*(?:(?:l[eé]e|lee|abre|[aá]brelo|mu[eé]strame)(?:lo|la|melo|mela)?|"
                   r"(?:l[eé]e|abre|muestra)\s+(?:eso|ese|esa|el\s+mismo))\s*$", _F),
        _foco_leer,
    ),
    (
        re.compile(r"^\s*(?:b[oó]rr(?:a|e)(?:lo|la|melo)?|elimin(?:a|e)(?:lo|la)?|"
                   r"(?:borra|elimina)\s+(?:eso|ese|esa))\s*$", _F),
        _foco_eliminar,
    ),
    (
        re.compile(r"^\s*(?:abre|lee|l[eé]eme|mu[eé]strame|dame|quiero)\s+"
                   r"(?:el|la)\s+(?P<cual>primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|"
                   r"quint[oa]|[uú]ltim[oa]|[1-5])\s*$", _F),
        _foco_ordinal,
    ),

    # ------------------------------------------------------------------
    # SELECCION DE ARCHIVOS
    # ------------------------------------------------------------------
    # Va ANTES del bloque de archivos: comparte los verbos "mueve", "entra"
    # y "selecciona", pero aqui operan sobre un conjunto ya elegido, no sobre
    # un archivo con nombre. Si fuera despues, "muevelos a documentos"
    # buscaria un archivo llamado "los".

    # "entra a descargas", "metete en la carpeta parciales"
    (
        re.compile(r"\b(?:entra|entre|m[eé]tete|ve|vete|abre)\s+(?:a|en|al|dentro\s+de)\s+"
                   r"(?:la\s+)?(?:carpeta\s+)(?P<carpeta>.+?)\s*$|"
                   r"\b(?:entra|entre|m[eé]tete)\s+(?:a|en|al)\s+"
                   r"(?:la\s+carpeta\s+|mis\s+)?(?P<carpeta2>descargas|escritorio|"
                   r"documentos|desktop|downloads|documents)\s*$", _F),
        lambda m: seleccion.entrar_en((m.groupdict().get("carpeta")
                                       or m.groupdict().get("carpeta2") or "").strip()),
    ),

    # "selecciona los 3 primeros", "coge los cinco primeros archivos"
    (
        re.compile(r"\b(?:selecciona|seleccione|coge|coja|marca|agarra|escoge)\s+"
                   r"(?:los?\s+|las?\s+)?(?P<cantidad>\d{1,2}|un[oa]|dos|tres|cuatro|cinco|"
                   r"seis|siete|ocho|nueve|diez)\s+"
                   r"(?:(?P<recientes>m[aá]s\s+recientes|[uú]ltimos?)|primeros?|de\s+arriba)"
                   r"(?:\s+archivos?)?\s*$", _F),
        _sel_coger,
    ),

    # "selecciona todos los pdf", "selecciona los word", "coge las imagenes"
    (
        re.compile(r"\b(?:selecciona|seleccione|coge|coja|marca|agarra|escoge)\s+"
                   r"(?:todos?\s+|todas?\s+)?(?:los?\s+|las?\s+)?"
                   r"(?:archivos?\s+)?"
                   # "los que digan calculo": lo que importa es lo de despues.
                   r"(?:que\s+(?:digan?|tengan?|se\s+llamen?|contengan?)\s+)?"
                   r"(?P<criterio>[a-z0-9\s.]+?)\s*$", _F),
        _sel_coger,
    ),

    # "olvida la seleccion", "suelta eso"
    (
        re.compile(r"\b(?:olvida|suelta|deselecciona|quita)\s+(?:la\s+)?"
                   r"(?:selecci[oó]n|seleccionados?|eso)\b", _F),
        _sel_soltar,
    ),

    # "que tengo seleccionado"
    (
        re.compile(r"\bqu[eé]\s+(?:tengo|hay|tienes)\s+(?:seleccionad[oa]s?|cogid[oa]s?|marcad[oa]s?)\b|"
                   r"\bcu[aá]ntos\s+(?:tengo\s+)?seleccionad[oa]s?\b|"
                   r"\bla\s+selecci[oó]n\b\s*$", _F),
        _sel_que_hay,
    ),

    # "leeme los titulos", "como se llaman"
    (
        re.compile(r"\b(?:l[eé]e(?:me)?|dime|di)\s+(?:los\s+)?"
                   r"(?:t[ií]tulos|nombres)(?:\s+de\s+(?:los|la\s+selecci[oó]n))?\s*$|"
                   r"\bc[oó]mo\s+se\s+llaman\b\s*$", _F),
        _sel_titulos,
    ),

    # "muevelos a documentos", "pasalos a la carpeta parciales"
    (
        re.compile(r"\b(?:mu[eé]ve|mueva|pasa|lleva|manda|met[eé])(?:los|las|lo|me)?\s+"
                   r"(?:a|al|hacia|para)\s+(?:la\s+carpeta\s+|mis\s+|el\s+|la\s+)?"
                   r"(?P<destino>.+?)\s*$", _F),
        _sel_mover,
    ),

    # "archivalos en la boveda"
    (
        re.compile(r"\b(?:arch[ií]va|archive|guarda|guarde|mete|sube)(?:los|las|lo)?\s+"
                   r"(?:en|a|al)\s+(?:la\s+|mi\s+)?(?:b[oó]veda|vault|obsidian)\b", _F),
        _sel_archivar,
    ),

    # "juega valorant", "abre epic y dale a jugar", "ponte a jugar lol"
    # Un lanzador no es el juego: abrirlo deja una tienda con un boton.
    (
        re.compile(rf"\b(?:juega|jugar|juguemos|ponte\s+a\s+jugar|"
                   r"echemos\s+(?:una|un)\s+\w+\s+(?:a|de)|"
                   r"arranca(?:me)?\s+(?:una\s+partida\s+(?:de|a)\s+)?)"
                   r"\s*(?:a\s+|al\s+)?(?P<juego>[a-z0-9\s]+?)\s*$", _F),
        _arrancar_partida,
    ),
    (
        re.compile(rf"\b{V_ABRIR}\s+(?P<juego>epic(?:\s+games)?|valorant|steam|"
                   r"riot|league|lol)\s+(?:y\s+)?(?:dale?\s+(?:a|al)\s+)?"
                   r"(?:jugar|play|iniciar|empezar)\s*$", _F),
        _arrancar_partida,
    ),

    # "dale a jugar", "dale al buscador", "pincha en aceptar"
    # Por lo que HACE el boton, no por como se llama en esta app concreta.
    (
        re.compile(r"\b(?:dale?|pincha|pulsa|presiona|haz\s+clic|click|clic)\s+"
                   r"(?:en\s+|a\s+|al\s+)?(?:el\s+|la\s+|los\s+|las\s+)?"
                   r"(?:bot[oó]n\s+(?:de\s+)?)?"
                   r"(?P<que>jugar|play|iniciar|empezar|comenzar|arrancar|"
                   r"buscar|buscador|el\s+buscador|lupa|la\s+lupa|b[uú]squeda|la\s+b[uú]squeda|"
                   r"aceptar|ok|continuar|siguiente|permitir|confirmar|"
                   r"cerrar|cancelar|descartar|enviar|mandar|publicar|"
                   r"descargar|bajar)\s*$", _F),
        _clic_intencion,
    ),

    # ------------------------------------------------------------------
    # ORDENES QUE SE ENTENDIAN AL REVES
    # ------------------------------------------------------------------
    # Todas estas salieron del registro del 22 de agosto. En los ocho casos
    # el patron que se las quedaba era demasiado generico y hacia algo
    # distinto de lo pedido, que es peor que no entender: no entender se
    # nota y se repite; hacer otra cosa parece que funciono.
    #
    # Van aqui arriba porque compiten contra bloques muy golosos: "busca",
    # "memoria", "apaga" y "abre" estan cada uno en tres sitios.

    # "apaga el modo voz baja" intentaba cerrar un programa con ese nombre.
    # Cualquier "modo X" desconocido se contesta enumerando los que hay.
    (
        re.compile(r"\b(?:apaga|apague|quita|quite|desactiva|desactive|activa|"
                   r"active|pon|ponga|cambia\s+a)\s+(?:el\s+)?modo\s+"
                   r"(?!normal|dedicado|gaming|juego|basico|ligero|estandar|"
                   r"rapido|avanzado|potente|pro|inteligente|razonamiento)"
                   r"[a-z\s]{2,30}$", _F),
        _modo_desconocido,
    ),

    # "que archivo tiene mas memoria" daba el porcentaje de RAM. Quien
    # pregunta por un ARCHIVO habla de disco.
    (
        re.compile(r"\b(?:qu[eé]|cu[aá]l(?:es)?)\s+(?:son\s+)?(?:los\s+|las\s+|el\s+|la\s+)?"
                   r"(?P<cantidad>\d{1,2}|tres|cinco|cuatro)?\s*"
                   r"archivos?\s+(?:que\s+)?(?:tiene[n]?|ocupa[n]?|pesa[n]?|usa[n]?|"
                   r"consume[n]?|es|son)\s+(?:el\s+|la\s+|los\s+|las\s+)?"
                   r"(?:m[aá]s|mayor(?:es)?|m[aá]ximo)\s+"
                   r"(?:memoria|espacio|peso|tama[nñ]o|disco|grande[s]?)\b|"
                   r"\barchivos?\s+m[aá]s\s+(?:grandes?|pesados?)\b|"
                   r"\bqu[eé]\s+(?:me\s+)?(?:est[aá]\s+)?ocupa(?:ndo)?\s+m[aá]s\s+"
                   r"(?:espacio|disco)\b", _F),
        _archivos_grandes,
    ),

    # "busca tame impala en spotify" buscaba en Google. Y "abre spotify y
    # busca tame impala" abria Spotify y buscaba en Google, que es peor.
    # El manejador devuelve None si la app no tiene buscador propio, y
    # entonces esto cae solo a la busqueda web de mas abajo.
    (
        re.compile(rf"\b{V_BUSCAR}(?:me)?\s+(?P<consulta>.+?)\s+en\s+"
                   r"(?:el\s+|la\s+|mi\s+)?(?P<app>spotify|spoti|espotifai|obsidian|"
                   r"obsidiana|teams|discord|steam|code|vs\s*code|whatsapp|"
                   r"telegram|explorador|explorer)\s*$", _F),
        _buscar_en_app,
    ),
    (
        re.compile(rf"\b{V_ABRIR}\s+(?:el\s+|la\s+|mi\s+)?(?P<app>spotify|spoti|espotifai|"
                   r"obsidian|obsidiana|teams|discord|steam|code|vs\s*code|whatsapp|"
                   r"telegram)\s+(?:y|e)\s+"
                   rf"(?:{V_BUSCAR}|busco|buscame|b[uú]squeme|pon|ponme|reproduce)\s+"
                   r"(?P<consulta>.+?)\s*$", _F),
        _buscar_en_app,
    ),

    # "abre 1 pestaña y entra a claude" -> Alexa lo oye "cloud". Antes se
    # tragaba la frase entera como nombre de programa y contestaba
    # "Abriendo un pestaña y entra a cloud" sin abrir nada.
    (
        re.compile(rf"\b{V_ABRIR}\s+(?:una?\s+|1\s+)?(?:pesta[nñ]a|ventana|tab|p[aá]gina)"
                   r"(?:\s+nueva)?\s*(?:en\s+(?:el\s+)?"
                   r"(?:comet|comic|c[oó]mic|comix|navegador|chrome|brave|edge|firefox))?"
                   r"\s*(?:y|e)\s+(?:entra|entre|ve|vete|anda|m[eé]tete|navega|vamos)"
                   r"\s+(?:a|en|al|hacia)\s+(?:la\s+)?(?:p[aá]gina\s+(?:de\s+)?)?"
                   r"(?P<sitio>.+?)\s*$", _F),
        _pestana_y_sitio,
    ),

    # "busca que" / "busca eso": Alexa cortó la frase. Pedir que la repita
    # es mejor que buscar la palabra "que" en internet, que es lo que hacia.
    (
        re.compile(r"^\s*(?:busca|b[uú]scame|busque|buscar|investiga)\s+"
                   r"(?:que|qu[eé]|eso|esto|aquello|algo|lo|una?\s+cosa)\s*$", _F),
        _pide_concrecion,
    ),

    # ------------------------------------------------------------------
    # MEMORIA SEMANTICA, ARCHIVAR Y PLANIFICAR
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:indexa|reindexa|actualiza|rehaz)\s+"
                   r"(?:(?:la|el|mi|mis|toda\s+la|todo\s+el)\s+)?"
                   r"(?:b[oó]veda|vault|memoria(?:\s+sem[aá]ntica)?|notas)\b"
                   r"(?:.*?(?P<todo>desde\s+cero|completa|entera|todo))?", _F),
        _memoria_indexar,
    ),
    (
        re.compile(r"\bc[oó]mo\s+(?:va|est[aá])\s+(?:la\s+|tu\s+)?memoria\s+"
                   r"(?:sem[aá]ntica|de\s+la\s+b[oó]veda|del\s+vault|de\s+notas)\b|"
                   r"\bestado\s+de\s+(?:la\s+)?(?:memoria\s+sem[aá]ntica|"
                   r"la\s+memoria\s+de\s+la\s+b[oó]veda)\b|"
                   r"\bc[oó]mo\s+va\s+(?:el\s+)?[ií]ndice\b", _F),
        _memoria_estado,
    ),
    (
        re.compile(r"\bqu[eé]\s+s[eé]\s+(?:yo\s+)?(?:de|sobre)\s+(?P<q>.+?)\s*$|"
                   r"\bbusca\s+por\s+significado\s+(?P<q2>.+?)\s*$|"
                   r"\bqu[eé]\s+recuerdas?\s+(?:de|sobre)\s+(?P<q3>.+?)\s*$", _F),
        _memoria_buscar,
    ),
    (
        re.compile(r"\b(?:archiva|guarda|mete|clasifica)\s+"
                   r"(?:el\s+|la\s+|los\s+|las\s+)?(?:archivo\s+|documento\s+)?"
                   r"(?P<que>.+?)\s+en\s+(?:la\s+)?(?:b[oó]veda|obsidian|vault)\s*$|"
                   r"\barchiva(?:me)?\s+(?P<que2>.+?)\s*$", _F),
        _archivar,
    ),
    (
        re.compile(r"\b(?:pr[eé]parame|org[aá]nizame|enc[aá]rgate\s+de|"
                   r"ocupate\s+de|hazme\s+todo\s+lo\s+de)\s+(?P<orden>.+?)\s*$|"
                   r"\bhaz\s+un\s+plan\s+(?:para|de)\s+(?P<orden2>.+?)\s*$", _F),
        _planificar,
    ),
    (
        re.compile(r"\ben\s+qu[eé]\s+(?:puedes|podr[ií]as)\s+mejorar\b|"
                   r"\bqu[eé]\s+te\s+(?:cuesta|atraganta)\b|"
                   r"\brevisa\s+tu\s+historial\b", _F),
        _aprendizaje_propuestas,
    ),
    (
        re.compile(r"\bqu[eé]\s+tan\s+r[aá]pido\s+(?:eres|vas)\b|"
                   r"\bcu[aá]ntas\s+[oó]rdenes\s+llevas\b|"
                   r"\btus\s+estad[ií]sticas\b", _F),
        _aprendizaje_resumen,
    ),

    # ------------------------------------------------------------------
    # MODOS  (frases cortas y muy usadas: van primero de todo)
    # ------------------------------------------------------------------
    (re.compile(r"\b(?:sal(?:ir|te|ga)?|quita|quite|desactiva|desactive|termina|termine)\s+(?:de(?:l)?\s+)?(?:el\s+)?(?:modo\s+)?(?:juego|gaming|dedicado)\b", _F), _modo_normal),
    (re.compile(rf"\bmodo\s+(?:de\s+)?gaming\b|\bmodo\s+juego\b|\b{V_ACTIVAR}\s+(?:el\s+)?juego\b|\bme\s+voy\s+a\s+jugar\b|\bvoy\s+a\s+jugar\b", _F), _modo_gaming),
    (re.compile(r"\bmodo\s+dedicado\b|\bmodo\s+(?:avanzado|potente|pro|inteligente)\b|\bmodelo\s+grande\b|\busa\s+la\s+gr[aá]fica\b|\bmodo\s+razonamiento\b", _F), _modo_dedicado),
    (re.compile(r"\bmodo\s+normal\b|\bmodo\s+(?:b[aá]sico|ligero|est[aá]ndar|r[aá]pido)\b|\bvuelve\s+a\s+la\s+normalidad\b", _F), _modo_normal),
    (re.compile(r"\b(?:en\s+)?qu[eé]\s+modo\s+est[aá]s\b|\bcu[aá]l\s+es\s+tu\s+modo\b|\bmodo\s+actual\b|\bqu[eé]\s+modo\s+tienes\b", _F), _que_modo),

    # ------------------------------------------------------------------
    # METRICAS DEL SISTEMA
    # ------------------------------------------------------------------
    (re.compile(r"\b(?:estado|resumen|c[oó]mo\s+est[aá]|c[oó]mo\s+va)\s+(?:general\s+)?(?:del?\s+)?(?:equipo|sistema|pc|computador(?:a)?|m[aá]quina|todo)\b", _F), _estado),
    (re.compile(SOBRE_MI_EQUIPO + r"\b(?:cpu|procesador|micro)\b", _F), _cpu),
    (re.compile(SOBRE_MI_EQUIPO + r"\b(?:ram|memoria)\b", _F), _ram),
    (re.compile(SOBRE_MI_EQUIPO + r"\b(?:disco|almacenamiento|espacio\s+(?:libre|en\s+disco))\b", _F), _disco),
    (re.compile(SOBRE_MI_EQUIPO + r"\b(?:gpu|gr[aá]fica|tarjeta\s+gr[aá]fica|vram|memoria\s+de\s+v[ií]deo|memoria\s+de\s+video|3050|nvidia)\b", _F), _gpu),
    (re.compile(r"\bqu[eé]\s+(?:programa|app|aplicaci[oó]n|proceso)s?\s+(?:est[aá]n?\s+)?(?:consum|gast|us|ocup)\w*|\bprocesos\s+pesados\b|\bqu[eé]\s+est[aá]\s+consumiendo\b|\bqu[eé]\s+consume\s+m[aá]s\b|\best[aá]\s+consumiendo\b|\bconsumiendo\s+m[aá]s\b|\bconsume\s+m[aá]s\s+(?:recursos|memoria|cpu)\b|\bque\s+hay\s+abierto\b", _F), _procesos),
    (re.compile(r"\bbater[ií]a\b", _F), _bateria),

    # ------------------------------------------------------------------
    # OBSIDIAN  (antes que archivos: "apunta" y "nota" son suyos)
    # ------------------------------------------------------------------
    (
        re.compile(
            r"\b(?:apunta|apunte|anota|anote|apuntame|guarda)\s+(?:en\s+(?:el\s+)?(?:diario|obsidian)\s+)?"
            r"(?:que\s+)?(?P<contenido>.+?)\s+en\s+(?:el\s+)?(?:diario|mi\s+diario|obsidian)\b|"
            r"\b(?:apunta|apunte|anota|anote)\s+en\s+(?:el\s+)?diario\s+(?:que\s+)?(?P<contenido2>.+)$",
            _F,
        ),
        lambda m: obsidian.agregar_al_diario(
            (m.groupdict().get("contenido") or m.groupdict().get("contenido2") or "").strip()
        ),
    ),
    (
        re.compile(r"\b(?:crea|cree|crear)\s+(?:una\s+)?nota\s+(?:en\s+obsidian\s+)?(?:llamada\s+|titulada\s+|sobre\s+)?(?P<titulo>.+?)(?:\s+(?:con|que\s+diga)\s+(?P<contenido>.+))?$", _F),
        _obs_nota,
    ),
    (
        re.compile(r"\b(?:agrega|agregue|a[nñ]ade|a[nñ]ada)\s+(?P<contenido>.+?)\s+a\s+la\s+nota\s+(?P<titulo>.+)$", _F),
        _obs_agregar,
    ),
    (
        re.compile(r"\b(?:busca|busque|buscar|b[uú]scame|encuentra|encuentre|mira|revisa)\s+"
                   r"(?:en\s+(?:mis\s+|el\s+|la\s+)?(?:notas|obsidian|vault|b[oó]veda|apuntes)\s+"
                   r"(?P<texto2>.+)|"
                   r"(?P<texto>.+?)\s+en\s+(?:mis\s+|el\s+|la\s+)?(?:notas|obsidian|vault|b[oó]veda|apuntes))\b", _F),
        _obs_buscar,
    ),
    (re.compile(r"\b(?:mi\s+)?(?:vault|b[oó]veda)\b|\bestado\s+de\s+obsidian\b|\bcu[aá]ntas\s+notas\s+tengo\b", _F), _obs_estado),

    # ------------------------------------------------------------------
    # BUSQUEDA PROFUNDA Y EXPLORACION
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:busca|busque|encuentra|encuentre)\s+(?P<texto>.+?)\s+(?:dentro\s+de|en\s+el\s+contenido\s+de)\s+(?:los\s+)?archivos\b|\bqu[eé]\s+archivo\s+(?:contiene|tiene|menciona)\s+(?P<texto2>.+)$", _F),
        lambda m: avanzado.buscar_en_contenido((m.groupdict().get("texto") or m.groupdict().get("texto2") or "").strip()),
    ),
    (
        re.compile(r"\b(?:explora|explore|revisa|revise|analiza|analice|qu[eé]\s+hay\s+dentro\s+de)\s+(?:la\s+carpeta\s+|el\s+)?(?P<carpeta>escritorio|descargas|documentos)\b", _F),
        _explorar,
    ),
    (
        re.compile(r"\b(?:toqu[eé]|modifiqu[eé]|modific[oó]|trabaj[eé]|cambi[eé]|edit[eé]|us[eé])\w*\s+"
            r"(?:en\s+)?(?:los\s+)?[uú]ltim[oa]s?\s+(?:(?P<dias>\d+)\s+)?d[ií]as?\b"
            r"|\ben\s+qu[eé]\s+(?:estaba|he\s+estado)\s+trabajando\b"
            r"|\barchivos\s+recientes\b"
            r"|\bqu[eé]\s+(?:he\s+)?(?:hecho|cambiado|tocado)\s+(?:estos|los)\s+[uú]ltimos\s+d[ií]as\b", _F),
        _recientes,
    ),

    # ------------------------------------------------------------------
    # EQUIPO E INFORME
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:informe|reporte)\s+(?:completo|detallado|avanzado|del\s+equipo|del\s+sistema)\b|\b(?:guarda|guarde|genera|genere)\s+(?:un\s+)?(?:informe|reporte)\b", _F),
        _informe,
    ),
    (
        re.compile(r"\bqu[eé]\s+(?:equipo|pc|computador(?:a)?|m[aá]quina)\s+(?:es\s+este|tengo|soy)\b|\bcaracter[ií]sticas\s+del?\s+(?:equipo|pc)\b|\bqu[eé]\s+hardware\b|\bqu[eé]\s+sabes\s+de\s+(?:mi|este)\s+(?:equipo|pc)\b", _F),
        _info_equipo,
    ),

    # ------------------------------------------------------------------
    # CONTEXTO PERSONAL
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:recuerda|recuerde|ten\s+en\s+cuenta|apunta\s+en\s+tu\s+contexto)\s+(?:que\s+)?(?P<dato>.+)$", _F),
        _recordar,
    ),
    (
        re.compile(r"\b(?:qu[eé]\s+)?archivos?\s+(?:he\s+|has\s+)?"
                   r"(?:tocado|editado|modificado|cambiado|usado|abierto)\s*"
                   r"(?:hoy|ultimamente|[uú]ltimamente|recientemente|"
                   r"estos\s+d[ií]as|esta\s+semana)?\b|"
                   r"\barchivos?\s+recientes?\b|\b[uú]ltimos?\s+archivos?\b|"
                   r"\ben\s+qu[eé]\s+(?:he\s+)?(?:estado\s+)?trabajando\b", _F),
        _recientes,
    ),
    (re.compile(r"\bd[oó]nde\s+est[aá]\s+(?:tu\s+|el\s+|mi\s+)?(?:archivo\s+de\s+)?contexto\b|\b(?:tu|el|mi)\s+contexto\b|\bqu[eé]\s+sabes\s+de\s+m[ií]\b|\barchivo\s+de\s+contexto\b", _F), _donde_contexto),

    # ------------------------------------------------------------------
    # CERRAR TODO  (antes de las apps, o "todo" se toma por un programa)
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:cierra|cierre|cerrar)\s+(?:todo|todos|todas)(?:\s+(?:las\s+ventanas|los\s+programas|las\s+aplicaciones|las\s+apps))?\s*$", _F),
        _cerrar_todo,
    ),

    # ------------------------------------------------------------------
    # ELIMINAR VARIOS
    # ------------------------------------------------------------------
    (
        re.compile(
            r"\b(?:elimina|elimine|borra|borre|quita|quite)\s+(?:tod[ao]s\s+)?(?:l[ao]s\s+)?(?:\d+\s+)?"
            r"(?P<patron>capturas?|im[aá]genes|fotos|archivos?\s+\w+|\w+)\s+"
            r"(?:de|del|en\s+el)\s+(?:la\s+)?(?P<carpeta>escritorio|descargas|documentos)\b",
            _F,
        ),
        _eliminar_varios,
    ),
    # Eliminar uno solo cuando el nombre lleva extension pegada ("elimine archivo.py")
    (
        re.compile(r"\b(?:elimina|elimine|borra|borre|quita|quite)\s+(?:el\s+)?(?:archivo\s+)?(?:llamado\s+)?(?P<nombre>[\w\-]+\.[a-zA-Z0-9]{1,5})(?:\s+(?:de|del)\s+(?:el\s+)?(?:escritorio|descargas|documentos))?\s*$", _F),
        _eliminar_archivo,
    ),

    # Contestando al "¿Qué le digo a X?" del turno anterior. Va en el bloque
    # del foco porque depende del turno anterior, no de la frase en si.
    (
        re.compile(r"^(?!.*\b(?:abre|cierra|lee|crea|busca|apaga|modo)\b)"
                   r"(?:dile\s+que\s+|que\s+)?(?P<texto>.{2,200})$", _F),
        _wa_texto_pendiente,
    ),

    # ------------------------------------------------------------------
    # CATALOGO DE APLICACIONES
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:actualiza|refresca|rehaz|vuelve\s+a\s+buscar|escanea)\s+"
                   r"(?:la\s+lista\s+de\s+|el\s+cat[aá]logo\s+de\s+)?"
                   r"(?:aplicaciones|apps|programas)\b|"
                   r"\bbusca\s+(?:mis\s+)?(?:aplicaciones|programas)\s+instalad[oa]s\b", _F),
        _catalogo_refrescar,
    ),
    (
        re.compile(r"\bqu[eé]\s+(?:aplicaciones|apps|programas)\s+"
                   r"(?:conoces|tengo\s+instalad[oa]s|hay\s+instalad[oa]s)\b|"
                   r"\bcu[aá]ntas\s+(?:aplicaciones|programas)\s+"
                   r"(?:conoces|tengo)\b", _F),
        _catalogo_resumen,
    ),
    (
        re.compile(r"\btengo\s+(?:instalado\s+)?(?P<que>[\w\s]{2,40}?)\s+instalado\b|"
                   r"\best[aá]\s+instalado\s+(?P<que2>.+?)\s*$", _F),
        _catalogo_tengo,
    ),

    # ------------------------------------------------------------------
    # ESTUDIO E INVESTIGACION
    # ------------------------------------------------------------------
    # Van pronto porque usan "busca", "explica" y "dime", que otros bloques
    # tambien reclaman. Aqui se distinguen por lo que las acompaña: un examen,
    # una comparacion, o las notas propias.
    (
        re.compile(r"\btengo\s+(?:un\s+)?(?:parcial|examen|prueba|quiz|final)\s+"
                   r"(?:el\s+|este\s+|la\s+)?(?P<cuando>lunes|martes|mi[eé]rcoles|"
                   r"jueves|viernes|s[aá]bado|domingo|ma[nñ]ana|hoy|"
                   r"la\s+pr[oó]xima\s+semana)?\s*(?:de\s+|sobre\s+)?(?P<tema>.+?)\s*$|"
                   r"\b(?:pr[eé]parame|ay[uú]dame\s+con)\s+(?:el\s+)?"
                   r"(?:parcial|examen)\s+de\s+(?P<tema2>.+?)\s*$", _F),
        _examen,
    ),
    (
        re.compile(r"\b(?:hazme|dame|ponme)\s+preguntas\s+(?:de\s+repaso\s+)?"
                   r"(?:de|sobre)\s+(?P<tema>.+?)\s*$|"
                   r"\bpreg[uú]ntame\s+(?:de|sobre)\s+(?P<tema2>.+?)\s*$|"
                   r"\btom[aá]me\s+la\s+lecci[oó]n\s+(?:de\s+)?(?P<tema3>.+?)\s*$", _F),
        _preguntas_repaso,
    ),
    (
        re.compile(r"\bexpl[ií]came\s+(?P<tema>.+?)\s+"
                   r"(?:con|desde|seg[uú]n|usando)\s+(?:mis\s+)?(?:notas|apuntes)\b|"
                   r"\bqu[eé]\s+dicen\s+mis\s+(?:notas|apuntes)\s+(?:de|sobre)\s+"
                   r"(?P<tema2>.+?)\s*$", _F),
        _explicar_notas,
    ),
    # Buscar por lo que la nota TRATA, no por como se llama. Es el caso
    # normal en una boveda grande: recuerdas la idea, no el titulo.
    (
        re.compile(r"\b(?:d[oó]nde\s+tengo|d[oó]nde\s+apunt[eé]|"
                   r"c[oó]mo\s+se\s+llama\s+la\s+nota\s+(?:de|sobre|donde))\s+"
                   r"(?:lo\s+de\s+|eso\s+de\s+)?(?P<desc>.+?)\s*$|"
                   r"\b(?:encuentra|busca)\s+la\s+nota\s+"
                   r"(?:de|sobre|que\s+habla\s+de)\s+(?P<desc2>.+?)\s*$", _F),
        _nota_encontrar,
    ),
    (
        re.compile(r"\b(?:abre|[aá]breme)\s+(?:la\s+)?nota\s+"
                   r"(?:de|sobre|que\s+habla\s+de)\s+(?P<desc>.+?)\s*$|"
                   r"\bll[eé]vame\s+a\s+(?:la\s+)?nota\s+de\s+(?P<desc2>.+?)\s*$", _F),
        _nota_abrir,
    ),
    (
        re.compile(r"\bqu[eé]\s+relaci[oó]n\s+hay\s+entre\s+(?P<desc>.+?)\s*$|"
                   r"\brelaciona(?:me)?\s+(?:mis\s+notas\s+(?:de|sobre)\s+)?"
                   r"(?P<desc2>.+?)\s*$|"
                   r"\bqu[eé]\s+une\s+(?:a\s+)?(?P<desc3>.+?)\s*$", _F),
        _nota_relacionar,
    ),
    (
        re.compile(r"\bqu[eé]\s+tengo\s+(?:apuntado\s+)?(?:de|sobre)\s+(?P<tema>.+?)\s*$|"
                   r"\bqu[eé]\s+notas\s+tengo\s+(?:de|sobre)\s+(?P<tema2>.+?)\s*$", _F),
        _que_tengo_de,
    ),
    (
        re.compile(r"\b(?:compara|comp[aá]rame|cu[aá]l\s+es\s+mejor|"
                   r"qu[eé]\s+conviene\s+m[aá]s)\s+(?P<consulta>.+?)\s*$|"
                   r"\bprecios?\s+de\s+(?P<consulta2>.+?)\s*$", _F),
        _comparar,
    ),
    (
        re.compile(r"\b(?:cu[aá]l\s+es\s+(?:el|la)\s+mejor|qu[eé]\s+me\s+recomiendas)\s+"
                   r"(?P<consulta>.+?)\s*$|"
                   r"\bmejor\s+opci[oó]n\s+(?:de|para)\s+(?P<consulta2>.+?)\s*$", _F),
        _mejor_opcion,
    ),
    (
        re.compile(r"\b(?:investiga|averigua|inf[oó]rmate|consulta)\s+"
                   r"(?:en\s+internet\s+)?(?:sobre\s+|acerca\s+de\s+|de\s+)?"
                   r"(?P<consulta>.+?)\s*$|"
                   r"\bqu[eé]\s+dice\s+internet\s+(?:de|sobre)\s+(?P<consulta2>.+?)\s*$", _F),
        _investigar,
    ),

    # ------------------------------------------------------------------
    # MENSAJES  (whatsapp y correo saliente)
    # ------------------------------------------------------------------
    # Van muy arriba porque llevan "manda", "escribe" y "dile", verbos que
    # otros bloques tambien usan. Y porque son las unicas ordenes que salen
    # del equipo: mas vale que las reclame quien las entiende del todo.
    (
        re.compile(r"\b(?:manda|mandale|env[ií]a|env[ií]ale|escr[ií]bele|dile|"
                   r"m[aá]ndale)\s+(?:un\s+)?(?:mensaje|whats?app|wasap|whatsapp)?\s*"
                   r"(?:a|al|a\s+la)\s+(?P<quien>.+?)\s+"
                   rf"{CONECTOR_TEXTO}\s+(?P<texto>.+?)\s*$|"
                   r"\b(?:manda|env[ií]a)\s+(?:por\s+)?(?:whats?app|wasap)\s+"
                   r"a\s+(?P<quien2>.+?)\s+(?:que\s+)?(?P<texto2>.+?)\s*$", _F),
        _wa_enviar,
    ),
    (
        re.compile(r"\b(?:manda|m[aá]ndale|env[ií]a|env[ií]ale|escr[ií]bele)\s+"
                   r"(?:un\s+|una\s+)?(?:mensaje|whats?app|wasap|recado)\s+"
                   r"(?:a|al|a\s+la)\s+(?P<quien>.+?)\s*$", _F),
        _wa_falta_texto,
    ),
    (
        re.compile(r"^\s*(?:sigue|contin[uú]a|continua|dale|ya|ya\s+est[aá]|"
                   r"ya\s+carg[oó]|m[aá]ndalo|env[ií]alo|reintenta|otra\s+vez|"
                   r"prueba\s+de\s+nuevo)\s*$", _F),
        _wa_reanudar,
    ),
    (
        re.compile(r"\b(?:abre|[aá]breme|inicia|entra\s+(?:a|en))\s+"
                   r"(?:el\s+)?whats?app(?:\s+web)?\s*$", _F),
        _wa_abrir_app,
    ),
    (
        re.compile(r"\b(?:abre|[aá]breme|ve\s+al?)\s+(?:el\s+)?(?:chat|conversaci[oó]n)\s+"
                   r"(?:de\s+|con\s+)?(?P<quien>.+?)(?:\s+en\s+whats?app)?\s*$", _F),
        _wa_abrir,
    ),
    (
        re.compile(r"\b(?:l[eé]e(?:me)?|qu[eé]\s+dice|qu[eé]\s+hay\s+en)\s+"
                   r"(?:el\s+)?(?:chat|whats?app|wasap|la\s+conversaci[oó]n)\b", _F),
        _wa_leer,
    ),
    (
        re.compile(r"\b(?:manda|env[ií]a|escribe)\s+(?:un\s+)?(?:correo|mail|email)\s+"
                   rf"a\s+(?P<quien>.+?)\s+{CONECTOR_TEXTO}\s+"
                   r"(?P<texto>.+?)\s*$|"
                   r"\bescr[ií]bele\s+(?:un\s+)?(?:correo|mail)\s+a\s+(?P<quien2>.+?)\s+"
                   r"(?:que\s+)?(?P<texto2>.+?)\s*$", _F),
        _correo_enviar,
    ),
    (
        re.compile(r"\b(?:responde|contesta|resp[oó]ndele|cont[eé]stale)\s+"
                   r"(?:al\s+|el\s+)?(?:[uú]ltimo\s+)?(?:correo|mail|email)?\s*"
                   r"(?:que|dici[eé]ndole\s+que|:)?\s*(?P<texto>.+?)\s*$", _F),
        _correo_responder,
    ),

    # ------------------------------------------------------------------
    # ARCHIVOS - CREAR
    # ------------------------------------------------------------------
    # Sin nombre: Alexa fusiona el sustantivo con la extension ("archivo.py").
    (
        re.compile(
            rf"\b{V_CREAR}\s+{ART}(?P<nombre>[\w\-]+\.[a-zA-Z0-9]{{1,5}}){EN_CARPETA}{CONTENIDO}{COLA}",
            _F,
        ),
        _crear_archivo,
    ),
    # Con nombre y contenido.
    (
        re.compile(
            rf"\b{V_CREAR}\s+{ART}{TIPO}{SUST_ARCHIVO}\s+{TIPO2}{NOMBRADO}"
            rf"(?P<nombre>.+?){EN_CARPETA}"
            rf"\s*(?:con|que|y\s+que)\s+(?:el\s+|la\s+|un\s+)?"
            rf"(?:c[oó]digo|texto|contenido|diga|digas|contenga|tenga|ponga|incluya)\s*"
            rf"(?P<contenido>.+?){COLA}",
            _F,
        ),
        _crear_archivo,
    ),
    # Con nombre, sin contenido.
    (
        re.compile(
            rf"\b{V_CREAR}\s+{ART}{TIPO}{SUST_ARCHIVO}\s+{TIPO2}{NOMBRADO}"
            rf"(?P<nombre>.+?){EN_CARPETA}$",
            _F,
        ),
        _crear_archivo,
    ),
    (
        re.compile(
            rf"\b{V_CREAR}\s+{ART}(?:carpeta|directorio|folder)\s+{NOMBRADO}(?P<nombre>.+?){EN_CARPETA}$",
            _F,
        ),
        _crear_carpeta,
    ),

    # ------------------------------------------------------------------
    # ARCHIVOS - EDITAR
    # ------------------------------------------------------------------
    (
        re.compile(
            rf"\b{V_REEMPLAZAR}\s+(?P<buscar>.+?)\s+(?:por|con)\s+(?P<reemplazar>.+?)\s+"
            rf"(?:en|dentro\s+de)\s+(?:el\s+)?(?:{SUST_REF}\s+)?(?P<nombre>.+)$",
            _F,
        ),
        _editar_reemplazar,
    ),
    (
        re.compile(
            rf"\b{V_EDITAR}\s+(?:el\s+)?(?:{SUST_REF}\s+)?"
            rf"(?P<nombre>.+?)\s+(?:y\s+)?{V_AGREGAR}\s+(?P<contenido>.+)$",
            _F,
        ),
        _editar_agregar,
    ),
    (
        re.compile(
            rf"\b{V_AGREGAR}\s+(?P<contenido>.+?)\s+"
            rf"(?:al|a\s+el|en\s+el|dentro\s+del|al\s+final\s+del)\s+(?:{SUST_REF}\s+)?(?P<nombre>.+)$",
            _F,
        ),
        _editar_agregar,
    ),

    # ------------------------------------------------------------------
    # ARCHIVOS - LEER, MOVER, COPIAR, ELIMINAR, LISTAR, BUSCAR
    # ------------------------------------------------------------------
    (
        re.compile(rf"\b{V_LEER}\s+(?:el\s+)?(?:contenido\s+del?\s+)?{SUST_ARCHIVO}\s+(?P<nombre>.+)$", _F),
        _leer_archivo,
    ),
    (re.compile(rf"\bqu[eé]\s+(?:dice|hay\s+en|contiene)\s+(?:el\s+)?(?:{SUST_REF}\s+)?(?P<nombre>[\w\-]+\.[a-zA-Z0-9]{{1,5}}|.+?\s+punto\s+\w+)$", _F), _leer_archivo),
    (
        re.compile(
            rf"\b{V_MOVER}\s+(?:el\s+)?(?:{SUST_REF}\s+)?"
            rf"(?P<nombre>.+?)\s+(?:a|hacia|hasta|para)\s+(?:la\s+|el\s+|las\s+|los\s+)?"
            rf"(?:carpeta\s+)?(?P<destino>.+)$",
            _F,
        ),
        _mover_archivo,
    ),
    (
        re.compile(
            rf"\b{V_COPIAR}\s+(?:el\s+)?(?:{SUST_REF}\s+)?"
            rf"(?P<nombre>.+?)\s+(?:a|hacia|en|para)\s+(?:la\s+|el\s+)?(?:carpeta\s+)?(?P<destino>.+)$",
            _F,
        ),
        _copiar_archivo,
    ),
    (
        re.compile(rf"\b{V_ELIMINAR}\s+(?:el\s+)?{SUST_ARCHIVO}\s+(?P<nombre>.+)$", _F),
        _eliminar_archivo,
    ),
    (
        re.compile(
            rf"\b{V_LISTAR}\s+(?:los\s+|las\s+|el\s+|la\s+|mis\s+)?(?:archivos?|elementos?|cosas|contenido)\s+"
            rf"(?:que\s+)?(?:hay\s+)?(?:en|de|del|dentro\s+de)\s+(?:el\s+|la\s+|las\s+|los\s+|mi\s+)?"
            rf"(?P<carpeta>{CARPETA_DEST})\b",
            _F,
        ),
        _listar_archivos,
    ),
    (re.compile(rf"\bqu[eé]\s+(?:archivos?|hay)\s+(?:hay\s+)?(?:en|de)\s+(?:el\s+|la\s+|mi\s+)?(?P<carpeta>{CARPETA_DEST})\b", _F), _listar_archivos),
    (
        re.compile(rf"\b{V_BUSCAR}\s+(?:el\s+|un\s+|los\s+)?{SUST_ARCHIVO}\s+{NOMBRADO}(?P<patron>.+)$", _F),
        _buscar_archivo,
    ),

    # ------------------------------------------------------------------
    # CLIC ESPACIAL  (antes que el clic normal: es mas especifico)
    # ------------------------------------------------------------------
    (
        # "haz clic en el archivo debajo del mensaje del profe Andres"
        re.compile(r"(?:clic|click|clik|pincha|pulsa|presiona)\s+"
                   r"(?:en|sobre|a|al)?\s*(?:el\s+|la\s+|lo\s+)?[\w\s]{0,25}?"
                   r"\b(?P<direccion>debajo|abajo\s+de|encima|arriba\s+de|"
                   r"a\s+la\s+derecha|a\s+la\s+izquierda|al\s+lado|junto)\s+"
                   r"(?:de\s+|del\s+|de\s+la\s+)?(?P<referencia>.+?)\s*$", _F),
        _clic_relativo,
    ),

    # ------------------------------------------------------------------
    # VENTANAS
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:cambia|cambiar|ve|vete|pasa|salta|mu[eé]strame|tr[aá]eme)\s+"
                   r"(?:a\s+|al\s+|a\s+la\s+)?(?:ventana\s+(?:de\s+)?)?"
                   r"(?P<cual>.+?)\s*$(?<!pantalla)|"
                   r"\bponme\s+(?:en\s+)?(?P<cual2>.+?)\s+delante\b", _F),
        _ventana_cambiar,
    ),
    (
        re.compile(r"\bqu[eé]\s+ventanas?\s+(?:tengo|hay)\b|"
                   r"\bventanas\s+abiertas\b|\bqu[eé]\s+tengo\s+abierto\b", _F),
        _ventanas_listar,
    ),
    (
        re.compile(r"\bminimiza(?:lo|las|\s+todo)?\b|\bmu[eé]strame\s+el\s+escritorio\b|"
                   r"\besconde\s+todo\b", _F),
        _ventana_minimizar,
    ),
    (
        re.compile(r"\bmaximiza(?:la|lo)?\b|\bpantalla\s+completa\b|"
                   r"\bagranda\s+(?:la\s+)?ventana\b", _F),
        _ventana_maximizar,
    ),

    # ------------------------------------------------------------------
    # RENDIMIENTO Y JUEGOS
    # ------------------------------------------------------------------
    (
        re.compile(r"\bpor\s+qu[eé]\s+(?:tengo\s+)?(?:lag|lagea|va\s+lento|"
                   r"se\s+traba|tironea|baja\s+(?:el\s+)?fps)\b|"
                   r"\bqu[eé]\s+(?:me\s+)?est[aá]\s+(?:ralentizando|frenando)\b|"
                   r"\bpor\s+qu[eé]\s+(?:est[aá]|va)\s+(?:tan\s+)?lento\b|"
                   r"\bdiagn[oó]stico\s+de\s+rendimiento\b|"
                   r"\bqu[eé]\s+pasa\s+con\s+(?:el\s+)?(?:rendimiento|equipo)\b", _F),
        _por_que_lag,
    ),
    (
        re.compile(r"\bcierra\s+(?:lo\s+que\s+(?:sobra|no\s+use)|"
                   r"(?:los\s+)?(?:procesos|programas)\s+(?:no\s+esenciales|innecesarios|"
                   r"que\s+sobran|prescindibles)|lo\s+innecesario)\b|"
                   r"\blibera\s+recursos\b|\boptim[ií]zame?\s+(?:el\s+)?(?:equipo|pc)\b", _F),
        _cerrar_prescindibles,
    ),
    (
        re.compile(r"\b(?:puedo|listo\s+para)\s+jugar\b|"
                   r"\bc[oó]mo\s+est[aá]\s+(?:el\s+)?equipo\s+para\s+jugar\b|"
                   r"\bvoy\s+a\s+jugar\s*$", _F),
        _listo_para_jugar,
    ),
    (
        re.compile(rf"\b{V_ABRIR}\s+(?:el\s+juego\s+)?"
                   r"(?P<juego>valorant|fortnite|league\s+of\s+legends|lol|"
                   r"rocket\s+league|counter\s*strike|cs\s*2|dota|gta|minecraft)\b|"
                   rf"\b(?:juguemos|vamos\s+a\s+jugar|ponme)\s+"
                   r"(?:a\s+)?(?P<juego2>valorant|fortnite|lol|dota|gta|minecraft)\b", _F),
        _abrir_juego,
    ),

    # ------------------------------------------------------------------
    # ALMACENAMIENTO
    # ------------------------------------------------------------------
    (
        re.compile(r"\bqu[eé]\s+(?:archivos?\s+)?puedo\s+(?:borrar|eliminar)\b|"
                   r"\barchivos?\s+(?:que\s+no\s+(?:uso|he\s+usado)|olvidados?|"
                   r"viejos?|sin\s+usar)\b|"
                   r"\blibera(?:r|me)?\s+(?:espacio|almacenamiento)\b|"
                   r"\bc[oó]mo\s+libero\s+espacio\b", _F),
        _archivos_olvidados,
    ),

    # ------------------------------------------------------------------
    # CORREO  (Outlook en local)
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:tengo|hay)\s+(?:correos?|mails?|emails?|mensajes)\s+"
                   r"(?:sin\s+leer|nuevos?|pendientes)\b|"
                   r"\bcorreos?\s+(?:sin\s+leer|nuevos?|pendientes)\b|"
                   r"\bcu[aá]ntos\s+correos\b", _F),
        _correos_sin_leer,
    ),
    (
        # "lee los ultimos 3 correos": con cuerpo, que es lo que pidio Kaled.
        re.compile(r"\b(?:l[eé]e(?:me)?|leer|dime|cu[eé]ntame|dame)\s+"
                   r"(?:el\s+|los\s+|las\s+|mis\s+)?(?:[uú]ltimos?\s+)?"
                   r"(?P<cuantos>\d+|un|una|dos|tres|cuatro|cinco)?\s*"
                   r"(?:[uú]ltimos?\s+)?(?:correos?|mails?|emails?)\b", _F),
        _correos_leidos,
    ),
    (
        re.compile(r"\b(?:qu[eé]\s+)?correos?\s+(?:tengo|hay|me\s+han\s+llegado)\b|"
                   r"\b(?:mira|revisa|checa)\s+(?:el\s+)?(?:correo|mail|bandeja)\b|"
                   r"\b[uú]ltimos?\s+correos?\b|\bbandeja\s+de\s+entrada\b", _F),
        _correos_ultimos,
    ),
    (
        re.compile(r"\bcorreos?\s+de\s+(?P<quien>[\w\sáéíóúñ]{2,40}?)\s*$|"
                   r"\b(?:busca|buscar)\s+correos?\s+de\s+(?P<quien2>.+)$", _F),
        _correos_de,
    ),
    (
        re.compile(r"\b(?:l[eé]e(?:me)?|abre)\s+el\s+"
                   r"(?P<cual>primer|segundo|tercer(?:o)?|\d+)\s*(?:correo|mail)\b", _F),
        _correo_uno,
    ),

    # ------------------------------------------------------------------
    # PANTALLA
    # ------------------------------------------------------------------
    (
        # Va antes que "leer": si no, "lee la pantalla" acabaria buscando un
        # archivo llamado "la pantalla".
        re.compile(r"\b(?:l[eé]e(?:me)?|leer|qu[eé]\s+(?:pone|dice|hay|ves)|"
                   r"dime\s+qu[eé]\s+(?:pone|dice|hay))\s+"
                   r"(?:en\s+)?(?:la\s+|mi\s+)?pantalla\b|"
                   r"\bqu[eé]\s+tengo\s+en\s+(?:la\s+)?pantalla\b", _F),
        _pantalla_leer,
    ),
    (
        re.compile(r"\b(?:haz|hazme|hazle|da|dale|le|dar)?\s*(?:un\s+)?"
                   r"(?:clic|click|clik|cliquea)\s+"
                   # "le click DONDE DICE equipos" no encajaba: el conector no
                   # estaba en la lista y la frase acababa en el modelo, que
                   # se puso a explorar carpetas. Es la forma mas natural de
                   # senalar algo que se ve en pantalla, asi que entra aqui.
                   r"(?:en|sobre|a|al|a\s+la|encima\s+de|"
                   r"donde\s+(?:dice|pone|est[aá]|aparece|sale|se\s+lee)|"
                   r"en\s+donde\s+dice|"
                   r"lo\s+que\s+dice|"
                   r"al?\s+que\s+dice)\s+"
                   r"(?:el\s+|la\s+|los\s+|las\s+)?(?:bot[oó]n\s+)?"
                   r"(?P<texto>.+?)\s*$|"
                   r"^(?![\s\S]*\b(?:atajo|tecla|teclas|combinaci[oó]n)\b)"
                   r".*\b(?:pincha|pulsa|presiona|clica)\s+(?:en\s+|sobre\s+)?"
                   r"(?:el\s+|la\s+)?(?:bot[oó]n\s+)?(?P<texto2>.+?)\s*$", _F),
        _pantalla_clic,
    ),
    (
        re.compile(r"\b(?:busca|encuentra|ves|hay)\s+(?P<texto>.+?)\s+en\s+"
                   r"(?:la\s+)?pantalla\b", _F),
        _pantalla_buscar,
    ),
    (
        re.compile(r"\b(?:describe|mira|analiza|qu[eé]\s+ves\s+en)\s+"
                   r"(?:la\s+|mi\s+)?pantalla\s*(?P<pregunta>.+)?$|"
                   r"\bqu[eé]\s+estoy\s+(?:viendo|haciendo)\b", _F),
        _pantalla_describir,
    ),

    # ------------------------------------------------------------------
    # TEAMS
    # ------------------------------------------------------------------
    (
        re.compile(r"\b(?:actividad|notificaciones)\s+(?:de\s+|en\s+)?teams\b|"
                   r"\bteams\s+(?:actividad|notificaciones)\b|"
                   r"\bqu[eé]\s+(?:hay|tengo)\s+(?:nuevo\s+)?en\s+teams\b", _F),
        _teams_actividad,
    ),
    (
        re.compile(r"\b(?:l[eé]e(?:me)?|leer|dime|qu[eé]\s+(?:dice|pone|hay))\s+"
                   r"(?:las\s+|los\s+|el\s+|la\s+)?"
                   r"(?:publicaciones|mensajes|conversaci[oó]n|chat|canal)?\s*"
                   r"(?:de\s+|en\s+)?teams\b", _F),
        _teams_leer,
    ),
    (
        re.compile(r"\b(?:abre|abrir|ve\s+a|entra\s+(?:a|en))\s+(?:el\s+)?canal\s+"
                   r"(?:de\s+)?(?P<canal>.+?)(?:\s+en\s+teams)?\s*$", _F),
        _teams_canal,
    ),
    (
        re.compile(r"\b(?:abre|abrir|inicia)\s+teams\s*(?P<seccion>.+)?$|"
                   r"\babre\s+(?:el\s+|los\s+|las\s+)?"
                   r"(?P<seccion2>chats?|equipos|canales|calendario|actividad)\s+"
                   r"(?:de\s+|en\s+)teams\b", _F),
        _teams_abrir,
    ),

    # ------------------------------------------------------------------
    # NAVEGADOR  (despues de los archivos, para no robarles "busca")
    # ------------------------------------------------------------------
    (
        re.compile(rf"\b(?:reproduce|reproduzca|pon|ponga|p[oó]nme|{V_BUSCAR})\s+(?P<consulta>.+?)\s+en\s+(?:you\s*tube|yutub|yutu)\b", _F),
        _youtube,
    ),
    (
        re.compile(
            rf"\b{V_INVESTIGAR}\s+(?:en\s+(?:internet|google|la\s+web|comet|el\s+navegador)\s+)?"
            rf"(?:(?:sobre|acerca\s+de|por)\s+)?(?P<consulta>.+?)"
            rf"(?:\s+en\s+(?:internet|google|la\s+web|comet|el\s+navegador))?$",
            _F,
        ),
        _buscar_web,
    ),
    (
        re.compile(rf"\b{V_ABRIR}\s+(?:la\s+)?(?:p[aá]gina|web|sitio|url)\s+(?:de\s+)?(?P<sitio>.+)$", _F),
        _abrir_sitio,
    ),
    # "abre una pestana en comet y busca oferta y demanda".
    # Antes esto caia en "abrir aplicacion" con el nombre "un pestana en comet
    # y busco oferta y demanda": abria Comet en blanco y se comia la busqueda
    # sin decir nada. Alexa ademas transcribe "y busca" como "y busco".
    (
        re.compile(
            rf"\b{V_ABRIR}\s+(?:una?\s+|1\s+)?(?:pesta[nñ]a|ventana|tab|p[aá]gina)\s+(?:nueva\s+)?"
            rf"(?:en|de|con|del?)?\s*(?:el\s+)?(?:comet|comic|c[oó]mic|comix|navegador|chrome|brave|edge|firefox)?"
            rf"\s*(?:y|para|e)?\s*(?:{V_INVESTIGAR}|busco|buscame|b[uú]squeme)\s+"
            rf"(?:(?:sobre|acerca\s+de|por)\s+)?(?P<consulta>.+)$",
            _F,
        ),
        _buscar_web,
    ),
    # La misma pestana, pero sin busqueda: solo abrir el navegador.
    (
        re.compile(
            rf"\b{V_ABRIR}\s+(?:una?\s+|1\s+)?(?:pesta[nñ]a|ventana|tab|p[aá]gina)\s+(?:nueva\s+)?"
            rf"(?:(?:en|de|con|del?)\s+)?(?:el\s+)?(?:comet|comic|c[oó]mic|comix|navegador|chrome|brave|edge|firefox)?\s*$",
            _F,
        ),
        _abrir_navegador,
    ),
    (re.compile(rf"\b{V_ABRIR}\s+(?:el\s+)?(?:navegador|comet)\b\s*$", _F), _abrir_navegador),

    # ------------------------------------------------------------------
    # ENERGIA  (antes que las apps: "apaga" esta en ambos bloques)
    # ------------------------------------------------------------------
    (re.compile(r"\bcancel(?:a|e)(?:r)?\s+el\s+(?:apagado|reinicio)\b|\bno\s+apagues\b|\bd[eé]jalo\s+encendido\b", _F), _cancelar_apagado),
    (re.compile(r"\bbloque(?:a|e)(?:r)?\s+(?:el\s+|la\s+)?(?:equipo|pc|computador(?:a)?|sesi[oó]n|pantalla)\b", _F), _bloquear),
    (re.compile(r"\bsuspend[ae](?:r)?\s+(?:el\s+)?(?:equipo|pc)\b|\bmodo\s+suspensi[oó]n\b|\bhiberna(?:r)?\b", _F), _suspender),
    (re.compile(r"\breinici(?:a|e)(?:r)?\s+(?:el\s+)?(?:equipo|pc|computador(?:a)?|sistema)\b", _F), _reiniciar),
    (re.compile(r"\bapag(?:a|ue)(?:r)?\s+(?:el\s+)?(?:equipo|pc|computador(?:a)?|sistema)(?:\s+en\s+(?P<minutos>\d+)\s+minutos?)?\b", _F), _apagar),

    # ------------------------------------------------------------------
    # TECLADO Y RATON  (antes que las apps: "pon"/"haz" se solapan)
    # ------------------------------------------------------------------
    (
        re.compile(
            rf"\b{V_CAPTURAR}\s+{ART}captura(?:\s+de\s+(?:la\s+)?pantalla)?\b"
            rf"|\b(?:captura|capture|capturar)\s+(?:la\s+)?pantalla\b"
            rf"|\bscreenshot\b|\bpantallazo\b",
            _F,
        ),
        _captura,
    ),
    (
        re.compile(
            r"\b(?:minimiza(?:r)?\s+todo|minimice\s+todo|muestra\s+el\s+escritorio|muestre\s+el\s+escritorio|"
            r"cambia\s+de\s+ventana|cambie\s+de\s+ventana|sube\s+el\s+volumen|suba\s+el\s+volumen|"
            r"baja\s+el\s+volumen|baje\s+el\s+volumen|silencia|silencie|quita\s+el\s+sonido|mutea)\b",
            _F,
        ),
        lambda m: entrada.ejecutar_atajo(
            {
                "minimiza todo": "minimizar todo", "minimizar todo": "minimizar todo",
                "minimice todo": "minimizar todo",
                "muestra el escritorio": "mostrar escritorio",
                "muestre el escritorio": "mostrar escritorio",
                "cambia de ventana": "cambiar ventana", "cambie de ventana": "cambiar ventana",
                "sube el volumen": "subir volumen", "suba el volumen": "subir volumen",
                "baja el volumen": "bajar volumen", "baje el volumen": "bajar volumen",
                "silencia": "silenciar", "silencie": "silenciar",
                "quita el sonido": "silenciar", "mutea": "silenciar",
            }.get(sin_acentos(m.group(0).lower()), "silenciar")
        ),
    ),
    (
        re.compile(
            rf"\b{V_PRESIONAR}\s+(?:el\s+atajo\s+|las\s+teclas\s+|el\s+comando\s+)?"
            r"(?P<atajo>copiar|pegar|cortar|deshacer|rehacer|guardar|seleccionar\s+todo|"
            r"cerrar\s+pesta[nñ]a|nueva\s+pesta[nñ]a|reabrir\s+pesta[nñ]a|cambiar\s+ventana|"
            r"cerrar\s+ventana|minimizar\s+todo|mostrar\s+escritorio|bloquear\s+equipo|"
            r"administrador\s+de\s+tareas|subir\s+volumen|bajar\s+volumen|silenciar|"
            r"reproducir|pausar|siguiente\s+canci[oó]n|canci[oó]n\s+anterior)\b",
            _F,
        ),
        _atajo,
    ),
    (
        re.compile(rf"\b{V_PRESIONAR}\s+(?:la\s+tecla\s+)?(?P<tecla>enter|entrar|escape|tabulador|espacio|borrar|suprimir|arriba|abajo|izquierda|derecha)\b", _F),
        _pulsar,
    ),
    (
        re.compile(r"\b(?:desplaza|desplace|scroll|baja|baje|sube|suba)\s+(?:la\s+p[aá]gina\s+)?(?:hacia\s+)?(?P<direccion>arriba|abajo)\b", _F),
        _desplazar,
    ),
    (
        re.compile(rf"\b{V_ESCRIBIR}\s+(?:el\s+texto\s+|lo\s+siguiente:?\s+)?(?P<texto>.+)$", _F),
        _escribir,
    ),

    # ------------------------------------------------------------------
    # APLICACIONES
    # ------------------------------------------------------------------
    (re.compile(rf"\b{V_ABRIR}\s+(?:el\s+|la\s+|mi\s+)?(?:programa\s+|aplicaci[oó]n\s+|app\s+)?(?P<app>.+)$", _F), _abrir_app),
    (re.compile(rf"\b{V_CERRAR}\s+(?:el\s+|la\s+|mi\s+)?(?:programa\s+|aplicaci[oó]n\s+|app\s+)?(?P<app>.+)$", _F), _cerrar_app),

    # ------------------------------------------------------------------
    # META
    # ------------------------------------------------------------------
    (re.compile(r"\b(?:c[oó]mo\s+(?:va|qued[oó]|vas)|qu[eé]\s+pas[oó]|resultado|ya\s+terminaste|est[aá]\s+listo|c[oó]mo\s+sali[oó])\b|^\s*(?:qued[oó]|termin[oó]|listo\s+ya|y\s+eso)\s*$", _F), _pendiente),
    (re.compile(r"\b(?:ayuda|ay[uú]dame|qu[eé]\s+puedes\s+hacer|qu[eé]\s+sabes\s+hacer|opciones|qu[eé]\s+comandos)\b", _F), _ayuda),
]


# =========================================================================
# FIN DE SESIÓN
# =========================================================================
# Con la sesión continua activada, Alexa deja el micrófono abierto tras cada
# orden. Estas frases son la forma de devolvérselo: mientras la sesión de la
# skill está abierta, Alexa NO atiende sus propios servicios ("pon música",
# "qué tiempo hace"), así que hace falta una salida explícita y natural.
_PATRON_DESPEDIDA = re.compile(
    r"^\s*(?:"
    r"pausa|pausate|para|parate|detente|det[eé]nte|descansa|desc[aá]nsate|"
    r"basta|ya\s+est[aá]|ya\s+esta|eso\s+es\s+todo|nada\s+m[aá]s|"
    r"gracias|muchas\s+gracias|listo\s+gracias|"
    r"ad[ií]os|adios|hasta\s+luego|hasta\s+pronto|chao|chau|bye|"
    r"suelta|su[eé]ltala|libera\s+a\s+alexa|d[eé]jala|dejala|"
    r"cierra\s+(?:la\s+)?sesi[oó]n|salir|sal\s+de\s+jarvis|"
    r"modo\s+espera|espera|silencio|c[aá]llate|duerme|du[eé]rmete|"
    r"termina|terminamos|listo|suficiente|ya\s+no|devu[eé]lveme\s+a\s+alexa|"
    r"devuelve\s+(?:el\s+)?micr[oó]fono|jarvis\s+pausa|pausa\s+jarvis"
    r")\s*$",
    _F,
)


def es_despedida(texto: str) -> bool:
    """¿El usuario está devolviéndole el micrófono a Alexa?"""
    limpio = limpiar_comando(texto or "")
    return bool(_PATRON_DESPEDIDA.match(sin_acentos(limpio)))


# =========================================================================
# ROUTER
# =========================================================================
def enrutar(texto: str):
    """
    Intenta resolver el comando sin usar el LLM.

    Devuelve la respuesta hablada, o None si ningún patrón coincide
    (en cuyo caso el servidor lo delega a Ollama).
    """
    foco.envejecer()

    # Lo primero de todo: si hay algo esperando un si o un no, esta frase es
    # la respuesta. Tiene que mirarse antes que cualquier patron, porque
    # "vale" y "ok" tambien encajan en el patron de ordenes vacias.
    decision = confirmaciones.resolver(texto or "")
    if decision is not None:
        return decision

    limpio = limpiar_comando(texto)
    if not limpio:
        return None

    # Buscamos sobre la versión sin acentos, pero extraemos del texto original.
    # Como la tabla de acentos preserva la longitud, las posiciones coinciden.
    plano = sin_acentos(limpio)

    for patron, manejadora in INTENTS:
        coincidencia = patron.search(plano)
        if not coincidencia:
            continue

        # Reconstruimos los grupos usando el texto ORIGINAL (con tildes).
        grupos_originales = {}
        for nombre, valor in (coincidencia.groupdict() or {}).items():
            if valor is None:
                grupos_originales[nombre] = None
            else:
                inicio, fin = coincidencia.span(nombre)
                grupos_originales[nombre] = limpio[inicio:fin] if inicio >= 0 else valor

        envoltura = _Coincidencia(coincidencia, grupos_originales, limpio)

        try:
            respuesta = manejadora(envoltura)
        except Exception as e:
            log.exception("Error ejecutando el intent %s", patron.pattern[:50])
            return f"Tuve un problema ejecutando esa orden: {e}"

        if respuesta:
            log.info("Intent resuelto localmente: %s", patron.pattern[:60])
            return respuesta

    return None


class _Coincidencia:
    """
    Envoltorio del match que devuelve los grupos con sus acentos originales.

    El regex corre sobre el texto sin acentos (para que 'código' y 'codigo'
    coincidan igual), pero el contenido que se guarda en los archivos debe
    conservar las tildes.
    """

    def __init__(self, coincidencia, grupos, texto_original):
        self._coincidencia = coincidencia
        self._grupos = grupos
        self._texto = texto_original

    def group(self, clave):
        if isinstance(clave, str) and clave in self._grupos:
            return self._grupos[clave] or ""
        if clave == 0:
            inicio, fin = self._coincidencia.span(0)
            return self._texto[inicio:fin]
        return self._coincidencia.group(clave)

    def groupdict(self):
        return dict(self._grupos)
