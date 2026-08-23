"""
Que Jarvis mejore solo con el uso.

La idea
-------
El router resuelve la mayoria de ordenes en menos de un milisegundo. Lo que no
reconoce se va al modelo: varios segundos, y a veces una respuesta regular.

Ese porcentaje hoy solo puede EMPEORAR: cada capacidad nueva trae frases que
nadie previo. Esto lo invierte. Se apunta que frases acaban en el modelo, y
cuando una se repite, se propone convertirla en un patron instantaneo.

Que NO hace
-----------
No toca el codigo. Escribe una propuesta y te la cuenta. Un asistente que se
reescribe solo el router mientras duermes es una idea preciosa hasta la
primera vez que se rompe a las tres de la mañana.
"""

import json
import logging
import re
import time
import unicodedata
from collections import Counter

from config import CARPETA_DATOS

log = logging.getLogger("jarvis.aprendizaje")

ARCHIVO = CARPETA_DATOS / "aprendizaje.jsonl"
PROPUESTAS = CARPETA_DATOS / "propuestas.md"

# A partir de cuantas repeticiones vale la pena proponer un patron. Con dos
# saldrian propuestas de cualquier cosa dicha dos veces por casualidad.
REPETICIONES = 3

_VACIAS = {"el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "o",
           "en", "para", "por", "con", "me", "mi", "a", "al", "es", "lo", "se"}


def _normalizar(texto: str) -> str:
    plano = "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower())
                    if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", plano)).strip()


def _esqueleto(texto: str) -> str:
    """
    La forma de la frase, sin los datos concretos.

    "crea un archivo llamado notas punto txt" y "crea un archivo llamado
    informe punto pdf" son la MISMA orden con distinto relleno. Sin esto,
    cada variante contaria como una frase nueva y nunca se repetiria nada.
    """
    plano = _normalizar(texto)
    plano = re.sub(r"\b\d+\b", "N", plano)
    palabras = [p for p in plano.split() if p not in _VACIAS]
    # Las primeras palabras son las que llevan el verbo y el objeto: es donde
    # esta la forma de la orden. El resto suele ser el contenido.
    return " ".join(palabras[:5])


def registrar(texto: str, origen: str, milisegundos: float) -> None:
    """Apunta una orden. Se llama en cada comando, asi que tiene que ser barato."""
    try:
        linea = json.dumps({
            "t": round(time.time()),
            "texto": (texto or "")[:200],
            "forma": _esqueleto(texto),
            "origen": origen,
            "ms": round(milisegundos),
        }, ensure_ascii=False)
        with ARCHIVO.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception as e:
        # Nunca puede romper una orden: es telemetria, no una funcion.
        log.debug("No pude registrar: %s", e)


def _leer(limite: int = 4000) -> list[dict]:
    if not ARCHIVO.is_file():
        return []
    try:
        lineas = ARCHIVO.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    salida = []
    for linea in lineas[-limite:]:
        try:
            salida.append(json.loads(linea))
        except Exception:
            continue
    return salida


def analizar() -> dict:
    """Que se repite, que va lento, y que se le atraganta."""
    registros = _leer()
    if not registros:
        return {}

    al_modelo = [r for r in registros if r.get("origen") == "modelo"]
    formas = Counter(r["forma"] for r in al_modelo if r.get("forma"))

    candidatas = []
    for forma, veces in formas.most_common(15):
        if veces < REPETICIONES or not forma:
            continue
        ejemplos = [r["texto"] for r in al_modelo if r.get("forma") == forma][:3]
        media = sum(r.get("ms", 0) for r in al_modelo if r.get("forma") == forma) / veces
        candidatas.append({
            "forma": forma,
            "veces": veces,
            "ejemplos": ejemplos,
            "ms_medio": round(media),
        })

    lentas = sorted((r for r in registros if r.get("ms", 0) > 4000),
                    key=lambda r: -r.get("ms", 0))[:5]

    return {
        "total": len(registros),
        "al_modelo": len(al_modelo),
        "porcentaje_router": round(100 * (1 - len(al_modelo) / max(len(registros), 1))),
        "candidatas": candidatas,
        "lentas": lentas,
    }


def escribir_propuestas() -> str:
    """Deja el informe en un archivo y devuelve el resumen hablado."""
    datos = analizar()
    if not datos:
        return "Todavía no tengo suficiente historial para aprender nada."

    candidatas = datos["candidatas"]

    lineas = [
        "# Propuestas de mejora de Jarvis",
        "",
        f"Generado el {time.strftime('%Y-%m-%d %H:%M')}.",
        "",
        f"- Órdenes registradas: {datos['total']}",
        f"- Resueltas por el router: {datos['porcentaje_router']} por ciento",
        f"- Delegadas al modelo: {datos['al_modelo']}",
        "",
    ]

    if candidatas:
        lineas += ["## Frases que se repiten y van al modelo", "",
                   "Cada una de estas tarda segundos y podría tardar milisegundos.",
                   ""]
        for c in candidatas:
            lineas.append(f"### {c['forma']}  ({c['veces']} veces, {c['ms_medio']} ms de media)")
            for ejemplo in c["ejemplos"]:
                lineas.append(f"- {ejemplo}")
            lineas.append("")
    else:
        lineas += ["## Nada que proponer", "",
                   "Ninguna frase se ha repetido lo suficiente yendo al modelo.", ""]

    if datos["lentas"]:
        lineas += ["## Las más lentas", ""]
        for r in datos["lentas"]:
            lineas.append(f"- {r.get('ms')} ms: {r.get('texto', '')[:80]}")

    try:
        PROPUESTAS.write_text("\n".join(lineas), encoding="utf-8")
    except OSError as e:
        log.warning("No pude escribir las propuestas: %s", e)

    if not candidatas:
        return (f"De {datos['total']} órdenes, el router resuelve el "
                f"{datos['porcentaje_router']} por ciento. No hay ninguna frase "
                "repetida que merezca un patrón nuevo.")

    principal = candidatas[0]
    return (f"De {datos['total']} órdenes resuelvo el {datos['porcentaje_router']} "
            f"por ciento al instante. Encontré {len(candidatas)} frases que se "
            f"repiten yendo al modelo; la más frecuente es "
            f"{principal['forma']}, {principal['veces']} veces. "
            "Lo dejé escrito en propuestas punto eme de.")


def resumen_corto() -> str:
    """Como va la cosa, en una frase. Instantaneo."""
    datos = analizar()
    if not datos:
        return "Aún no tengo historial suficiente."
    return (f"Llevo {datos['total']} órdenes registradas y resuelvo el "
            f"{datos['porcentaje_router']} por ciento sin usar el modelo.")
