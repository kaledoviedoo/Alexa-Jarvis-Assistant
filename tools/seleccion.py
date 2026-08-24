"""
Un puñado de archivos en la mano, para hacerles algo después.

Por voz no se puede arrastrar el ratón sobre cuatro archivos. Esto es el
equivalente hablado: te paras en una carpeta, dices qué quieres coger, y
queda ahí guardado hasta que digas qué hacer con ello.

    "entra a descargas"                 -> se para ahí y te dice qué hay
    "selecciona los 3 primeros"         -> coge tres
    "selecciona todos los pdf"          -> coge por tipo
    "selecciona los que digan calculo"  -> coge por lo que ponga en el nombre
    "que tengo seleccionado"            -> te los lee
    "muevelos a documentos"             -> los mueve
    "archivalos en la boveda"           -> los reparte por el vault razonando

Dos decisiones que valen la pena explicar:

**El orden es el que ves en el explorador**, o sea alfabético, no por fecha.
"Los 3 primeros" tiene que significar lo mismo mirando la pantalla que
diciéndolo en voz alta, o la orden es una lotería. Para lo otro está
"selecciona los 3 más recientes", que lo dice explícito.

**La selección caduca a los cinco minutos.** Coger cuatro archivos y moverlos
media hora después, cuando ya no te acuerdas de cuáles eran, es la receta
para mover lo que no era. Si caducó, lo dice y no hace nada.
"""

import logging
import os
import time
from pathlib import Path

from config import DESCARGAS, DOCUMENTOS, ESCRITORIO
from tools import archivos

log = logging.getLogger("jarvis.seleccion")

# Cuánto vive una selección sin que la toques.
VIDA_SEGUNDOS = 300

# Cuántos archivos se leen en voz alta antes de resumir. Más de seis por un
# altavoz y dejas de escuchar a la mitad.
MAXIMO_QUE_SE_LEEN = 6

# Familias de extensiones por como las nombras hablando. "word" son tres
# extensiones distintas y nadie dice "punto docx" en una conversacion.
FAMILIAS = {
    "pdf":       (".pdf",),
    "word":      (".docx", ".doc", ".rtf", ".odt"),
    "excel":     (".xlsx", ".xls", ".csv", ".ods"),
    "power":     (".pptx", ".ppt", ".odp"),
    "imagen":    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic"),
    "video":     (".mp4", ".mkv", ".avi", ".mov", ".webm"),
    "audio":     (".mp3", ".wav", ".m4a", ".ogg", ".flac"),
    "texto":     (".txt", ".md", ".rtf"),
    "nota":      (".md",),
    "codigo":    (".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".html",
                  ".css", ".json", ".sql", ".sh", ".ps1"),
    "comprimido": (".zip", ".rar", ".7z", ".tar", ".gz"),
}

# Como los nombras hablando -> familia. Las variantes valen la pena: Alexa
# transcribe "pdfs" y "pedeefes" con la misma facilidad.
ALIAS_FAMILIA = {
    "pdf": "pdf", "pdfs": "pdf", "pe de efe": "pdf", "pedeefe": "pdf",
    "word": "word", "words": "word", "wor": "word", "documentos de word": "word",
    "docx": "word", "documento": "word",
    "excel": "excel", "exceles": "excel", "hojas de calculo": "excel",
    "hoja de calculo": "excel", "csv": "excel",
    "power point": "power", "powerpoint": "power", "presentaciones": "power",
    "presentacion": "power", "diapositivas": "power",
    "imagen": "imagen", "imagenes": "imagen", "fotos": "imagen", "foto": "imagen",
    "png": "imagen", "jpg": "imagen", "capturas": "imagen",
    "video": "video", "videos": "video", "peliculas": "video",
    "audio": "audio", "audios": "audio", "canciones": "audio", "musica": "audio",
    "texto": "texto", "textos": "texto", "txt": "texto",
    "nota": "nota", "notas": "nota", "markdown": "nota", "md": "nota",
    "codigo": "codigo", "scripts": "codigo", "script": "codigo", "py": "codigo",
    "zip": "comprimido", "comprimidos": "comprimido", "rar": "comprimido",
}


# =========================================================================
# EL ESTADO
# =========================================================================
_carpeta: Path = ESCRITORIO
_elegidos: list[Path] = []
_sellado: float = 0.0


def _refrescar_sello() -> None:
    global _sellado
    _sellado = time.monotonic()


def _caducada() -> bool:
    return bool(_elegidos) and (time.monotonic() - _sellado) > VIDA_SEGUNDOS


def olvidar() -> None:
    """Suelta lo que hubiera cogido. Sin tocar ningun archivo."""
    global _elegidos
    _elegidos = []


def carpeta_actual() -> Path:
    return _carpeta


def seleccionados() -> list[Path]:
    """Los archivos vivos de la seleccion. Vacia si caduco."""
    if _caducada():
        log.info("La seleccion caduco tras %d s", VIDA_SEGUNDOS)
        olvidar()
    # Uno pudo desaparecer entre medias: no lo arrastramos.
    return [r for r in _elegidos if r.exists()]


# =========================================================================
# MOVERSE
# =========================================================================
def _listar(carpeta: Path) -> list[Path]:
    """Los archivos de una carpeta, en el mismo orden que los ves."""
    try:
        entradas = [e for e in carpeta.iterdir() if e.is_file()]
    except (OSError, PermissionError) as e:
        log.warning("No pude leer %s: %s", carpeta, e)
        return []
    # Ordenado por nombre, sin distinguir mayusculas: es lo que hace el
    # explorador de Windows y por tanto lo que tienes delante al hablar.
    return sorted(entradas, key=lambda r: r.name.lower())


def entrar_en(nombre_carpeta: str) -> str:
    """Se planta en una carpeta y te dice que hay dentro."""
    global _carpeta

    crudo = (nombre_carpeta or "").strip()
    if not crudo:
        return "¿A qué carpeta entro?"

    destino = archivos.ALIAS_CARPETAS.get(crudo.lower())
    if destino is None:
        # Una subcarpeta de donde estamos, o de las tres de siempre.
        for base in (_carpeta, ESCRITORIO, DESCARGAS, DOCUMENTOS):
            candidata = base / crudo
            if candidata.is_dir():
                destino = candidata
                break

    if destino is None:
        return f"No encuentro ninguna carpeta llamada {crudo}."

    try:
        destino = archivos.resolver_ruta(str(destino))
    except archivos.RutaNoPermitida as e:
        return str(e)

    if not destino.is_dir():
        return f"{crudo} no es una carpeta."

    _carpeta = destino
    olvidar()          # cambiar de sitio invalida lo que tuvieras cogido

    dentro = _listar(destino)
    subcarpetas = sum(1 for e in destino.iterdir() if e.is_dir())

    if not dentro:
        return f"Estoy en {destino.name}. No hay archivos sueltos, {subcarpetas} carpetas."

    return (f"Estoy en {destino.name}: {len(dentro)} archivos y "
            f"{subcarpetas} carpetas. El primero es {dentro[0].name}.")


# =========================================================================
# COGER
# =========================================================================
def _familia_de(palabra: str) -> tuple | None:
    clave = (palabra or "").strip().lower()
    familia = ALIAS_FAMILIA.get(clave)
    if familia:
        return FAMILIAS[familia]
    # Una extension dicha tal cual: "selecciona todos los .ini"
    limpia = clave.lstrip(".")
    if limpia and limpia.isalnum() and len(limpia) <= 5:
        return ("." + limpia,)
    return None


def _describir(elegidos: list[Path], que_hice: str) -> str:
    cuantos = len(elegidos)
    if cuantos == 0:
        return "No encontré ninguno que encaje."

    if cuantos <= MAXIMO_QUE_SE_LEEN:
        nombres = ", ".join(r.stem for r in elegidos)
        return f"{que_hice} {cuantos}: {nombres}."

    primeros = ", ".join(r.stem for r in elegidos[:3])
    return f"{que_hice} {cuantos}. Los primeros: {primeros}."


def seleccionar(criterio: str = "", cantidad: int = 0, recientes: bool = False) -> str:
    """
    Coge archivos de la carpeta actual.

    `criterio` puede ser una familia ("pdf", "word"), un trozo del nombre, o
    nada, que significa todos. `cantidad` recorta a los N primeros.
    """
    global _elegidos

    dentro = _listar(_carpeta)
    if not dentro:
        return f"En {_carpeta.name} no hay archivos que seleccionar."

    crudo = (criterio or "").strip().lower()
    candidatos = dentro
    etiqueta = "Seleccioné"

    if crudo and crudo not in ("todos", "todo", "todas", "los archivos", "archivos"):
        familia = _familia_de(crudo)
        if familia:
            candidatos = [r for r in dentro if r.suffix.lower() in familia]
            if not candidatos:
                return f"En {_carpeta.name} no hay ningún archivo de ese tipo."
        else:
            # Por lo que ponga en el nombre. Sin acentos y sin mayusculas,
            # porque lo estas diciendo en voz alta y Alexa transcribe regular.
            aguja = _sin_tildes(crudo)
            candidatos = [r for r in dentro if aguja in _sin_tildes(r.stem.lower())]
            if not candidatos:
                return f"En {_carpeta.name} no hay ninguno que diga {criterio}."

    if recientes:
        candidatos = sorted(candidatos, key=lambda r: _fecha(r), reverse=True)
        etiqueta = "Cogí los más recientes,"

    if cantidad and cantidad > 0:
        candidatos = candidatos[:cantidad]

    _elegidos = candidatos
    _refrescar_sello()
    log.info("Seleccionados %d archivos en %s", len(_elegidos), _carpeta)

    return _describir(_elegidos, etiqueta)


def _fecha(ruta: Path) -> float:
    try:
        return ruta.stat().st_mtime
    except OSError:
        return 0.0


def _sin_tildes(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def que_hay() -> str:
    """Lee lo que tienes cogido ahora mismo."""
    vivos = seleccionados()
    if not vivos:
        if _caducada():
            return "Tenía una selección pero ya caducó. Vuelve a decirme cuáles."
        return f"No tengo nada seleccionado. Estoy en {_carpeta.name}."
    return _describir(vivos, "Tienes seleccionados")


def leer_titulos() -> str:
    """
    Los nombres completos, uno a uno.

    Distinto de `que_hay`: ahi se dicen sin extension y resumidos, aqui se
    leen enteros porque lo que quieres es identificarlos antes de moverlos.
    """
    vivos = seleccionados()
    if not vivos:
        return "No tengo nada seleccionado."

    if len(vivos) > MAXIMO_QUE_SE_LEEN:
        cuerpo = "; ".join(r.name for r in vivos[:MAXIMO_QUE_SE_LEEN])
        return (f"Los primeros {MAXIMO_QUE_SE_LEEN} de {len(vivos)}: {cuerpo}. "
                "Dime menos si quieres oírlos todos.")

    return "; ".join(r.name for r in vivos) + "."


# =========================================================================
# HACERLES ALGO
# =========================================================================
def mover_a(destino: str) -> str:
    """Mueve lo seleccionado a otra carpeta permitida."""
    vivos = seleccionados()
    if not vivos:
        return "No tengo nada seleccionado que mover."

    crudo = (destino or "").strip()
    if not crudo:
        return "¿A dónde los muevo?"

    carpeta_destino = archivos.ALIAS_CARPETAS.get(crudo.lower())
    if carpeta_destino is None:
        for base in (ESCRITORIO, DESCARGAS, DOCUMENTOS, _carpeta):
            candidata = base / crudo
            if candidata.is_dir():
                carpeta_destino = candidata
                break

    if carpeta_destino is None:
        return f"No encuentro ninguna carpeta llamada {crudo}."

    try:
        carpeta_destino = archivos.resolver_ruta(str(carpeta_destino))
    except archivos.RutaNoPermitida as e:
        return str(e)

    movidos, chocaron, fallaron = 0, 0, 0
    for ruta in vivos:
        try:
            final = carpeta_destino / ruta.name
            if final.exists():
                # Nunca se pisa un archivo existente sin avisar. Renombrar en
                # silencio esconde el choque; fallar entero por uno castiga a
                # los otros nueve. Se salta ese y se cuenta.
                chocaron += 1
                continue
            ruta.replace(final)
            movidos += 1
        except OSError as e:
            log.warning("No pude mover %s: %s", ruta.name, e)
            fallaron += 1

    olvidar()

    partes = [f"Moví {movidos} a {carpeta_destino.name}"]
    if chocaron:
        partes.append(f"{chocaron} ya existían allí y los dejé donde estaban")
    if fallaron:
        partes.append(f"{fallaron} no pude moverlos")
    return ". ".join(partes) + "."


def contar() -> int:
    return len(seleccionados())


def archivar_en_boveda() -> str:
    """
    Reparte lo seleccionado por la boveda, razonando carpeta por carpeta.

    Cada archivo se clasifica por separado a proposito. Un lote de descargas
    no es homogeneo: en los mismos cuatro archivos suele haber un pdf de una
    materia, una entrega y algo que no encaja en nada. Meterlos todos donde
    vaya el primero es peor que no archivarlos.

    Tarda: por cada archivo hay que leerlo, clasificarlo con el modelo y
    buscarle notas relacionadas. Quien llame a esto tiene que hacerlo en
    segundo plano, nunca dentro de los ocho segundos de Alexa.
    """
    from tools import archivar as archivador

    vivos = seleccionados()
    if not vivos:
        return "No tengo nada seleccionado que archivar."

    hechos, fallados = [], []
    for ruta in vivos:
        try:
            resultado = archivador.archivar_ruta(ruta)
        except Exception as e:
            log.exception("Fallo archivando %s", ruta.name)
            fallados.append(f"{ruta.stem}: {e}")
            continue

        # archivar_ruta contesta en lenguaje normal, y las frases de fallo
        # empiezan por "No". No hay codigo de error que mirar, asi que se
        # distingue por ahi: es fragil, pero lo alternativo seria cambiar la
        # firma de una funcion que ya usan otras dos cosas.
        if resultado.startswith(("No ", "Ya ", "Copié el archivo pero")):
            fallados.append(f"{ruta.stem} ({resultado[:60]})")
        else:
            hechos.append(resultado)

    olvidar()

    if not hechos:
        return "No pude archivar ninguno. " + (fallados[0] if fallados else "")

    # Se lee el detalle del primero, con su razonamiento, y de los demas solo
    # la cuenta. Cuatro razonamientos seguidos por un altavoz no se siguen.
    cabeza = hechos[0]
    if len(hechos) == 1 and not fallados:
        return cabeza

    resto = f" Y {len(hechos) - 1} más" if len(hechos) > 1 else ""
    cola = f". {len(fallados)} se quedaron fuera" if fallados else ""
    return f"{cabeza}{resto}{cola}."
