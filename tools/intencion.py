"""Entender la orden cuando no dijiste la palabra exacta.

Se mete entre el router y el modelo. Compara el significado de tu frase con
el de unas frases de ejemplo y, si se parece lo suficiente, la traduce a la
version que el router SI entiende y la vuelve a pasar por el.

Traduce en vez de llamar a la herramienta para no duplicar logica: los
ejemplos apuntan a una frase canonica, y toda la logica sigue viviendo en
nlu.INTENTS. Si no se parece a nada, o si dos ordenes empatan, se calla y
deja pasar la frase al modelo.
"""

import hashlib
import json
import logging

from config import CARPETA_DATOS
from tools import memoria

log = logging.getLogger("jarvis.intencion")

ARCHIVO = CARPETA_DATOS / "intenciones.json"

UMBRAL = 0.80

VENTAJA_MINIMA = 0.04

EJEMPLOS: dict[str, list[str]] = {
    # ---- Estado del equipo ----
    "estado del equipo": [
        "que tal anda la maquina",
        "como va todo por ahi",
        "dame un panorama del pc",
        "como esta el computador",
    ],
    "uso de cpu": [
        "que tan cargado esta el procesador",
        "el micro esta sufriendo",
    ],
    "uso de gpu": [
        "cuanto le queda a la grafica",
        "como va la tarjeta de video",
        "queda vram libre",
    ],
    "uso de ram": [
        "cuanta memoria me queda",
        "estoy quedandome sin memoria",
    ],
    "cuanto espacio libre tengo en el disco": [
        "me estoy quedando sin espacio",
        "cuanto disco me queda",
    ],
    "que programas estan consumiendo mas": [
        "que me esta comiendo los recursos",
        "quien esta chupando la memoria",
        "que hay corriendo que pese",
    ],

    # ---- Aplicaciones ----
    "cierra spotify": [
        "quitame el spotify de encima",
        "mata el spotify",
        "sacame spotify",
    ],
    "cierra todo": [
        "cierrame todo lo que hay abierto",
        "limpia el escritorio de ventanas",
        "sacame todos los programas",
    ],

    # ---- Archivos ----
    "que archivos hay en el escritorio": [
        "que tengo tirado en el escritorio",
        "muestrame lo del escritorio",
    ],
    "archivos mas grandes": [
        "que me esta ocupando el disco",
        "cual es el archivo mas pesado",
        "que puedo borrar para ganar espacio",
    ],
    "que archivos he tocado hoy": [
        "en que estuve trabajando",
        "que abri ultimamente",
    ],

    # ---- Seleccion ----
    "que tengo seleccionado": [
        "cuales cogiste",
        "que llevas en la mano",
    ],
    "olvida la seleccion": [
        "deja eso",
        "suelta lo que cogiste",
        "cancela lo de los archivos",
    ],

    # ---- Modos ----
    "activa el modo gaming": [
        "me voy a jugar un rato",
        "libera la grafica que voy a jugar",
        "necesito la maquina para el juego",
    ],
    "vuelve al modo normal": [
        "ya termine de jugar",
        "deja todo como estaba",
    ],
    "en que modo estas": [
        "como estas configurado",
        "con que modelo andas",
    ],

    # ---- Rendimiento ----
    "por que tengo lag": [
        "esto va lentisimo",
        "se me esta trabando todo",
        "por que va tan pesado",
    ],

    # ---- Pantalla ----
    "lee la pantalla": [
        "que dice ahi",
        "que tengo delante",
        "leeme lo que hay",
    ],

}


_indice: dict | None = None


def _firma() -> str:
    """Huella de los ejemplos, para saber si el indice guardado sirve."""
    crudo = json.dumps(EJEMPLOS, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:16]


def _construir() -> dict:
    """Vectoriza todos los ejemplos. Tarda, pero solo pasa cuando cambian."""
    entradas = []
    for canonica, variantes in EJEMPLOS.items():
        # La canonica tambien entra: a veces dices casi exactamente eso pero
        # con una palabra que rompe el regex.
        for frase in [canonica] + variantes:
            vector = memoria._vector(frase)
            if vector:
                entradas.append({"frase": frase, "canonica": canonica, "v": vector})

    log.info("Intenciones vectorizadas: %d ejemplos de %d ordenes",
             len(entradas), len(EJEMPLOS))
    return {"firma": _firma(), "entradas": entradas}


def cargar(forzar: bool = False) -> dict:
    global _indice

    if _indice is not None and not forzar:
        return _indice

    if not forzar and ARCHIVO.is_file():
        try:
            guardado = json.loads(ARCHIVO.read_text(encoding="utf-8"))
            # Si los ejemplos cambiaron, el indice guardado ya no vale.
            if guardado.get("firma") == _firma():
                _indice = guardado
                return _indice
            log.info("Los ejemplos cambiaron: hay que rehacer el indice.")
        except Exception as e:
            log.warning("No pude leer %s: %s", ARCHIVO, e)

    _indice = _construir()
    try:
        ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVO.write_text(json.dumps(_indice), encoding="utf-8")
    except OSError as e:
        log.warning("No pude guardar el indice de intenciones: %s", e)

    return _indice


def preparar_en_segundo_plano() -> None:
    import threading
    threading.Thread(target=cargar, daemon=True, name="intenciones").start()


def traducir(texto: str) -> tuple[str, float]:
    """Devuelve (frase canonica, parecido) o ("", 0.0) si no se parece a nada."""
    texto = (texto or "").strip()
    if len(texto) < 4:
        return "", 0.0

    indice = cargar()
    entradas = indice.get("entradas") or []
    if not entradas:
        return "", 0.0

    vector = memoria._vector(texto)
    if not vector:
        return "", 0.0

    np = memoria._numpy()
    if np is not None:
        matriz = np.array([e["v"] for e in entradas], dtype="float32")
        consulta = np.array(vector, dtype="float32")
        normas = np.linalg.norm(matriz, axis=1) * np.linalg.norm(consulta)
        normas[normas == 0] = 1e-9
        notas = (matriz @ consulta) / normas
    else:
        notas = [memoria._parecido(vector, e["v"]) for e in entradas]

    mejor_por_canonica: dict[str, float] = {}
    for entrada, nota in zip(entradas, notas):
        canonica = entrada["canonica"]
        valor = float(nota)
        if valor > mejor_por_canonica.get(canonica, -1.0):
            mejor_por_canonica[canonica] = valor

    ordenadas = sorted(mejor_por_canonica.items(), key=lambda x: x[1], reverse=True)
    canonica, nota = ordenadas[0]

    if nota < UMBRAL:
        log.info("Nada se parece lo bastante a %r (mejor: %s con %.2f)",
                 texto[:50], canonica, nota)
        return "", float(nota)

    if len(ordenadas) > 1 and (nota - ordenadas[1][1]) < VENTAJA_MINIMA:
        log.info("Empate entre %r (%.2f) y %r (%.2f) para %r: que decida el modelo",
                 canonica, nota, ordenadas[1][0], ordenadas[1][1], texto[:50])
        return "", float(nota)

    log.info("Intencion: %r se parece a %r (%.2f)", texto[:50], canonica, nota)
    return canonica, float(nota)


def disponible() -> bool:
    """Sin el modelo de vectores esto no puede funcionar."""
    return memoria.modelo_disponible()
