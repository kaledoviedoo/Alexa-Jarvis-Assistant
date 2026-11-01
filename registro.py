"""Registro unico de herramientas.

Cada capacidad se declara una sola vez, con el decorador @herramienta encima
de su funcion: nombre, descripcion, parametros que ve el modelo, riesgo y lo
que necesita para funcionar. De aqui salen el esquema que recibe Ollama, la
funcion que se ejecuta, las pistas del freno y el catalogo del planificador.

Antes cada herramienta vivia en hasta cuatro listas escritas a mano, y por eso
la mitad de las capacidades nunca llegaron al modelo.

Este modulo no importa nada del proyecto al cargarse: las herramientas se
registran solas cuando cargar() importa sus modulos.
"""

import importlib
import importlib.util
import inspect
import logging
import os
import shutil
import sys
import time
import unicodedata
from dataclasses import dataclass

log = logging.getLogger("jarvis.registro")

# lectura: solo mira. escritura: cambia algo del equipo que se puede deshacer.
# externa: sale del equipo (mensajes, correos). irreversible: no tiene vuelta.
RIESGOS = ("lectura", "escritura", "externa", "irreversible")

# Lo que el modelo puede ejecutar por su cuenta. Lo externo y lo irreversible
# queda registrado pero fuera de su alcance hasta que exista la confirmacion
# por plan (Fase 3): un correo leido no puede acabar mandando un WhatsApp.
OFRECIBLES = ("lectura", "escritura")

# Modulos que declaran herramientas. cargar() los importa en este orden.
MODULOS = (
    "tools.archivos",
    "tools.sistema",
    "tools.navegador",
    "tools.entrada",
    "tools.avanzado",
    "tools.obsidian",
    "modes",
    "tools.correo",
    "tools.whatsapp",
    "tools.pantalla",
    "tools.ventanas",
    "tools.investigar",
    "tools.estudio",
    "tools.seleccion",
    "tools.archivar",
    "tools.teams",
    "tools.rendimiento",
    "tools.catalogo",
    "tools.plan",
)

_TIPOS = {
    str: "string", int: "integer", bool: "boolean", float: "number",
    "str": "string", "int": "integer", "bool": "boolean", "float": "number",
}

LARGO_MAXIMO_DESCRIPCION = 200


@dataclass(frozen=True)
class Herramienta:
    nombre: str
    funcion: object
    descripcion: str
    riesgo: str
    parametros: tuple          # ((nombre, tipo, descripcion), ...) en orden de firma
    requeridos: tuple
    necesita: tuple = ()
    corroboran: tuple = ()     # pistas que el freno busca en la frase (hasta la Fase 3)
    nucleo: bool = False       # se ofrece siempre al modelo
    palabras: tuple = ()       # se ofrece si la frase contiene alguna
    en_plan: bool = False      # la puede usar el planificador

    def esquema(self) -> dict:
        """El formato de function calling que espera Ollama."""
        return {
            "type": "function",
            "function": {
                "name": self.nombre,
                "description": self.descripcion,
                "parameters": {
                    "type": "object",
                    "properties": {
                        p: {"type": t, "description": d} for p, t, d in self.parametros
                    },
                    "required": list(self.requeridos),
                },
            },
        }

    @property
    def nombres_parametros(self) -> tuple:
        return tuple(p for p, _, _ in self.parametros)


_registro: dict[str, Herramienta] = {}
_cargado = False


def herramienta(nombre: str | None = None, *, descripcion: str, riesgo: str = "lectura",
                args: dict | None = None, necesita: tuple = (), corroboran: tuple = (),
                nucleo: bool = False, palabras: tuple = (), en_plan: bool = False):
    """Registra la funcion decorada como herramienta. La devuelve sin tocarla.

    `args` dice que parametros ve el modelo y como se le explican. Los que no
    esten ahi se quedan con su valor por defecto: asi el modelo no puede tocar
    detalles como el limite de resultados o la zona de la pantalla.
    """
    def decorar(funcion):
        n = nombre or funcion.__name__
        if riesgo not in RIESGOS:
            raise ValueError(f"{n}: riesgo '{riesgo}' no es uno de {RIESGOS}")
        for clave in necesita:
            if clave not in _CHEQUEOS:
                raise ValueError(f"{n}: no se como comprobar '{clave}'")
        if n in _registro and _registro[n].funcion is not funcion:
            raise ValueError(f"Herramienta duplicada: {n}")

        firma = inspect.signature(funcion)
        descritos = dict(args or {})
        for p in descritos:
            if p not in firma.parameters:
                raise ValueError(f"{n}: '{p}' no es un parametro de {funcion.__name__}")

        parametros, requeridos = [], []
        for p, par in firma.parameters.items():
            if par.kind not in (par.POSITIONAL_OR_KEYWORD, par.KEYWORD_ONLY):
                continue
            obligatorio = par.default is inspect.Parameter.empty
            if p not in descritos:
                if obligatorio:
                    raise ValueError(f"{n}: el parametro obligatorio '{p}' necesita descripcion")
                continue
            tipo = _TIPOS.get(par.annotation, "string")
            parametros.append((p, tipo, descritos[p]))
            if obligatorio:
                requeridos.append(p)

        _registro[n] = Herramienta(
            nombre=n, funcion=funcion, descripcion=descripcion.strip(), riesgo=riesgo,
            parametros=tuple(parametros), requeridos=tuple(requeridos),
            necesita=tuple(necesita), corroboran=tuple(corroboran), nucleo=nucleo,
            palabras=tuple(_normalizar(x) for x in palabras), en_plan=en_plan,
        )
        return funcion

    return decorar


# ---------------------------------------------------------------------------
# Lo que una herramienta puede necesitar del equipo
# ---------------------------------------------------------------------------

def _hay_modulo(nombre: str) -> bool:
    try:
        return importlib.util.find_spec(nombre) is not None
    except (ImportError, ValueError):
        return False


def _hay_tesseract() -> bool:
    if not _hay_modulo("pytesseract"):
        return False
    try:
        from config import TESSERACT_EXE
    except Exception:
        TESSERACT_EXE = ""
    candidatos = [TESSERACT_EXE, r"C:\Program Files\Tesseract-OCR\tesseract.exe"]
    return bool(shutil.which("tesseract")) or any(c and os.path.isfile(c) for c in candidatos)


_CHEQUEOS = {
    "windows": lambda: sys.platform == "win32",
    "outlook": lambda: sys.platform == "win32" and _hay_modulo("win32com"),
    "tesseract": _hay_tesseract,
    "ollama": lambda: _hay_modulo("ollama"),
}

_cache_chequeos: dict[str, tuple[float, bool]] = {}
SEGUNDOS_CACHE = 300


def _cumple(clave: str) -> bool:
    ahora = time.monotonic()
    guardado = _cache_chequeos.get(clave)
    if guardado and ahora - guardado[0] < SEGUNDOS_CACHE:
        return guardado[1]
    try:
        valor = bool(_CHEQUEOS[clave]())
    except Exception as e:
        log.debug("Fallo comprobando %s: %s", clave, e)
        valor = False
    _cache_chequeos[clave] = (ahora, valor)
    return valor


def disponible(h: Herramienta) -> bool:
    """¿Puede funcionar ahora en este equipo?"""
    return all(_cumple(c) for c in h.necesita)


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def cargar() -> dict[str, Herramienta]:
    """Importa los modulos que declaran herramientas. Se puede llamar mil veces."""
    global _cargado
    if not _cargado:
        for modulo in MODULOS:
            try:
                importlib.import_module(modulo)
            except Exception as e:
                # Un modulo roto no puede tumbar a Jarvis: se pierden sus
                # herramientas, no las demas.
                log.warning("No pude cargar las herramientas de %s: %s", modulo, e)
        _cargado = True
        log.info("Registro: %d herramientas", len(_registro))
    return _registro


def todas() -> dict[str, Herramienta]:
    return dict(cargar())


def obtener(nombre: str) -> Herramienta | None:
    return cargar().get(nombre)


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def para_el_modelo(frase: str = "", extras: int = 4) -> list[Herramienta]:
    """Lo que se le ofrece al modelo para esta frase.

    El nucleo siempre; del resto, las `extras` que mas palabras clave comparten
    con la frase. Un modelo de 3B con 4.096 de contexto no cabe con sesenta
    esquemas: en la Fase 4 esto se hara por significado.
    """
    frase_n = _normalizar(frase)
    nucleo, candidatas = [], []
    for h in cargar().values():
        if h.riesgo not in OFRECIBLES or not disponible(h):
            continue
        if h.nucleo:
            nucleo.append(h)
            continue
        aciertos = sum(1 for p in h.palabras if p and p in frase_n)
        if aciertos:
            candidatas.append((aciertos, h))
    candidatas.sort(key=lambda par: -par[0])
    return nucleo + [h for _, h in candidatas[:max(0, extras)]]


def esquemas(herramientas: list[Herramienta]) -> list[dict]:
    return [h.esquema() for h in herramientas]


def despachador() -> dict:
    """Vista nombre -> funcion, generada."""
    return {n: h.funcion for n, h in cargar().items()}


def corroboracion() -> dict:
    """Vista nombre -> pistas del freno, generada."""
    return {n: h.corroboran for n, h in cargar().items() if h.corroboran}


def para_el_plan() -> dict[str, Herramienta]:
    return {n: h for n, h in cargar().items()
            if h.en_plan and h.riesgo in OFRECIBLES and disponible(h)}


def llamar_con_texto(h: Herramienta, texto: str):
    """Llama a la herramienta con un solo texto, como lo da el planificador.

    Con varios parametros, el texto se parte por '|' en el orden de la firma:
    'titulo | contenido'.
    """
    nombres = [p for p, t, _ in h.parametros if t == "string"]
    if not nombres:
        return h.funcion()
    partes = [x.strip() for x in (texto or "").split("|", len(nombres) - 1)]
    return h.funcion(**{p: v for p, v in zip(nombres, partes) if v})
