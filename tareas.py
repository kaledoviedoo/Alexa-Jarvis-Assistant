"""Los trabajos de fondo, guardados en ~/.jarvis/jarvis.db.

Lo que no cabe en los ocho segundos de Alexa sigue en un hilo y su resultado
queda aqui, para recogerlo despues con "como quedo lo ultimo".

Desde la Fase 5 esto sobrevive a un reinicio. Es SQLite, que viene con
Python: no hay nada que instalar ni que pagar.

- Lo que estaba corriendo cuando Jarvis se apago queda "interrumpida" y se
  cuenta en la siguiente sesion.
- Lo que termina sin que nadie lo estuviera esperando salta como aviso de
  Windows y queda "sin leer" hasta que se cuenta ("Por cierto, termine...").
- Cada herramienta que usa una tarea queda apuntada como un paso.
"""

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("jarvis.tareas")

ESTADOS = ("en_cola", "corriendo", "hecha", "fallo", "interrumpida")
DIAS_DE_HISTORIA = 30
MAX_TAREAS = 500

ESQUEMA = """
CREATE TABLE IF NOT EXISTS tareas (
    id          TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL,
    estado      TEXT NOT NULL,
    origen      TEXT NOT NULL DEFAULT 'voz',
    inicio      TEXT NOT NULL,
    fin         TEXT,
    resultado   TEXT,
    leida       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS pasos (
    tarea       TEXT NOT NULL,
    n           INTEGER NOT NULL,
    herramienta TEXT NOT NULL,
    argumentos  TEXT,
    resultado   TEXT,
    momento     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tareas_por_inicio ON tareas(inicio);
CREATE INDEX IF NOT EXISTS pasos_por_tarea ON pasos(tarea);
"""

# Frases con las que empiezan los resultados que en realidad son un fallo.
_EMPIEZA_FALLO = ("la tarea falló", "la tarea fallo", "tuve un problema", "falló la tarea")

_lock = threading.RLock()
_ruta: Path | None = None
_conexion: sqlite3.Connection | None = None
_local = threading.local()          # la tarea que corre en este hilo


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def _db() -> sqlite3.Connection:
    global _conexion, _ruta
    if _conexion is None:
        if _ruta is None:
            from config import CARPETA_DATOS
            _ruta = Path(CARPETA_DATOS) / "jarvis.db"
        _ruta.parent.mkdir(parents=True, exist_ok=True)
        _conexion = sqlite3.connect(str(_ruta), check_same_thread=False, isolation_level=None)
        _conexion.row_factory = sqlite3.Row
        _conexion.executescript(ESQUEMA)
    return _conexion


def usar_base(ruta) -> None:
    """Cambia de archivo (las pruebas usan uno temporal)."""
    global _conexion, _ruta
    with _lock:
        if _conexion is not None:
            _conexion.close()
        _conexion = None
        _ruta = Path(ruta)


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _consulta(sql: str, parametros: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return _db().execute(sql, parametros).fetchall()


def _ejecutar(sql: str, parametros: tuple = ()) -> int:
    with _lock:
        return _db().execute(sql, parametros).rowcount


# ---------------------------------------------------------------------------
# Ciclo de vida de una tarea
# ---------------------------------------------------------------------------
def registrar_inicio(identificador: str, descripcion: str, origen: str = "voz",
                     estado: str = "corriendo") -> None:
    _ejecutar("INSERT OR REPLACE INTO tareas (id, descripcion, estado, origen, inicio, leida) "
              "VALUES (?, ?, ?, ?, ?, 1)",
              (identificador, descripcion[:200], estado, origen, _ahora()))


def registrar_resultado(identificador: str, descripcion: str, resultado,
                        avisar: bool = True) -> None:
    """Cierra una tarea con su resultado.

    avisar=False es para lo que ya se le dijo a quien esperaba (la respuesta
    llego dentro del tiempo de Alexa): ni aviso de Windows ni "sin leer".
    """
    texto = "" if resultado is None else str(resultado)
    estado = "fallo" if texto.strip().lower().startswith(_EMPIEZA_FALLO) else "hecha"
    with _lock:
        cambiadas = _ejecutar(
            "UPDATE tareas SET estado = ?, fin = ?, resultado = ?, leida = ? WHERE id = ?",
            (estado, _ahora(), texto, 0 if avisar else 1, identificador))
        if not cambiadas:
            _ejecutar("INSERT INTO tareas (id, descripcion, estado, origen, inicio, fin, resultado, leida) "
                      "VALUES (?, ?, ?, 'voz', ?, ?, ?, ?)",
                      (identificador, descripcion[:200], estado, _ahora(), _ahora(), texto,
                       0 if avisar else 1))
    log.info("Tarea en segundo plano terminada: %s", descripcion)
    if avisar:
        try:
            import avisos
            avisos.avisar(f"Jarvis · {descripcion[:50]}", texto)
        except Exception as e:
            log.debug("No pude avisar: %s", e)


def descartar(identificador: str) -> None:
    """Una rutina que miro y no tenia nada que decir no deja rastro."""
    with _lock:
        _ejecutar("DELETE FROM pasos WHERE tarea = ?", (identificador,))
        _ejecutar("DELETE FROM tareas WHERE id = ?", (identificador,))


def observar_hilo(funcion) -> None:
    """Quien quiere ver los pasos de este hilo en vivo (el chat). None lo quita."""
    _local.observador = funcion


def registrar_paso(herramienta: str, argumentos, resultado) -> None:
    """Apunta una herramienta usada por la tarea que corre en este hilo."""
    observador = getattr(_local, "observador", None)
    if observador is not None:
        try:
            observador(herramienta, argumentos, resultado)
        except Exception as e:
            log.debug("El observador del paso fallo: %s", e)
    tarea = getattr(_local, "tarea", None)
    if not tarea:
        return
    try:
        args = json.dumps(argumentos, ensure_ascii=False, default=str)[:1000]
    except Exception:
        args = str(argumentos)[:1000]
    with _lock:
        n = _consulta("SELECT COUNT(*) FROM pasos WHERE tarea = ?", (tarea,))[0][0] + 1
        _ejecutar("INSERT INTO pasos (tarea, n, herramienta, argumentos, resultado, momento) "
                  "VALUES (?, ?, ?, ?, ?, ?)",
                  (tarea, n, herramienta, args, str(resultado)[:2000], _ahora()))


def con_tarea(identificador: str, funcion, *args, **kwargs):
    """Ejecuta funcion sabiendo a que tarea pertenecen sus pasos."""
    anterior = getattr(_local, "tarea", None)
    _local.tarea = identificador
    try:
        return funcion(*args, **kwargs)
    finally:
        _local.tarea = anterior


def pasos(identificador: str) -> list[dict]:
    return [dict(f) for f in _consulta("SELECT n, herramienta, argumentos, resultado FROM pasos "
                                       "WHERE tarea = ? ORDER BY n", (identificador,))]


def obtener(identificador: str) -> dict | None:
    filas = _consulta("SELECT * FROM tareas WHERE id = ?", (identificador,))
    return dict(filas[0]) if filas else None


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------
def al_arrancar() -> int:
    """Lo que corria cuando Jarvis se apago queda interrumpido, para contarlo."""
    with _lock:
        n = _ejecutar("UPDATE tareas SET estado = 'interrumpida', fin = ?, leida = 0 "
                      "WHERE estado IN ('corriendo', 'en_cola')", (_ahora(),))
        limite = (datetime.now() - timedelta(days=DIAS_DE_HISTORIA)).isoformat(timespec="seconds")
        viejas = [f[0] for f in _consulta("SELECT id FROM tareas WHERE inicio < ? AND leida = 1", (limite,))]
        sobran = [f[0] for f in _consulta("SELECT id FROM tareas ORDER BY inicio DESC LIMIT -1 OFFSET ?",
                                          (MAX_TAREAS,))]
        for identificador in set(viejas) | set(sobran):
            descartar(identificador)
    if n:
        log.warning("Al arrancar quedaron %d tareas interrumpidas.", n)
    return n


# ---------------------------------------------------------------------------
# Lo que se le cuenta al usuario
# ---------------------------------------------------------------------------
def hay_tareas_en_curso() -> bool:
    return bool(_consulta("SELECT 1 FROM tareas WHERE estado IN ('corriendo', 'en_cola') LIMIT 1"))


def _hace(momento: str | None) -> str:
    try:
        segundos = (datetime.now() - datetime.fromisoformat(momento)).total_seconds()
    except Exception:
        return "hace un rato"
    if segundos < 90:
        return "hace un momento"
    if segundos < 3600:
        return f"hace {int(segundos // 60)} minutos"
    if segundos < 86400:
        horas = int(segundos // 3600)
        return "hace una hora" if horas == 1 else f"hace {horas} horas"
    return "ayer" if segundos < 172800 else f"hace {int(segundos // 86400)} días"


def _marcar_leidas(ids: list[str]) -> None:
    for identificador in ids:
        _ejecutar("UPDATE tareas SET leida = 1 WHERE id = ?", (identificador,))


def marcar_leida(identificador: str) -> None:
    _marcar_leidas([identificador])


def ultimo_resultado() -> str | None:
    """El resultado mas reciente, o None si no hay ninguno."""
    filas = _consulta("SELECT * FROM tareas WHERE estado IN ('hecha', 'fallo') "
                      "ORDER BY fin DESC, rowid DESC LIMIT 1")
    if not filas:
        return None
    t = filas[0]
    _marcar_leidas([t["id"]])
    return f"{(t['resultado'] or '').rstrip('.')} (terminó {_hace(t['fin'])})."


def consultar_pendiente() -> str:
    """Respuesta a '¿cómo quedó lo último?'."""
    resultado = ultimo_resultado()
    corriendo = _consulta("SELECT descripcion FROM tareas WHERE estado IN ('corriendo', 'en_cola') "
                          "ORDER BY inicio DESC LIMIT 1")
    interrumpidas = _consulta("SELECT id, descripcion FROM tareas WHERE estado = 'interrumpida' "
                              "AND leida = 0 ORDER BY inicio DESC")
    partes = []
    if resultado:
        partes.append(resultado)
    if corriendo:
        partes.append(f"Todavía estoy trabajando en: {corriendo[0]['descripcion']}.")
    if interrumpidas:
        partes.append(_frase_interrumpidas([f["descripcion"] for f in interrumpidas]))
        _marcar_leidas([f["id"] for f in interrumpidas])
    return " ".join(partes) or "No tengo ninguna tarea pendiente ni resultados recientes."


def _frase_interrumpidas(descripciones: list[str]) -> str:
    if len(descripciones) == 1:
        return (f"Se me quedó a medias «{descripciones[0]}» porque me reiniciaron. "
                "Pídemelo otra vez si lo necesitas.")
    return (f"Se me quedaron a medias {len(descripciones)} tareas porque me reiniciaron, "
            f"la última «{descripciones[0]}».")


def novedades(limite: int = 2, max_caracteres: int = 220) -> str:
    """Lo que termino sin que nadie lo oyera, para decirlo al abrir la sesion.

    Devuelve "" si no hay nada. Lo que cuenta queda leido.
    """
    hechas = _consulta("SELECT id, descripcion, resultado, fin FROM tareas WHERE leida = 0 "
                       "AND estado IN ('hecha', 'fallo') ORDER BY fin DESC")
    cortadas = _consulta("SELECT id, descripcion FROM tareas WHERE leida = 0 "
                         "AND estado = 'interrumpida' ORDER BY inicio DESC")
    if not hechas and not cortadas:
        return ""
    partes = []
    for t in hechas[:limite]:
        texto = (t["resultado"] or "").split("\n")[0].strip()
        if len(texto) > max_caracteres:
            texto = texto[:max_caracteres].rsplit(" ", 1)[0] + "…"
        partes.append(f"terminé «{t['descripcion']}» {_hace(t['fin'])}: {texto.rstrip('.')}.")
    if len(hechas) > limite:
        partes.append(f"y {len(hechas) - limite} cosas más; pregúntame cómo quedó lo último.")
    if cortadas:
        partes.append(_frase_interrumpidas([f["descripcion"] for f in cortadas]))
    _marcar_leidas([f["id"] for f in hechas] + [f["id"] for f in cortadas])
    return "Por cierto, " + " ".join(partes)


def historial(limite: int = 10) -> list[dict]:
    return [dict(f) for f in _consulta("SELECT * FROM tareas ORDER BY inicio DESC LIMIT ?", (limite,))]


# ---------------------------------------------------------------------------
# Lanzar al fondo
# ---------------------------------------------------------------------------
def lanzar_en_segundo_plano(descripcion: str, funcion, origen: str = "voz",
                            avisar: bool = True) -> str:
    """Ejecuta algo lento sin hacer esperar a Alexa.

    Si funcion devuelve None (una rutina que miro y no tenia nada que decir),
    la tarea se descarta sin aviso.
    """
    identificador = uuid.uuid4().hex[:8]
    registrar_inicio(identificador, descripcion, origen=origen)

    def _trabajar():
        try:
            resultado = con_tarea(identificador, funcion)
        except Exception as e:
            log.exception("Falló la tarea de fondo %s", descripcion)
            resultado = f"La tarea falló: {e}"
        if resultado is None:
            descartar(identificador)
            return
        registrar_resultado(identificador, descripcion, resultado, avisar=avisar)

    threading.Thread(target=_trabajar, daemon=True, name=f"fondo-{identificador}").start()
    return identificador
