"""Titulares de noticias por RSS: gratis, sin cuentas y sin API.

Por defecto lee Google Noticias de Colombia en espanol; con un tema
("noticias de inteligencia artificial") usa la busqueda de Google Noticias.
Se cambian las fuentes con JARVIS_NOTICIAS en el .env:

    JARVIS_NOTICIAS=general=https://...rss, tecnologia=https://...xml

Las peticiones pasan por la proteccion SSRF y se guardan 30 minutos, asi
"buenos dias" y una pregunta despues no bajan lo mismo dos veces. Los
titulares son texto de fuera: al modelo le llegan como dato externo y los
que parecen instrucciones se descartan.

La lectura de RSS y Atom parte del conector de OpenJarvis
(https://github.com/open-jarvis/OpenJarvis, licencia Apache-2.0:
src/openjarvis/connectors/news_rss.py). Cambios: urllib en vez de httpx, la
proteccion SSRF de seguridad.py, cache, fuentes en espanol y respuesta para
la voz. Ver NOTICE.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import seguridad
from registro import herramienta

log = logging.getLogger("jarvis.noticias")

FUENTES_POR_DEFECTO = {
    "general": "https://news.google.com/rss?hl=es-419&gl=CO&ceid=CO:es-419",
}
URL_TEMA = "https://news.google.com/rss/search?q={tema}&hl=es-419&gl=CO&ceid=CO:es-419"
AGENTE = "Mozilla/5.0 (Jarvis; lector RSS personal)"
MAX_BYTES = 3 * 1024 * 1024
MINUTOS_CACHE = 30
_cache: dict[str, tuple[float, list[dict]]] = {}


def fuentes() -> dict[str, str]:
    crudo = os.environ.get("JARVIS_NOTICIAS", "").strip()
    if not crudo:
        return dict(FUENTES_POR_DEFECTO)
    salida = {}
    for i, trozo in enumerate(t.strip() for t in crudo.split(",") if t.strip()):
        m = re.match(r"^([^=:/]+)=(https?://\S+)$", trozo)
        nombre, url = (m.group(1), m.group(2)) if m else (f"fuente{i + 1}", trozo)
        salida[nombre.strip().lower()] = url.strip()
    return salida or dict(FUENTES_POR_DEFECTO)


def _local(etiqueta: str) -> str:
    return etiqueta.rsplit("}", 1)[-1].lower()


def _texto(nodo, *nombres) -> str:
    for hijo in nodo:
        if _local(hijo.tag) in nombres:
            if _local(hijo.tag) == "link" and hijo.get("href"):
                return hijo.get("href")
            return (hijo.text or "").strip()
    return ""


def leer_feed(xml: bytes | str) -> list[dict]:
    """RSS 2.0 o Atom -> [{titulo, fuente, enlace, fecha}]."""
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as e:
        log.warning("Feed ilegible: %s", e)
        return []
    elementos = [n for n in raiz.iter() if _local(n.tag) in ("item", "entry")]
    salida = []
    for n in elementos:
        titulo = html.unescape(re.sub(r"<[^>]+>", "", _texto(n, "title"))).strip()
        if not titulo:
            continue
        fuente = _texto(n, "source")
        # Google Noticias pone "Titular - Medio": se separa para la voz.
        if not fuente and " - " in titulo:
            titulo, _, fuente = titulo.rpartition(" - ")
        elif fuente and titulo.endswith(" - " + fuente):
            titulo = titulo[: -len(" - " + fuente)]
        salida.append({"titulo": titulo.strip(), "fuente": fuente.strip(),
                       "enlace": _texto(n, "link"), "fecha": _texto(n, "pubdate", "updated", "published")})
    return salida


def _bajar(url: str) -> bytes:
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    with seguridad.abrir_url(peticion, timeout=10) as respuesta:
        return respuesta.read(MAX_BYTES + 1)[:MAX_BYTES]


def titulares(tema: str = "", cuantos: int = 5, bajar=None) -> list[dict]:
    """Los primeros titulares de un tema o de las fuentes configuradas."""
    bajar = bajar or _bajar
    tema = (tema or "").strip()
    fuentes_ = fuentes()
    if tema and tema.lower() in fuentes_:
        urls = [fuentes_[tema.lower()]]
    elif tema and tema.lower() not in ("general", "hoy", "del dia", "del día", "colombia"):
        urls = [URL_TEMA.format(tema=urllib.parse.quote_plus(tema))]
    else:
        urls = list(fuentes_.values())

    por_fuente = []
    for url in urls:
        cacheado = _cache.get(url)
        if cacheado and time.time() - cacheado[0] < MINUTOS_CACHE * 60:
            items = cacheado[1]
        else:
            try:
                items = leer_feed(bajar(url))
            except seguridad.URLBloqueada as e:
                log.warning("Fuente de noticias bloqueada %s: %s", url[:80], e)
                items = []
            except Exception as e:
                log.warning("No pude leer %s: %s", url[:80], e)
                items = []
            if items:
                _cache[url] = (time.time(), items)
        por_fuente.append(items)

    # Se mezclan las fuentes (uno de cada una por turno) en vez de leer
    # cinco seguidos de la primera.
    vistos, salida = set(), []
    for ronda in range(max((len(x) for x in por_fuente), default=0)):
        for items in por_fuente:
            if ronda >= len(items):
                continue
            item = items[ronda]
            clave = item["titulo"].lower()
            if clave in vistos or seguridad.escanear_inyeccion(item["titulo"]).peligroso:
                continue
            vistos.add(clave)
            salida.append(item)
            if len(salida) >= max(1, cuantos):
                return salida
    return salida


def para_voz(items: list[dict], tema: str = "") -> str:
    if not items:
        return ("No pude traer noticias ahora. Revisa la conexión o prueba en un rato."
                if not tema else f"No encontré noticias de {tema} ahora mismo.")
    cabeza = f"Titulares de {tema}" if tema else "Titulares de hoy"
    lineas = [f"{i}. {t['titulo']}" + (f" ({t['fuente']})" if t["fuente"] else "")
              for i, t in enumerate(items, 1)]
    return f"{cabeza}: " + " ".join(lineas)


@herramienta(
    descripcion="Titulares de noticias de hoy, en general o de un tema. Gratis, por RSS.",
    args={"tema": "Tema de las noticias, vacío para las generales", "cuantas": "Cuántos titulares (1 a 8)"},
    palabras=("noticias", "titulares", "que paso hoy", "que esta pasando", "actualidad"),
    fuente_externa=True, en_plan=True,
)
def noticias(tema: str = "", cuantas: int = 5) -> str:
    """Titulares de hoy, en general o de un tema."""
    try:
        cuantas = max(1, min(8, int(cuantas)))
    except (TypeError, ValueError):
        cuantas = 5
    return para_voz(titulares(tema, cuantas), tema)
