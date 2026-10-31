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

# Dos indices, mismo motor. Estaban juntos y el codigo se comio la boveda:
# 540 fragmentos del proyecto contra 105 de notas, asi que buscar algo de
# clase competia contra medio millar de trozos de Python.
ARCHIVOS = {
    "boveda": CARPETA_DATOS / "memoria_vault.json",
    "codigo": CARPETA_DATOS / "memoria_codigo.json",
}
ARCHIVO = ARCHIVOS["boveda"]          # nombre viejo, por si algo lo usaba

MODELO = "nomic-embed-text"

TAMANO_TROZO = 1000
SOLAPE = 150

_indices: dict[str, dict] = {}

# La matriz de numpy y su version, para no rehacerla en cada busqueda. Antes
# se reconstruia entera cada vez: 9 ms con 645 fragmentos, y sube en linea
# recta con el tamaño del indice.
_matrices: dict[str, tuple] = {}


def _numpy():
    try:
        import numpy
        return numpy
    except ImportError:
        return None


def _normalizar(vector: list[float]) -> list[float]:
    """Deja el vector de longitud 1.

    Se hace al INDEXAR, no al buscar. Con los vectores ya normalizados el
    coseno es un producto escalar y punto: no hay que calcular normas en cada
    consulta, que era trabajo repetido sobre datos que nunca cambian.
    """
    norma = math.sqrt(sum(x * x for x in vector))
    if norma == 0:
        return vector
    return [x / norma for x in vector]


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


def _vacio() -> dict:
    return {"generado": 0, "notas": {}, "trozos": [], "normalizado": True}


def _migrar_indice_unico() -> None:
    """Parte en dos el indice viejo que tenia la boveda y el codigo juntos.

    Se reparte por la ruta de cada fragmento en vez de re-vectorizarlo todo:
    volver a pasar 645 trozos por el modelo son varios minutos y ademas
    necesita Ollama funcionando, que no siempre es el caso justo cuando
    arrancas.
    """
    viejo = ARCHIVOS["boveda"]
    nuevo_codigo = ARCHIVOS["codigo"]

    if nuevo_codigo.is_file() or not viejo.is_file():
        return

    try:
        datos = json.loads(viejo.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("No pude leer el indice viejo para migrarlo: %s", e)
        return

    trozos = datos.get("trozos") or []
    raiz = str(RAIZ_PROYECTO)
    del_codigo = [t for t in trozos if str(t.get("ruta", "")).startswith(raiz)]
    if not del_codigo:
        return

    de_boveda = [t for t in trozos if not str(t.get("ruta", "")).startswith(raiz)]
    notas = datos.get("notas") or {}

    partir = lambda lista: {  # noqa: E731
        "generado": datos.get("generado", 0),
        "notas": {k: v for k, v in notas.items()
                  if (k.startswith(raiz)) == (lista is del_codigo)},
        "trozos": lista,
        "normalizado": datos.get("normalizado", False),
    }

    try:
        nuevo_codigo.write_text(json.dumps(partir(del_codigo)), encoding="utf-8")
        viejo.write_text(json.dumps(partir(de_boveda)), encoding="utf-8")
        log.info("Indice partido en dos: %d trozos de codigo, %d de la boveda",
                 len(del_codigo), len(de_boveda))
    except OSError as e:
        log.warning("No pude partir el indice: %s", e)


def _cargar(cual: str = "boveda") -> dict:
    if cual in _indices:
        return _indices[cual]

    _migrar_indice_unico()

    archivo = ARCHIVOS[cual]
    if archivo.is_file():
        try:
            indice = json.loads(archivo.read_text(encoding="utf-8"))

            # Los indices de antes guardaban los vectores sin normalizar. Se
            # normalizan una vez al cargarlos, para no tener que decidir en
            # cada busqueda cual de los dos formatos es.
            if not indice.get("normalizado"):
                for t in indice.get("trozos", []):
                    if t.get("v"):
                        t["v"] = _normalizar(t["v"])
                indice["normalizado"] = True
                log.info("Vectores de %s normalizados (formato viejo)", cual)

            _indices[cual] = indice
            log.info("Memoria %s: %d trozos de %d archivos", cual,
                     len(indice.get("trozos", [])), len(indice.get("notas", {})))
            return indice
        except Exception as e:
            log.warning("La memoria guardada de %s no sirve: %s", cual, e)

    _indices[cual] = _vacio()
    return _indices[cual]


def _guardar(indice: dict, cual: str = "boveda") -> None:
    indice["normalizado"] = True
    try:
        ARCHIVOS[cual].parent.mkdir(parents=True, exist_ok=True)
        ARCHIVOS[cual].write_text(json.dumps(indice), encoding="utf-8")
    except OSError as e:
        log.warning("No pude guardar la memoria de %s: %s", cual, e)
    _matrices.pop(cual, None)     # la matriz cacheada ya no vale


def _matriz(cual: str):
    """La matriz de numpy del indice, construida una sola vez.

    Se invalida al guardar. Con los vectores ya normalizados, buscar es
    `matriz @ consulta` y nada mas.
    """
    np = _numpy()
    if np is None:
        return None

    trozos = _cargar(cual).get("trozos") or []
    if not trozos:
        return None

    cacheada = _matrices.get(cual)
    if cacheada is not None and cacheada[1] == len(trozos):
        return cacheada[0]

    matriz = np.array([t["v"] for t in trozos], dtype="float32")
    _matrices[cual] = (matriz, len(trozos))
    return matriz


def indexar(forzar: bool = False) -> str:
    """Recorre la boveda y vectoriza lo que haga falta."""
    vault = obsidian.vault()
    if vault is None:
        return "No encuentro tu bóveda de Obsidian."

    if not modelo_disponible():
        return (f"Me falta el modelo {MODELO}. Instálalo con: ollama pull {MODELO}. "
                "Son unos 270 megas.")

    indice = _cargar("boveda")
    if forzar:
        indice = _vacio()

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
            vector = _normalizar(vector)
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
    _guardar(indice, "boveda")
    _indices["boveda"] = indice

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
                      "memoria_vault.json", "memoria_codigo.json",
                      "aplicaciones.json"}

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

    indice = _cargar("codigo")
    if forzar:
        indice = _vacio()

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
            vector = _normalizar(vector)
            trozos.append({
                "ruta": clave,
                "titulo": ruta.name,
                "n": i,
                "texto": trozo[:1400],
                "v": vector,
            })

        notas_previas[clave] = firma

    # Antes habia que filtrar por la ruta de la raiz para no borrar notas de
    # la boveda que vivian en el mismo indice. Ahora este indice es solo
    # codigo, asi que lo que no se vio es que ya no esta.
    borrados = set(notas_previas) - vistos
    for clave in borrados:
        notas_previas.pop(clave, None)
    if borrados:
        trozos = [t for t in trozos if t["ruta"] not in borrados]

    indice["trozos"] = trozos
    indice["generado"] = time.time()
    _guardar(indice, "codigo")
    _indices["codigo"] = indice

    tardo = time.perf_counter() - inicio
    log.info("Memoria del proyecto: %d nuevos, %d actualizados, %d borrados, %.1f s",
             nuevos, actualizados, len(borrados), tardo)

    if not nuevos and not actualizados and not borrados:
        return "El código ya estaba indexado."

    return (f"Indexé el proyecto: {nuevos} archivos nuevos, "
            f"{actualizados} actualizados.")


def buscar(consulta: str, cuantos: int = 5, minimo: float = 0.45,
           donde: str = "boveda") -> list[dict]:
    """Los fragmentos que mas se parecen en SIGNIFICADO a la consulta.

    `donde` elige el indice: "boveda" para tus notas, "codigo" para el propio
    proyecto. Estan separados a proposito (una busqueda de clase no tiene que
    competir contra medio millar de trozos de Python).
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return []

    trozos = _cargar(donde).get("trozos", [])
    if not trozos:
        return []

    vector = _vector(consulta)
    if vector is None:
        return []

    matriz = _matriz(donde)
    if matriz is not None:
        np = _numpy()
        # Los vectores del indice ya vienen normalizados, asi que basta con
        # normalizar el de la consulta y multiplicar: el coseno sale solo.
        objetivo = np.array(_normalizar(vector), dtype="float32")
        notas = matriz @ objetivo
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
    boveda = _cargar("boveda")
    codigo = _cargar("codigo")
    trozos = len(boveda.get("trozos", []))
    notas = len(boveda.get("notas", {}))

    if not trozos:
        if not modelo_disponible():
            return (f"No tengo memoria semántica todavía: falta el modelo. "
                    f"Instálalo con ollama pull {MODELO}")
        return "No tengo memoria semántica todavía. Dime indexa la bóveda."

    edad = (time.time() - boveda.get("generado", 0)) / 3600
    cuando = "hace menos de una hora" if edad < 1 else f"hace {edad:.0f} horas"
    respuesta = (f"Tengo {notas} notas en memoria, {trozos} fragmentos. "
                 f"Actualizada {cuando}.")

    del_codigo = len(codigo.get("trozos", []))
    if del_codigo:
        respuesta += (f" Del código llevo {del_codigo} fragmentos, "
                      f"en un índice aparte.")
    return respuesta
