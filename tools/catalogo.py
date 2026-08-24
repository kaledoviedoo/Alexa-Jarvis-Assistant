"""
Catalogo de las aplicaciones que hay instaladas de verdad.

El problema de las listas a mano
--------------------------------
Habia una tabla con veinte programas y su comando de arranque. Funciona hasta
que pides uno que no esta: "abre epic games" fallaba porque nadie habia
escrito su ruta, aunque el lanzador estuviera instalado. Y esa tabla no puede
crecer al ritmo de lo que instalas.

De donde sale la informacion
----------------------------
De donde la saca Windows para su propio buscador, tres fuentes que se
complementan:

  1. MENU INICIO. Cada programa deja ahi un acceso directo al instalarse. Es
     la fuente mas fiable y la que da el nombre tal y como lo ves.
  2. REGISTRO, claves de desinstalacion. Cubre lo que no puso acceso directo,
     y aporta el nombre comercial completo.
  3. APLICACIONES DE LA STORE. No tienen .exe accesible; se lanzan por un
     identificador propio. Aqui viven WhatsApp, la calculadora o Teams nuevo.

El catalogo se guarda en disco. Rastrear las tres fuentes tarda unos segundos,
demasiado para hacerlo en cada orden con Alexa esperando ocho.
"""

import json
import logging
import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path

from config import CARPETA_DATOS

log = logging.getLogger("jarvis.catalogo")

ARCHIVO = CARPETA_DATOS / "aplicaciones.json"

# Cada cuanto se rehace solo. Una semana: instalar programas no es algo de
# todos los dias, y siempre se puede forzar con "actualiza las aplicaciones".
DIAS_VIGENCIA = 7

# Ruido del menu Inicio: desinstaladores, manuales, enlaces a webs. Abrir un
# "Uninstall Spotify" por confundirlo con "Spotify" seria bastante malo.
_BASURA = re.compile(
    r"\b(uninstall|desinstalar|remove|readme|l[ée]ame|manual|documentation|"
    r"documentaci[oó]n|help|ayuda|website|sitio\s+web|changelog|licen[cs]e|"
    r"licencia|report\s+a\s+bug|feedback|repair|reparar|modify|troubleshoot)\b",
    re.IGNORECASE,
)


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "")
                   if unicodedata.category(c) != "Mn")


def normalizar(nombre: str) -> str:
    """Nombre comparable: sin acentos, sin version, sin adornos."""
    limpio = _sin_acentos((nombre or "").lower())
    # Fuera versiones y arquitecturas: "Spotify (x64)" y "Spotify 1.2" son
    # el mismo programa que "Spotify".
    limpio = re.sub(r"\b(x86|x64|32\s*bits?|64\s*bits?|v?\d+[\d.]*)\b", " ", limpio)
    limpio = re.sub(r"[^\w\s]", " ", limpio)
    return re.sub(r"\s+", " ", limpio).strip()


# -------------------------------------------------------------------------
# FUENTE 1: MENU INICIO
# -------------------------------------------------------------------------
def _destino_del_acceso(ruta: Path) -> str:
    """
    Lee a donde apunta un .lnk sin librerias externas.

    El formato guarda la ruta en texto plano dentro del binario, asi que basta
    con pescar algo que parezca "X:\\...\\algo.exe". No es elegante, pero
    evita depender de pywin32 para algo que tiene que funcionar siempre.
    """
    try:
        crudo = ruta.read_bytes()
    except OSError:
        return ""

    for codificacion in ("utf-16-le", "latin-1"):
        try:
            texto = crudo.decode(codificacion, errors="ignore")
        except Exception:
            continue
        for trozo in re.findall(r"[A-Za-z]:\\[^\x00\n\r]{3,240}?\.exe", texto):
            if Path(trozo).is_file():
                return trozo
    return ""


def _del_menu_inicio() -> dict:
    if os.name != "nt":
        return {}

    menus = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]

    encontradas = {}
    for menu in menus:
        if not menu.is_dir():
            continue
        try:
            accesos = list(menu.rglob("*.lnk"))
        except OSError:
            continue

        for acceso in accesos:
            nombre = acceso.stem
            if _BASURA.search(nombre):
                continue

            destino = _destino_del_acceso(acceso)
            if not destino:
                continue

            clave = normalizar(nombre)
            if clave and clave not in encontradas:
                encontradas[clave] = {
                    "nombre": nombre,
                    "tipo": "exe",
                    "comando": destino,
                    "origen": "menu inicio",
                }

    log.info("Menú Inicio: %d aplicaciones", len(encontradas))
    return encontradas


# -------------------------------------------------------------------------
# FUENTE 2: REGISTRO
# -------------------------------------------------------------------------
def _del_registro() -> dict:
    if os.name != "nt":
        return {}
    try:
        import winreg
    except ImportError:
        return {}

    raices = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    encontradas = {}
    for raiz, camino in raices:
        try:
            with winreg.OpenKey(raiz, camino) as clave_padre:
                total = winreg.QueryInfoKey(clave_padre)[0]
                for i in range(total):
                    try:
                        nombre_sub = winreg.EnumKey(clave_padre, i)
                        with winreg.OpenKey(clave_padre, nombre_sub) as sub:
                            def _leer(campo):
                                try:
                                    return winreg.QueryValueEx(sub, campo)[0]
                                except OSError:
                                    return ""

                            nombre = (_leer("DisplayName") or "").strip()
                            if not nombre or _BASURA.search(nombre):
                                continue

                            # DisplayIcon suele apuntar al ejecutable principal.
                            icono = (_leer("DisplayIcon") or "").split(",")[0].strip('"')
                            ejecutable = icono if icono.lower().endswith(".exe") else ""

                            if not ejecutable:
                                carpeta = (_leer("InstallLocation") or "").strip('"')
                                if carpeta and Path(carpeta).is_dir():
                                    ejecutable = _mejor_exe(Path(carpeta), nombre)

                            if not ejecutable or not Path(ejecutable).is_file():
                                continue

                            clave = normalizar(nombre)
                            if clave and clave not in encontradas:
                                encontradas[clave] = {
                                    "nombre": nombre,
                                    "tipo": "exe",
                                    "comando": ejecutable,
                                    "origen": "registro",
                                }
                    except OSError:
                        continue
        except OSError:
            continue

    log.info("Registro: %d aplicaciones", len(encontradas))
    return encontradas


def _mejor_exe(carpeta: Path, nombre: str) -> str:
    """
    El ejecutable principal de una carpeta de instalacion.

    Una carpeta tiene muchos .exe: actualizadores, ayudantes, informes de
    fallos. El bueno suele llamarse como el programa, asi que ese gana.
    """
    try:
        candidatos = list(carpeta.glob("*.exe")) + list(carpeta.glob("*/*.exe"))
    except OSError:
        return ""

    if not candidatos:
        return ""

    objetivo = normalizar(nombre)
    for exe in candidatos:
        if normalizar(exe.stem) == objetivo:
            return str(exe)
    for exe in candidatos:
        if objetivo.split()[0] in normalizar(exe.stem) if objetivo else False:
            return str(exe)

    # Ninguno se parece: descartamos los sospechosos y cogemos el mas grande,
    # que casi siempre es el programa de verdad.
    utiles = [e for e in candidatos
              if not re.search(r"(unins|update|crash|report|helper|setup)", e.stem, re.I)]
    if not utiles:
        return ""
    try:
        return str(max(utiles, key=lambda e: e.stat().st_size))
    except OSError:
        return str(utiles[0])


# -------------------------------------------------------------------------
# FUENTE 3: APLICACIONES DE LA STORE
# -------------------------------------------------------------------------
def _de_la_store() -> dict:
    """
    Aplicaciones empaquetadas (UWP). No tienen .exe al que llamar: se lanzan
    con shell:appsFolder y su identificador de familia.
    """
    if os.name != "nt":
        return {}

    orden = (
        "Get-StartApps | Where-Object { $_.AppID -like '*!*' } | "
        "ConvertTo-Json -Compress"
    )
    try:
        resultado = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", orden],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        datos = json.loads(resultado.stdout or "[]")
    except Exception as e:
        log.debug("No pude listar las apps de la Store: %s", e)
        return {}

    if isinstance(datos, dict):
        datos = [datos]

    encontradas = {}
    for entrada in datos:
        nombre = (entrada.get("Name") or "").strip()
        identificador = (entrada.get("AppID") or "").strip()
        if not nombre or not identificador or _BASURA.search(nombre):
            continue

        clave = normalizar(nombre)
        if clave and clave not in encontradas:
            encontradas[clave] = {
                "nombre": nombre,
                "tipo": "store",
                "comando": f"shell:appsFolder\\{identificador}",
                "origen": "store",
            }

    log.info("Store: %d aplicaciones", len(encontradas))
    return encontradas


# -------------------------------------------------------------------------
# CATALOGO
# -------------------------------------------------------------------------
_memoria: dict | None = None


def construir(guardar: bool = True) -> dict:
    """Rastrea las tres fuentes. Tarda unos segundos."""
    inicio = time.perf_counter()

    catalogo = {}
    # El orden importa: lo que ya esta no se pisa. El menu Inicio va primero
    # porque da los nombres tal y como los ves escritos.
    for fuente in (_del_menu_inicio, _del_registro, _de_la_store):
        try:
            for clave, datos in fuente().items():
                catalogo.setdefault(clave, datos)
        except Exception as e:
            log.warning("Falló una fuente del catálogo: %s", e)

    tardo = time.perf_counter() - inicio
    log.info("Catálogo de aplicaciones: %d entradas en %.1f s", len(catalogo), tardo)

    if guardar and catalogo:
        try:
            ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
            ARCHIVO.write_text(
                json.dumps({"generado": time.time(), "apps": catalogo},
                           ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("No pude guardar el catálogo: %s", e)

    return catalogo


def cargar(forzar: bool = False) -> dict:
    """El catalogo, del disco si sigue fresco."""
    global _memoria

    if _memoria is not None and not forzar:
        return _memoria

    if not forzar and ARCHIVO.is_file():
        try:
            datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
            edad_dias = (time.time() - datos.get("generado", 0)) / 86400
            if edad_dias < DIAS_VIGENCIA and datos.get("apps"):
                _memoria = datos["apps"]
                log.info("Catálogo cargado: %d aplicaciones (%.1f días)",
                         len(_memoria), edad_dias)
                return _memoria
        except Exception as e:
            log.debug("El catálogo guardado no sirve: %s", e)

    _memoria = construir()
    return _memoria


def refrescar_en_segundo_plano() -> None:
    """Rehace el catalogo sin bloquear nada."""
    import threading
    threading.Thread(target=lambda: cargar(forzar=True), daemon=True,
                     name="catalogo").start()


# -------------------------------------------------------------------------
# BUSCAR EN EL CATALOGO
# -------------------------------------------------------------------------
# Aqui esta el razonamiento. Alexa transcribe "epic games" como "epi games",
# "epicgeims" o "e pick games", y tu dices "el epic" cuando el programa se
# llama "Epic Games Launcher". Exigir el nombre exacto seria inutil.
#
# En vez de acertar o fallar, se PUNTUA cada candidato y se devuelven los
# mejores con su nota. Quien llama decide: nota alta, se abre; nota media, se
# pregunta; nota baja, se sugiere.
import difflib  # noqa: E402


def _puntuar(consulta: str, clave: str, nombre: str) -> float:
    """De 0 a 1: cuanto se parece lo que dijiste a esta aplicacion."""
    if consulta == clave:
        return 1.0

    palabras_consulta = set(consulta.split())
    palabras_clave = set(clave.split())

    # Todas las palabras que dijiste estan en el nombre: "epic games" dentro
    # de "epic games launcher". Es practicamente seguro.
    if palabras_consulta and palabras_consulta <= palabras_clave:
        return 0.95

    # El nombre empieza por lo que dijiste.
    if clave.startswith(consulta):
        return 0.9

    # Lo que dijiste aparece entero dentro del nombre.
    if consulta in clave:
        return 0.85

    # Comparten palabras: cuenta cuantas.
    if palabras_consulta and palabras_clave:
        comunes = palabras_consulta & palabras_clave
        if comunes:
            proporcion = len(comunes) / len(palabras_consulta)
            if proporcion >= 0.5:
                return 0.6 + proporcion * 0.2

    # Y por ultimo el parecido de letras, que cubre la transcripcion torcida.
    return difflib.SequenceMatcher(None, consulta, clave).ratio() * 0.8


def buscar(nombre_hablado: str, cuantos: int = 5) -> list[dict]:
    """
    Las aplicaciones que mas se parecen a lo que se dijo, con su nota.

    Ordenadas de mejor a peor. Nunca devuelve vacio por capricho: si hay
    catalogo, siempre hay algo que proponer.
    """
    consulta = normalizar(nombre_hablado)
    if not consulta:
        return []

    catalogo = cargar()
    if not catalogo:
        return []

    puntuadas = []
    for clave, datos in catalogo.items():
        nota = _puntuar(consulta, clave, datos["nombre"])
        if nota >= 0.35:
            puntuadas.append({**datos, "nota": nota, "clave": clave})

    puntuadas.sort(key=lambda d: d["nota"], reverse=True)
    return puntuadas[:cuantos]


def lanzar(entrada: dict) -> bool:
    """Arranca una entrada del catalogo. True si el intento salio bien."""
    comando = entrada.get("comando", "")
    if not comando:
        return False

    try:
        if entrada.get("tipo") == "store":
            # Las de la Store no son un .exe: se abren por el explorador.
            subprocess.Popen(f'explorer.exe "{comando}"', shell=True)
        else:
            subprocess.Popen([comando],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        log.info("Lanzado desde el catálogo: %s (%s)", entrada["nombre"], comando)
        return True
    except Exception as e:
        log.warning("No pude lanzar %s: %s", entrada.get("nombre"), e)
        return False


def resumen() -> str:
    """Cuantas aplicaciones conoce y de donde salieron."""
    catalogo = cargar()
    if not catalogo:
        return "No tengo catálogo de aplicaciones todavía."

    por_origen: dict = {}
    for datos in catalogo.values():
        por_origen[datos["origen"]] = por_origen.get(datos["origen"], 0) + 1

    detalle = ", ".join(f"{n} del {o}" for o, n in sorted(por_origen.items()))
    return f"Conozco {len(catalogo)} aplicaciones instaladas: {detalle}."
