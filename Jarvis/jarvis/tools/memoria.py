"""
Memoria semantica de la boveda: buscar por SIGNIFICADO, no por palabras.

El problema
-----------
Buscar por texto encuentra lo que comparte letras. Pero "normalizar tablas" y
"tercera forma normal" son lo mismo sin compartir casi nada, y "el profe que
da redes" no comparte una palabra con la nota que se llama "Andres Ramirez".
Ahi es donde la busqueda por palabras se queda corta, y es justo el caso de
alguien que recuerda la IDEA y no el titulo.

Como funciona
-------------
Cada trozo de nota se convierte en un vector de 768 numeros con
nomic-embed-text, un modelo pequeño de Ollama hecho para esto. Los textos que
significan cosas parecidas caen cerca en ese espacio, aunque no compartan ni
una palabra. Buscar es convertir tu frase en otro vector y mirar cual esta mas
cerca.

Sobre el reloj
--------------
Indexar es lento: hay que pasar cada nota por el modelo. Se hace UNA vez en
segundo plano, y despues solo lo que cambie (se compara la fecha y un hash del
contenido). BUSCAR es rapido: un vector para tu frase y una multiplicacion.
Eso si cabe en los ocho segundos de Alexa.
"""

import hashlib
import json
import logging
import math
import re
import time
from pathlib import Path

from config import CARPETA_DATOS
from tools import obsidian

log = logging.getLogger("jarvis.memoria")

ARCHIVO = CARPETA_DATOS / "memoria_vault.json"

MODELO = "nomic-embed-text"

# Trozos de unos mil caracteres. Una nota entera en un solo vector diluye los
# temas: si habla de tres cosas, el vector queda en un punto medio que no se
# parece a ninguna. Trozos mas pequeños tampoco: pierden el contexto.
TAMANO_TROZO = 1000
SOLAPE = 150

_indice: dict | None = None


# -------------------------------------------------------------------------
# VECTORES
# -------------------------------------------------------------------------
def _numpy():
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def _vector(texto: str) -> list[float] | None:
    """Convierte un texto en su vector. None si Ollama no puede."""
    try:
        import ollama
    except ImportError:
        return None

    try:
        respuesta = ollama.embeddings(model=MODELO, prompt=texto[:8000])
        return respuesta.get("embedding")
    except Exception as e:
        log.debug("No pude vectorizar: %s", e)
        return None


def modelo_disponible() -> bool:
    try:
        import ollama_client
        instalados = ollama_client.modelos_instalados() or []
        return any(MODELO in m for m in instalados)
    except Exception:
        return False


def _parecido(a: list[float], b: list[float]) -> float:
    """Coseno entre dos vectores: 1 es identico, 0 no tiene nada que ver."""
    producto = sum(x * y for x, y in zip(a, b))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if not norma_a or not norma_b:
        return 0.0
    return producto / (norma_a * norma_b)


# -------------------------------------------------------------------------
# TROCEAR
# -------------------------------------------------------------------------
def _partir_largo(parrafo: str) -> list[str]:
    """Parte un parrafo que por si solo ya pasa del tamaño de trozo."""
    piezas = []
    resto = parrafo
    while len(resto) > TAMANO_TROZO:
        # Cortamos en el ultimo punto o salto que quepa, para no partir una
        # frase por la mitad: media frase vectoriza a cualquier cosa.
        corte = max(resto.rfind(". ", 0, TAMANO_TROZO),
                    resto.rfind("\n", 0, TAMANO_TROZO))
        if corte < TAMANO_TROZO // 2:
            corte = TAMANO_TROZO
        piezas.append(resto[:corte].strip())
        resto = resto[max(0, corte - SOLAPE):]
    if resto.strip():
        piezas.append(resto.strip())
    return piezas


def _trozos(texto: str, titulo: str) -> list[str]:
    """
    Parte una nota en trozos aprovechables.

    Dos cosas que parecen detalles y no lo son:

    - El titulo va DENTRO de cada trozo. Es la señal mas fuerte de que trata
      la nota, y sin el se pierde al vectorizar el cuerpo suelto.
    - Nada de trozos minusculos. Un fragmento de cuarenta caracteres vectoriza
      a un punto casi aleatorio y luego aparece como resultado de cualquier
      busqueda. Mejor pegarlo al siguiente.
    """
    limpio = re.sub(r"\n{3,}", "\n\n", (texto or "").strip())
    if not limpio:
        return []

    if len(limpio) <= TAMANO_TROZO:
        return [f"{titulo}\n\n{limpio}"]

    # Primero desmenuzamos los parrafos que ya son mas grandes que un trozo.
    parrafos = []
    for parrafo in limpio.split("\n\n"):
        if len(parrafo) > TAMANO_TROZO:
            parrafos.extend(_partir_largo(parrafo))
        else:
            parrafos.append(parrafo)

    partes, actual = [], ""
    for parrafo in parrafos:
        if actual and len(actual) + len(parrafo) + 2 > TAMANO_TROZO:
            partes.append(actual.strip())
            actual = actual[-SOLAPE:].strip() + "\n\n" + parrafo
        else:
            actual += ("\n\n" if actual else "") + parrafo

    if actual.strip():
        partes.append(actual.strip())

    # Pegamos los restos cortos al trozo anterior.
    fusionados = []
    for parte in partes:
        if fusionados and len(parte) < 250:
            fusionados[-1] += "\n\n" + parte
        else:
            fusionados.append(parte)

    return [f"{titulo}\n\n{parte}" for parte in fusionados]


def _firma(ruta: Path) -> str:
    """Identifica el contenido de un archivo para saber si cambio."""
    try:
        datos = ruta.stat()
        crudo = f"{datos.st_mtime_ns}:{datos.st_size}".encode()
        return hashlib.sha1(crudo).hexdigest()[:16]
    except OSError:
        return ""


# -------------------------------------------------------------------------
# INDICE
# -------------------------------------------------------------------------
def _cargar() -> dict:
    global _indice
    if _indice is not None:
        return _indice

    if ARCHIVO.is_file():
        try:
            _indice = json.loads(ARCHIVO.read_text(encoding="utf-8"))
            log.info("Memoria cargada: %d trozos de %d notas",
                     len(_indice.get("trozos", [])), len(_indice.get("notas", {})))
            return _indice
        except Exception as e:
            log.warning("La memoria guardada no sirve: %s", e)

    _indice = {"generado": 0, "notas": {}, "trozos": []}
    return _indice


def _guardar(indice: dict) -> None:
    try:
        ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVO.write_text(json.dumps(indice), encoding="utf-8")
    except OSError as e:
        log.warning("No pude guardar la memoria: %s", e)


def indexar(forzar: bool = False) -> str:
    """
    Recorre la boveda y vectoriza lo que haga falta.

    Solo toca lo que cambio: comparar la firma de cada archivo cuesta
    microsegundos, y vectorizar cuesta cientos de milisegundos. En una boveda
    que ya esta indexada, esto termina casi al instante.
    """
    vault = obsidian.vault()
    if vault is None:
        return "No encuentro tu bóveda de Obsidian."

    if not modelo_disponible():
        return (f"Me falta el modelo {MODELO}. Instálalo con: ollama pull {MODELO}. "
                "Son unos 270 megas.")

    indice = _cargar()
    if forzar:
        indice = {"generado": 0, "notas": {}, "trozos": []}

    notas_previas = indice["notas"]
    trozos = [t for t in indice["trozos"]]

    inicio = time.perf_counter()
    vistas, nuevas, actualizadas = set(), 0, 0

    for ruta in vault.rglob("*.md"):
        if any(parte.startswith(".") for parte in ruta.parts):
            continue
        # Las plantillas no son conocimiento: son formularios vacios.
        if "template" in str(ruta).lower() or "plantilla" in str(ruta).lower():
            continue

        clave = str(ruta)
        vistas.add(clave)
        firma = _firma(ruta)

        if not forzar and notas_previas.get(clave) == firma:
            continue

        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # Fuera los trozos viejos de esta nota antes de meter los nuevos.
        if clave in notas_previas:
            trozos = [t for t in trozos if t["ruta"] != clave]
            actualizadas += 1
        else:
            nuevas += 1

        for i, trozo in enumerate(_trozos(texto, ruta.stem)):
            vector = _vector(trozo)
            if vector is None:
                continue
            trozos.append({
                "ruta": clave,
                "titulo": ruta.stem,
                "n": i,
                "texto": trozo[:1400],
                "v": vector,
            })

        notas_previas[clave] = firma

    # Notas borradas: fuera del indice.
    borradas = set(notas_previas) - vistas
    for clave in borradas:
        notas_previas.pop(clave, None)
    if borradas:
        trozos = [t for t in trozos if t["ruta"] not in borradas]

    indice["trozos"] = trozos
    indice["generado"] = time.time()
    _guardar(indice)

    global _indice
    _indice = indice

    tardo = time.perf_counter() - inicio
    log.info("Memoria: %d nuevas, %d actualizadas, %d borradas, %d trozos, %.1f s",
             nuevas, actualizadas, len(borradas), len(trozos), tardo)

    if not nuevas and not actualizadas and not borradas:
        return f"La memoria ya estaba al día: {len(trozos)} fragmentos indexados."

    partes = []
    if nuevas:
        partes.append(f"{nuevas} notas nuevas")
    if actualizadas:
        partes.append(f"{actualizadas} actualizadas")
    if borradas:
        partes.append(f"{len(borradas)} borradas")

    return (f"Memoria al día: {', '.join(partes)}. "
            f"{len(trozos)} fragmentos en total, en {tardo:.0f} segundos.")


def indexar_en_segundo_plano(forzar: bool = False) -> None:
    import threading
    threading.Thread(target=lambda: indexar(forzar), daemon=True,
                     name="memoria-vault").start()


# -------------------------------------------------------------------------
# BUSCAR
# -------------------------------------------------------------------------
def buscar(consulta: str, cuantos: int = 5, minimo: float = 0.45) -> list[dict]:
    """
    Los fragmentos que mas se parecen en SIGNIFICADO a la consulta.

    Rapido: un vector para la consulta y una comparacion contra el indice.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return []

    indice = _cargar()
    trozos = indice.get("trozos", [])
    if not trozos:
        return []

    vector = _vector(consulta)
    if vector is None:
        return []

    np = _numpy()
    if np is not None:
        # Con numpy son milisegundos aunque haya miles de fragmentos.
        matriz = np.array([t["v"] for t in trozos], dtype="float32")
        objetivo = np.array(vector, dtype="float32")
        normas = np.linalg.norm(matriz, axis=1) * np.linalg.norm(objetivo)
        normas[normas == 0] = 1e-9
        notas = (matriz @ objetivo) / normas
        orden = np.argsort(-notas)[: cuantos * 3]
        candidatos = [(float(notas[i]), trozos[i]) for i in orden]
    else:
        candidatos = sorted(
            ((_parecido(vector, t["v"]), t) for t in trozos),
            key=lambda par: par[0], reverse=True,
        )[: cuantos * 3]

    # Una sola nota puede acaparar los primeros puestos con varios fragmentos.
    # Nos quedamos con el mejor de cada nota: quieres notas distintas.
    vistas, salida = set(), []
    for nota, trozo in candidatos:
        if nota < minimo or trozo["ruta"] in vistas:
            continue
        vistas.add(trozo["ruta"])
        salida.append({
            "titulo": trozo["titulo"],
            "ruta": trozo["ruta"],
            "texto": trozo["texto"],
            "parecido": round(nota, 3),
        })
        if len(salida) >= cuantos:
            break

    return salida


def estado() -> str:
    indice = _cargar()
    trozos = len(indice.get("trozos", []))
    notas = len(indice.get("notas", {}))

    if not trozos:
        if not modelo_disponible():
            return (f"No tengo memoria semántica todavía: falta el modelo. "
                    f"Instálalo con ollama pull {MODELO}")
        return "No tengo memoria semántica todavía. Dime indexa la bóveda."

    edad = (time.time() - indice.get("generado", 0)) / 3600
    cuando = "hace menos de una hora" if edad < 1 else f"hace {edad:.0f} horas"
    return f"Tengo {notas} notas en memoria, {trozos} fragmentos. Actualizada {cuando}."
