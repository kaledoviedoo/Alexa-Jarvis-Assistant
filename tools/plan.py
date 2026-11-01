"""Planificador de ordenes que necesitan varios pasos.

El modelo propone una secuencia y aqui se filtra: solo se ejecutan pasos de
un catalogo cerrado (lo marcado en_plan en el registro, nunca externo ni
irreversible), con un maximo de cinco. Los pasos inventados se descartan, y si uno falla se dice cuantos salieron de
verdad en vez de cantar exito.
"""

import json
import logging
import re

from config import MODO_DEDICADO
from registro import herramienta

log = logging.getLogger("jarvis.plan")

MAX_PASOS = 5



def _chat(**kwargs):
    """Habla con el modelo por el mismo sitio que el resto de Jarvis."""
    import ollama_client
    return ollama_client.chat_modelo(**kwargs)

@herramienta(
    descripcion="Busca notas de la bóveda relacionadas con un tema y dice sus títulos.",
    en_plan=True, args={"tema": "El tema de las notas"},
    palabras=("notas sobre", "apuntes sobre", "notas de"),
)
def buscar_notas(tema: str) -> str:
    from tools import estudio
    notas = estudio.buscar_por_relacion(tema, cuantas=5)
    if not notas:
        return f"No encontré notas sobre {tema}."
    return "Notas encontradas: " + ", ".join(n["titulo"] for n in notas)


@herramienta(
    descripcion="Busca en la memoria semántica (bóveda indexada) por significado, no por palabras exactas.",
    en_plan=True, args={"consulta": "Lo que se busca, dicho como sea"},
    palabras=("parecido a", "algo sobre", "lo que escribi", "por significado"),
)
def buscar_significado(consulta: str) -> str:
    from tools import memoria
    encontradas = memoria.buscar(consulta, cuantos=5)
    if not encontradas:
        return f"La memoria no tiene nada sobre {consulta}."
    return "Por significado: " + ", ".join(n["titulo"] for n in encontradas)


def _acciones() -> dict:
    """Lo que puede usar el planificador: lo marcado en_plan en el registro."""
    import registro
    return registro.para_el_plan()


def _descripcion_para_plan(h) -> str:
    if not h.parametros:
        return f"- {h.nombre}: {h.descripcion} (argumento: vacío)"
    partes = " | ".join(p for p, _, _ in h.parametros)
    return f"- {h.nombre}: {h.descripcion} (argumento: {partes})"


def _modelo():
    import modes
    import ollama_client
    perfil = modes.PERFILES[MODO_DEDICADO]
    modelo = perfil["modelo"]
    if modelo not in (ollama_client.modelos_instalados() or []):
        modelo = modes.perfil_actual()["modelo"]
    return modelo


def _pedir_plan(orden: str) -> list[dict]:
    """Le pide al modelo una lista de pasos. Devuelve [] si no sale nada util."""
    try:
        import ollama
    except ImportError:
        return []

    acciones = _acciones()
    catalogo = "\n".join(_descripcion_para_plan(h) for h in acciones.values())

    try:
        respuesta = _chat(
            model=_modelo(),
            messages=[
                {"role": "system", "content":
                    "Descompones una peticion en pasos. SOLO puedes usar estas "
                    f"acciones:\n{catalogo}\n\n"
                    "Responde SOLO con un JSON: una lista de objetos con las "
                    'claves "accion" y "argumento". Maximo '
                    f"{MAX_PASOS} pasos, y usa los menos posibles. "
                    "Nada de texto fuera del JSON."},
                {"role": "user", "content": orden},
            ],
            options={"temperature": 0.1, "num_predict": 400},
        )
        crudo = (respuesta.get("message", {}).get("content") or "").strip()
    except Exception as e:
        log.warning("El modelo no pudo planificar: %s", e)
        return []

    # El modelo suele envolver el JSON en explicaciones o en ```json.
    coincidencia = re.search(r"\[[\s\S]*\]", crudo)
    if not coincidencia:
        log.info("El plan no traía JSON: %r", crudo[:120])
        return []

    try:
        pasos = json.loads(coincidencia.group(0))
    except Exception as e:
        log.info("El JSON del plan no se puede leer: %s", e)
        return []

    if not isinstance(pasos, list):
        return []

    # Filtro: fuera todo lo que no este en el catalogo. Aqui es donde se cae
    # la mayor parte de lo que un modelo pequeño se inventa.
    validos = []
    for paso in pasos[:MAX_PASOS]:
        if not isinstance(paso, dict):
            continue
        accion = str(paso.get("accion", "")).strip()
        argumento = str(paso.get("argumento", "")).strip()
        h = acciones.get(accion)
        if h is not None and (argumento or not h.requeridos):
            validos.append({"accion": accion, "argumento": argumento})
        else:
            log.info("Paso descartado: %r", paso)

    return validos


def ejecutar(orden: str) -> str:
    """Planifica y ejecuta. Devuelve lo que REALMENTE paso."""
    orden = (orden or "").strip()
    if not orden:
        return "¿Qué quieres que haga?"

    pasos = _pedir_plan(orden)
    if not pasos:
        return ("No conseguí descomponer eso en pasos que sepa hacer. "
                "Pídemelo de una cosa a la vez.")

    log.info("Plan de %d pasos: %s", len(pasos),
             ", ".join(p["accion"] for p in pasos))

    resultados, fallos = [], 0

    import registro
    acciones = _acciones()

    for numero, paso in enumerate(pasos, 1):
        try:
            salida = registro.llamar_con_texto(acciones[paso["accion"]], paso["argumento"])
        except Exception as e:
            log.exception("Falló el paso %d", numero)
            salida = f"falló: {e}"
            fallos += 1

        texto = str(salida)
        if re.search(r"^\s*(no (encontr|tengo|pude|hay)|me falta)", texto, re.I):
            fallos += 1

        resultados.append({"paso": numero, "accion": paso["accion"], "salida": texto})
        log.info("Paso %d (%s): %s", numero, paso["accion"], texto[:100])

    return _resumir(orden, resultados, fallos)


def _resumir(orden: str, resultados: list[dict], fallos: int) -> str:
    """Cuenta que salio, sin adornar."""
    hechos = "\n".join(f"{r['paso']}. {r['accion']}: {r['salida'][:400]}"
                       for r in resultados)

    try:
        import ollama
        respuesta = _chat(
            model=_modelo(),
            messages=[
                {"role": "system", "content":
                    "Resumes en español lo que se hizo, para leerlo en voz "
                    "alta. Maximo cuatro frases, sin markdown ni listas. "
                    "Cuenta SOLO lo que dicen los resultados: si un paso no "
                    "encontro nada, dilo en vez de disimularlo."},
                {"role": "user", "content": f"Pediste: {orden}\n\nResultados:\n{hechos}"},
            ],
            options={"temperature": 0.2, "num_predict": 200},
        )
        resumen = (respuesta.get("message", {}).get("content") or "").strip()
    except Exception:
        resumen = ""

    if not resumen:
        # Sin modelo para resumir, se dicen los pasos tal cual.
        resumen = " ".join(f"{r['accion']}: {r['salida'][:120]}." for r in resultados)

    if fallos:
        total = len(resultados)
        resumen += f" Ojo: {fallos} de {total} pasos no dieron resultado."

    return resumen
