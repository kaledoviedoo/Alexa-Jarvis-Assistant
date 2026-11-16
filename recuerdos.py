"""Memoria a largo plazo: lo que Jarvis sabe de ti y no caduca.

La sesion recuerda las ultimas ordenes durante unos minutos; contexto.md lo
escribes tu a mano. Esto es lo de en medio: datos sueltos y duraderos ("mi
hermana se llama Ana", "prefiero los resumenes cortos") que entran por dos
caminos:

- Dichos: "recuerda que...", sin pasar por el modelo.
- Aprendidos: si en una orden cuentas algo de ti ("me gusta...", "trabajo
  en..."), el modelo pequeno lo saca en segundo plano. Solo de lo que TU
  dijiste, nunca de una web o un correo. Se puede apagar con JARVIS_APRENDER=0.

Antes de guardar se tapan claves (una contrasena no se guarda) y se pasa el
detector de inyecciones. Al modelo le llegan solo los que tienen que ver con
la orden, en pocas lineas. Todo vive en ~/.jarvis/recuerdos.json.

El extractor y el almacen parten de OpenJarvis (https://github.com/open-jarvis/OpenJarvis),
licencia Apache-2.0: src/openjarvis/memory/extractor.py y store.py. Cambios:
en espanol, filtro previo para no llamar al modelo en cada orden, cada dato
tiene que salir de palabras que dijiste, sin cola de eventos ni bloqueo entre
procesos, y relevancia por palabras para no gastar contexto. Ver NOTICE.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path

import seguridad
from config import CARPETA_DATOS

log = logging.getLogger("jarvis.recuerdos")

MAX_RECUERDOS = 300
MAX_CARACTERES = 200
MAX_POR_TURNO = 3
CARACTERES_PARA_EL_MODELO = 700

_lock = threading.RLock()
_ruta: Path | None = None
_hilo_aprender: threading.Thread | None = None


def _archivo() -> Path:
    return _ruta or (CARPETA_DATOS / "recuerdos.json")


def usar_archivo(ruta) -> None:
    """Las pruebas trabajan en un archivo temporal."""
    global _ruta
    _ruta = Path(ruta)


def aprender_activo() -> bool:
    return os.environ.get("JARVIS_APRENDER", "1").strip().lower() not in ("0", "false", "no")


# ---- Almacen ---------------------------------------------------------------

def _leer() -> list[dict]:
    try:
        datos = json.loads(_archivo().read_text(encoding="utf-8"))
        return [d for d in datos if isinstance(d, dict) and d.get("texto")]
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("recuerdos.json ilegible (%s): empiezo de cero sin borrarlo.", e)
        return []


def _escribir(recuerdos: list[dict]) -> None:
    ruta = _archivo()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(recuerdos, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporal, ruta)


def listar() -> list[dict]:
    with _lock:
        return _leer()


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ ]+", " ", t).strip()


_VACIAS = {"el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "que", "y", "o", "en",
           "para", "por", "con", "me", "mi", "mis", "a", "al", "es", "lo", "se", "le", "su", "sus",
           "yo", "tu", "te", "muy", "mas", "pero", "como", "cuando", "esta", "este", "eso", "esto",
           "hay", "tengo", "tiene", "son", "ser", "soy", "no", "si", "ya", "usuario"}


def _palabras(texto: str) -> set[str]:
    return {p for p in _plano(texto).split() if len(p) > 2 and p not in _VACIAS}


def _limpio(texto: str) -> str:
    texto = re.sub(r"\s+", " ", str(texto or "")).strip().strip("\"'").strip()
    texto = texto[:1].upper() + texto[1:] if texto else ""
    if len(texto) > MAX_CARACTERES:
        texto = texto[:MAX_CARACTERES].rsplit(" ", 1)[0]
    return texto.rstrip(" .") + "." if texto else ""


def _repetido(nuevo: str, existentes: list[dict]) -> dict | None:
    """El que ya dice lo mismo (o casi): mismas palabras con sentido."""
    p = _palabras(nuevo)
    for r in existentes:
        q = _palabras(r["texto"])
        if p and q and (p == q or (len(p & q) / len(p | q)) >= 0.8):
            return r
    return None


def guardar(texto: str, fuente: str = "dicho") -> tuple[bool, str]:
    """Guarda un dato. Devuelve (guardado, frase para el usuario)."""
    hecho = _limpio(texto)
    if len(_palabras(hecho)) == 0:
        return False, "No entendí qué tengo que recordar."
    if seguridad.quitar_credenciales(hecho) != hecho or re.search(
            r"\b(?:contrasenas?|password|clave|claves|pin|token|codigo\s+de\s+seguridad|cvv)\b", _plano(hecho)):
        return False, "Eso parece una contraseña o una clave. Por seguridad no la guardo."
    if seguridad.escanear_inyeccion(hecho).peligroso:
        log.warning("Recuerdo rechazado por parecer una instruccion: %r", hecho[:80])
        return False, "Eso parece una orden para mí, no un dato tuyo. No lo guardo."
    with _lock:
        recuerdos = _leer()
        igual = _repetido(hecho, recuerdos)
        if igual is not None:
            if fuente == "dicho" and igual.get("fuente") != "dicho":
                igual["fuente"] = "dicho"          # lo confirmaste tu: sube de categoria
                _escribir(recuerdos)
            return False, f"Eso ya lo sabía: {igual['texto']}"
        recuerdos.append({"id": uuid.uuid4().hex[:8], "texto": hecho, "fuente": fuente,
                          "momento": time.strftime("%Y-%m-%dT%H:%M:%S")})
        while len(recuerdos) > MAX_RECUERDOS:
            # Se va antes lo aprendido mas viejo; lo que dijiste tu, lo ultimo.
            aprendidos = [r for r in recuerdos[:-1] if r.get("fuente") != "dicho"]
            recuerdos.remove((aprendidos or recuerdos)[0])
        _escribir(recuerdos)
    log.info("Recuerdo nuevo (%s): %s", fuente, hecho)
    return True, f"Lo recordaré: {hecho}"


def olvidar(consulta: str) -> list[str]:
    """Borra los recuerdos que mejor encajan con la consulta. Devuelve lo borrado."""
    buscadas = _palabras(consulta)
    if not buscadas:
        return []
    with _lock:
        recuerdos = _leer()
        puntos = [(len(buscadas & _palabras(r["texto"])), r) for r in recuerdos]
        mejor = max((p for p, _ in puntos), default=0)
        if mejor == 0:
            return []
        borrar = [r for p, r in puntos if p == mejor]
        _escribir([r for r in recuerdos if r not in borrar])
    for r in borrar:
        log.info("Recuerdo olvidado: %s", r["texto"])
    return [r["texto"] for r in borrar]


def olvidar_todo() -> int:
    with _lock:
        n = len(_leer())
        _escribir([])
    log.info("Olvidados todos los recuerdos (%d).", n)
    return n


def resumen_para_voz(limite: int = 8) -> str:
    recuerdos = listar()
    if not recuerdos:
        return ("Todavía no sé nada tuyo aparte de tu archivo de contexto. "
                "Dime «recuerda que...» y lo guardo.")
    ultimos = recuerdos[-limite:]
    texto = " ".join(r["texto"] for r in reversed(ultimos))
    extra = f" Y {len(recuerdos) - limite} cosas más; en el chat las ves todas." if len(recuerdos) > limite else ""
    if len(recuerdos) == 1:
        return f"Sé una cosa de ti: {texto}"
    return f"Sé {len(recuerdos)} cosas de ti. Las últimas: {texto}{extra}"


def para_el_modelo(frase: str, max_caracteres: int = CARACTERES_PARA_EL_MODELO) -> str:
    """Los recuerdos que vienen al caso, para el prompt. Vacio si no hay."""
    recuerdos = listar()
    if not recuerdos or max_caracteres <= 0:
        return ""
    buscadas = _palabras(frase)
    total = sum(len(r["texto"]) + 3 for r in recuerdos)
    if total <= max_caracteres:
        elegidos = recuerdos
    else:
        # Primero los que comparten palabras con la orden; despues lo que
        # dijiste tu, de lo mas nuevo a lo mas viejo.
        def peso(par):
            i, r = par
            return (len(buscadas & _palabras(r["texto"])), r.get("fuente") == "dicho", i)
        elegidos, usados = [], 0
        for _, r in sorted(enumerate(recuerdos), key=peso, reverse=True):
            if usados + len(r["texto"]) + 3 > max_caracteres:
                continue
            elegidos.append(r)
            usados += len(r["texto"]) + 3
    return "\n".join(f"- {r['texto']}" for r in elegidos)


# ---- Aprender de lo que cuentas ---------------------------------------------

# Solo si la frase cuenta algo de ti. Asi el modelo no se llama en cada orden.
_CUENTA_ALGO = re.compile(
    r"\b(?:me\s+(?:gusta|gustan|encanta|encantan|interesa|interesan|llamo|dicen|choca|molesta)|"
    r"no\s+me\s+gusta|odio|prefiero|soy\s+(?:de|un|una|alergic|vegetarian|zurd|hincha|fan)|"
    r"trabajo\s+(?:en|como|de|para)|estudio\s+(?:en|ingenieria|medicina|derecho|\w+cion)|vivo\s+en|"
    r"naci\s+en|mi\s+(?:\w+\s+)?(?:se\s+llama|es|son|cumple|favorit[oa]|novia|novio|esposa|esposo|"
    r"hermana|hermano|mama|papa|jefe|perro|gato|equipo|carro|moto|universidad|trabajo)|"
    r"siempre\s+(?:uso|juego|escucho|trabajo|estudio)|suelo\s+\w+|uso\s+(?:siempre|mucho))\b")

_NO_APRENDER = re.compile(r"^\s*(?:jarvis\s*,?\s*)?(?:recuerda|acuerdate|olvida|borra|que\s+sabes)\b")

PROMPT_EXTRAER = (
    "Sacas datos duraderos sobre el usuario de UNA frase suya. Un buen dato sigue "
    "siendo cierto dentro de meses: gustos, preferencias, a que se dedica, personas "
    "cercanas y sus nombres, proyectos, equipo que usa, restricciones. Ignora lo que "
    "pide hacer ahora, lo pasajero (hoy, ahora, este rato) y la charla.\n"
    "Escribe cada dato en tercera persona y en espanol, corto (menos de 20 palabras), "
    "usando sus mismas palabras. Responde SOLO con una lista JSON de textos. "
    "Si no hay nada que valga la pena, responde []."
)


def cuenta_algo_de_si(frase: str) -> bool:
    plano = _plano(frase)
    return bool(_CUENTA_ALGO.search(plano)) and not _NO_APRENDER.search(plano)


def _como_lista(contenido: str) -> list[str]:
    m = re.search(r"\[.*\]", contenido or "", re.DOTALL)
    if m:
        try:
            datos = json.loads(m.group(0))
            if isinstance(datos, list):
                return [str(x) for x in datos]
        except ValueError:
            pass
    return [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", l) for l in (contenido or "").splitlines() if l.strip()]


def extraer(frase: str, chat=None, modelo: str = "") -> list[str]:
    """Datos que valen la pena en la frase. Nunca lanza."""
    try:
        if chat is None:
            import modes
            import ollama_client
            chat = ollama_client.chat_modelo
            perfil = modes.perfil_actual()
            modelo = modelo or perfil["modelo"]
            extra = {"keep_alive": perfil.get("keep_alive"),
                     "options": {"temperature": 0, "num_predict": 160}}
        else:
            extra = {}
        respuesta = chat(model=modelo, messages=[{"role": "system", "content": PROMPT_EXTRAER},
                                                 {"role": "user", "content": frase}], **extra)
        contenido = (respuesta.get("message") or {}).get("content", "")
    except Exception as e:
        log.debug("No pude extraer recuerdos: %s", e)
        return []

    dichas = _palabras(frase)
    datos = []
    for crudo in _como_lista(contenido):
        hecho = _limpio(crudo)
        propias = _palabras(hecho)
        # Que salga de lo que dijiste, no de la imaginacion del modelo: la
        # mitad de sus palabras con sentido tienen que estar en tu frase.
        if not propias or crudo.strip().lower() in ("[]", "none", "null", "ninguno"):
            continue
        if len(propias & dichas) < max(1, len(propias) // 2):
            log.debug("Descarto %r: no sale de la frase.", hecho)
            continue
        datos.append(hecho)
        if len(datos) >= MAX_POR_TURNO:
            break
    return datos


def aprender(frase: str, chat=None, modelo: str = "") -> list[str]:
    """Extrae y guarda. Devuelve lo nuevo que se guardo."""
    nuevos = []
    for hecho in extraer(frase, chat=chat, modelo=modelo):
        guardado, _ = guardar(hecho, fuente="aprendido")
        if guardado:
            nuevos.append(hecho)
    return nuevos


def quizas_aprender(frase: str, origen: str = "") -> bool:
    """Tras cada orden: si contaste algo de ti, se aprende en segundo plano.

    No se hace en modo gaming (el modelo va por CPU y molestaria al juego),
    ni con las ordenes de una rutina, ni si ya hay un aprendizaje en curso.
    """
    global _hilo_aprender
    if not aprender_activo() or origen == "rutina" or not cuenta_algo_de_si(frase):
        return False
    if seguridad.escanear_inyeccion(frase).peligroso:
        return False
    try:
        import modes
        if modes.modo_actual() == "gaming":
            return False
    except Exception:
        pass
    if _hilo_aprender is not None and _hilo_aprender.is_alive():
        return False
    _hilo_aprender = threading.Thread(target=aprender, args=(frase,), name="aprender", daemon=True)
    _hilo_aprender.start()
    return True


# ---- Herramientas para el modelo -------------------------------------------

from registro import herramienta  # noqa: E402


@herramienta(
    "recordar_dato",
    descripcion="Guarda para siempre un dato del usuario que te pidió recordar (gustos, nombres, "
                "preferencias). No sirve para recordatorios con hora.",
    riesgo="escritura", args={"dato": "El dato, con sus palabras: 'mi hermana se llama Ana'"},
    palabras=("recuerda que", "acuerdate", "no olvides", "memoriza"),
    corroboran=("recuerda", "acuerdate", "acuérdate", "olvides", "memoriza", "guarda", "apunta"),
)
def recordar_dato(dato: str) -> str:
    """Guarda un dato duradero del usuario."""
    return guardar(dato, fuente="dicho")[1]


@herramienta(
    "que_sabes_de_mi",
    descripcion="Lo que Jarvis recuerda del usuario: datos que pidió guardar o que contó.",
    palabras=("que sabes de mi", "que recuerdas", "tu memoria"),
)
def que_sabes_de_mi() -> str:
    """Lista lo que se recuerda del usuario."""
    return resumen_para_voz()
