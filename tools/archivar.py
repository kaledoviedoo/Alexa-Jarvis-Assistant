"""
Meter un archivo en la boveda como debe ir, sin que tengas que pensarlo.

Que hace exactamente
--------------------
Coges un pdf de Descargas, dices "archiva la guia de bases de datos", y Jarvis:

  1. Encuentra el archivo, aunque digas el nombre a medias.
  2. Lee su contenido si puede (pdf, docx, txt, md).
  3. Decide QUE es: apunte de clase, documento, recurso o captura sin
     clasificar. Y con eso, en que carpeta va y que plantilla usa.
  4. Copia el archivo a la boveda y crea la nota, con el frontmatter, los tags
     y la fecha que manda el CLAUDE.md.
  5. Busca en la MEMORIA SEMANTICA notas que traten de lo mismo y las enlaza en
     la seccion Relacionado.

Ese ultimo paso es el que convierte esto en un segundo cerebro y no en una
carpeta ordenada. Un archivo que entra sin enlaces esta tan perdido como en
Descargas; lo que lo hace util dentro de seis meses es estar conectado con lo
que ya sabias.

Nada se borra
-------------
El archivo original se COPIA, no se mueve. Si algo sale mal, tu pdf sigue
donde estaba.
"""

import logging
import re
import shutil
from datetime import date
from pathlib import Path

from config import CARPETAS_PERMITIDAS, MODO_DEDICADO
from tools import convenciones, memoria, obsidian

log = logging.getLogger("jarvis.archivar")

CARPETA_ADJUNTOS = "adjuntos"
MAX_TEXTO = 4000

EXTENSIONES = {
    ".pdf": "pdf", ".docx": "documento de Word", ".doc": "documento de Word",
    ".pptx": "presentación", ".ppt": "presentación",
    ".xlsx": "hoja de cálculo", ".xls": "hoja de cálculo", ".csv": "tabla",
    ".md": "nota", ".txt": "texto", ".png": "imagen", ".jpg": "imagen",
    ".jpeg": "imagen", ".zip": "comprimido", ".py": "código", ".ipynb": "cuaderno",
}


# -------------------------------------------------------------------------
# ENCONTRAR EL ARCHIVO
# -------------------------------------------------------------------------
def buscar_archivo(descripcion: str) -> list[Path]:
    """Archivos que encajan con lo que se dijo, los recientes primero."""
    import difflib

    pistas = [p for p in re.split(r"\W+", (descripcion or "").lower()) if len(p) > 2]
    if not pistas:
        return []

    candidatos = []
    for carpeta in CARPETAS_PERMITIDAS:
        if not carpeta.is_dir():
            continue
        try:
            for ruta in carpeta.rglob("*"):
                if not ruta.is_file() or ruta.suffix.lower() not in EXTENSIONES:
                    continue
                nombre = ruta.stem.lower()
                aciertos = sum(1 for p in pistas if p in nombre)
                if not aciertos:
                    # Y si no, por parecido: los nombres de archivo vienen de
                    # descargas y suelen estar llenos de codigos y guiones.
                    if difflib.SequenceMatcher(
                            None, " ".join(pistas), nombre).ratio() < 0.45:
                        continue
                    aciertos = 0.5
                try:
                    momento = ruta.stat().st_mtime
                except OSError:
                    momento = 0
                candidatos.append((aciertos, momento, ruta))
        except OSError:
            continue

    # Mas coincidencias primero y, a igualdad, el mas reciente: si acabas de
    # descargarlo, es casi seguro ese.
    candidatos.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [ruta for _, _, ruta in candidatos[:5]]


# -------------------------------------------------------------------------
# LEER EL CONTENIDO
# -------------------------------------------------------------------------
def _texto_del_archivo(ruta: Path) -> str:
    """Lo que se pueda leer del archivo. Vacio si es binario opaco."""
    sufijo = ruta.suffix.lower()

    try:
        if sufijo in (".md", ".txt", ".py", ".csv"):
            return ruta.read_text(encoding="utf-8", errors="ignore")[:MAX_TEXTO]

        if sufijo == ".pdf":
            try:
                import pypdf
                lector = pypdf.PdfReader(str(ruta))
                # Las primeras paginas bastan para saber de que va.
                trozos = [(p.extract_text() or "") for p in lector.pages[:4]]
                return "\n".join(trozos)[:MAX_TEXTO]
            except ImportError:
                log.info("Sin pypdf no puedo leer el contenido del pdf.")
                return ""

        if sufijo == ".docx":
            try:
                import docx
                documento = docx.Document(str(ruta))
                return "\n".join(p.text for p in documento.paragraphs)[:MAX_TEXTO]
            except ImportError:
                return ""

        if sufijo == ".ipynb":
            import json
            datos = json.loads(ruta.read_text(encoding="utf-8", errors="ignore"))
            celdas = ["".join(c.get("source", [])) for c in datos.get("cells", [])]
            return "\n".join(celdas)[:MAX_TEXTO]

    except Exception as e:
        log.debug("No pude leer %s: %s", ruta.name, e)

    return ""


# -------------------------------------------------------------------------
# DECIDIR QUE ES
# -------------------------------------------------------------------------
# Señales del nombre y del contenido. Se mira esto ANTES de preguntarle al
# modelo: es instantaneo y acierta en la mayoria de los casos reales.
_PISTAS = {
    "clase": r"\b(clase|apunte|tema|unidad|cap[ií]tulo|lecci[oó]n|teor[ií]a|"
             r"semana\s*\d|sesi[oó]n)\b",
    "tarea": r"\b(tarea|entrega|deber|actividad|taller|ejercicio|parcial|"
             r"examen|quiz|laboratorio)\b",
    "proyecto": r"\b(proyecto|propuesta|anteproyecto|informe\s+final|tesis|"
                r"monograf[ií]a)\b",
    "recurso": r"\b(gu[ií]a|manual|cheat\s*sheet|referencia|documentaci[oó]n|"
               r"tutorial|libro|paper|art[ií]culo)\b",
}


def clasificar(nombre: str, texto: str) -> tuple[str, str]:
    """
    Devuelve (tipo, por que). El 'por que' se dice en voz alta.

    Si las pistas no bastan, se le pregunta al modelo con las reglas de la
    boveda delante. Y si el modelo tampoco lo tiene claro, va a inbox, que es
    exactamente para lo que existe segun tu CLAUDE.md.
    """
    # Los separadores de los nombres de archivo tienen que volverse espacios
    # ANTES de buscar. "_" cuenta como letra para una expresion regular, asi
    # que en "BD_clase_3" la palabra "clase" no tiene limites a los lados y no
    # encajaba: el archivo mas obvio del mundo acababa en inbox.
    base = re.sub(r"[_\-.]+", " ", f"{nombre} {texto[:1500]}").lower()

    for tipo, patron in _PISTAS.items():
        encontrado = re.search(patron, base)
        if encontrado:
            return tipo, f"dice {encontrado.group(0)}"

    tipo_modelo = _clasificar_con_modelo(nombre, texto)
    if tipo_modelo:
        return tipo_modelo, "lo deduje del contenido"

    return "inbox", "no tengo claro dónde va"


def _clasificar_con_modelo(nombre: str, texto: str) -> str:
    """
    Le pregunta al modelo a que carpeta va, con las carpetas REALES delante.

    Antes se le daba una lista fija de seis palabras. El problema es que esa
    lista es la del CLAUDE.md, no la de tu boveda: si tienes una carpeta que
    no esta en la lista, el modelo no podia elegirla ni sabiendo que era la
    buena. Ahora se le enseña lo que hay de verdad en disco, con para que
    sirve cada carpeta, y elige entre eso.
    """
    try:
        import ollama
        import modes
        import ollama_client
    except ImportError:
        return ""

    carpetas = convenciones.carpetas_reales()
    if not carpetas:
        return ""

    reglas = convenciones.resumen_para_modelo()
    if not reglas:
        return ""

    # Los tipos que de verdad se pueden elegir: los que tienen carpeta creada.
    posibles = sorted(carpetas)
    if not posibles:
        return ""

    perfil = modes.PERFILES[MODO_DEDICADO]
    modelo = perfil["modelo"]
    if modelo not in (ollama_client.modelos_instalados() or []):
        modelo = modes.perfil_actual()["modelo"]

    catalogo = "\n".join(f"- {t}  ->  carpeta {carpetas[t].name}/" for t in posibles)

    try:
        respuesta = ollama.chat(
            model=modelo,
            messages=[
                {"role": "system", "content":
                    reglas
                    + "\n\nCarpetas que existen ahora mismo en la boveda:\n"
                    + catalogo
                    + "\n\nPiensa en PARA QUE sirve cada carpeta y elige la que "
                      "mejor encaje con este archivo. Si ninguna encaja de verdad, "
                      "responde inbox: es preferible dejarlo sin clasificar a "
                      "meterlo donde no va.\n"
                      "Responde SOLO con una de estas palabras: "
                    + ", ".join(posibles) + ", inbox."},
                {"role": "user", "content":
                    f"Archivo: {nombre}\n\nContenido:\n{texto[:1200]}\n\n¿A que carpeta va?"},
            ],
            options={"temperature": 0.1, "num_predict": 10},
        )
        crudo = (respuesta.get("message", {}).get("content") or "").strip().lower()
        # El mas largo primero: si contesta "documento" y buscaramos "docu"
        # antes, un tipo mas corto que sea prefijo de otro ganaria por error.
        for tipo in sorted(posibles + ["inbox"], key=len, reverse=True):
            if tipo in crudo:
                return tipo
    except Exception as e:
        log.debug("El modelo no clasificó: %s", e)

    return ""


# -------------------------------------------------------------------------
# CREAR LA NOTA
# -------------------------------------------------------------------------
def _relacionadas(consulta: str, cuantas: int = 4) -> list[str]:
    """Titulos de notas que tratan de lo mismo, por significado."""
    try:
        encontradas = memoria.buscar(consulta, cuantos=cuantas, minimo=0.5)
        return [n["titulo"] for n in encontradas]
    except Exception as e:
        log.debug("La memoria no respondió: %s", e)
        return []


def _titulo_legible(ruta: Path) -> str:
    """Un titulo decente a partir de un nombre de archivo de descargas."""
    crudo = ruta.stem
    crudo = re.sub(r"[_\-]+", " ", crudo)
    # Fuera los codigos que meten las plataformas: "(1)", "v2", hashes.
    crudo = re.sub(r"\(\d+\)|\bv?\d{1,2}\b$|\b[0-9a-f]{8,}\b", " ", crudo)
    crudo = re.sub(r"\s+", " ", crudo).strip()
    return crudo[:70] or ruta.stem


def _nota(tipo: str, titulo: str, archivo_rel: str, resumen: str,
          relacionadas: list[str]) -> str:
    """El markdown de la nota, con las convenciones de la boveda."""
    hoy = date.today().isoformat()
    tag = convenciones.TAGS.get(tipo, tipo)

    enlaces = "\n".join(f"- [[{t}]]" for t in relacionadas) or "- "

    # El adjunto va embebido si es imagen o pdf (Obsidian los muestra dentro
    # de la nota) y enlazado si no, que para un zip o un xlsx es lo util.
    sufijo = Path(archivo_rel).suffix.lower()
    incrustar = "!" if sufijo in (".png", ".jpg", ".jpeg", ".pdf") else ""

    return f"""---
tags:
  - {tag}
date: {hoy}
origen: archivado por Jarvis
---

# {titulo}

{resumen}

## Archivo

{incrustar}[[{archivo_rel}]]

## Relacionado

{enlaces}
"""


def archivar(descripcion: str) -> str:
    """
    Archiva en la boveda el archivo que encaje con la descripcion.

    Lento de verdad: leer un pdf, clasificarlo y buscar relacionadas son varios
    segundos. Quien llama debe hacerlo en segundo plano.
    """
    vault = obsidian.vault()
    if vault is None:
        return "No encuentro tu bóveda de Obsidian."

    candidatos = buscar_archivo(descripcion)
    if not candidatos:
        return f"No encuentro ningún archivo que se parezca a {descripcion}."

    return archivar_ruta(candidatos[0])


def archivar_ruta(origen: Path) -> str:
    """
    Lo mismo, pero cuando YA sabes cual es el archivo.

    Existe para la seleccion por voz: ahi los archivos ya estan elegidos uno
    a uno y volver a buscarlos por descripcion podria dar con otro distinto,
    que es justo lo que no queremos al mover cosas.
    """
    vault = obsidian.vault()
    if vault is None:
        return "No encuentro tu bóveda de Obsidian."

    if not origen.exists():
        return f"{origen.name} ya no está donde estaba."

    log.info("Archivando %s", origen)

    texto = _texto_del_archivo(origen)
    tipo, motivo = clasificar(origen.name, texto)

    carpetas = convenciones.carpetas_reales()
    destino_carpeta = carpetas.get(tipo) or carpetas.get("inbox") or vault
    destino_carpeta.mkdir(parents=True, exist_ok=True)

    # El adjunto, a su carpeta dentro de la boveda.
    adjuntos = vault / CARPETA_ADJUNTOS
    adjuntos.mkdir(parents=True, exist_ok=True)

    destino_archivo = adjuntos / origen.name
    if destino_archivo.exists():
        destino_archivo = adjuntos / f"{origen.stem}-{date.today().isoformat()}{origen.suffix}"

    try:
        # Copia, no movimiento: si algo falla, el original sigue donde estaba.
        shutil.copy2(origen, destino_archivo)
    except OSError as e:
        return f"No pude copiar el archivo a la bóveda: {e}"

    titulo = _titulo_legible(origen)
    nombre_nota = convenciones.nombre_de_archivo(titulo) + ".md"
    ruta_nota = destino_carpeta / nombre_nota

    if ruta_nota.exists():
        return (f"Ya tienes una nota llamada {titulo} en {destino_carpeta.name}. "
                "Copié el archivo a adjuntos pero no toqué la nota.")

    resumen = _resumir(titulo, texto) if texto else \
        f"{EXTENSIONES.get(origen.suffix.lower(), 'Archivo')} archivado desde {origen.parent.name}."

    relacionadas = _relacionadas(f"{titulo} {texto[:600]}")

    try:
        ruta_nota.write_text(
            _nota(tipo, titulo, f"{CARPETA_ADJUNTOS}/{destino_archivo.name}",
                  resumen, relacionadas),
            encoding="utf-8",
        )
    except OSError as e:
        return f"Copié el archivo pero no pude crear la nota: {e}"

    log.info("Nota creada: %s (tipo %s)", ruta_nota, tipo)

    # La nota nueva entra en la memoria enseguida, para que la siguiente
    # busqueda ya la encuentre.
    memoria.indexar_en_segundo_plano()

    detalle = f"Lo puse en {destino_carpeta.name} porque {motivo}"
    if relacionadas:
        detalle += f", y lo enlacé con {', '.join(relacionadas[:2])}"
    return f"{detalle}. La nota se llama {titulo}."


def _resumir(titulo: str, texto: str) -> str:
    """Dos frases sobre de que va el archivo."""
    try:
        import ollama
        import modes
        import ollama_client
    except ImportError:
        return "Archivo adjunto."

    perfil = modes.PERFILES[MODO_DEDICADO]
    modelo = perfil["modelo"]
    if modelo not in (ollama_client.modelos_instalados() or []):
        modelo = modes.perfil_actual()["modelo"]

    try:
        respuesta = ollama.chat(
            model=modelo,
            messages=[
                {"role": "system", "content":
                    "Resumes documentos en dos frases, en español, para una "
                    "nota de Obsidian. Sin markdown, sin listas. Si el texto "
                    "no da para saber de que va, dilo en una frase."},
                {"role": "user", "content": f"{titulo}\n\n{texto[:2500]}"},
            ],
            options={"temperature": 0.2, "num_predict": 120},
        )
        return (respuesta.get("message", {}).get("content") or "").strip() or "Archivo adjunto."
    except Exception:
        return "Archivo adjunto."
