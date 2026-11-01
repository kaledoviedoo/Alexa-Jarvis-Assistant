"""Rutinas: lo que Jarvis hace solo, a una hora o cuando pasa algo.

Viven en ~/.jarvis/rutinas.yaml para que puedas leerlas y editarlas a mano;
si lo cambias, Jarvis lo relee solo en menos de un minuto. Lo que recuerda de
cada rutina (cuando corrio por ultima vez) va aparte, en rutinas_estado.json,
para que el YAML siga limpio.

Cada rutina tiene:
  nombre      como la llamas por voz ("apaga la rutina de buenos dias")
  activa      true o false
  disparador  cuando corre (ver DISPARADORES)
  y una de:
    accion    una de las de ACCIONES (las de serie)
    aviso     un texto que se te recuerda
    orden     una orden en lenguaje normal que resuelve el modelo
  riesgos     lo que puede tocar. Nunca externa ni irreversible: enviar,
              apagar o cerrar programas siempre necesita tu "si" en vivo.
  avisar      false para que no salte la notificacion de Windows

Todo es local y gratis: un hilo dentro del servidor que mira cada 30 s.
"""

import json
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("jarvis.rutinas")

DIAS = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
DIAS_HABLADOS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos")
TODOS = [0, 1, 2, 3, 4, 5, 6]
SEMANA = [0, 1, 2, 3, 4]
FINDE = [5, 6]

DISPARADORES = ("hora", "una_vez", "disco", "juego")
PROHIBIDOS = ("externa", "irreversible")
SEGUNDOS_TICK = 30
MINUTOS_ENTRE_DISCOS = 10

PREDETERMINADAS = [
    {"nombre": "buenos dias", "activa": True,
     "disparador": {"tipo": "hora", "dias": SEMANA, "hora": "07:00", "margen": 180},
     "accion": "buenos_dias", "riesgos": ["lectura"]},
    {"nombre": "descargas", "activa": True,
     "disparador": {"tipo": "hora", "dias": TODOS, "hora": "23:00", "margen": 120},
     "accion": "proponer_descargas", "riesgos": ["lectura"]},
    {"nombre": "disco lleno", "activa": True,
     "disparador": {"tipo": "disco", "umbral": 90},
     "accion": "disco_lleno", "riesgos": ["lectura"]},
    {"nombre": "modo juego", "activa": True,
     "disparador": {"tipo": "juego"},
     "accion": "modo_juego", "riesgos": ["escritura"]},
    {"nombre": "memoria al dia", "activa": True,
     "disparador": {"tipo": "hora", "dias": TODOS, "hora": "03:00", "margen": 360, "inactivo": True},
     "accion": "memoria_al_dia", "riesgos": ["escritura"], "avisar": False},
]

CABECERA = """\
# Rutinas de Jarvis. Puedes editarlo a mano: se relee solo.
#
# disparador:
#   {tipo: hora, dias: [0..6], hora: "07:00", margen: 180}   0 = lunes; margen en minutos
#                                                            por si el PC estaba apagado
#   {tipo: una_vez, cuando: "2026-11-02T17:00"}              se borra al cumplirse
#   {tipo: disco, umbral: 90}                                 % de disco usado
#   {tipo: juego}                                             al abrir Valorant o un juego de Steam
# Y una de:  accion (las de serie) | aviso: "texto" | orden: "orden normal"
# riesgos: [lectura] o [lectura, escritura]. Nunca externa ni irreversible.

"""

_lock = threading.RLock()
_dir: Path | None = None
_cache: dict = {"mtime": None, "rutinas": []}
_hilo: threading.Thread | None = None
_parar = threading.Event()
_corriendo: set = set()


# ---------------------------------------------------------------------------
# Archivos
# ---------------------------------------------------------------------------
def _carpeta() -> Path:
    global _dir
    if _dir is None:
        from config import CARPETA_DATOS
        _dir = Path(CARPETA_DATOS)
    _dir.mkdir(parents=True, exist_ok=True)
    return _dir


def usar_carpeta(ruta) -> None:
    """Las pruebas trabajan en una carpeta temporal."""
    global _dir
    with _lock:
        _dir = Path(ruta)
        _cache.update(mtime=None, rutinas=[])


def ruta_rutinas() -> Path:
    return _carpeta() / "rutinas.yaml"


def _ruta_estado() -> Path:
    return _carpeta() / "rutinas_estado.json"


def ruta_propuesta() -> Path:
    return _carpeta() / "propuesta_descargas.json"


def _leer_yaml(texto: str):
    try:
        import yaml
        return yaml.safe_load(texto)
    except ImportError:
        # Sin PyYAML el archivo se guarda como JSON, que tambien es YAML valido.
        return json.loads(texto)


def _escribir_yaml(datos) -> str:
    try:
        import yaml
        return CABECERA + yaml.safe_dump(datos, allow_unicode=True, sort_keys=False,
                                         default_flow_style=None, width=100)
    except ImportError:
        return json.dumps(datos, ensure_ascii=False, indent=2)


def validar(r) -> dict | None:
    """Una rutina bien formada, o None. Quita los riesgos prohibidos."""
    if not isinstance(r, dict) or not str(r.get("nombre", "")).strip():
        return None
    d = r.get("disparador")
    if not isinstance(d, dict) or d.get("tipo") not in DISPARADORES:
        log.warning("Rutina %r: disparador no valido (%r).", r.get("nombre"), d)
        return None
    if d["tipo"] == "hora" and not re.fullmatch(r"\d{1,2}:\d{2}", str(d.get("hora", ""))):
        log.warning("Rutina %r: hora no valida (%r).", r.get("nombre"), d.get("hora"))
        return None
    if d["tipo"] == "una_vez":
        try:
            datetime.fromisoformat(str(d.get("cuando")))
        except Exception:
            log.warning("Rutina %r: fecha no valida (%r).", r.get("nombre"), d.get("cuando"))
            return None
    if not any(k in r for k in ("accion", "aviso", "orden")):
        log.warning("Rutina %r: no dice que hacer.", r.get("nombre"))
        return None
    if "accion" in r and r["accion"] not in ACCIONES:
        log.warning("Rutina %r: accion desconocida (%r).", r.get("nombre"), r["accion"])
        return None
    limpia = dict(r)
    limpia["nombre"] = str(r["nombre"]).strip()
    limpia["activa"] = bool(r.get("activa", True))
    riesgos = [x for x in (r.get("riesgos") or ["lectura"]) if x in ("lectura", "escritura", "externa", "irreversible")]
    prohibidos = [x for x in riesgos if x in PROHIBIDOS]
    if prohibidos:
        log.warning("Rutina %r: quito %s; una rutina nunca envia ni hace nada irreversible.",
                    limpia["nombre"], prohibidos)
    limpia["riesgos"] = [x for x in riesgos if x not in PROHIBIDOS] or ["lectura"]
    return limpia


def cargar() -> list[dict]:
    """Las rutinas del archivo. Si no existe, lo crea con las de serie."""
    ruta = ruta_rutinas()
    with _lock:
        if not ruta.exists():
            guardar([dict(r) for r in PREDETERMINADAS])
        mtime = ruta.stat().st_mtime
        if _cache["mtime"] == mtime:
            return [dict(r) for r in _cache["rutinas"]]
        try:
            datos = _leer_yaml(ruta.read_text(encoding="utf-8")) or []
        except Exception as e:
            log.error("No pude leer %s: %s. Sigo con las que tenia.", ruta, e)
            return [dict(r) for r in _cache["rutinas"]]
        if isinstance(datos, dict):
            datos = datos.get("rutinas", [])
        rutinas = [v for v in (validar(r) for r in datos) if v]
        _cache.update(mtime=mtime, rutinas=rutinas)
        return [dict(r) for r in rutinas]


def guardar(rutinas: list[dict]) -> None:
    ruta = ruta_rutinas()
    with _lock:
        ruta.write_text(_escribir_yaml(rutinas), encoding="utf-8")
        _cache.update(mtime=ruta.stat().st_mtime, rutinas=[dict(r) for r in rutinas])


def _cargar_estado() -> dict:
    try:
        return json.loads(_ruta_estado().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _guardar_estado(estado: dict) -> None:
    try:
        _ruta_estado().write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.debug("No pude guardar el estado de las rutinas: %s", e)


# ---------------------------------------------------------------------------
# Cuando toca
# ---------------------------------------------------------------------------
def _hora_de(texto: str) -> tuple[int, int]:
    h, m = str(texto).split(":")
    return int(h), int(m)


def _ultima(est: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(est.get("ultima"))
    except Exception:
        return None


def toca(r: dict, ahora: datetime, est: dict) -> bool:
    """Si la rutina tiene que correr ahora."""
    if not r.get("activa", True):
        return False
    d = r["disparador"]
    ultima = _ultima(est)

    if d["tipo"] == "hora":
        if ahora.weekday() not in (d.get("dias") or TODOS):
            return False
        h, m = _hora_de(d["hora"])
        programada = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
        margen = timedelta(minutes=int(d.get("margen", 60)))
        if not (programada <= ahora <= programada + margen):
            return False
        if ultima is not None and ultima >= programada:
            return False
        if d.get("inactivo") and not pc_libre():
            return False
        return True

    if d["tipo"] == "una_vez":
        return ultima is None and datetime.fromisoformat(str(d["cuando"])) <= ahora

    if d["tipo"] == "disco":
        return ultima is None or ahora - ultima >= timedelta(minutes=MINUTOS_ENTRE_DISCOS)

    if d["tipo"] == "juego":
        return True

    return False


def pc_libre() -> bool:
    """Nadie le habla a Jarvis y el procesador esta tranquilo."""
    try:
        import mantener_caliente
        if mantener_caliente.hay_actividad_reciente():
            return False
    except Exception:
        pass
    try:
        import psutil
        return psutil.cpu_percent(interval=1) < 25
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Herramientas desde una rutina: siempre con su limite de riesgo
# ---------------------------------------------------------------------------
def usar(nombre: str, r: dict, **args) -> str:
    """Llama a una herramienta del registro si el riesgo de la rutina lo permite."""
    import registro
    h = registro.obtener(nombre)
    if h is None:
        return f"No existe {nombre}."
    permitidos = set(r.get("riesgos") or ["lectura"]) | {"lectura"}
    if h.riesgo in PROHIBIDOS or h.riesgo not in permitidos:
        log.warning("Rutina %r: no puede usar %s (riesgo %s).", r.get("nombre"), nombre, h.riesgo)
        return f"No usé {nombre}: una rutina no puede hacer cosas de riesgo {h.riesgo}."
    try:
        return str(h.funcion(**args))
    except Exception as e:
        log.warning("Rutina %r: %s falló: %s", r.get("nombre"), nombre, e)
        return f"{nombre} falló: {e}"


def _primera_linea(texto: str, maximo: int = 200) -> str:
    linea = (str(texto or "").strip().split("\n") or [""])[0].strip()
    if len(linea) > maximo:
        linea = linea[:maximo].rsplit(" ", 1)[0] + "…"
    return linea.rstrip(".")


# ---------------------------------------------------------------------------
# Acciones de serie
# ---------------------------------------------------------------------------
def buenos_dias(r: dict, est: dict) -> str:
    partes = ["Buenos días"]
    partes.append(_primera_linea(usar("estado_sistema", r)))
    correo = usar("correos_sin_leer", r)
    if correo and "falló" not in correo:
        partes.append(_primera_linea(correo))
    diario = usar("obsidian_leer_diario", r)
    if diario:
        partes.append(_primera_linea(diario, 260))
    return ". ".join(p for p in partes if p) + "."


# Descargas: se propone de noche y se mueve solo si dices que si.
TIPOS = {
    "PDF": (".pdf",),
    "Documentos": (".doc", ".docx", ".odt", ".rtf", ".txt", ".md"),
    "Hojas de calculo": (".xls", ".xlsx", ".xlsm", ".csv", ".ods"),
    "Presentaciones": (".ppt", ".pptx", ".odp"),
    "Imagenes": (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic"),
    "Videos": (".mp4", ".mkv", ".mov", ".avi", ".webm"),
    "Audio": (".mp3", ".wav", ".flac", ".m4a", ".ogg"),
    "Comprimidos": (".zip", ".rar", ".7z", ".tar", ".gz"),
    "Instaladores": (".exe", ".msi"),
    "Codigo": (".py", ".js", ".ts", ".java", ".c", ".cpp", ".html", ".css", ".json", ".sql", ".ipynb"),
}
A_MEDIAS = (".crdownload", ".part", ".partial", ".tmp", ".download")
HORAS_DE_PROPUESTA = 48


def _descargas() -> Path:
    from config import DESCARGAS
    return Path(DESCARGAS)


def _tipo(ruta: Path) -> str | None:
    ext = ruta.suffix.lower()
    for carpeta, extensiones in TIPOS.items():
        if ext in extensiones:
            return carpeta
    return None


def plan_de_descargas(carpeta: Path | None = None) -> list[tuple[str, str]]:
    """(archivo, subcarpeta) para los archivos sueltos de Descargas.

    Nunca toca carpetas, ni lo que se esta bajando, ni lo de los ultimos 10
    minutos. Solo propone subcarpetas dentro de la propia Descargas.
    """
    carpeta = carpeta or _descargas()
    limite = time.time() - 600
    plan = []
    try:
        hijos = sorted(carpeta.iterdir())
    except Exception:
        return []
    for p in hijos:
        try:
            if not p.is_file() or p.name.startswith(".") or p.name.lower() == "desktop.ini":
                continue
            if p.suffix.lower() in A_MEDIAS or p.stat().st_mtime > limite:
                continue
        except OSError:
            continue
        tipo = _tipo(p)
        if tipo:
            plan.append((p.name, tipo))
    return plan


def _resumen_plan(plan: list[tuple[str, str]]) -> str:
    cuenta: dict = {}
    for _, tipo in plan:
        cuenta[tipo] = cuenta.get(tipo, 0) + 1
    trozos = [f"{n} a {tipo}" for tipo, n in sorted(cuenta.items(), key=lambda x: -x[1])]
    if len(trozos) > 1:
        return ", ".join(trozos[:-1]) + " y " + trozos[-1]
    return trozos[0] if trozos else ""


def proponer_descargas(r: dict, est: dict) -> str | None:
    plan = plan_de_descargas()
    if not plan:
        return None
    ruta_propuesta().write_text(json.dumps(
        {"fecha": datetime.now().isoformat(timespec="seconds"), "carpeta": str(_descargas()),
         "plan": plan}, ensure_ascii=False, indent=1), encoding="utf-8")
    return (f"Preparé cómo ordenar {len(plan)} archivos sueltos de Descargas en subcarpetas: "
            f"{_resumen_plan(plan)}. No he movido nada; dime «ordena las descargas» para hacerlo.")


def propuesta_pendiente() -> dict | None:
    try:
        datos = json.loads(ruta_propuesta().read_text(encoding="utf-8"))
        if datetime.now() - datetime.fromisoformat(datos["fecha"]) > timedelta(hours=HORAS_DE_PROPUESTA):
            return None
        return datos if datos.get("plan") else None
    except Exception:
        return None


def marcar_preguntada() -> None:
    datos = propuesta_pendiente()
    if datos:
        datos["preguntada"] = True
        ruta_propuesta().write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def aplicar_descargas() -> str:
    """Mueve lo que se propuso. Lo que ya no esta, se salta; nada se sobrescribe."""
    import shutil
    datos = propuesta_pendiente()
    if datos:
        carpeta, plan = Path(datos["carpeta"]), [tuple(x) for x in datos["plan"]]
    else:
        carpeta = _descargas()
        plan = plan_de_descargas(carpeta)
    if not plan:
        return "No hay nada suelto que ordenar en Descargas."
    base = carpeta.resolve()
    movidos = []
    for nombre, tipo in plan:
        origen = carpeta / nombre
        destino_dir = carpeta / tipo
        if not origen.is_file() or Path(nombre).name != nombre or tipo not in TIPOS:
            continue
        if destino_dir.resolve().parent != base:
            continue
        destino_dir.mkdir(exist_ok=True)
        destino = destino_dir / nombre
        n = 1
        while destino.exists():
            destino = destino_dir / f"{origen.stem} ({n}){origen.suffix}"
            n += 1
        try:
            shutil.move(str(origen), str(destino))
            movidos.append((nombre, tipo))
        except Exception as e:
            log.warning("No pude mover %s: %s", origen, e)
    try:
        ruta_propuesta().unlink()
    except Exception:
        pass
    if not movidos:
        return "Los archivos de la propuesta ya no estaban en Descargas; no moví nada."
    log.info("Descargas ordenadas: %d archivos.", len(movidos))
    return f"Ordené {len(movidos)} archivos de Descargas: {_resumen_plan(movidos)}. No borré nada."


def disco_lleno(r: dict, est: dict) -> str | None:
    import psutil
    umbral = float(r["disparador"].get("umbral", 90))
    avisados = est.setdefault("avisados", {})
    llenos = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in (part.opts or "") or not part.fstype:
            continue
        try:
            uso = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        if uso.percent < umbral:
            avisados.pop(part.mountpoint, None)
            continue
        ultimo = avisados.get(part.mountpoint)
        if ultimo and datetime.now() - datetime.fromisoformat(ultimo) < timedelta(hours=24):
            continue
        avisados[part.mountpoint] = datetime.now().isoformat(timespec="seconds")
        llenos.append(f"El disco {part.mountpoint.rstrip(chr(92))} está al {uso.percent:.0f} % "
                      f"({uso.free / 1e9:.1f} GB libres)")
    if not llenos:
        return None
    try:
        from tools import sistema
        grandes = sistema.archivos_mas_grandes(5)      # solo mira, no toca nada
    except Exception:
        grandes = ""
    texto = ". ".join(llenos) + "."
    if grandes:
        texto += " " + _primera_linea(grandes, 300) + "."
    return texto


def juego_abierto(procesos=None) -> str | None:
    """El juego que esta corriendo (Valorant o uno de Steam), o None."""
    if procesos is None:
        import psutil
        procesos = []
        for p in psutil.process_iter(["name", "exe"]):
            try:
                procesos.append((p.info.get("name") or "", p.info.get("exe") or ""))
            except Exception:
                continue
    for nombre, exe in procesos:
        n, e = (nombre or "").lower(), (exe or "").lower().replace("/", "\\")
        if "valorant" in n:
            return "Valorant"
        if "\\steamapps\\common\\" in e:
            juego = e.split("\\steamapps\\common\\", 1)[1].split("\\", 1)[0]
            return juego.title() if juego else "un juego de Steam"
    return None


def modo_juego(r: dict, est: dict, procesos=None) -> str | None:
    """Al abrir un juego pasa a gaming; al cerrarlo vuelve al modo de antes."""
    import modes
    juego = juego_abierto(procesos)
    if juego:
        est["sin_juego"] = 0
        if est.get("juego"):
            return None
        est["juego"] = juego
        actual = modes.modo_actual()
        if actual == "gaming":
            return None
        est["modo_previo"] = actual
        usar("cambiar_modo", r, nuevo_modo="gaming")
        return (f"Abriste {juego}: pasé a modo gaming para dejarte la gráfica libre. "
                "Si quieres liberar más memoria, dime «cierra lo que sobra».")
    if not est.get("juego"):
        return None
    est["sin_juego"] = est.get("sin_juego", 0) + 1
    if est["sin_juego"] < 2:            # un parpadeo no cuenta
        return None
    est.pop("juego", None)
    previo = est.pop("modo_previo", None)
    if previo and modes.modo_actual() == "gaming":
        usar("cambiar_modo", r, nuevo_modo=previo)
        return f"Cerraste el juego: volví al modo {previo}."
    return None


def memoria_al_dia(r: dict, est: dict) -> str:
    from tools import memoria
    partes = []
    for funcion in (memoria.indexar, memoria.indexar_proyecto):
        try:
            partes.append(_primera_linea(funcion()))
        except Exception as e:
            partes.append(f"{funcion.__name__} falló: {e}")
    return "Memoria al día. " + ". ".join(partes) + "."


def _orden(r: dict, est: dict) -> str:
    """Una orden en lenguaje normal: la resuelve el modelo, con el riesgo limitado."""
    import ollama_client
    return ollama_client.con_riesgos(r.get("riesgos") or ["lectura"], ollama_client._conversar, r["orden"])


ACCIONES = {
    "buenos_dias": buenos_dias,
    "proponer_descargas": proponer_descargas,
    "disco_lleno": disco_lleno,
    "modo_juego": modo_juego,
    "memoria_al_dia": memoria_al_dia,
}
RAPIDAS = ("disco", "juego")       # se miran en el propio hilo y casi nunca dicen nada


def _funcion(r: dict):
    if "accion" in r:
        return ACCIONES[r["accion"]]
    if "aviso" in r:
        return lambda r, est: f"Recordatorio: {str(r['aviso']).rstrip('.')}."
    return _orden


def ejecutar(r: dict, estado: dict, ahora: datetime | None = None) -> None:
    """Corre una rutina. Las rapidas en este hilo; el resto al fondo, como una tarea."""
    import tareas
    ahora = ahora or datetime.now()
    nombre = r["nombre"]
    est = estado.setdefault(nombre, {})
    est["ultima"] = ahora.isoformat(timespec="seconds")
    funcion = _funcion(r)
    avisar = r.get("avisar", True)

    if r["disparador"]["tipo"] in RAPIDAS:
        try:
            texto = funcion(r, est)
        except Exception as e:
            log.warning("Rutina %r falló: %s", nombre, e)
            return
        if texto:
            tareas.registrar_resultado(f"r{int(time.time() * 1000) % 10**9:09d}",
                                       f"rutina {nombre}", texto, avisar=avisar)
        return

    with _lock:
        if nombre in _corriendo:
            return
        _corriendo.add(nombre)

    def _trabajo():
        try:
            return funcion(r, est)
        finally:
            with _lock:
                _corriendo.discard(nombre)

    log.info("Rutina %r: en marcha.", nombre)
    tareas.lanzar_en_segundo_plano(f"rutina {nombre}", _trabajo, origen="rutina", avisar=avisar)

    if r["disparador"]["tipo"] == "una_vez":
        guardar([x for x in cargar() if x["nombre"] != nombre])
        estado.pop(nombre, None)


def tick(ahora: datetime | None = None) -> list[str]:
    """Una vuelta del planificador. Devuelve los nombres que corrieron."""
    ahora = ahora or datetime.now()
    estado = _cargar_estado()
    corridas = []
    for r in cargar():
        est = estado.setdefault(r["nombre"], {})
        try:
            if toca(r, ahora, est):
                ejecutar(r, estado, ahora)
                corridas.append(r["nombre"])
        except Exception as e:
            log.warning("Rutina %r: %s", r.get("nombre"), e)
    _guardar_estado(estado)
    return corridas


def _bucle() -> None:
    log.info("Planificador de rutinas en marcha (%d rutinas).", len(cargar()))
    while not _parar.wait(SEGUNDOS_TICK):
        try:
            tick()
        except Exception as e:
            log.exception("Fallo en el planificador: %s", e)


def arrancar() -> bool:
    global _hilo
    if _hilo and _hilo.is_alive():
        return False
    _parar.clear()
    _hilo = threading.Thread(target=_bucle, daemon=True, name="rutinas")
    _hilo.start()
    return True


def detener() -> None:
    _parar.set()


# ---------------------------------------------------------------------------
# Por voz
# ---------------------------------------------------------------------------
def _plano(texto: str) -> str:
    """Minusculas y sin tildes, con la misma longitud que el original."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in (texto or "").lower())


_NUM = {"una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
        "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "un": 1, "media": 30}
_DIA = r"(?:lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)"
RE_DIAS = re.compile(
    r"\b(?P<todos>todos\s+los\s+dias|todas\s+las\s+(?:mananas|tardes|noches)|cada\s+dia|a\s+diario|diariamente)\b"
    r"|\b(?P<semana>entre\s+semana|de\s+lunes\s+a\s+viernes|(?:los\s+)?dias\s+(?:de\s+semana|laborales|habiles))\b"
    r"|\b(?P<finde>(?:los\s+|todos\s+los\s+|cada\s+)?fines?\s+de\s+semana)\b"
    rf"|\b(?:todos\s+los|cada|los)\s+(?P<lista>{_DIA}(?:\s*(?:,|\by\b|\be\b)\s*(?:los\s+|el\s+)?{_DIA})*)\b")
RE_HORA = re.compile(
    r"\ba\s+las?\s+(?P<h>\d{1,2}|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
    r"(?:(?::|\.|\s+y\s+)(?P<m>\d{2}|media|cuarto))?"
    r"(?:\s*(?P<suf>de\s+la\s+manana|de\s+la\s+tarde|de\s+la\s+noche|en\s+punto|a\.?\s?m\.?|p\.?\s?m\.?))?")
RE_MEDIODIA = re.compile(r"\b(?:al|a\s+el)\s+mediodia\b|\ba\s+medianoche\b")
RE_EN = re.compile(r"\ben\s+(?P<n>\d{1,3}|un|una|media|dos|tres|cuatro|cinco|diez|quince|veinte|treinta)"
                   r"\s+(?P<u>minutos?|horas?)\b")
RE_MANANA = re.compile(r"\b(?P<pasado>pasado\s+)?manana\b(?<!de\sla\smanana)")
RE_EL_DIA = re.compile(rf"\b(?:el|este|el\s+proximo|el\s+otro)\s+(?P<dia>{_DIA})\b")
RE_CLAVE = re.compile(r"\b(?:recuerdame|recordarme|recuerdeme|avisame|aviseme|ponme\s+un\s+recordatorio|"
                      r"crea\s+un\s+recordatorio|recordatorio)\b(?:\s+(?:de|para|que))?")
VERBOS_ORDEN = ("investiga", "busca", "dime", "revisa", "lee", "resume", "resumeme", "comprueba",
                "mira", "compara", "cuentame", "dame")
_MAS_NUM = {"quince": 15, "veinte": 20, "treinta": 30, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "diez": 10}


def _dias_de(m) -> list[int]:
    if m.group("todos"):
        return TODOS
    if m.group("semana"):
        return SEMANA
    if m.group("finde"):
        return FINDE
    dias = []
    for d in re.findall(_DIA, m.group("lista")):
        i = DIAS.index(d.rstrip("s") if d.rstrip("s") in DIAS else d)
        if i not in dias:
            dias.append(i)
    return sorted(dias)


def _hora_hablada(m) -> tuple[int, int]:
    h = m.group("h")
    h = int(h) if h.isdigit() else _NUM[h]
    mm = m.group("m")
    minutos = 0 if not mm else (30 if mm == "media" else 15 if mm == "cuarto" else int(mm))
    suf = (m.group("suf") or "").replace(".", "").replace(" ", "")
    if suf in ("delamanana", "am"):
        h = 0 if h == 12 else h
    elif suf in ("delatarde", "pm"):
        h = h if h == 12 else (h % 12) + 12
    elif suf == "delanoche":
        h = 0 if h == 12 else (h % 12) + 12
    elif 1 <= h <= 6:
        h += 12                         # "a las 5" casi siempre es por la tarde
    return h % 24, minutos % 60


def interpretar(frase: str, ahora: datetime | None = None) -> dict | str | None:
    """La rutina que pide una frase, un texto si falta algo, o None si no es una rutina.

    "todos los lunes a las 8 recuérdame revisar GPIN"
    "recuérdame en 20 minutos sacar la ropa"
    "mañana a las 7 de la mañana recuérdame el parcial"
    "cada día a las 7 investiga noticias de inteligencia artificial"
    """
    ahora = ahora or datetime.now()
    plano = _plano(frase)

    m_clave = RE_CLAVE.search(plano)
    m_dias = RE_DIAS.search(plano)
    m_hora = RE_HORA.search(plano)
    m_medio = RE_MEDIODIA.search(plano) if not m_hora else None
    m_en = RE_EN.search(plano)
    m_man = RE_MANANA.search(plano) if not m_dias else None
    m_el = RE_EL_DIA.search(plano) if not m_dias else None

    if m_clave:
        tipo = "aviso"
    elif m_dias and m_dias.start() < 4:
        tipo = "orden"
    else:
        return None

    # El contenido es la frase sin la palabra clave, los dias y las horas.
    mascara = list(frase)
    for m in (m_clave, m_dias, m_hora, m_medio, m_en, m_man, m_el):
        if m:
            for k in range(*m.span()):
                mascara[k] = " "
    contenido = "".join(mascara)
    contenido = re.sub(r"\s+", " ", contenido)
    contenido = re.sub(r"^[\s,.:;]*(?:(?:y|que|de|a|para)\s+)*", "", contenido, flags=re.I)
    contenido = re.sub(r"(?:[\s,.;:]+(?:y|a|que|de))*[\s,.;:]*$", "", contenido, flags=re.I).strip()

    if tipo == "orden" and (not contenido or _plano(contenido).split()[0] not in VERBOS_ORDEN):
        return None
    if not contenido:
        return "¿Qué quieres que te recuerde? Dímelo así: recuérdame a las 5 llamar a mamá."

    hora = None
    if m_hora:
        hora = _hora_hablada(m_hora)
    elif m_medio:
        hora = (0, 0) if "medianoche" in m_medio.group(0) else (12, 0)

    if m_dias:
        if hora is None:
            return ("¿A qué hora? Dímelo así: todos los lunes a las 8 recuérdame "
                    f"{contenido if tipo == 'aviso' else 'revisar el correo'}.")
        disparador = {"tipo": "hora", "dias": _dias_de(m_dias),
                      "hora": f"{hora[0]:02d}:{hora[1]:02d}", "margen": 60}
    elif m_en:
        n = m_en.group("n")
        hora_u = m_en.group("u").startswith("hora")
        if n == "media":
            delta = timedelta(minutes=30) if hora_u else timedelta(seconds=30)
        else:
            n = int(n) if n.isdigit() else _MAS_NUM.get(n, _NUM.get(n, 1))
            delta = timedelta(hours=n) if hora_u else timedelta(minutes=n)
        disparador = {"tipo": "una_vez", "cuando": (ahora + delta).isoformat(timespec="minutes")}
    elif hora is not None or m_man or m_el:
        h, mi = hora if hora is not None else (8, 0)      # "mañana recuérdame…" sin hora: a las 8
        cuando = ahora.replace(hour=h, minute=mi, second=0, microsecond=0)
        if m_el:
            dia = m_el.group("dia")
            objetivo = DIAS.index(dia.rstrip("s") if dia.rstrip("s") in DIAS else dia)
            faltan = (objetivo - ahora.weekday()) % 7 or (7 if cuando <= ahora else 0)
            cuando += timedelta(days=faltan)
        elif m_man:
            cuando += timedelta(days=2 if m_man.group("pasado") else 1)
        elif cuando <= ahora:
            cuando += timedelta(days=1)
        disparador = {"tipo": "una_vez", "cuando": cuando.isoformat(timespec="minutes")}
    else:
        if tipo == "aviso":
            return ("¿Cuándo te lo recuerdo? Dime por ejemplo: recuérdame a las 5 "
                    f"{contenido}, o en 20 minutos.")
        return None

    r = {"nombre": _nombre_nuevo(contenido), "activa": True, "disparador": disparador,
         "riesgos": ["lectura"]}
    r["aviso" if tipo == "aviso" else "orden"] = contenido
    return r


def _nombre_nuevo(contenido: str) -> str:
    base = " ".join(_plano(contenido).split()[:5])[:40] or "recordatorio"
    existentes = {r["nombre"] for r in cargar()}
    nombre, n = base, 2
    while nombre in existentes:
        nombre, n = f"{base} {n}", n + 1
    return nombre


def _hora_bonita(texto: str) -> str:
    h, m = _hora_de(texto)
    return f"{h}:{m:02d}"


def describir(r: dict, ahora: datetime | None = None) -> str:
    """Cuando corre, dicho en voz alta."""
    ahora = ahora or datetime.now()
    d = r["disparador"]
    if d["tipo"] == "hora":
        dias = sorted(d.get("dias") or TODOS)
        if dias == TODOS:
            cuando = "todos los días"
        elif dias == SEMANA:
            cuando = "de lunes a viernes"
        elif dias == FINDE:
            cuando = "los fines de semana"
        else:
            nombres = [DIAS_HABLADOS[i] for i in dias]
            cuando = "los " + (", ".join(nombres[:-1]) + " y " + nombres[-1] if len(nombres) > 1 else nombres[0])
        return f"{cuando} a las {_hora_bonita(d['hora'])}"
    if d["tipo"] == "una_vez":
        c = datetime.fromisoformat(str(d["cuando"]))
        if c.date() == ahora.date():
            dia = "hoy"
        elif c.date() == (ahora + timedelta(days=1)).date():
            dia = "mañana"
        else:
            dia = f"el {c.day}/{c.month}"
        return f"{dia} a las {c.hour}:{c.minute:02d}"
    if d["tipo"] == "disco":
        return f"cuando un disco pase del {d.get('umbral', 90)} %"
    if d["tipo"] == "juego":
        return "cuando abras Valorant o un juego de Steam"
    return ""


def crear_desde_frase(frase: str) -> str | None:
    r = interpretar(frase)
    if r is None or isinstance(r, str):
        return r
    r = validar(r)
    if r is None:
        return None
    guardar(cargar() + [r])
    log.info("Rutina nueva: %s", r)
    if "aviso" in r:
        return f"Listo: {describir(r)} te recuerdo {r['aviso']}."
    return f"Listo: {describir(r)} haré esto: {r['orden']}. Te aviso con el resultado."


def buscar(nombre: str, rutinas: list[dict] | None = None) -> list[dict]:
    """Las rutinas que encajan con como las nombraste."""
    rutinas = rutinas if rutinas is not None else cargar()
    q = _plano(nombre).strip(" .")
    q = re.sub(r"^(?:de\s+|del\s+|la\s+|las\s+|el\s+|los\s+|que\s+)+", "", q).strip()
    if not q:
        return []
    exactas = [r for r in rutinas if _plano(r["nombre"]) == q]
    if exactas:
        return exactas
    contiene = [r for r in rutinas if q in _plano(r["nombre"]) or _plano(r["nombre"]) in q]
    if contiene:
        return contiene
    palabras = [w for w in q.split() if len(w) >= 3]
    return [r for r in rutinas
            if any(w in _plano(r["nombre"]).split() or w in _plano(str(r.get("aviso", r.get("orden", ""))))
                   for w in palabras)]


def listar() -> str:
    rutinas = cargar()
    if not rutinas:
        return "No tienes rutinas."
    activas = [r for r in rutinas if r.get("activa", True)]
    apagadas = [r for r in rutinas if not r.get("activa", True)]
    trozos = [f"{r['nombre']} ({describir(r)})" for r in activas]
    texto = f"Tienes {len(activas)} rutinas activas: " + "; ".join(trozos) + "." if activas else "No tienes rutinas activas."
    if apagadas:
        texto += " Apagadas: " + ", ".join(r["nombre"] for r in apagadas) + "."
    return texto


def cambiar(nombre: str, accion: str) -> str:
    """accion: 'apagar', 'encender' o 'borrar'."""
    rutinas = cargar()
    q = _plano(nombre).strip(" .")
    if not q:
        nombres = ", ".join(r["nombre"] for r in rutinas) or "ninguna"
        return f"¿Cuál? Tienes: {nombres}."
    if re.fullmatch(r"todas(?:\s+las\s+rutinas)?|todos(?:\s+los\s+recordatorios)?", q):
        if accion == "borrar":
            return "No borro todas de golpe. Dime cuál, o apágalas todas."
        elegidas = rutinas
    else:
        elegidas = buscar(nombre, rutinas)
    if not elegidas:
        nombres = ", ".join(r["nombre"] for r in rutinas) or "ninguna"
        return f"No encontré esa rutina. Tienes: {nombres}."
    if len(elegidas) > 1 and accion == "borrar":
        return ("Encajan varias: " + ", ".join(r["nombre"] for r in elegidas) +
                ". Dime cuál borro.")
    nombres = {r["nombre"] for r in elegidas}
    if accion == "borrar":
        guardar([r for r in rutinas if r["nombre"] not in nombres])
        return f"Borré la rutina {elegidas[0]['nombre']}."
    activa = accion == "encender"
    for r in rutinas:
        if r["nombre"] in nombres:
            r["activa"] = activa
    guardar(rutinas)
    lista = ", ".join(sorted(nombres))
    return f"{'Encendí' if activa else 'Apagué'} {'las rutinas' if len(nombres) > 1 else 'la rutina'} {lista}."
