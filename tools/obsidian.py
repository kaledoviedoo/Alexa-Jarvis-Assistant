"""
Integracion con Obsidian.

Un vault de Obsidian no es mas que una carpeta con archivos .md, asi que no
hace falta ninguna API: basta con encontrarla y escribir markdown correcto.

Lo que si importa es respetar sus convenciones, porque si no las notas quedan
sueltas y no sirven de nada:
  - Enlaces internos con [[dobles corchetes]]
  - Etiquetas con #almohadilla
  - Frontmatter YAML al principio para fecha y origen
  - Diario en la carpeta que Obsidian tenga configurada
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from config import DOCUMENTOS, HOME, _env

log = logging.getLogger("jarvis.obsidian")

# Un vault siempre tiene esta carpeta oculta: es la firma que lo identifica.
MARCA_VAULT = ".obsidian"

# Sitios donde la gente suele tener el vault.
_CANDIDATOS = [
    HOME / "Documents" / "Obsidian",
    HOME / "Documentos" / "Obsidian",
    DOCUMENTOS,
    HOME / "OneDrive" / "Documents",
    HOME / "OneDrive" / "Documentos",
    HOME / "Obsidian",
    HOME / "Desktop",
    HOME / "OneDrive" / "Desktop",
]


def _buscar_vault() -> Path | None:
    """Encuentra el vault buscando la carpeta .obsidian."""
    forzado = _env("JARVIS_OBSIDIAN_VAULT")
    if forzado:
        ruta = Path(forzado)
        return ruta if ruta.is_dir() else None

    for base in _CANDIDATOS:
        if not base.is_dir():
            continue

        # ¿La carpeta misma es un vault?
        if (base / MARCA_VAULT).is_dir():
            return base

        # ¿Alguna subcarpeta directa lo es?
        try:
            for hijo in base.iterdir():
                if hijo.is_dir() and (hijo / MARCA_VAULT).is_dir():
                    return hijo
        except (PermissionError, OSError):
            continue

    return None


_vault_cache: Path | None = None
_buscado = False


def vault() -> Path | None:
    """Ruta del vault, buscada una sola vez."""
    global _vault_cache, _buscado
    if not _buscado:
        _vault_cache = _buscar_vault()
        _buscado = True
        if _vault_cache:
            log.info("Vault de Obsidian: %s", _vault_cache)
        else:
            log.info("No se encontró ningún vault de Obsidian.")
    return _vault_cache


def _nombre_nota(titulo: str) -> str:
    """Convierte un titulo hablado en un nombre de archivo valido para Obsidian."""
    limpio = (titulo or "").strip()
    limpio = re.sub(r'[<>:"|?*\\/]', "", limpio)
    limpio = re.sub(r"\s+", " ", limpio).strip()
    if not limpio:
        limpio = f"Nota {datetime.now():%Y-%m-%d %H%M}"
    if not limpio.lower().endswith(".md"):
        limpio += ".md"
    return limpio


def _frontmatter(etiquetas: str = "") -> str:
    """Cabecera YAML: fecha y origen, para poder filtrar despues en Obsidian."""
    lineas = [
        "---",
        f"fecha: {datetime.now():%Y-%m-%d %H:%M}",
        "origen: jarvis",
    ]
    if etiquetas:
        limpias = [e.strip().lstrip("#") for e in re.split(r"[,\s]+", etiquetas) if e.strip()]
        if limpias:
            lineas.append("tags: [" + ", ".join(limpias) + "]")
    lineas.append("---")
    return "\n".join(lineas)


# -------------------------------------------------------------------------
# OPERACIONES
# -------------------------------------------------------------------------
def crear_nota(titulo: str, contenido: str = "", etiquetas: str = "", carpeta: str = "") -> str:
    """Crea una nota nueva en el vault."""
    v = vault()
    if v is None:
        return (
            "No encontré tu vault de Obsidian. "
            "Añade JARVIS_OBSIDIAN_VAULT a tu archivo .env con la ruta."
        )

    destino = v / carpeta if carpeta else v
    try:
        destino.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"No pude preparar la carpeta: {e}"

    ruta = destino / _nombre_nota(titulo)

    # No pisamos una nota existente: eso seria destruir trabajo tuyo.
    if ruta.exists():
        return f"Ya existe una nota llamada {ruta.stem}. Dime 'agrega a la nota {ruta.stem}' si quieres ampliarla."

    cuerpo = [
        _frontmatter(etiquetas),
        "",
        f"# {Path(_nombre_nota(titulo)).stem}",
        "",
        contenido.strip() if contenido else "",
    ]

    try:
        ruta.write_text("\n".join(cuerpo).rstrip() + "\n", encoding="utf-8")
    except Exception as e:
        log.exception("Error creando nota")
        return f"No pude crear la nota: {e}"

    log.info("Nota creada: %s", ruta)
    return f"Creé la nota {ruta.stem} en tu vault de Obsidian."


def agregar_a_nota(titulo: str, contenido: str) -> str:
    """Añade texto al final de una nota existente. Si no existe, la crea."""
    v = vault()
    if v is None:
        return "No encontré tu vault de Obsidian."

    objetivo = _nombre_nota(titulo).lower()
    encontrada = None

    for raiz, dirs, ficheros in os.walk(v):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fichero in ficheros:
            if fichero.lower() == objetivo:
                encontrada = Path(raiz) / fichero
                break
        if encontrada:
            break

    if encontrada is None:
        return crear_nota(titulo, contenido)

    try:
        actual = encontrada.read_text(encoding="utf-8")
        marca = f"\n\n_{datetime.now():%d/%m %H:%M}_ — {contenido.strip()}\n"
        encontrada.write_text(actual.rstrip() + marca, encoding="utf-8")
    except Exception as e:
        return f"No pude escribir en la nota: {e}"

    log.info("Añadido a nota: %s", encontrada)
    return f"Añadido a {encontrada.stem}."


def agregar_al_diario(contenido: str) -> str:
    """
    Añade una linea a la nota diaria de hoy, creandola si hace falta.

    Busca la carpeta de diario que ya uses; si no hay ninguna, usa la raiz.
    """
    v = vault()
    if v is None:
        return "No encontré tu vault de Obsidian."

    hoy = f"{datetime.now():%Y-%m-%d}"

    # Carpetas tipicas de diario en Obsidian, en orden de preferencia.
    carpeta_diario = v
    for nombre in ("Diario", "Daily", "Daily Notes", "Journal", "Notas diarias", "00 Diario"):
        candidata = v / nombre
        if candidata.is_dir():
            carpeta_diario = candidata
            break

    ruta = carpeta_diario / f"{hoy}.md"

    try:
        if ruta.exists():
            actual = ruta.read_text(encoding="utf-8")
            ruta.write_text(
                actual.rstrip() + f"\n- {datetime.now():%H:%M} — {contenido.strip()}\n",
                encoding="utf-8",
            )
            accion = "Añadido a"
        else:
            cuerpo = [
                _frontmatter("diario"),
                "",
                f"# {hoy}",
                "",
                f"- {datetime.now():%H:%M} — {contenido.strip()}",
            ]
            ruta.write_text("\n".join(cuerpo) + "\n", encoding="utf-8")
            accion = "Creé"
    except Exception as e:
        return f"No pude escribir en el diario: {e}"

    log.info("Diario actualizado: %s", ruta)
    return f"{accion} tu nota de hoy en Obsidian."


def buscar_en_vault(texto: str, limite: int = 8) -> str:
    """Busca una frase dentro de todas las notas del vault."""
    v = vault()
    if v is None:
        return "No encontré tu vault de Obsidian."

    aguja = (texto or "").strip().lower()
    if not aguja:
        return "¿Qué quieres que busque en tus notas?"

    encontradas = []
    for raiz, dirs, ficheros in os.walk(v):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fichero in ficheros:
            if not fichero.lower().endswith(".md"):
                continue
            ruta = Path(raiz) / fichero
            try:
                contenido = ruta.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            # Coincidencia en el titulo o en el cuerpo.
            if aguja in fichero.lower() or aguja in contenido.lower():
                encontradas.append(ruta)
            if len(encontradas) >= limite:
                break
        if len(encontradas) >= limite:
            break

    if not encontradas:
        return f"No encontré '{texto}' en tus notas."

    if len(encontradas) == 1:
        return f"Encontré una nota: {encontradas[0].stem}."

    nombres = ", ".join(r.stem for r in encontradas[:5])
    return f"Encontré {len(encontradas)} notas: {nombres}."


def listar_notas_recientes(limite: int = 5) -> str:
    """Las notas que has tocado ultimamente."""
    v = vault()
    if v is None:
        return "No encontré tu vault de Obsidian."

    notas = []
    for raiz, dirs, ficheros in os.walk(v):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fichero in ficheros:
            if fichero.lower().endswith(".md"):
                ruta = Path(raiz) / fichero
                try:
                    notas.append((ruta, ruta.stat().st_mtime))
                except Exception:
                    continue

    if not notas:
        return "Tu vault está vacío."

    notas.sort(key=lambda x: x[1], reverse=True)
    nombres = ", ".join(r.stem for r, _ in notas[:limite])
    return f"Tus notas más recientes: {nombres}."


def estado_vault() -> str:
    """Dice si hay vault y cuanto tiene."""
    v = vault()
    if v is None:
        return (
            "No encontré ningún vault de Obsidian. "
            "Si lo tienes en otra ruta, añade JARVIS_OBSIDIAN_VAULT a tu .env."
        )

    total = 0
    for raiz, dirs, ficheros in os.walk(v):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        total += sum(1 for f in ficheros if f.lower().endswith(".md"))

    return f"Tu vault está en {v.name} y tiene {total} notas."
