"""
Capacidades avanzadas: contexto personal, informes completos y busqueda profunda.

El contexto personal
--------------------
Un modelo de 3B no sabe nada de ti ni de tu equipo. Sin eso, "crea un script
para ordenar mis descargas" produce codigo generico que no encaja con nada.

contexto.md resuelve eso: es un archivo que TU escribes, en la carpeta de
Jarvis, con quien eres, en que trabajas, que herramientas usas y como quieres
que se comporte. Jarvis lo lee y lo inyecta en el prompt del modelo antes de
cada peticion, junto con datos reales del equipo leidos en ese momento.

El resultado es que el modelo deja de improvisar sobre un usuario imaginario.
"""

import logging
import os
import platform
import socket
from datetime import datetime, timedelta
from pathlib import Path

import psutil

from config import (
    CARPETA_DATOS,
    CARPETAS_PERMITIDAS,
    DESCARGAS,
    DOCUMENTOS,
    ESCRITORIO,
)

log = logging.getLogger("jarvis.avanzado")

# El contexto vive en la carpeta del PROYECTO, junto a los .py, porque es un
# archivo que tu editas a mano y tiene que estar a la vista. Si por lo que sea
# esa carpeta no admite escritura, cae a la carpeta de datos.
_CARPETA_PROYECTO = Path(__file__).resolve().parent.parent


def _elegir_ruta_contexto() -> Path:
    candidata = _CARPETA_PROYECTO / "contexto.md"
    try:
        candidata.parent.mkdir(parents=True, exist_ok=True)
        # Comprobamos que se pueda escribir de verdad, no solo que exista.
        if candidata.exists() or os.access(candidata.parent, os.W_OK):
            return candidata
    except Exception:
        pass
    return CARPETA_DATOS / "contexto.md"


ARCHIVO_CONTEXTO = _elegir_ruta_contexto()

PLANTILLA_CONTEXTO = """# Contexto de {usuario}

Este archivo lo lee Jarvis antes de cada peticion al modelo. Editalo a mano:
cuanto mejor te describa, mejores seran las respuestas y el codigo que genere.

## Quien soy

- Nombre: {usuario}
- Que hago: (describe tu trabajo o estudios)
- Idioma: espanol

## Este equipo

- Sistema: {sistema}
- Procesador: {cpu}
- Memoria: {ram} GB
- Grafica: {gpu}

## Como trabajo

- Editor principal: (VS Code, Obsidian, ...)
- Lenguajes que uso: (Python, JavaScript, ...)
- Navegador: Comet
- Donde guardo las cosas: Escritorio para lo temporal, Documentos para lo que dura

## Prioridades

- (Que es urgente para ti ahora mismo)
- (En que proyecto estas)

## Como quiero que trabajes

- Responde en espanol, breve y directo.
- Cuando generes codigo, que sea funcional y comentado, no un esqueleto.
- Si algo es ambiguo, elige la opcion mas util y dilo.
- No inventes rutas ni nombres de archivo: usa los que existen.
"""


# -------------------------------------------------------------------------
# CONTEXTO PERSONAL
# -------------------------------------------------------------------------
def asegurar_contexto() -> Path:
    """Crea contexto.md con una plantilla si aun no existe."""
    if ARCHIVO_CONTEXTO.exists():
        return ARCHIVO_CONTEXTO

    datos = info_equipo_dict()
    try:
        ARCHIVO_CONTEXTO.write_text(
            PLANTILLA_CONTEXTO.format(
                usuario=datos["usuario"],
                sistema=datos["sistema"],
                cpu=datos["cpu"],
                ram=datos["ram_gb"],
                gpu=datos["gpu"],
            ),
            encoding="utf-8",
        )
        log.info("Creado contexto.md en %s", ARCHIVO_CONTEXTO)
    except Exception as e:
        log.warning("No pude crear contexto.md: %s", e)

    return ARCHIVO_CONTEXTO


def leer_contexto() -> str:
    """Devuelve el contexto personal, o cadena vacia si no hay."""
    try:
        asegurar_contexto()
        texto = ARCHIVO_CONTEXTO.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

    # Recortamos: el contexto no puede comerse la ventana del modelo.
    return texto[:2500]


def editar_contexto(contenido: str, accion: str = "agregar") -> str:
    """Permite a Jarvis añadir cosas a su propio contexto por voz."""
    asegurar_contexto()
    try:
        actual = ARCHIVO_CONTEXTO.read_text(encoding="utf-8")
        if accion == "sobrescribir":
            nuevo = contenido
        else:
            nuevo = actual.rstrip() + "\n- " + contenido.strip() + "\n"
        ARCHIVO_CONTEXTO.write_text(nuevo, encoding="utf-8")
    except Exception as e:
        return f"No pude actualizar el contexto: {e}"

    return "Apuntado en mi contexto."


def donde_esta_contexto() -> str:
    asegurar_contexto()
    return f"Mi contexto está en {ARCHIVO_CONTEXTO}. Edítalo para que te conozca mejor."


# -------------------------------------------------------------------------
# CONOCIMIENTO DEL EQUIPO
# -------------------------------------------------------------------------
def info_equipo_dict() -> dict:
    """Datos reales del equipo, leidos en el momento."""
    from tools import sistema as _sistema

    try:
        memoria = psutil.virtual_memory()
        ram_gb = round(memoria.total / (1024**3))
    except Exception:
        ram_gb = 0

    gpu = "no detectada"
    try:
        datos = _sistema.info_gpu()
        if datos.get("disponible"):
            gpu = f"{datos['nombre']} ({datos['vram_total_mb'] / 1024:.0f} GB)"
    except Exception:
        pass

    try:
        arranque = datetime.fromtimestamp(psutil.boot_time())
        encendido = str(timedelta(seconds=int((datetime.now() - arranque).total_seconds())))
    except Exception:
        encendido = "desconocido"

    return {
        "usuario": os.environ.get("USERNAME") or os.environ.get("USER") or "usuario",
        "equipo": socket.gethostname(),
        "sistema": f"{platform.system()} {platform.release()}",
        "cpu": platform.processor() or "desconocido",
        "nucleos": psutil.cpu_count(logical=True) or 0,
        "ram_gb": ram_gb,
        "gpu": gpu,
        "encendido_desde": encendido,
        "escritorio": str(ESCRITORIO),
        "descargas": str(DESCARGAS),
        "documentos": str(DOCUMENTOS),
    }


def info_equipo() -> str:
    """Descripcion hablada del equipo."""
    d = info_equipo_dict()
    return (
        f"Estás en {d['equipo']}, con {d['sistema']}, "
        f"{d['nucleos']} hilos de procesador, {d['ram_gb']} gigas de memoria "
        f"y una {d['gpu']}. Lleva encendido {d['encendido_desde']}."
    )


def resumen_equipo_para_modelo() -> str:
    """Bloque compacto que se inyecta en el prompt del modelo."""
    d = info_equipo_dict()
    return (
        f"EQUIPO: {d['sistema']}, {d['nucleos']} hilos, {d['ram_gb']} GB RAM, {d['gpu']}.\n"
        f"USUARIO: {d['usuario']}\n"
        f"RUTAS REALES (usa estas, no inventes):\n"
        f"  Escritorio: {d['escritorio']}\n"
        f"  Descargas : {d['descargas']}\n"
        f"  Documentos: {d['documentos']}\n"
        f"FECHA: {datetime.now():%A %d de %B de %Y, %H:%M}"
    )


# -------------------------------------------------------------------------
# INFORME COMPLETO
# -------------------------------------------------------------------------
def informe_completo(guardar: bool = False) -> str:
    """
    Informe detallado del equipo.

    Hablado devuelve un resumen; con guardar=True escribe el informe entero
    como archivo, porque leer veinte cifras en voz alta no sirve de nada.
    """
    from tools import sistema as _sistema

    d = info_equipo_dict()
    lineas = [
        f"# Informe del equipo {d['equipo']}",
        f"Generado el {datetime.now():%d/%m/%Y a las %H:%M}",
        "",
        "## Sistema",
        f"- Sistema operativo: {d['sistema']}",
        f"- Procesador: {d['cpu']} ({d['nucleos']} hilos)",
        f"- Encendido desde hace: {d['encendido_desde']}",
        "",
        "## Carga actual",
        f"- CPU: {psutil.cpu_percent(interval=0.5):.0f}%",
    ]

    memoria = psutil.virtual_memory()
    lineas.append(
        f"- Memoria: {memoria.percent:.0f}% "
        f"({memoria.used / 1024**3:.1f} de {memoria.total / 1024**3:.1f} GB)"
    )

    try:
        swap = psutil.swap_memory()
        if swap.total:
            lineas.append(f"- Archivo de paginación: {swap.percent:.0f}%")
    except Exception:
        pass

    # ---- Discos, unidad por unidad ----
    lineas += ["", "## Discos"]
    for particion in psutil.disk_partitions(all=False):
        try:
            uso = psutil.disk_usage(particion.mountpoint)
        except (PermissionError, OSError):
            continue
        lineas.append(
            f"- {particion.device} {uso.percent:.0f}% usado, "
            f"{uso.free / 1024**3:.0f} GB libres de {uso.total / 1024**3:.0f} GB"
        )

    # ---- GPU ----
    datos = _sistema.info_gpu()
    if datos.get("disponible"):
        lineas += [
            "",
            "## Gráfica",
            f"- {datos['nombre']}",
            f"- Uso: {datos['uso_pct']:.0f}%   Temperatura: {datos['temperatura']:.0f} °C",
            f"- Memoria de vídeo: {datos['vram_usada_mb'] / 1024:.1f} de "
            f"{datos['vram_total_mb'] / 1024:.1f} GB usados",
        ]

    # ---- Red ----
    try:
        red = psutil.net_io_counters()
        lineas += [
            "",
            "## Red",
            f"- Enviado: {red.bytes_sent / 1024**3:.2f} GB",
            f"- Recibido: {red.bytes_recv / 1024**3:.2f} GB",
        ]
    except Exception:
        pass

    # ---- Procesos ----
    agregados: dict = {}
    for proceso in psutil.process_iter(["name", "memory_info"]):
        try:
            info = proceso.info
            if info["name"] and info.get("memory_info"):
                agregados[info["name"]] = (
                    agregados.get(info["name"], 0) + info["memory_info"].rss
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    top = sorted(agregados.items(), key=lambda x: x[1], reverse=True)[:8]
    lineas += ["", "## Lo que más memoria consume"]
    for nombre, bytes_ in top:
        lineas.append(f"- {nombre.replace('.exe', '')}: {bytes_ / 1024**3:.2f} GB")

    # ---- Carpetas ----
    lineas += ["", "## Carpetas de trabajo"]
    for carpeta in CARPETAS_PERMITIDAS:
        try:
            n = len(list(carpeta.iterdir()))
            lineas.append(f"- {carpeta}: {n} elementos")
        except Exception:
            continue

    informe = "\n".join(lineas)

    if guardar:
        from tools import archivos as _archivos

        nombre = f"informe_equipo_{datetime.now():%Y%m%d_%H%M}.md"
        resultado = _archivos.crear_archivo(nombre, informe)
        return f"{resultado} Incluye discos, gráfica, red y procesos."

    # Resumen hablado: lo importante, no las veinte cifras.
    partes = [
        f"Procesador al {psutil.cpu_percent(interval=0.3):.0f} por ciento",
        f"memoria al {memoria.percent:.0f} por ciento",
    ]
    if datos.get("disponible"):
        partes.append(
            f"gráfica al {datos['uso_pct']:.0f} por ciento a {datos['temperatura']:.0f} grados"
        )
    partes.append(f"encendido desde hace {d['encendido_desde'].split('.')[0]}")

    if top:
        partes.append(f"lo que más consume es {top[0][0].replace('.exe', '')}")

    return ", ".join(partes) + ". Di 'guarda el informe' si lo quieres completo en un archivo."


# -------------------------------------------------------------------------
# BUSQUEDA PROFUNDA
# -------------------------------------------------------------------------
_EXTENSIONES_TEXTO = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css",
    ".yml", ".yaml", ".ini", ".cfg", ".log", ".xml", ".sql", ".sh", ".ps1",
}

_CARPETAS_IGNORADAS = {
    "node_modules", "__pycache__", ".git", "venv", ".venv", "env",
    "dist", "build", ".next", ".cache", "site-packages",
}


def buscar_en_contenido(texto: str, carpeta: str = "", limite: int = 10) -> str:
    """
    Busca una frase DENTRO de los archivos, no solo en sus nombres.

    Es la diferencia entre "no sé dónde guardé eso" y encontrarlo.
    """
    texto = (texto or "").strip()
    if not texto:
        return "¿Qué texto quieres que busque dentro de los archivos?"

    from tools.archivos import _base_desde_alias

    bases = [_base_desde_alias(carpeta)] if carpeta else list(CARPETAS_PERMITIDAS)
    aguja = texto.lower()
    encontrados = []

    for base in bases:
        if not base.is_dir():
            continue
        for raiz, dirs, ficheros in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _CARPETAS_IGNORADAS and not d.startswith(".")]
            for fichero in ficheros:
                ruta = Path(raiz) / fichero
                if ruta.suffix.lower() not in _EXTENSIONES_TEXTO:
                    continue
                try:
                    if ruta.stat().st_size > 2_000_000:
                        continue
                    contenido = ruta.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                if aguja in contenido.lower():
                    # Guardamos tambien el numero de linea: es lo util.
                    for i, linea in enumerate(contenido.splitlines(), start=1):
                        if aguja in linea.lower():
                            encontrados.append((ruta, i, linea.strip()[:80]))
                            break

                if len(encontrados) >= limite:
                    break
            if len(encontrados) >= limite:
                break
        if len(encontrados) >= limite:
            break

    if not encontrados:
        return f"No encontré '{texto}' dentro de ningún archivo."

    if len(encontrados) == 1:
        ruta, linea, _ = encontrados[0]
        return f"Encontré '{texto}' en {ruta.name}, línea {linea}, dentro de {ruta.parent.name}."

    nombres = ", ".join(r.name for r, _, _ in encontrados[:4])
    return f"Encontré '{texto}' en {len(encontrados)} archivos: {nombres}."


def explorar_carpeta(carpeta: str = "escritorio", profundidad: int = 2) -> str:
    """
    Recorre una carpeta y sus subcarpetas, y resume que hay dentro.

    Pensado para orientarse: cuantos archivos, de que tipo y que subcarpetas.
    """
    from tools.archivos import _base_desde_alias

    base = _base_desde_alias(carpeta)
    if not base.is_dir():
        return f"No encontré la carpeta {carpeta}."

    subcarpetas = []
    por_tipo: dict = {}
    total = 0

    for raiz, dirs, ficheros in os.walk(base):
        nivel = len(Path(raiz).relative_to(base).parts)
        if nivel >= profundidad:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in _CARPETAS_IGNORADAS and not d.startswith(".")]

        if nivel == 0:
            subcarpetas = sorted(dirs)[:8]

        for fichero in ficheros:
            if fichero.startswith("."):
                continue
            total += 1
            ext = Path(fichero).suffix.lower() or "(sin extensión)"
            por_tipo[ext] = por_tipo.get(ext, 0) + 1

    if not total and not subcarpetas:
        return f"La carpeta {base.name} está vacía."

    tipos = sorted(por_tipo.items(), key=lambda x: x[1], reverse=True)[:4]
    desc_tipos = ", ".join(f"{n} archivos {e}" for e, n in tipos)

    partes = [f"En {base.name} hay {total} archivos"]
    if desc_tipos:
        partes.append(f"principalmente {desc_tipos}")
    if subcarpetas:
        partes.append(f"y {len(subcarpetas)} carpetas: {', '.join(subcarpetas[:5])}")

    return ". ".join(partes) + "."


def archivos_recientes(dias: int = 7, carpeta: str = "", limite: int = 10) -> str:
    """Lista lo modificado ultimamente. Util para 'en que estaba trabajando'."""
    from tools.archivos import _base_desde_alias

    bases = [_base_desde_alias(carpeta)] if carpeta else list(CARPETAS_PERMITIDAS)
    corte = datetime.now() - timedelta(days=max(1, int(dias)))
    recientes = []

    for base in bases:
        if not base.is_dir():
            continue
        for raiz, dirs, ficheros in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _CARPETAS_IGNORADAS and not d.startswith(".")]
            for fichero in ficheros:
                if fichero.startswith("."):
                    continue
                ruta = Path(raiz) / fichero
                try:
                    momento = datetime.fromtimestamp(ruta.stat().st_mtime)
                except Exception:
                    continue
                if momento >= corte:
                    recientes.append((ruta, momento))

    if not recientes:
        return f"No has tocado ningún archivo en los últimos {dias} días."

    recientes.sort(key=lambda x: x[1], reverse=True)
    nombres = ", ".join(r.name for r, _ in recientes[:limite][:5])

    return (
        f"En los últimos {dias} días has tocado {len(recientes)} archivos. "
        f"Los más recientes: {nombres}."
    )


# -------------------------------------------------------------------------
# ARCHIVOS OLVIDADOS
# -------------------------------------------------------------------------
def archivos_olvidados(dias: int = 120, minimo_mb: float = 40.0,
                       cuantos: int = 8) -> str:
    """
    Archivos grandes que llevan mucho sin abrirse.

    Dos condiciones a la vez, y las dos importan. Solo por tamaño saldrian
    programas y archivos de trabajo en uso; solo por antiguedad saldrian mil
    ficheros de cien kilobytes que no liberan nada. Grande Y olvidado es lo
    que de verdad ocupa espacio sin dar nada a cambio.

    No borra nada. Solo mira y cuenta.
    """
    import time
    from config import CARPETAS_PERMITIDAS

    ahora = time.time()
    limite = dias * 86400
    encontrados = []

    for carpeta in CARPETAS_PERMITIDAS:
        if not carpeta.is_dir():
            continue
        try:
            for ruta in carpeta.rglob("*"):
                try:
                    if not ruta.is_file():
                        continue
                    datos = ruta.stat()
                    mb = datos.st_size / (1024 ** 2)
                    if mb < minimo_mb:
                        continue

                    # st_atime en Windows es poco de fiar (muchos sistemas lo
                    # tienen desactivado por rendimiento), asi que nos quedamos
                    # con la fecha mas reciente entre acceso y modificacion.
                    ultimo = max(datos.st_atime, datos.st_mtime)
                    if ahora - ultimo < limite:
                        continue

                    encontrados.append({
                        "nombre": ruta.name,
                        "mb": mb,
                        "meses": int((ahora - ultimo) / 2_592_000),
                        "carpeta": carpeta.name,
                    })
                except (OSError, PermissionError):
                    continue
        except (OSError, PermissionError):
            continue

    if not encontrados:
        return (f"No encontré archivos de más de {minimo_mb:.0f} megas "
                f"sin tocar en {dias // 30} meses. Lo tienes limpio.")

    encontrados.sort(key=lambda d: d["mb"], reverse=True)
    total_gb = sum(d["mb"] for d in encontrados) / 1024

    detalle = ", ".join(
        f"{d['nombre']} de {d['mb']:.0f} megas, sin abrir hace {d['meses']} meses"
        for d in encontrados[:3]
    )

    return (f"Encontré {len(encontrados)} archivos grandes olvidados, "
            f"{total_gb:.1f} gigas en total. Los mayores: {detalle}. "
            "No he borrado nada: dime cuál quieres que elimine.")
