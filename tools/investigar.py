"""Buscar en internet y razonar sobre los resultados.

Compara precios y opciones y da una recomendacion con el porque, en vez de
soltar una lista de enlaces. Corre en segundo plano porque implica varias
busquedas y una pasada del modelo.
"""

from datetime import datetime
import html
import json
import logging
import re
import urllib.parse
import urllib.request

from config import MODO_DEDICADO
from registro import herramienta

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



def _chat(**kwargs):
    """Habla con el modelo por el mismo sitio que el resto de Jarvis."""
    import ollama_client
    return ollama_client.chat_modelo(**kwargs)

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
    """Comprueba que la busqueda funciona de verdad."""
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
        respuesta = _chat(
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


def _sin_resultados(consulta: str) -> str:
    return (f"No conseguí resultados para {consulta}. "
            "Puede que no haya conexión, o prueba a decirlo de otra forma.")



def _palabras_de(texto: str) -> list[str]:
    import unicodedata
    t = unicodedata.normalize("NFD", (texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return [w for w in re.split(r"\W+", t) if len(w) > 2]


def afinar_consulta(consulta: str) -> str:
    """Aclara una consulta ambigua con lo que se sabe del usuario.

    "como funciona un transformer" trajo el transformador electrico; el
    usuario estudia Ingenieria de Sistemas y queria el de inteligencia
    artificial. El modelo puede añadir una o dos palabras que lo aclaren,
    pero la consulta nueva tiene que conservar todas las palabras de la
    original y no crecer mas de cuatro: si no, se usa la original.
    """
    consulta = (consulta or "").strip()
    originales = _palabras_de(consulta)
    if not originales or len(originales) > 7:
        return consulta              # una consulta larga ya es especifica

    try:
        from tools import avanzado
        contexto = (avanzado.leer_contexto() or "")[:700]
    except Exception:
        contexto = ""
    if not contexto:
        return consulta

    try:
        import modes
        respuesta = _chat(
            model=modes.perfil_actual()["modelo"],
            messages=[
                {"role": "system", "content":
                    "Ajustas consultas para un buscador web. Si un termino tiene varios "
                    "significados (por ejemplo 'transformer': electrico o de inteligencia "
                    "artificial; 'python': serpiente o lenguaje) y lo que sabes del usuario "
                    "indica cual busca, añade una o dos palabras que lo aclaren. Si no hace "
                    "falta, devuelve la consulta igual. Responde SOLO la consulta, sin comillas."},
                {"role": "user", "content": f"Sobre el usuario:\n{contexto}\n\nConsulta: {consulta}"},
            ],
            options={"temperature": 0, "num_predict": 40},
        )
        mensaje = respuesta.get("message", {}) if isinstance(respuesta, dict) else respuesta.message
        nueva = ((mensaje.get("content") if isinstance(mensaje, dict) else mensaje.content) or "")
    except Exception as e:
        log.debug("No pude afinar la consulta: %s", e)
        return consulta

    nueva = nueva.strip().splitlines()[0].strip(" \"'`.") if nueva.strip() else ""
    nuevas = _palabras_de(nueva)
    if not nueva or any(p not in nuevas for p in originales) or len(nuevas) > len(originales) + 4:
        return consulta
    if nueva.lower() != consulta.lower():
        log.info("Consulta afinada: %r -> %r", consulta, nueva)
    return nueva


@herramienta(
    descripcion="Busca en internet y resume lo que se dice sobre un tema.",
    en_plan=True, args={"consulta": "Qué investigar"},
    palabras=("investiga", "internet", "averigua", "que se sabe", "noticias", "busca informacion"),
    fuente_externa=True,
)
def investigar(consulta: str) -> str:
    """Busca y resume lo que dice internet."""
    resultados = buscar(afinar_consulta(consulta))
    if not resultados:
        return _sin_resultados(consulta)

    return _razonar(
        f"Alguien pregunta: {consulta}. Contesta con lo que digan estos resultados.",
        _material(resultados),
    )


@herramienta(
    descripcion="Compara opciones o precios buscando en internet y recomienda una.",
    args={"consulta": "Qué comparar, por ejemplo 'rtx 4060 vs rx 7600'"},
    palabras=("compara", "precio", "precios", "diferencia entre", " vs ", "versus"),
    fuente_externa=True,
)
def comparar(consulta: str) -> str:
    """Compara opciones o precios y recomienda."""
    resultados = buscar(f"{afinar_consulta(consulta)} precio comparativa opiniones")
    if not resultados:
        return _sin_resultados(consulta)

    return _razonar(
        f"Quiero decidir sobre: {consulta}. Compara las opciones que aparezcan, "
        "di precios si los hay, y termina con cual recomiendas y por que. "
        "Si los resultados no dan para comparar, dilo claramente.",
        _material(resultados),
    )


@herramienta(
    descripcion="Busca en internet cuál es la mejor opción para algo y la dice sin rodeos.",
    args={"consulta": "Para qué se busca la mejor opción"},
    palabras=("mejor opcion", "cual es mejor", "recomienda", "me conviene"),
    fuente_externa=True,
)
def mejor_opcion(consulta: str) -> str:
    """Cual es la mejor opcion, sin rodeos."""
    resultados = buscar(f"{afinar_consulta(consulta)} mejor recomendado {datetime.now().year}")
    if not resultados:
        return _sin_resultados(consulta)

    return _razonar(
        f"Pregunta: cual es la mejor opcion para {consulta}. "
        "Moja te: da una recomendacion concreta y una razon. "
        "Si los resultados no coinciden entre si, dilo.",
        _material(resultados),
    )
