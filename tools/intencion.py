"""
Entender lo que quisiste decir cuando no dijiste la palabra exacta.

El router de `nlu.py` es rapidisimo y resuelve nueve de cada diez ordenes en
menos de un milisegundo, pero es literal: reconoce "cierra spotify" y no
reconoce "quitame el spotify de encima". Cuando ninguna expresion encaja, la
orden se iba entera al modelo, que tarda segundos y a veces se inventa cosas.

Esto se mete justo en ese hueco. Compara el SIGNIFICADO de lo que dijiste con
el de un puñado de frases de ejemplo, y si se parece lo suficiente, traduce
tu frase a la version que el router SI entiende y la vuelve a pasar por el.

    "quitame el spotify de encima"  ~  "cierra spotify"    -> router
    "que tal anda la maquina"       ~  "estado del equipo" -> router
    "cuanto le queda a la grafica"  ~  "uso de gpu"        -> router

Por que traducir en vez de llamar a la herramienta directamente
---------------------------------------------------------------
Porque asi esto NO duplica nada. No hay una segunda tabla de manejadoras que
mantener en paralelo, ni riesgo de que las dos se desincronicen. Los ejemplos
solo apuntan a una frase canonica; toda la logica sigue viviendo en un unico
sitio, que es `nlu.INTENTS`. Si mañana cambia el comportamiento de "cierra X",
cambia para los dos caminos a la vez.

Lo que cuesta
-------------
Un vector de la frase (unos 30 ms con nomic-embed-text en local) y una
comparacion contra un centenar de vectores ya calculados, que en numpy es
tiempo despreciable. Los ejemplos se vectorizan UNA vez y se guardan en disco.

Frente a los varios segundos que cuesta el modelo, sale a cuenta. Y frente al
milisegundo del router no compite: esto solo corre cuando el router ya ha
dicho que no.
"""

import hashlib
import json
import logging

from config import CARPETA_DATOS
from tools import memoria

log = logging.getLogger("jarvis.intencion")

ARCHIVO = CARPETA_DATOS / "intenciones.json"

# A partir de que parecido se considera que quisiste decir eso.
#
# Con nomic-embed-text, dos formas de pedir lo mismo suelen quedar por encima
# de 0,80, y dos frases que no tienen nada que ver, por debajo de 0,65. El
# umbral va alto a proposito: equivocarse aqui significa EJECUTAR algo que no
# pediste, y eso es mucho peor que mandar la frase al modelo, que es lo que
# pasaba antes y como mucho tarda.
UMBRAL = 0.80

# Y ademas tiene que ganar por diferencia. Si "cierra spotify" y "abre
# spotify" empatan a 0,82, no sabemos cual quisiste: mejor que lo decida el
# modelo, que ve la frase entera, que jugarnosla a una centesima.
VENTAJA_MINIMA = 0.04

# -------------------------------------------------------------------------
# LOS EJEMPLOS
# -------------------------------------------------------------------------
# Cada entrada es:  frase canonica que el router entiende  ->  formas de
# decir lo mismo que el router NO entiende.
#
# No hace falta que esten todas las variantes imaginables: para eso esta el
# significado. Con tres o cuatro por intencion, bien distintas entre si, el
# vector ya cubre un espacio amplio. Poner veinte parecidas no añade nada y
# hace mas lenta la comparacion.
#
# Lo que SI importa es que las canonicas esten en nlu.INTENTS de verdad. Hay
# una prueba que lo comprueba, porque una canonica que el router no reconoce
# convierte esta capa en un agujero silencioso.
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

    # Ojo: aqui NO van las despedidas. "pausa" no la resuelve el router sino
    # `es_despedida`, antes de llegar a esto, y ademas cerrar la sesion por
    # parecido semantico es justo el error que mas molesta: te callas a media
    # frase porque algo sono parecido a "dejalo".
}


# -------------------------------------------------------------------------
# EL INDICE
# -------------------------------------------------------------------------
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


# -------------------------------------------------------------------------
# TRADUCIR
# -------------------------------------------------------------------------
def traducir(texto: str) -> tuple[str, float]:
    """
    Devuelve (frase canonica, parecido) o ("", 0.0) si no se parece a nada.

    Quien llama decide que hacer con eso. Aqui no se ejecuta nada: esta capa
    solo opina sobre que quisiste decir.
    """
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

    # La mejor de cada canonica, no las mejores en bruto: si una intencion
    # tiene cuatro ejemplos y otra uno, la primera copaba el podio y la
    # comprobacion de ventaja se volvia inutil.
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
