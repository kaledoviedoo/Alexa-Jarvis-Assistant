"""Memoria semantica: buscar por significado, no por nombre.

Trocea las notas de la boveda y el codigo del proyecto, convierte cada trozo
en un vector con nomic-embed-text y los guarda. Buscar es vectorizar la
consulta y comparar por coseno, con numpy si esta disponible.

El codigo se trocea por definiciones usando el arbol sintactico, porque un
fragmento util es una funcion entera con su docstring y no quince lineas
cortadas por donde cayera.
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

TAMANO_TROZO = 1000
SOLAPE = 150

_indice: dict | None = None


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
    """Parte una nota en trozos aprovechables."""
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
    """Recorre la boveda y vectoriza lo que haga falta."""
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


RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

# Que se lee. Nada de binarios ni de datos: solo lo que un humano escribio.
EXTENSIONES_CODIGO = {".py", ".md", ".ps1", ".json", ".txt"}

# Que NO se lee nunca. El .env queda fuera por motivos obvios; el resto es
# ruido que ensuciaria las busquedas.
CARPETAS_IGNORADAS = {"__pycache__", ".git", ".venv", "venv", "node_modules",
                      ".pytest_cache", ".idea", ".vscode"}
ARCHIVOS_IGNORADOS = {".env", ".env.respaldo", "intenciones.json",
                      "memoria_vault.json", "aplicaciones.json"}

# Tope por archivo. nlu.py son cien mil caracteres: sin tope se comeria el
# indice entero y las busquedas devolverian siempre lo mismo.
MAXIMO_TROZOS_POR_ARCHIVO = 12


def _trozos_de_codigo(texto: str, ruta: Path) -> list[str]:
    """Parte un archivo de codigo por sus definiciones, no por parrafos."""
    if ruta.suffix.lower() != ".py":
        return _trozos(texto, ruta.stem)

    try:
        import ast
        arbol = ast.parse(texto)
    except (SyntaxError, ValueError):
        return _trozos(texto, ruta.stem)

    lineas = texto.splitlines()
    piezas = []

    # La cabecera del modulo: su docstring explica para que existe el archivo,
    # que suele ser lo mas util de todo para orientarse.
    docstring = ast.get_docstring(arbol)
    if docstring:
        piezas.append(f"[{ruta.name}] Para que sirve este archivo:\n{docstring}")

    for nodo in arbol.body:
        if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        desde = nodo.lineno - 1
        hasta = getattr(nodo, "end_lineno", nodo.lineno)
        cuerpo = "\n".join(lineas[desde:hasta])[:1800]
        piezas.append(f"[{ruta.name}] {nodo.name}\n{cuerpo}")

    if not piezas:
        return _trozos(texto, ruta.stem)

    return piezas[:MAXIMO_TROZOS_POR_ARCHIVO]


def _archivos_del_proyecto():
    """Los archivos del repo que vale la pena recordar."""
    for ruta in RAIZ_PROYECTO.rglob("*"):
        if not ruta.is_file():
            continue
        if ruta.suffix.lower() not in EXTENSIONES_CODIGO:
            continue
        if ruta.name in ARCHIVOS_IGNORADOS or ruta.name.startswith(".env"):
            continue
        if any(parte in CARPETAS_IGNORADAS for parte in ruta.parts):
            continue
        yield ruta


def indexar_proyecto(forzar: bool = False) -> str:
    """Mete el codigo del propio Jarvis en la memoria."""
    if not modelo_disponible():
        return (f"Me falta el modelo {MODELO}. Instálalo con: ollama pull {MODELO}.")

    indice = _cargar()
    notas_previas = indice["notas"]
    trozos = list(indice["trozos"])

    inicio = time.perf_counter()
    vistos, nuevos, actualizados = set(), 0, 0

    for ruta in _archivos_del_proyecto():
        clave = str(ruta)
        vistos.add(clave)
        firma = _firma(ruta)

        if not forzar and notas_previas.get(clave) == firma:
            continue

        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if clave in notas_previas:
            trozos = [t for t in trozos if t["ruta"] != clave]
            actualizados += 1
        else:
            nuevos += 1

        for i, trozo in enumerate(_trozos_de_codigo(texto, ruta)):
            vector = _vector(trozo)
            if vector is None:
                continue
            trozos.append({
                "ruta": clave,
                "titulo": ruta.name,
                "n": i,
                "texto": trozo[:1400],
                "v": vector,
            })

        notas_previas[clave] = firma

    del_proyecto = {c for c in notas_previas
                    if c.startswith(str(RAIZ_PROYECTO))}
    borrados = del_proyecto - vistos
    for clave in borrados:
        notas_previas.pop(clave, None)
    if borrados:
        trozos = [t for t in trozos if t["ruta"] not in borrados]

    indice["trozos"] = trozos
    indice["generado"] = time.time()
    _guardar(indice)

    global _indice
    _indice = indice

    tardo = time.perf_counter() - inicio
    log.info("Memoria del proyecto: %d nuevos, %d actualizados, %d borrados, %.1f s",
             nuevos, actualizados, len(borrados), tardo)

    if not nuevos and not actualizados and not borrados:
        return "El código ya estaba indexado."

    return (f"Indexé el proyecto: {nuevos} archivos nuevos, "
            f"{actualizados} actualizados.")


def buscar(consulta: str, cuantos: int = 5, minimo: float = 0.45) -> list[dict]:
    """Los fragmentos que mas se parecen en SIGNIFICADO a la consulta."""
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
