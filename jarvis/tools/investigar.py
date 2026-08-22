"""
Buscar en internet y RAZONAR sobre lo que sale.

La diferencia con "busca X en Comet"
------------------------------------
Esa orden abre el navegador y te deja a ti leyendo. Esta trae los resultados
al equipo, se los da al modelo local y te devuelve una conclusion: cual es mas
barato, cual conviene, que dicen los que ya lo probaron.

Todo se queda en casa: la busqueda sale a internet, pero el razonamiento lo
hace Ollama en tu maquina. Ninguna API de pago y ninguna clave que guardar.

Por que va en segundo plano, siempre
------------------------------------
Buscar tarda un par de segundos, y leer diez resultados y compararlos son
miles de tokens: quince segundos largos con un modelo local. Alexa concede
ocho. Se contesta al momento y el resultado se recoge con "como quedo lo
ultimo".
"""

import html
import json
import logging
import re
import urllib.parse
import urllib.request

from config import MODO_DEDICADO

log = logging.getLogger("jarvis.investigar")

# DuckDuckGo tiene una version sin JavaScript pensada para navegadores viejos.
# Devuelve HTML plano y sin clave de API, que es justo lo que hace falta.
URL_BUSQUEDA = "https://html.duckduckgo.com/html/?q={consulta}"

AGENTE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

MAX_RESULTADOS = 8
MAX_CARACTERES = 5000

_ETIQUETA = re.compile(r"<[^>]+>")
# Plan B por si cambian las clases: solo los titulos de los enlaces.
_SOLO_TITULOS = re.compile(r'class="result__a"[^>]*>(.*?)</a>', re.DOTALL)

_RESULTADO = re.compile(
    r'result__a[^>]*>(?P<titulo>.*?)</a>.*?'
    r'result__snippet[^>]*>(?P<resumen>.*?)</a>',
    re.DOTALL,
)


def _texto_limpio(crudo: str) -> str:
    return html.unescape(_ETIQUETA.sub("", crudo or "")).strip()


def buscar(consulta: str, cuantos: int = MAX_RESULTADOS) -> list[dict]:
    """Devuelve titulo y resumen de los primeros resultados."""
    consulta = (consulta or "").strip()
    if not consulta:
        return []

    url = URL_BUSQUEDA.format(consulta=urllib.parse.quote_plus(consulta))
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE})

    try:
        with urllib.request.urlopen(peticion, timeout=12) as respuesta:
            pagina = respuesta.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.warning("La búsqueda falló: %s", e)
        return []

    salida = []
    for coincidencia in _RESULTADO.finditer(pagina):
        titulo = _texto_limpio(coincidencia.group("titulo"))
        resumen = _texto_limpio(coincidencia.group("resumen"))
        if not titulo:
            continue
        salida.append({"titulo": titulo, "resumen": resumen})
        if len(salida) >= cuantos:
            break

    # Plan B: si la pagina llego pero no encajo el patron, es que DuckDuckGo
    # cambio sus clases de CSS. Sacamos al menos los titulos, que es mejor que
    # devolver nada, y lo dejamos escrito en el registro para saber POR QUE
    # las respuestas salieron pobres.
    if not salida and len(pagina) > 2000:
        log.warning(
            "La búsqueda devolvió %d caracteres pero el analizador no encontró "
            "nada: probablemente cambió el formato de la página.", len(pagina)
        )
        for coincidencia in _SOLO_TITULOS.finditer(pagina):
            titulo = _texto_limpio(coincidencia.group(1))
            if len(titulo) > 8:
                salida.append({"titulo": titulo, "resumen": ""})
            if len(salida) >= cuantos:
                break

    if not salida:
        log.warning("Búsqueda %r sin resultados (página de %d caracteres)",
                    consulta, len(pagina))

    log.info("Búsqueda %r: %d resultados", consulta, len(salida))
    return salida


def diagnostico() -> str:
    """
    Comprueba que la busqueda funciona de verdad.

    Sirve para separar tres fallos que se parecen mucho desde fuera: no hay
    internet, DuckDuckGo bloquea, o el analizador se quedo obsoleto.
    """
    url = URL_BUSQUEDA.format(consulta=urllib.parse.quote_plus("prueba"))
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE})

    try:
        with urllib.request.urlopen(peticion, timeout=12) as respuesta:
            pagina = respuesta.read().decode("utf-8", errors="ignore")
            estado = respuesta.status
    except Exception as e:
        return f"No hay conexión con el buscador: {e}"

    encaja = len(_RESULTADO.findall(pagina))
    titulos = len(_SOLO_TITULOS.findall(pagina))

    if encaja:
        return f"Búsqueda correcta: estado {estado}, {encaja} resultados completos."
    if titulos:
        return (f"La página llega (estado {estado}) pero solo saco títulos, "
                f"{titulos}. DuckDuckGo cambió su formato: hay que actualizar "
                "el patrón en tools/investigar.py")
    return (f"La página llega (estado {estado}, {len(pagina)} caracteres) pero no "
            "reconozco nada. Puede que el buscador esté pidiendo captcha.")


def _material(resultados: list[dict]) -> str:
    trozos, total = [], 0
    for i, r in enumerate(resultados, 1):
        trozo = f"{i}. {r['titulo']}\n   {r['resumen']}\n"
        if total + len(trozo) > MAX_CARACTERES:
            break
        trozos.append(trozo)
        total += len(trozo)
    return "".join(trozos)


def _razonar(instruccion: str, material: str) -> str:
    import modes
    import ollama_client

    try:
        import ollama
    except ImportError:
        return "No tengo Ollama instalado."

    # Comparar opciones es justo lo que el modelo pequeño hace peor: mezcla
    # cifras de resultados distintos. Usamos el grande si esta descargado.
    perfil = modes.PERFILES[MODO_DEDICADO]
    modelo = perfil["modelo"]
    if modelo not in (ollama_client.modelos_instalados() or []):
        modelo = modes.perfil_actual()["modelo"]

    try:
        respuesta = ollama.chat(
            model=modelo,
            messages=[
                {"role": "system", "content":
                    "Analizas resultados de busqueda y sacas conclusiones. "
                    "Reglas: usa SOLO lo que aparece en los resultados; si un "
                    "dato no esta, di que no lo sabes en vez de inventarlo. "
                    "Los precios y las cifras copialos tal cual, sin "
                    "redondear ni calcular. Responde en español para leerse "
                    "en voz alta: sin markdown, sin listas, maximo cinco frases."},
                {"role": "user", "content": f"{instruccion}\n\nRESULTADOS:\n{material}"},
            ],
            options={"temperature": 0.2, "num_ctx": 8192, "num_predict": 320},
        )
        return (respuesta.get("message", {}).get("content") or "").strip()
    except Exception as e:
        log.warning("El modelo falló razonando: %s", e)
        return f"Encontré los resultados pero no pude analizarlos: {e}"


# -------------------------------------------------------------------------
# ORDENES
# -------------------------------------------------------------------------
def _sin_resultados(consulta: str) -> str:
    return (f"No conseguí resultados para {consulta}. "
            "Puede que no haya conexión, o prueba a decirlo de otra forma.")


def investigar(consulta: str) -> str:
    """Busca y resume lo que dice internet."""
    resultados = buscar(consulta)
    if not resultados:
        return _sin_resultados(consulta)

    return _razonar(
        f"Alguien pregunta: {consulta}. Contesta con lo que digan estos resultados.",
        _material(resultados),
    )


def comparar(consulta: str) -> str:
    """Compara opciones o precios y recomienda."""
    # Añadimos las palabras que hacen aflorar comparativas y precios: sin
    # ellas, la busqueda devuelve paginas de producto sueltas y no hay nada
    # que comparar.
    resultados = buscar(f"{consulta} precio comparativa opiniones")
    if not resultados:
        return _sin_resultados(consulta)

    return _razonar(
        f"Quiero decidir sobre: {consulta}. Compara las opciones que aparezcan, "
        "di precios si los hay, y termina con cual recomiendas y por que. "
        "Si los resultados no dan para comparar, dilo claramente.",
        _material(resultados),
    )


def mejor_opcion(consulta: str) -> str:
    """Cual es la mejor opcion, sin rodeos."""
    resultados = buscar(f"{consulta} mejor recomendado 2026")
    if not resultados:
        return _sin_resultados(consulta)

    return _razonar(
        f"Pregunta: cual es la mejor opcion para {consulta}. "
        "Moja te: da una recomendacion concreta y una razon. "
        "Si los resultados no coinciden entre si, dilo.",
        _material(resultados),
    )
