"""Cliente MCP: Jarvis usa herramientas de otros programas sin dejarlos encendidos.

MCP (Model Context Protocol) es un estandar abierto: un programa aparte (un
"servidor MCP") ofrece herramientas y Jarvis las llama hablandole por su
entrada y salida, con mensajes JSON-RPC de una linea.

Para no gastar recursos:
- Un servidor arranca la primera vez que una orden lo necesita, no al
  encender Jarvis.
- Se apaga solo tras MINUTOS_SIN_USO sin llamadas, y "libera la memoria" lo
  apaga ya.
- Si se cae, se vuelve a arrancar una vez en la siguiente llamada.

Los servidores se declaran en ~/.jarvis/mcp.json (se crea con los de serie la
primera vez). Cada uno con su comando, su entorno y, si hace falta, la
variable del .env de la que sale su token: el token solo lo ve ese proceso.

La forma de hablar por stdio (un hilo que lee la salida, otro que vacia los
errores y un candado por peticion) sigue la del cliente de OpenJarvis
(https://github.com/open-jarvis/OpenJarvis, licencia Apache-2.0:
src/openjarvis/mcp/client.py y transport.py). Cambios: sin dependencias,
arranque bajo demanda, apagado por inactividad, reinicio tras una caida y
ventana oculta en Windows. Ver NOTICE.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path

from config import CARPETA_DATOS

log = logging.getLogger("jarvis.mcp")

VERSION_PROTOCOLO = "2025-06-18"
MINUTOS_SIN_USO = 10
SEGUNDOS_ARRANQUE = 30
SEGUNDOS_LLAMADA = 90
MAX_CARACTERES_RESULTADO = 60_000

CARPETA_MCP = CARPETA_DATOS / "mcp"

# Los de serie. {python} es el Python que corre Jarvis; {mcp} la carpeta
# ~/.jarvis/mcp, donde instalar_mcp.ps1 deja el ejecutable de GitHub.
POR_DEFECTO = {
    "web": {
        "comando": ["{script:mcp-server-fetch}"],
        "alternativa": ["{python}", "-m", "mcp_server_fetch"],
        "instalar": "py -m pip install mcp-server-fetch",
    },
    "youtube": {
        "comando": ["{script:mcp-youtube-transcript}"],
        "alternativa": ["{python}", "-m", "mcp_youtube_transcript"],
        "instalar": "py -m pip install mcp-youtube-transcript",
    },
    "github": {
        # Solo lectura: aunque el modelo lo intentara, este servidor no
        # puede crear, cerrar ni borrar nada en GitHub.
        "comando": ["{mcp}/github-mcp-server.exe", "stdio", "--read-only",
                    "--toolsets", "context,repos,issues,pull_requests,actions"],
        "token": {"variable": "JARVIS_GITHUB_TOKEN", "como": "GITHUB_PERSONAL_ACCESS_TOKEN"},
        "instalar": ".\\scripts\\instalar_mcp.ps1",
    },
}


class ErrorMCP(Exception):
    """Algo fallo hablando con un servidor. El mensaje ya es para el usuario."""


def _ruta_config() -> Path:
    return CARPETA_DATOS / "mcp.json"


def config() -> dict:
    """mcp.json; si no existe, se crea con los de serie."""
    ruta = _ruta_config()
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        servidores = datos.get("servidores", {})
        return {**POR_DEFECTO, **servidores} if isinstance(servidores, dict) else dict(POR_DEFECTO)
    except FileNotFoundError:
        try:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(json.dumps({"servidores": POR_DEFECTO}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log.debug("No pude crear mcp.json: %s", e)
        return dict(POR_DEFECTO)
    except Exception as e:
        log.warning("mcp.json ilegible (%s): uso los de serie.", e)
        return dict(POR_DEFECTO)


def _script(nombre: str) -> str | None:
    """El ejecutable que pip deja en Scripts/ (o bin/), donde sea que este."""
    encontrado = shutil.which(nombre)
    if encontrado:
        return encontrado
    sufijos = (".exe", "") if os.name == "nt" else ("",)
    esquemas = [None, f"{os.name}_user"]
    for esquema in esquemas:
        try:
            carpeta = Path(sysconfig.get_path("scripts", esquema) if esquema else sysconfig.get_path("scripts"))
        except KeyError:
            continue
        for sufijo in sufijos:
            candidato = carpeta / f"{nombre}{sufijo}"
            if candidato.exists():
                return str(candidato)
    return None


def _expandir(parte: str) -> str | None:
    if parte == "{python}":
        return sys.executable
    if parte.startswith("{script:") and parte.endswith("}"):
        return _script(parte[8:-1])
    return parte.replace("{mcp}", str(CARPETA_MCP)).replace("{datos}", str(CARPETA_DATOS))


def comando_de(nombre: str, conf: dict) -> list[str] | None:
    """El comando listo para lanzar, o None si no esta instalado."""
    for clave in ("comando", "alternativa"):
        crudo = conf.get(clave)
        if not crudo:
            continue
        partes = [_expandir(str(p)) for p in crudo]
        if None in partes:
            continue
        ejecutable = partes[0]
        if os.path.isabs(ejecutable) and not Path(ejecutable).exists():
            continue
        if clave == "alternativa" and partes[1:2] == ["-m"]:
            import importlib.util
            if importlib.util.find_spec(partes[2]) is None:
                continue
        if not os.path.isabs(ejecutable) and shutil.which(ejecutable) is None:
            continue
        return partes
    return None


def _entorno(conf: dict) -> tuple[dict, str]:
    """Entorno del proceso y, si falta el token, que variable falta."""
    entorno = dict(os.environ)
    # Ningun servidor ve los secretos de Jarvis salvo el suyo.
    for clave in list(entorno):
        if clave.startswith("JARVIS_") or clave in ("ALEXA_SKILL_ID",):
            entorno.pop(clave)
    entorno.update({str(k): str(v) for k, v in (conf.get("entorno") or {}).items()})
    token = conf.get("token")
    if token:
        valor = os.environ.get(token["variable"], "").strip()
        if not valor:
            return entorno, token["variable"]
        entorno[token["como"]] = valor
    entorno.setdefault("PYTHONIOENCODING", "utf-8")
    return entorno, ""


class ServidorMCP:
    """Un proceso MCP por stdio, con su handshake y sus herramientas."""

    def __init__(self, nombre: str, comando: list[str], entorno: dict | None = None):
        self.nombre = nombre
        self.comando = comando
        self.entorno = entorno
        self.proceso: subprocess.Popen | None = None
        self.herramientas: dict[str, dict] = {}
        self.ultimo_uso = 0.0
        self._salida: queue.Queue = queue.Queue()
        self._candado = threading.Lock()
        self._id = 0

    # -- ciclo de vida ----------------------------------------------------
    def vivo(self) -> bool:
        return self.proceso is not None and self.proceso.poll() is None

    def arrancar(self) -> None:
        opciones = {}
        if os.name == "nt":
            opciones["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log.info("MCP: arranco %s", self.nombre)
        try:
            self.proceso = subprocess.Popen(
                self.comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self.entorno, cwd=str(CARPETA_DATOS) if CARPETA_DATOS.exists() else None, **opciones)
        except OSError as e:
            raise ErrorMCP(f"No pude arrancar el servidor {self.nombre}: {e}")
        self._salida = queue.Queue()
        threading.Thread(target=self._leer, args=(self.proceso, self._salida),
                         name=f"mcp-{self.nombre}", daemon=True).start()
        threading.Thread(target=self._vaciar_errores, args=(self.proceso,),
                         name=f"mcp-{self.nombre}-err", daemon=True).start()
        respuesta = self._peticion("initialize", {
            "protocolVersion": VERSION_PROTOCOLO, "capabilities": {},
            "clientInfo": {"name": "jarvis", "version": "7.3"}}, SEGUNDOS_ARRANQUE)
        log.info("MCP: %s listo (%s)", self.nombre, (respuesta.get("serverInfo") or {}).get("name", "?"))
        self._enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
        lista = self._peticion("tools/list", {}, SEGUNDOS_ARRANQUE)
        self.herramientas = {t["name"]: t for t in lista.get("tools", []) if isinstance(t, dict) and t.get("name")}
        self.ultimo_uso = time.monotonic()

    def cerrar(self) -> None:
        proceso, self.proceso = self.proceso, None
        if proceso is None:
            return
        log.info("MCP: apago %s", self.nombre)
        try:
            proceso.stdin.close()
            proceso.wait(timeout=3)
        except Exception:
            try:
                proceso.kill()
                proceso.wait(timeout=3)
            except Exception:
                pass

    # -- E/S ----------------------------------------------------------------
    @staticmethod
    def _leer(proceso, salida: queue.Queue) -> None:
        for linea in iter(proceso.stdout.readline, b""):
            salida.put(linea)
        salida.put(None)

    def _vaciar_errores(self, proceso) -> None:
        for linea in iter(proceso.stderr.readline, b""):
            texto = linea.decode("utf-8", errors="replace").rstrip()
            if texto:
                log.debug("MCP %s: %s", self.nombre, texto[:300])

    def _enviar(self, mensaje: dict) -> None:
        if not self.vivo():
            raise ErrorMCP(f"El servidor {self.nombre} no está corriendo.")
        try:
            self.proceso.stdin.write((json.dumps(mensaje, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proceso.stdin.flush()
        except (OSError, ValueError) as e:
            raise ErrorMCP(f"Se cortó la conexión con {self.nombre}: {e}")

    def _peticion(self, metodo: str, parametros: dict, espera: float) -> dict:
        with self._candado:
            self._id += 1
            ident = self._id
            self._enviar({"jsonrpc": "2.0", "id": ident, "method": metodo, "params": parametros})
            limite = time.monotonic() + espera
            while True:
                restante = limite - time.monotonic()
                if restante <= 0:
                    raise ErrorMCP(f"{self.nombre} tardó demasiado en contestar.")
                try:
                    linea = self._salida.get(timeout=restante)
                except queue.Empty:
                    continue
                if linea is None:
                    raise ErrorMCP(f"El servidor {self.nombre} se cerró.")
                try:
                    mensaje = json.loads(linea.decode("utf-8", errors="replace"))
                except ValueError:
                    continue          # algo que no es JSON-RPC en la salida
                if not isinstance(mensaje, dict) or mensaje.get("id") != ident:
                    continue          # notificaciones y avisos de progreso
                if "error" in mensaje:
                    error = mensaje["error"] or {}
                    raise ErrorMCP(f"{self.nombre} dio un error: {error.get('message', error)}")
                return mensaje.get("result") or {}

    def llamar(self, herramienta: str, argumentos: dict, espera: float = SEGUNDOS_LLAMADA) -> str:
        """Llama a una herramienta y devuelve su texto."""
        self.ultimo_uso = time.monotonic()
        resultado = self._peticion("tools/call", {"name": herramienta, "arguments": argumentos}, espera)
        self.ultimo_uso = time.monotonic()
        trozos = []
        for bloque in resultado.get("content") or []:
            if isinstance(bloque, dict) and bloque.get("type") == "text":
                trozos.append(str(bloque.get("text", "")))
        if not trozos and resultado.get("structuredContent") is not None:
            trozos.append(json.dumps(resultado["structuredContent"], ensure_ascii=False))
        texto = "\n".join(trozos).strip()[:MAX_CARACTERES_RESULTADO]
        if resultado.get("isError"):
            raise ErrorMCP(f"{self.nombre}: {texto[:300] or 'la herramienta falló'}")
        return texto


# ---- Gestor: uno de cada, bajo demanda ---------------------------------------

_servidores: dict[str, ServidorMCP] = {}
_candado_gestor = threading.Lock()
_vigilante: threading.Thread | None = None


def _vigilar() -> None:
    """Apaga lo que lleva un rato sin usarse. Termina cuando no queda nada."""
    while True:
        time.sleep(30)
        with _candado_gestor:
            for nombre, s in list(_servidores.items()):
                if not s.vivo():
                    _servidores.pop(nombre, None)
                elif time.monotonic() - s.ultimo_uso > MINUTOS_SIN_USO * 60:
                    s.cerrar()
                    _servidores.pop(nombre, None)
            if not _servidores:
                global _vigilante
                _vigilante = None
                return


def apagar_inactivos(segundos: float | None = None) -> int:
    """Apaga los que llevan mas de `segundos` sin usarse (para pruebas y ahorro)."""
    limite = MINUTOS_SIN_USO * 60 if segundos is None else segundos
    apagados = 0
    with _candado_gestor:
        for nombre, s in list(_servidores.items()):
            if not s.vivo() or time.monotonic() - s.ultimo_uso > limite:
                s.cerrar()
                _servidores.pop(nombre, None)
                apagados += 1
    return apagados


def apagar_todos() -> int:
    with _candado_gestor:
        n = len(_servidores)
        for s in _servidores.values():
            s.cerrar()
        _servidores.clear()
    return n


atexit.register(apagar_todos)


def encendidos() -> list[str]:
    with _candado_gestor:
        return [n for n, s in _servidores.items() if s.vivo()]


def servidor(nombre: str) -> ServidorMCP:
    """El servidor ya arrancado, o lo arranca ahora."""
    global _vigilante
    conf = config().get(nombre)
    if conf is None:
        raise ErrorMCP(f"No tengo configurado el servidor {nombre}.")
    with _candado_gestor:
        actual = _servidores.get(nombre)
        if actual is not None and actual.vivo():
            return actual
        comando = comando_de(nombre, conf)
        if comando is None:
            raise ErrorMCP(f"El servidor {nombre} no está instalado. Instálalo con: {conf.get('instalar', '?')}")
        entorno, falta = _entorno(conf)
        if falta:
            raise ErrorMCP(f"Para {nombre} falta {falta} en el archivo .env.")
        nuevo = ServidorMCP(nombre, comando, entorno)
        try:
            nuevo.arrancar()
        except ErrorMCP:
            nuevo.cerrar()
            raise
        _servidores[nombre] = nuevo
        if _vigilante is None:
            _vigilante = threading.Thread(target=_vigilar, name="mcp-vigilante", daemon=True)
            _vigilante.start()
        return nuevo


def llamar(nombre: str, herramientas: tuple[str, ...] | str, argumentos: dict,
           espera: float = SEGUNDOS_LLAMADA) -> str:
    """Llama a la primera herramienta de la lista que el servidor tenga.

    Los argumentos que la herramienta no declara se quitan: asi una version
    nueva del servidor que renombra o quita un parametro no rompe la llamada.
    Si el servidor se habia caido, se reintenta una vez arrancandolo de nuevo.
    """
    candidatas = (herramientas,) if isinstance(herramientas, str) else tuple(herramientas)
    for intento in (1, 2):
        s = servidor(nombre)
        elegida = next((c for c in candidatas if c in s.herramientas), None)
        if elegida is None:
            raise ErrorMCP(f"El servidor {nombre} no tiene {' ni '.join(candidatas)}.")
        esquema = (s.herramientas[elegida].get("inputSchema") or {})
        propias = esquema.get("properties")
        args = {k: v for k, v in argumentos.items()
                if v not in (None, "") and (not isinstance(propias, dict) or k in propias)}
        # "open" donde el servidor espera "OPEN" (o al reves): se ajusta.
        for clave, valor in list(args.items()):
            opciones = ((propias or {}).get(clave) or {}).get("enum") if isinstance(propias, dict) else None
            if isinstance(valor, str) and opciones and valor not in opciones:
                parecida = next((o for o in opciones if str(o).lower() == valor.lower()), None)
                if parecida is not None:
                    args[clave] = parecida
        faltan = [r for r in esquema.get("required", []) if r not in args]
        if faltan:
            raise ErrorMCP(f"A {elegida} le falta: {', '.join(faltan)}.")
        try:
            return s.llamar(elegida, args, espera)
        except ErrorMCP:
            if intento == 1 and not s.vivo():
                log.warning("MCP: %s se cayó; lo arranco otra vez.", nombre)
                with _candado_gestor:
                    _servidores.pop(nombre, None)
                continue
            raise
    raise ErrorMCP(f"No pude hablar con {nombre}.")


def estado() -> str:
    """Una linea por servidor: instalado, encendido o que le falta."""
    lineas = []
    for nombre, conf in config().items():
        if nombre in encendidos():
            lineas.append(f"{nombre}: encendido")
        elif comando_de(nombre, conf) is None:
            lineas.append(f"{nombre}: sin instalar ({conf.get('instalar', '?')})")
        elif _entorno(conf)[1]:
            lineas.append(f"{nombre}: falta {_entorno(conf)[1]} en el .env")
        else:
            lineas.append(f"{nombre}: listo, apagado hasta que lo uses")
    return "; ".join(lineas) + "."
