"""
Las reglas de la boveda, leidas de la propia boveda.

El usuario tiene un CLAUDE.md en la raiz del vault con como esta organizado todo:
que carpeta para cada cosa, que plantilla usar, que tags existen, y las reglas
de enlazado. Este modulo lo lee y lo pone a disposicion del resto.

Por que no dejarlo escrito aqui
-------------------------------
Porque la boveda cambia y este archivo no se enteraria. Si mañana añades una
carpeta o un tag, lo escribes en tu CLAUDE.md —donde ya lo escribirias de
todas formas— y Jarvis se entera solo. Los valores de aqui abajo son solo la
red de seguridad para cuando ese archivo no exista.
"""

import logging
import re

from config import NOMBRE_USUARIO

from tools import obsidian

log = logging.getLogger("jarvis.convenciones")

ARCHIVO_REGLAS = "CLAUDE.md"

# Red de seguridad, tomada del CLAUDE.md actual. Si el archivo esta, gana el.
CARPETAS = {
    "clase": "Clases",
    "documento": "Documentos",
    "proyecto": "Recursos y Proyectos",
    "tarea": "Tareas",
    "persona": "personas",
    "recurso": "resources",
    "inbox": "inbox",
    "plantillas": "templates",
}

PLANTILLAS = {
    "clase": "templates/clase.md",
    "documento": "templates/documento.md",
    "proyecto": "templates/proyecto.md",
    "tarea": "templates/tarea.md",
    "persona": "templates/persona.md",
    "inbox": "templates/inbox.md",
}

TAGS = {
    "clase": "clases",
    "documento": "documentos",
    "proyecto": "proyecto",
    "tarea": "tareas",
}

_cache: dict | None = None


def nombre_de_archivo(titulo: str) -> str:
    """
    Minusculas con guiones, que es la convencion de la boveda.

    "Bases de Datos - Clase 3" -> "bases-de-datos-clase-3.md"
    """
    import unicodedata

    limpio = "".join(c for c in unicodedata.normalize("NFD", titulo or "")
                     if unicodedata.category(c) != "Mn")
    limpio = re.sub(r"[^\w\s-]", " ", limpio.lower())
    limpio = re.sub(r"[\s_]+", "-", limpio)
    # Un titulo que ya trae guiones ("Bases de Datos - Clase 3") produciria
    # "---" al juntarlos con los de los espacios.
    limpio = re.sub(r"-{2,}", "-", limpio).strip("-")
    return (limpio or "sin-titulo")[:80]


def leer_reglas() -> str:
    """El CLAUDE.md de la boveda, tal cual. Vacio si no existe."""
    global _cache
    if _cache is not None:
        return _cache.get("texto", "")

    vault = obsidian.vault()
    if vault is None:
        _cache = {"texto": ""}
        return ""

    ruta = vault / ARCHIVO_REGLAS
    texto = ""
    if ruta.is_file():
        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
            log.info("Convenciones leídas de %s (%d caracteres)", ruta, len(texto))
        except OSError as e:
            log.warning("No pude leer %s: %s", ruta, e)

    _cache = {"texto": texto}
    return texto


def carpetas_reales() -> dict:
    """
    Las carpetas que EXISTEN en la boveda, mapeadas por tipo.

    Se comprueba en disco a proposito: da igual lo que diga el CLAUDE.md si la
    carpeta no esta creada. Y los nombres se comparan sin distinguir mayusculas
    ni acentos, porque "Recursos y Proyectos" tiene ambas cosas.
    """
    vault = obsidian.vault()
    if vault is None:
        return {}

    existentes = {}
    try:
        directorios = [d for d in vault.iterdir() if d.is_dir()
                       and not d.name.startswith(".")]
    except OSError:
        return {}

    for tipo, nombre in CARPETAS.items():
        objetivo = nombre.lower()
        for d in directorios:
            if d.name.lower() == objetivo:
                existentes[tipo] = d
                break
        else:
            # Coincidencia parcial: "Recursos" encaja con "Recursos y Proyectos".
            primera = objetivo.split()[0]
            for d in directorios:
                if primera in d.name.lower():
                    existentes[tipo] = d
                    break

    return existentes


def plantilla(tipo: str) -> str:
    """El contenido de la plantilla de ese tipo, o cadena vacia."""
    vault = obsidian.vault()
    if vault is None:
        return ""

    relativa = PLANTILLAS.get(tipo)
    if not relativa:
        return ""

    ruta = vault / relativa
    if not ruta.is_file():
        # La carpeta puede llamarse distinto; buscamos por el nombre del archivo.
        candidatos = list(vault.rglob(f"{tipo}.md"))
        candidatos = [c for c in candidatos if "template" in str(c).lower()
                      or "plantilla" in str(c).lower()]
        if not candidatos:
            return ""
        ruta = candidatos[0]

    try:
        return ruta.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def resumen_para_modelo() -> str:
    """
    Las reglas condensadas, para meterlas en el prompt del modelo.

    El CLAUDE.md entero son ~2.500 caracteres. En un modelo local cada token
    del prompt es tiempo de GPU, y aqui solo hacen falta las decisiones: donde
    va cada cosa y como se nombra.
    """
    carpetas = carpetas_reales()
    if not carpetas:
        return ""

    lineas = [f"Reglas de la boveda de Obsidian de {NOMBRE_USUARIO}:"]
    descripciones = {
        "clase": "apuntes de una materia",
        "documento": "un archivo importante (pdf, guia, entrega)",
        "proyecto": "un proyecto activo o recurso de trabajo",
        "tarea": "un pendiente",
        "persona": "alguien relevante",
        "recurso": "material externo que no es nota propia",
        "inbox": "captura rapida sin clasificar todavia",
    }
    for tipo, carpeta in carpetas.items():
        if tipo in descripciones:
            lineas.append(f"- {carpeta.name}/ : {descripciones[tipo]}")

    lineas += [
        "Nombres de archivo en minusculas con guiones.",
        "Fechas en formato AAAA-MM-DD.",
        "Frontmatter con tags, y enlaces internos entre dobles corchetes.",
        "Cada nota termina con una seccion Relacionado con enlaces.",
        "Antes de crear una nota, comprobar si ya existe una parecida.",
    ]
    return "\n".join(lineas)


def olvidar_cache() -> None:
    global _cache
    _cache = None
