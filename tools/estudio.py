"""
Repasar para un examen con lo que ya tienes escrito.

"Tengo parcial el lunes de bases de datos" -> Jarvis busca en tu boveda de
Obsidian todo lo relacionado, lo lee, y te devuelve un resumen y preguntas de
repaso hechas con TUS apuntes. No con lo que el modelo crea saber del tema:
con lo que tu escribiste, que es lo que van a preguntarte.

Por que va en segundo plano, siempre
------------------------------------
Leer varias notas y razonar sobre ellas son miles de tokens. Un 3B en una RTX
3050 tarda entre quince segundos y un minuto. Alexa concede ocho. No hay
optimizacion que arregle eso, asi que ni se intenta en directo: se contesta al
momento y el resultado se recoge con "como quedo lo ultimo".
"""

import logging
import pathlib
import re

from config import MODO_DEDICADO
from tools import obsidian

log = logging.getLogger("jarvis.estudio")

# Cuanto texto se le pasa al modelo. Mas contexto no siempre es mejor: a un 3B
# se le va la cabeza con prompts largos, y ademas cada token cuesta tiempo.
MAX_CARACTERES_NOTAS = 6000
MAX_NOTAS = 6

# Palabras que no ayudan a buscar: estan en cualquier nota.
_VACIAS = {
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "y", "o", "en",
    "para", "por", "con", "sin", "sobre", "tengo", "hay", "es", "son", "mi",
    "mis", "tu", "tus", "este", "esta", "lunes", "martes", "miercoles",
    "jueves", "viernes", "sabado", "domingo", "manana", "hoy", "semana",
    "parcial", "examen", "prueba", "quiz", "evaluacion", "final",
}


def _palabras_clave(tema: str) -> list[str]:
    piezas = [p for p in re.split(r"\W+", (tema or "").lower()) if len(p) > 2]
    return [p for p in piezas if p not in _VACIAS]


def _notas_relacionadas(tema: str) -> list[dict]:
    """Busca en el vault las notas que hablen del tema."""
    vault = obsidian.vault()
    if vault is None:
        return []

    claves = _palabras_clave(tema)
    if not claves:
        return []

    encontradas = []
    for ruta in vault.rglob("*.md"):
        # .obsidian y .trash son configuracion y papelera, no apuntes.
        if any(parte.startswith(".") for parte in ruta.parts):
            continue
        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        bajo = (ruta.stem + " " + texto).lower()
        # Puntuamos: el titulo vale mas que el cuerpo, porque una nota que se
        # LLAMA "bases de datos" es mas relevante que una que lo menciona.
        puntos = sum(3 for c in claves if c in ruta.stem.lower())
        puntos += sum(1 for c in claves if c in bajo)

        if puntos:
            encontradas.append({
                "titulo": ruta.stem,
                "texto": texto.strip(),
                "puntos": puntos,
                "tamano": len(texto),
            })

    encontradas.sort(key=lambda d: d["puntos"], reverse=True)
    return encontradas[:MAX_NOTAS]


def _juntar(notas: list[dict]) -> str:
    """Une las notas respetando el limite, sin cortar una a la mitad si cabe."""
    trozos, total = [], 0
    for nota in notas:
        cabecera = f"\n\n### {nota['titulo']}\n"
        cuerpo = nota["texto"]

        disponible = MAX_CARACTERES_NOTAS - total - len(cabecera)
        if disponible < 200:
            break
        if len(cuerpo) > disponible:
            cuerpo = cuerpo[:disponible] + "\n[...]"

        trozos.append(cabecera + cuerpo)
        total += len(cabecera) + len(cuerpo)

    return "".join(trozos)


def _preguntar_al_modelo(instruccion: str, material: str) -> str:
    import modes
    import ollama_client

    try:
        import ollama
    except ImportError:
        return "No tengo Ollama instalado."

    # Para razonar sobre varias notas usamos el modelo grande si esta
    # disponible: el pequeño resume mal cuando hay que cruzar fuentes.
    perfil = modes.PERFILES[MODO_DEDICADO]
    modelo = perfil["modelo"]
    if modelo not in (ollama_client.modelos_instalados() or []):
        modelo = modes.perfil_actual()["modelo"]

    try:
        respuesta = ollama.chat(
            model=modelo,
            messages=[
                {"role": "system", "content":
                    "Eres un tutor. Trabajas SOLO con los apuntes que te dan. "
                    "Si algo no esta en ellos, dilo en vez de inventarlo. "
                    "Respondes en español, para que se lea en voz alta: sin "
                    "markdown, sin listas con guiones, sin emojis."},
                {"role": "user", "content": f"{instruccion}\n\nAPUNTES:\n{material}"},
            ],
            options={"temperature": 0.3, "num_ctx": 8192, "num_predict": 400},
        )
        return (respuesta.get("message", {}).get("content") or "").strip()
    except Exception as e:
        log.warning("El modelo falló estudiando: %s", e)
        return f"No pude procesar los apuntes: {e}"


# -------------------------------------------------------------------------
# ORDENES
# -------------------------------------------------------------------------
def _sin_notas(tema: str) -> str:
    return (f"No encontré nada sobre {tema} en tu bóveda. "
            "Si lo tienes apuntado con otro nombre, dime cuál.")


def preparar_examen(tema: str, cuando: str = "") -> str:
    """Resumen de lo que hay que repasar. Lento: va en segundo plano."""
    notas = _notas_relacionadas(tema)
    if not notas:
        return _sin_notas(tema)

    material = _juntar(notas)
    titulos = ", ".join(n["titulo"] for n in notas[:3])

    resumen = _preguntar_al_modelo(
        f"Voy a tener un examen de {tema}{' ' + cuando if cuando else ''}. "
        "Con estos apuntes mios, dime en pocas frases QUE tengo que repasar: "
        "los conceptos centrales y lo que mas facil se olvida. "
        "Maximo seis frases.",
        material,
    )

    return (f"Encontré {len(notas)} notas sobre eso: {titulos}. {resumen}")


def preguntas_de_repaso(tema: str, cuantas: int = 5) -> str:
    """Preguntas para autoevaluarse, sacadas de las notas propias."""
    notas = _notas_relacionadas(tema)
    if not notas:
        return _sin_notas(tema)

    preguntas = _preguntar_al_modelo(
        f"Hazme {cuantas} preguntas de repaso sobre {tema}, sacadas SOLO de "
        "estos apuntes. Numeralas hablando: primera, segunda, y asi. "
        "No des las respuestas todavia.",
        _juntar(notas),
    )
    return preguntas


def explicar_desde_notas(tema: str) -> str:
    """Explica un concepto usando lo que hay escrito en la boveda."""
    notas = _notas_relacionadas(tema)
    if not notas:
        return _sin_notas(tema)

    return _preguntar_al_modelo(
        f"Explicame {tema} usando solo estos apuntes, en cuatro frases como "
        "mucho. Si los apuntes no lo cubren bien, dilo.",
        _juntar(notas),
    )


def que_tengo_de(tema: str) -> str:
    """Rapido y sin modelo: que notas hay sobre algo."""
    notas = _notas_relacionadas(tema)
    if not notas:
        return _sin_notas(tema)

    detalle = ", ".join(f"{n['titulo']}" for n in notas[:5])
    return f"Sobre {tema} tienes {len(notas)} notas: {detalle}."


# -------------------------------------------------------------------------
# BUSCAR POR RELACION, NO POR NOMBRE
# -------------------------------------------------------------------------
# "Hay muchos datos de los que no conozco el nombre, solo la relacion".
#
# Ese es el caso normal en una boveda que lleva tiempo creciendo: recuerdas
# que escribiste algo sobre normalizar tablas, pero no si la nota se llama
# "Formas normales", "BD - diseño" o "Clase 7". Buscar por titulo no sirve.
#
# Lo que si sirve es puntuar CADA nota por cuanto tiene que ver con lo que
# describes, mirando titulo, enlaces y cuerpo, y devolver las mejores aunque
# ninguna se llame como dijiste.

# Los enlaces [[asi]] de Obsidian son la relacion explicita entre notas. Una
# nota que enlaza a otra que si encaja tambien viene al caso.
_ENLACE = re.compile(r"\[\[([^\]|#]+)")


def _raiz(palabra: str) -> str:
    """
    Recorta terminaciones para que 'normalizar' encuentre 'normalizacion'.

    No es un lematizador de verdad, y no hace falta: en español, cortar por
    la raiz de cinco o seis letras basta para emparentar las variantes de una
    misma palabra sin traerse media boveda por delante.
    """
    p = palabra.lower()
    for fin in ("aciones", "acion", "amientos", "amiento", "ciones", "cion",
                "mente", "ando", "endo", "ados", "adas", "ado", "ada",
                "ares", "ar", "er", "ir", "es", "s"):
        if len(p) - len(fin) >= 5 and p.endswith(fin):
            return p[: -len(fin)]
    return p


def buscar_por_relacion(descripcion: str, cuantas: int = 5) -> list[dict]:
    """Notas que tienen que ver con lo que describes, ordenadas."""
    vault = obsidian.vault()
    if vault is None:
        return []

    claves = _palabras_clave(descripcion)
    if not claves:
        return []

    raices = {_raiz(c) for c in claves}
    encontradas = []

    for ruta in vault.rglob("*.md"):
        if any(parte.startswith(".") for parte in ruta.parts):
            continue
        try:
            texto = ruta.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        titulo_bajo = ruta.stem.lower()
        cuerpo_bajo = texto.lower()
        enlaces = " ".join(_ENLACE.findall(texto)).lower()

        puntos = 0.0
        aciertos = set()

        for raiz in raices:
            # El titulo pesa mucho mas: una nota que se LLAMA asi es la nota.
            if raiz in titulo_bajo:
                puntos += 5
                aciertos.add(raiz)
            # Los enlaces son la relacion que el usuario escribio a mano.
            if raiz in enlaces:
                puntos += 2
                aciertos.add(raiz)
            veces = cuerpo_bajo.count(raiz)
            if veces:
                # Con raiz de tope: una nota que repite la palabra treinta
                # veces no es seis veces mejor que otra que la repite cinco.
                puntos += min(veces, 9) ** 0.5
                aciertos.add(raiz)

        if not puntos:
            continue

        # Cubrir varias de las palabras que dijiste vale mas que repetir una
        # sola muchas veces: es la señal de que la nota trata DE ESO.
        cobertura = len(aciertos) / len(raices)
        puntos *= 0.5 + cobertura

        encontradas.append({
            "titulo": ruta.stem,
            "ruta": str(ruta),
            "texto": texto.strip(),
            "puntos": round(puntos, 2),
            "cobertura": round(cobertura, 2),
        })

    encontradas.sort(key=lambda d: d["puntos"], reverse=True)
    return encontradas[:cuantas]


def encontrar_nota(descripcion: str) -> str:
    """Dice que notas tratan de eso, sin llamar al modelo. Instantaneo."""
    notas = buscar_por_relacion(descripcion)
    if not notas:
        return (f"No encuentro nada relacionado con {descripcion} en tu bóveda. "
                "Prueba a describirlo con otras palabras.")

    mejor = notas[0]

    # Muy por encima de las demas: es esa y punto.
    if len(notas) == 1 or mejor["puntos"] > notas[1]["puntos"] * 2:
        return f"Eso es {mejor['titulo']}."

    nombres = ", ".join(n["titulo"] for n in notas[:4])
    return f"Lo más relacionado: {nombres}."


def abrir_nota_relacionada(descripcion: str) -> str:
    """Abre en Obsidian la nota que mejor encaje con lo que describes."""
    import subprocess
    import urllib.parse

    notas = buscar_por_relacion(descripcion, cuantas=3)
    if not notas:
        return f"No encuentro ninguna nota sobre {descripcion}."

    mejor = notas[0]
    try:
        vault = obsidian.vault()
        relativa = str(pathlib.Path(mejor["ruta"]).relative_to(vault))
        url = ("obsidian://open?vault=" + urllib.parse.quote(vault.name)
               + "&file=" + urllib.parse.quote(relativa[:-3]))
        subprocess.Popen(f'start "" "{url}"', shell=True)
    except Exception as e:
        log.warning("No pude abrir la nota: %s", e)
        return f"Encontré {mejor['titulo']} pero no pude abrirla: {e}"

    return f"Abriendo {mejor['titulo']}."


def relacionar(descripcion: str) -> str:
    """
    Lee las notas relacionadas y explica que tienen que ver entre si.

    Lento: llama al modelo con varias notas. Va en segundo plano.
    """
    notas = buscar_por_relacion(descripcion, cuantas=MAX_NOTAS)
    if not notas:
        return f"No encuentro nada relacionado con {descripcion}."

    titulos = ", ".join(n["titulo"] for n in notas)
    analisis = _preguntar_al_modelo(
        f"Estas notas mias tienen que ver con {descripcion}. Dime en pocas "
        "frases QUE relacion hay entre ellas y que idea las une. Si alguna no "
        "pinta nada, dilo.",
        _juntar(notas),
    )
    return f"Encontré {titulos}. {analisis}"
