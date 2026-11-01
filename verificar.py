"""Comprobar que lo que se dijo hecho esta hecho de verdad.

Despues de cada herramienta que cambia algo se mira el disco o el sistema:
el archivo existe, la nota de hoy tiene el texto, el programa ya no corre.
Si no, el modelo tiene un intento mas, y si vuelve a fallar Jarvis para y
dice que paso fallo. Un "listo" sin comprobar es lo que separa a un macro
de un agente.

Cada comprobacion devuelve "" si todo esta bien, o el motivo si no.
"""

import logging
import time
import unicodedata
from datetime import datetime

log = logging.getLogger("jarvis.verificar")

_COMPROBACIONES: dict = {}

# Respuestas que ya dicen que fallo: no hay nada que verificar.
_YA_FALLO = ("no ", "hubo", "la herramienta", "ya existe", "error", "falta", "¿")


def comprueba(nombre: str):
    """Registra la comprobacion de una herramienta."""
    def decorar(funcion):
        _COMPROBACIONES[nombre] = funcion
        return funcion
    return decorar


def tiene_comprobacion(nombre: str) -> bool:
    return nombre in _COMPROBACIONES


def comprobar(nombre: str, argumentos: dict, resultado: str) -> str:
    """"" si esta hecho (o no se puede comprobar), el motivo si no lo esta."""
    funcion = _COMPROBACIONES.get(nombre)
    if funcion is None or (resultado or "").strip().lower().startswith(_YA_FALLO):
        return ""
    try:
        motivo = funcion(argumentos, resultado) or ""
    except Exception as e:
        # Una comprobacion rota no puede tumbar una accion que si salio.
        log.debug("No pude comprobar %s: %s", nombre, e)
        return ""
    if motivo:
        log.warning("VERIFICACION FALLIDA en %s: %s", nombre, motivo)
    return motivo


def _normal(texto: str) -> str:
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return " ".join("".join(c for c in t if unicodedata.category(c) != "Mn").split())


# ---------------------------------------------------------------------------
# Archivos
# ---------------------------------------------------------------------------

def _ruta(nombre: str, carpeta: str = ""):
    from tools import archivos
    return archivos.resolver_ruta(nombre, archivos._base_desde_alias(carpeta))


@comprueba("crear_archivo")
def _creado(args: dict, resultado: str) -> str:
    ruta = _ruta(args.get("nombre_archivo", ""), args.get("carpeta", ""))
    if not ruta.suffix:
        ruta = ruta.with_suffix(".txt")
    return "" if ruta.exists() else f"dice que creo {ruta.name} pero no esta en {ruta.parent.name}"


@comprueba("crear_carpeta")
def _carpeta(args: dict, resultado: str) -> str:
    ruta = _ruta(args.get("nombre", ""), args.get("carpeta", ""))
    return "" if ruta.is_dir() else f"la carpeta {ruta.name} no existe"


@comprueba("mover_archivo")
def _movido(args: dict, resultado: str) -> str:
    ruta = _ruta(args.get("origen", ""), args.get("carpeta", ""))
    return f"{ruta.name} sigue en {ruta.parent.name}" if ruta.exists() else ""


@comprueba("eliminar_archivo")
def _eliminado(args: dict, resultado: str) -> str:
    ruta = _ruta(args.get("nombre_archivo", ""), args.get("carpeta", ""))
    return f"{ruta.name} sigue en {ruta.parent.name}" if ruta.exists() else ""


@comprueba("editar_archivo")
def _editado(args: dict, resultado: str) -> str:
    ruta = _ruta(args.get("nombre_archivo", ""), args.get("carpeta", ""))
    if not ruta.exists():
        return f"{ruta.name} no existe"
    esperado = args.get("reemplazar") if args.get("accion") == "reemplazar" else args.get("contenido")
    if not esperado:
        return ""
    texto = ruta.read_text(encoding="utf-8", errors="ignore")
    return "" if _normal(esperado)[:60] in _normal(texto) else f"{ruta.name} no tiene el texto nuevo"


# ---------------------------------------------------------------------------
# Obsidian
# ---------------------------------------------------------------------------

@comprueba("obsidian_agregar_al_diario")
def _diario(args: dict, resultado: str) -> str:
    from tools import obsidian
    v = obsidian.vault()
    if v is None:
        return ""
    hoy = f"{datetime.now():%Y-%m-%d}.md"
    notas = [p for p in v.rglob(hoy) if not any(x.startswith(".") for x in p.parts)]
    if not notas:
        return "la nota de hoy no existe"
    contenido = _normal(args.get("contenido", ""))[:60]
    if contenido and not any(contenido in _normal(n.read_text(encoding="utf-8", errors="ignore"))
                             for n in notas):
        return "la nota de hoy no tiene ese texto"
    return ""


@comprueba("obsidian_crear_nota")
def _nota(args: dict, resultado: str) -> str:
    from tools import obsidian
    v = obsidian.vault()
    if v is None:
        return ""
    nombre = obsidian._nombre_nota(args.get("titulo", ""))
    return "" if any(v.rglob(nombre)) else f"la nota {nombre} no aparece en la boveda"


# ---------------------------------------------------------------------------
# Programas
# ---------------------------------------------------------------------------

@comprueba("cerrar_aplicacion")
def _cerrada(args: dict, resultado: str) -> str:
    from tools import sistema
    time.sleep(1.5)            # terminar un proceso no es instantaneo
    vivos = sistema._procesos_por_nombre()
    real = sistema._buscar_proceso_parecido(sistema._canonizar_app(args.get("nombre_app", "")), vivos)
    return f"{real} sigue abierto" if real else ""
