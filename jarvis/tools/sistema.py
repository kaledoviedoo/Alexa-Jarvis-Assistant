"""
Herramientas de sistema: métricas, procesos, aplicaciones y energía.

Incluye lectura de la GPU vía nvidia-smi, que es lo que permite a Jarvis saber
cuánta VRAM queda libre en la RTX 3050 antes de cargar un modelo grande.
"""

import logging
import os
import shutil
import subprocess
import time

import psutil

from config import PROCESOS_PROTEGIDOS, localizar_ejecutable

log = logging.getLogger("jarvis.sistema")

# En Windows, evita que aparezca una ventana negra en cada subproceso.
_SIN_VENTANA = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _ejecutar(comando: list[str], timeout: int = 5) -> tuple[bool, str]:
    """Ejecuta un comando sin abrir ventana y devuelve (éxito, salida)."""
    try:
        resultado = subprocess.run(
            comando,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_SIN_VENTANA,
        )
        salida = (resultado.stdout or "") + (resultado.stderr or "")
        return resultado.returncode == 0, salida.strip()
    except FileNotFoundError:
        return False, f"No se encontró el programa: {comando[0]}"
    except subprocess.TimeoutExpired:
        return False, "El comando tardó demasiado."
    except Exception as e:
        return False, str(e)


# -------------------------------------------------------------------------
# MÉTRICAS
# -------------------------------------------------------------------------
def uso_cpu() -> str:
    porcentaje = psutil.cpu_percent(interval=0.4)
    nucleos = psutil.cpu_count(logical=True)
    try:
        frecuencia = psutil.cpu_freq()
        extra = f" a {frecuencia.current / 1000:.1f} gigahercios" if frecuencia else ""
    except Exception:
        extra = ""
    return f"El procesador está al {porcentaje:.0f} por ciento con {nucleos} hilos{extra}."


def uso_ram() -> str:
    memoria = psutil.virtual_memory()
    usados = memoria.used / (1024**3)
    total = memoria.total / (1024**3)
    return (
        f"La memoria está al {memoria.percent:.0f} por ciento, "
        f"{usados:.1f} de {total:.1f} gigas en uso."
    )


def uso_disco() -> str:
    try:
        disco = psutil.disk_usage(os.path.abspath(os.sep))
    except Exception as e:
        return f"No pude leer el disco: {e}"
    libres = disco.free / (1024**3)
    return f"El disco está al {disco.percent:.0f} por ciento, quedan {libres:.0f} gigas libres."


def info_gpu() -> dict:
    """
    Lee el estado de la GPU con nvidia-smi.

    Devuelve un dict con: disponible, nombre, vram_usada_mb, vram_total_mb,
    vram_libre_mb, uso_pct, temperatura.
    """
    vacio = {"disponible": False}

    if not shutil.which("nvidia-smi"):
        return vacio

    ok, salida = _ejecutar([
        "nvidia-smi",
        "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
        "--format=csv,noheader,nounits",
    ])

    if not ok or not salida:
        return vacio

    try:
        primera = salida.splitlines()[0]
        partes = [p.strip() for p in primera.split(",")]
        usada, total = float(partes[1]), float(partes[2])
        return {
            "disponible": True,
            "nombre": partes[0],
            "vram_usada_mb": usada,
            "vram_total_mb": total,
            "vram_libre_mb": total - usada,
            "uso_pct": float(partes[3]),
            "temperatura": float(partes[4]),
        }
    except Exception as e:
        log.warning("No pude interpretar la salida de nvidia-smi: %s", e)
        return vacio


def uso_gpu() -> str:
    datos = info_gpu()
    if not datos.get("disponible"):
        return "No detecté una GPU NVIDIA con nvidia-smi disponible."

    libre_gb = datos["vram_libre_mb"] / 1024
    total_gb = datos["vram_total_mb"] / 1024
    return (
        f"La gráfica está al {datos['uso_pct']:.0f} por ciento "
        f"a {datos['temperatura']:.0f} grados. "
        f"Quedan {libre_gb:.1f} de {total_gb:.1f} gigas de memoria de video libres."
    )


def estado_general() -> str:
    """Resumen corto de todo el equipo, pensado para decirse en voz alta."""
    cpu = psutil.cpu_percent(interval=0.3)
    ram = psutil.virtual_memory().percent
    partes = [f"Procesador al {cpu:.0f} por ciento", f"memoria al {ram:.0f} por ciento"]

    datos = info_gpu()
    if datos.get("disponible"):
        partes.append(f"gráfica al {datos['uso_pct']:.0f} por ciento a {datos['temperatura']:.0f} grados")

    return ", ".join(partes) + "."


def procesos_pesados(cantidad: int = 3) -> str:
    """Dice qué programas están consumiendo más memoria."""
    procesos = []
    for proceso in psutil.process_iter(["name", "memory_info"]):
        try:
            info = proceso.info
            if info["name"] and info.get("memory_info"):
                procesos.append((info["name"], info["memory_info"].rss / (1024**2)))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Agrupamos por nombre: Chrome tiene 30 procesos y hay que sumarlos.
    agregados: dict[str, float] = {}
    for nombre, mb in procesos:
        agregados[nombre] = agregados.get(nombre, 0) + mb

    top = sorted(agregados.items(), key=lambda x: x[1], reverse=True)[:cantidad]
    if not top:
        return "No pude leer los procesos."

    descripcion = ", ".join(f"{n.replace('.exe', '')} con {mb / 1024:.1f} gigas" for n, mb in top)
    return f"Lo que más memoria consume: {descripcion}."


def bateria() -> str:
    try:
        bat = psutil.sensors_battery()
    except Exception:
        bat = None
    if bat is None:
        return "Este equipo no tiene batería, está conectado a corriente."
    estado = "cargando" if bat.power_plugged else "descargándose"
    return f"La batería está al {bat.percent:.0f} por ciento y {estado}."



# -------------------------------------------------------------------------
# NOMBRES QUE EL RECONOCIMIENTO DE VOZ DESTROZA
# -------------------------------------------------------------------------
# Alexa transcribe "Comet" como "cometa", "comer", "covid", "comed"... Sin
# esta tabla, "cierra comet" busca un proceso llamado "cometa.exe" y contesta
# alegremente que no estaba abierto, que es peor que fallar: miente.
ALIAS_VOZ = {
    "comet": ["cometa", "comer", "covid", "comed", "comett", "komet", "cornet",
              "comete", "comer.exe", "cometa.exe",
              # De los registros reales del 22 de agosto: "abre 1 pestaña en
              # comic". Alexa oye la palabra inglesa y la escribe en espanol.
              "comic", "cómic", "comix"],
    "chrome": ["crome", "crom", "grom", "google chrome", "cromo"],
    "spotify": ["spotifai", "espotifai", "espotify", "spoti"],
    "discord": ["discor", "díscord", "disco"],
    "steam": ["stim", "estim", "esteam"],
    "code": ["visual studio code", "vs code", "vscode", "vicecode", "vi es code",
             "codigo", "código"],
    "explorer": ["explorador", "explorador de archivos", "el explorador"],
    "notepad": ["bloc de notas", "bloc", "notas"],
    "whatsapp": ["guasap", "wasap", "whatsap", "guatsap"],
    "telegram": ["telegran", "telegrama"],
    "obsidian": ["obsidiana", "obsidian"],
    "msedge": ["edge", "eich", "microsoft edge"],
    "firefox": ["fire fox", "fairfox"],
}


def _canonizar_app(nombre: str) -> str:
    """Devuelve el nombre real de la app a partir de como la oyo Alexa."""
    limpio = (nombre or "").strip().lower().replace(".exe", "")

    for canonico, variantes in ALIAS_VOZ.items():
        if limpio == canonico or limpio in variantes:
            return canonico
        # Coincidencia parcial: "la ventana de comet" -> comet
        for v in [canonico] + variantes:
            if v in limpio:
                return canonico

    return limpio


def _procesos_por_nombre() -> dict:
    """Mapa nombre_sin_exe -> lista de procesos vivos."""
    vivos: dict = {}
    for proceso in psutil.process_iter(["name"]):
        try:
            nombre = (proceso.info["name"] or "").lower()
            if not nombre:
                continue
            vivos.setdefault(nombre.replace(".exe", ""), []).append(proceso)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return vivos


def _buscar_proceso_parecido(objetivo: str, vivos: dict) -> str | None:
    """
    Busca el proceso vivo cuyo nombre mas se parezca al pedido.

    Sirve para cuando la transcripcion no es exacta pero el proceso si existe:
    'cometa' no esta, pero 'comet' si.
    """
    if objetivo in vivos:
        return objetivo

    # Uno contiene al otro
    for nombre in vivos:
        if objetivo in nombre or nombre in objetivo:
            return nombre

    # Parecido por letras (evita depender de librerias externas)
    import difflib

    parecidos = difflib.get_close_matches(objetivo, list(vivos), n=1, cutoff=0.75)
    return parecidos[0] if parecidos else None


# Palabras que NO son una aplicacion: si llegan aqui es que el usuario dijo
# algo generico y hay que tratarlo aparte, no buscar un "todo.exe".
PALABRAS_GENERICAS = {
    "todo", "todos", "todas", "todo lo que hay", "todas las ventanas",
    "todas las aplicaciones", "todos los programas", "las ventanas",
    "los programas", "las aplicaciones", "las apps", "nada", "algo",
}


# -------------------------------------------------------------------------
# APLICACIONES
# -------------------------------------------------------------------------
# Aplicaciones conocidas: alias hablado -> comando de arranque.
APPS_CONOCIDAS = {
    "spotify": "start spotify:",
    "chrome": "start chrome",
    "comet": "start comet",
    "edge": "start msedge",
    "brave": "start brave",
    "discord": "start discord:",
    "steam": "start steam:",
    "calculadora": "start calc",
    "bloc de notas": "start notepad",
    "notepad": "start notepad",
    "explorador": "start explorer",
    "configuracion": "start ms-settings:",
    "configuración": "start ms-settings:",
    "terminal": "start wt",
    "cmd": "start cmd",
    "powershell": "start powershell",
    "visual studio code": "code",
    "vs code": "code",
    "código": "code",
    "codigo": "code",
    "word": "start winword",
    "excel": "start excel",
    # WhatsApp NO va por protocolo: si la app de escritorio no esta
    # instalada, Windows abre la Microsoft Store ofreciendola. Se abre
    # por web, que es donde esta la sesion iniciada. Lo gestiona
    # tools/whatsapp.py; aqui solo queda la ruta web como respaldo.
    "whatsapp": "start https://web.whatsapp.com/",
    "telegram": "start telegram:",
    "obs": "start obs",
    "epic games": "start com.epicgames.launcher:",
    "epic": "start com.epicgames.launcher:",
    "riot": "start riotclient:",
    "battlenet": "start battlenet:",
    "ea": "start origin:",
}


# Juegos: alias hablado -> como se lanza.
#
# Un juego no se abre como un programa normal. Valorant necesita el cliente de
# Riot, y los de Epic se lanzan con una URL del lanzador que lleva dentro el
# identificador del juego. Llamar al .exe directamente falla o abre el
# antitrampas suelto, que es peor.
JUEGOS = {
    "valorant": "start riotclient://rnet-lcu/launch?gameName=valorant",
    "league of legends": "start riotclient://rnet-lcu/launch?gameName=league_of_legends",
    "lol": "start riotclient://rnet-lcu/launch?gameName=league_of_legends",
    "fortnite": "start com.epicgames.launcher://apps/Fortnite?action=launch",
    "rocket league": "start com.epicgames.launcher://apps/Sugar?action=launch",
    "gta": "start steam://rungameid/271590",
    "counter strike": "start steam://rungameid/730",
    "cs2": "start steam://rungameid/730",
    "dota": "start steam://rungameid/570",
    "minecraft": "start minecraft:",
}


def abrir_juego(nombre: str) -> str:
    """
    Lanza un juego y avisa de lo que conviene hacer antes.

    No cambia de modo por su cuenta: decidir por ti que se cierra media
    partida antes de empezar seria pasarse. Lo propone y ya.
    """
    crudo = (nombre or "").strip().lower()
    if not crudo:
        return "¿Qué juego quieres que abra?"

    comando = JUEGOS.get(crudo)
    if comando is None:
        for alias, cmd in JUEGOS.items():
            if alias in crudo or crudo in alias:
                comando, crudo = cmd, alias
                break

    if comando is None:
        # Puede ser un juego de Steam que no esta en la lista: que lo busque
        # el propio Steam en vez de decir que no lo conocemos.
        return abrir_aplicacion(crudo)

    try:
        subprocess.Popen(comando, shell=True, creationflags=subprocess.DETACHED_PROCESS
                         if os.name == "nt" else 0)
    except Exception as e:
        return f"No pude abrir {crudo}: {e}"

    log.info("Juego lanzado: %s", crudo)

    from tools import rendimiento
    cerrables = rendimiento.candidatos_a_cerrar()
    if cerrables:
        nombres = ", ".join(p["nombre"] for p in cerrables[:3])
        return (f"Abriendo {crudo}. Tienes {nombres} comiendo recursos. "
                "¿Los cierro y paso a modo gaming?")

    return f"Abriendo {crudo}. ¿Paso a modo gaming?"


# Nombre real del ejecutable de cada app. Se usa para LOCALIZARLO en el disco
# en vez de confiar en `start`, que solo funciona con lo que esta en el PATH o
# registrado como protocolo. Los navegadores modernos no cumplen ninguna de las
# dos cosas: se instalan en la carpeta del usuario y `start comet` responde con
# el sonido de error de Windows... mientras Jarvis decia "Abriendo comet".
EXE_POR_ALIAS = {
    "comet": "Comet.exe",
    "chrome": "chrome.exe",
    "brave": "brave.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "obsidian": "Obsidian.exe",
    "discord": "Discord.exe",
    "spotify": "Spotify.exe",
    "steam": "steam.exe",
    "telegram": "Telegram.exe",
    "whatsapp": "WhatsApp.exe",
    "obs": "obs64.exe",
    "visual studio code": "Code.exe",
    "vs code": "Code.exe",
    "codigo": "Code.exe",
}

# Cache: localizar un .exe puede costar rastrear carpetas, y no queremos
# pagarlo en cada orden cuando Alexa solo nos da 8 segundos.
_cache_exe: dict = {}


def _ruta_del_exe(clave: str) -> str:
    """Ruta real del ejecutable de una app conocida, o cadena vacia."""
    # Escotilla de escape: si alguna vez Jarvis no encuentra un programa,
    # se fija su ruta en el .env y se acabo la discusion. Vale para cualquier
    # app de la tabla:  JARVIS_COMET_EXE, JARVIS_OBSIDIAN_EXE, JARVIS_STEAM_EXE...
    variable = "JARVIS_" + clave.upper().replace(" ", "_") + "_EXE"
    forzado = os.environ.get(variable, "").strip().strip('"')
    if forzado and os.path.isfile(forzado):
        return forzado

    nombre_exe = EXE_POR_ALIAS.get(clave)
    if not nombre_exe:
        return ""
    if clave not in _cache_exe:
        try:
            _cache_exe[clave] = localizar_ejecutable(nombre_exe)
        except Exception as e:
            log.debug("Fallo localizando %s: %s", nombre_exe, e)
            _cache_exe[clave] = ""
        if _cache_exe[clave]:
            log.info("Ejecutable localizado: %s -> %s", clave, _cache_exe[clave])
    return _cache_exe[clave]


def _proceso_aparecio(clave: str, antes: int, segundos: float = 1.8) -> bool:
    """Espera a que el programa aparezca en la lista de procesos."""
    limite = time.monotonic() + segundos
    esperado = clave.replace(" ", "")
    while time.monotonic() < limite:
        time.sleep(0.15)
        vivos = _procesos_por_nombre()
        if len(vivos.get(clave, [])) > antes or len(vivos.get(esperado, [])) > antes:
            return True
        if _buscar_proceso_parecido(clave, vivos):
            return True
    return False


def _abrir_por_buscador_windows(nombre: str) -> bool:
    """
    Ultimo recurso: hacer lo que harias tu.

    Tecla Windows, escribir el nombre, Enter. El buscador del sistema conoce
    TODO lo que hay instalado, incluso lo que no dejo acceso directo ni entrada
    en el registro, asi que llega donde el catalogo no llega.

    Se comprueba que de verdad se abrio algo: si el buscador no aparece o no
    encuentra nada, el Enter no hace nada y no podemos cantar victoria.
    """
    try:
        import pyautogui
    except ImportError:
        return False

    try:
        ventana_antes = _ventana_al_frente()

        pyautogui.press("win")
        time.sleep(0.7)          # al menu Inicio le cuesta pintarse

        pyautogui.write(nombre, interval=0.02)
        # El buscador tarda en resolver: si pulsamos Enter antes de que haya
        # resultados, abre una busqueda web en el navegador, que es justo lo
        # que no queremos.
        time.sleep(1.3)

        pyautogui.press("enter")
        time.sleep(1.5)

        ventana_despues = _ventana_al_frente()

        if ventana_despues and ventana_despues != ventana_antes:
            log.info("Abierto por el buscador de Windows: %r -> %r", nombre, ventana_despues)
            return True

        # No cambio nada: cerramos el menu Inicio para no dejarlo abierto.
        pyautogui.press("escape")
        log.info("El buscador de Windows no encontró %r", nombre)
        return False

    except Exception as e:
        log.warning("Falló el buscador de Windows: %s", e)
        try:
            import pyautogui
            pyautogui.press("escape")
        except Exception:
            pass
        return False


def _ventana_al_frente() -> str:
    """Titulo de la ventana activa, para saber si algo cambio."""
    try:
        import pygetwindow
        activa = pygetwindow.getActiveWindow()
        return activa.title if activa else ""
    except Exception:
        return ""


def abrir_aplicacion(nombre_app: str) -> str:
    """
    Abre una aplicacion por su nombre hablado.

    El orden va de lo seguro a lo razonado:

      1. Ruta fijada a mano en el .env, si la hay.
      2. Catalogo de lo que hay instalado de verdad (tools/catalogo.py), que
         se construye rastreando el menu Inicio, el registro y la Store.
      3. Tabla de protocolos conocidos, para lo que no es un .exe.
      4. Si nada encaja del todo, se RAZONA: lo mas parecido del catalogo,
         y segun lo seguro que sea se abre o se propone.
      5. Y si aun asi no hay nada, el BUSCADOR DE WINDOWS: tecla Windows,
         escribir el nombre, Enter. Es lo que harias tu, y el sistema conoce
         cosas que ni el menu Inicio ni el registro exponen.

    Antes, "abre epic games" contestaba "no conozco ninguna aplicacion llamada
    epic games" aunque el lanzador estuviera instalado, solo porque nadie
    habia escrito su ruta a mano. Ahora hay cinco formas de llegar antes de
    darse por vencido.
    """
    from tools import catalogo

    crudo = (nombre_app or "").strip().lower()
    if not crudo:
        return "¿Qué aplicación quieres que abra?"

    if crudo in PALABRAS_GENERICAS:
        return "Dime qué programa concreto quieres abrir."

    clave = _canonizar_app(crudo)

    # --- 1. Ruta fijada a mano ---
    ruta = _ruta_del_exe(clave)
    if ruta:
        try:
            subprocess.Popen([ruta], creationflags=subprocess.DETACHED_PROCESS
                             if os.name == "nt" else 0)
            log.info("Aplicacion abierta: %s (%s)", clave, ruta)
            return f"Abriendo {clave}."
        except Exception as e:
            log.warning("No pude lanzar %s desde %s: %s", clave, ruta, e)

    # --- 2. Catalogo de lo instalado ---
    candidatos = catalogo.buscar(clave) or catalogo.buscar(crudo)

    if candidatos and candidatos[0]["nota"] >= 0.85:
        mejor = candidatos[0]
        if catalogo.lanzar(mejor):
            return f"Abriendo {mejor['nombre']}."

    # --- 3. Protocolos y comandos conocidos ---
    comando = APPS_CONOCIDAS.get(clave) or APPS_CONOCIDAS.get(crudo)
    if comando is None:
        for alias, cmd in APPS_CONOCIDAS.items():
            if alias in clave or clave in alias:
                comando, clave = cmd, alias
                break

    if comando:
        try:
            antes = len(_procesos_por_nombre().get(clave, []))
            subprocess.Popen(comando, shell=True,
                             creationflags=subprocess.DETACHED_PROCESS
                             if os.name == "nt" else 0)
            if os.name != "nt" or _proceso_aparecio(clave, antes):
                log.info("Aplicacion abierta: %s (dicho: %s)", clave, crudo)
                return f"Abriendo {clave}."
            log.warning("Lance %r pero no aparecio ningun proceso %r", comando, clave)
        except Exception as e:
            log.warning("Falló el comando conocido de %s: %s", clave, e)

    # --- 4. Razonar ---
    # Ni ruta fija, ni coincidencia clara, ni comando conocido. Pero eso NO
    # significa que no este instalada: puede que se llame de otra forma o que
    # Alexa la transcribiera torcida. Miramos lo mas parecido.
    if candidatos:
        mejor = candidatos[0]

        # Bastante seguro: lo abrimos y decimos QUE abrimos, para que si es lo
        # que no era, te enteres al momento.
        if mejor["nota"] >= 0.6:
            if catalogo.lanzar(mejor):
                return f"No encontré {crudo} exactamente, pero abrí {mejor['nombre']}."

    # --- 5. El buscador de Windows ---
    # Si el catalogo no lo tiene o no estamos seguros, se lo preguntamos al
    # sistema: tecla Windows, escribir, Enter. Conoce cosas que ni el menu
    # Inicio ni el registro exponen.
    if os.name == "nt" and _abrir_por_buscador_windows(crudo):
        return f"Abriendo {crudo}."

    # --- 6. Rendirse, pero con algo util ---
    if candidatos:
        nombres = ", ".join(c["nombre"] for c in candidatos[:3])
        return (f"No conseguí abrir {crudo}. Lo más parecido que tienes: "
                f"{nombres}. ¿Cuál abro?")

    total = len(catalogo.cargar())
    if total:
        return (f"No encuentro nada parecido a {crudo}, ni entre las {total} "
                "aplicaciones del catálogo ni en el buscador de Windows.")

    return (f"No conozco ninguna aplicación llamada {crudo}, y todavía no he "
            "rastreado lo que tienes instalado. Dime actualiza las aplicaciones.")


def cerrar_aplicacion(nombre_app: str) -> str:
    """Cierra una aplicacion por su nombre, tolerando la transcripcion de voz."""
    crudo = (nombre_app or "").strip().lower()
    if not crudo:
        return "¿Qué aplicación quieres que cierre?"

    # "cierra todo" / "cierra todas las ventanas" son ordenes distintas.
    if crudo in PALABRAS_GENERICAS:
        return cerrar_todo()

    clave = _canonizar_app(crudo)

    if clave in PROCESOS_PROTEGIDOS or f"{clave}.exe" in PROCESOS_PROTEGIDOS:
        return f"No voy a cerrar {clave}, es un proceso protegido del sistema."

    vivos = _procesos_por_nombre()
    real = _buscar_proceso_parecido(clave, vivos)

    if real is None:
        # Somos honestos: distinguimos "no existe" de "no esta abierto".
        log.info("No hay proceso parecido a %r (dicho: %r)", clave, crudo)
        return f"No encuentro ningún programa abierto que se parezca a {crudo}."

    if real in PROCESOS_PROTEGIDOS or f"{real}.exe" in PROCESOS_PROTEGIDOS:
        return f"No voy a cerrar {real}, es un proceso protegido del sistema."

    procesos = vivos[real]
    cerrados = 0
    for proceso in procesos:
        try:
            proceso.terminate()
            cerrados += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Damos 2 segundos para que cierren solos; los rezagados se matan.
    _, vivos_aun = psutil.wait_procs(procesos, timeout=2)
    for proceso in vivos_aun:
        try:
            proceso.kill()
        except Exception:
            pass

    log.info("Cerrada aplicacion %s (%d procesos)", real, cerrados)
    return f"Cerré {real}."


def cerrar_todo() -> str:
    """Cierra las aplicaciones de usuario mas comunes, respetando lo protegido."""
    from config import APPS_A_CERRAR_EN_GAMING

    cerrados = cerrar_varias(APPS_A_CERRAR_EN_GAMING)
    if cerrados:
        return f"Cerré {cerrados} procesos de las aplicaciones abiertas."
    return "No había ninguna de las aplicaciones habituales abierta."


def cerrar_varias(nombres: list[str]) -> int:
    """Cierra varias apps de golpe. Devuelve cuántos procesos se cerraron."""
    objetivos = {n.lower() for n in nombres} - PROCESOS_PROTEGIDOS
    total = 0
    for proceso in psutil.process_iter(["name"]):
        try:
            nombre = (proceso.info["name"] or "").lower()
            if nombre in objetivos:
                proceso.terminate()
                total += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


# -------------------------------------------------------------------------
# ENERGÍA Y APAGADO
# -------------------------------------------------------------------------
# GUIDs de los planes de energía de Windows.
PLANES_ENERGIA = {
    "alto": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",       # Alto rendimiento
    "equilibrado": "381b4222-f694-41f0-9685-ff5bb260df2e",  # Equilibrado
    "ahorro": "a1841308-3541-4fab-bc81-f71556f20b4a",       # Economizador
}


def cambiar_plan_energia(plan: str) -> bool:
    """Cambia el plan de energía de Windows. Devuelve True si funcionó."""
    guid = PLANES_ENERGIA.get(plan)
    if not guid or os.name != "nt":
        return False
    ok, _ = _ejecutar(["powercfg", "/setactive", guid])
    return ok


def bloquear_equipo() -> str:
    if os.name != "nt":
        return "Solo puedo bloquear el equipo en Windows."
    _ejecutar(["rundll32.exe", "user32.dll,LockWorkStation"])
    return "Bloqueando el equipo."


def suspender_equipo() -> str:
    if os.name != "nt":
        return "Solo puedo suspender en Windows."
    _ejecutar(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    return "Suspendiendo el equipo."


def apagar_equipo(minutos: int = 1) -> str:
    """Programa el apagado. Siempre con retraso, para poder cancelarlo."""
    if os.name != "nt":
        return "Solo puedo apagar en Windows."
    segundos = max(30, int(minutos) * 60)
    ok, salida = _ejecutar(["shutdown", "/s", "/t", str(segundos)])
    if not ok:
        return f"No pude programar el apagado: {salida}"
    return f"Voy a apagar el equipo en {segundos // 60} minuto(s). Di 'cancela el apagado' para detenerlo."


def cancelar_apagado() -> str:
    ok, _ = _ejecutar(["shutdown", "/a"])
    return "Apagado cancelado." if ok else "No había ningún apagado programado."


def reiniciar_equipo(minutos: int = 1) -> str:
    if os.name != "nt":
        return "Solo puedo reiniciar en Windows."
    segundos = max(30, int(minutos) * 60)
    ok, salida = _ejecutar(["shutdown", "/r", "/t", str(segundos)])
    if not ok:
        return f"No pude programar el reinicio: {salida}"
    return f"Reiniciando en {segundos // 60} minuto(s). Di 'cancela el apagado' para detenerlo."


# =========================================================================
# BUSCAR DENTRO DE UNA APLICACION
# =========================================================================
# "abre spotify y busca tame impala" acababa buscando "tame impala" en la web.
# Se entendia el "busca" y se perdia el "en spotify". Cada programa tiene su
# propio buscador y su propio atajo; aqui estan los que valen la pena.
#
# La clave es que el atajo se manda a la ventana YA ENFOCADA. Si la app no
# esta abierta hay que abrirla y esperar; si ya lo estaba, solo traerla al
# frente, porque relanzarla la pondria a cargar otra vez desde cero.
ATAJOS_BUSQUEDA = {
    "spotify":   ["ctrl", "l"],          # va directo a la caja de busqueda
    "obsidian":  ["ctrl", "o"],          # busqueda rapida de notas
    "code":      ["ctrl", "p"],          # ir a archivo
    "teams":     ["ctrl", "e"],
    "discord":   ["ctrl", "k"],
    "steam":     ["ctrl", "e"],
    "explorer":  ["ctrl", "e"],
    "chrome":    ["ctrl", "l"],
    "comet":     ["ctrl", "l"],
    "msedge":    ["ctrl", "l"],
    "firefox":   ["ctrl", "l"],
    "whatsapp":  ["ctrl", "f"],
    "telegram":  ["ctrl", "f"],
}

# Alias hablados que apuntan a una de las claves de arriba.
APPS_CON_BUSCADOR = set(ATAJOS_BUSQUEDA)


def app_tiene_buscador(nombre_app: str) -> str:
    """
    Devuelve la clave canonica si esa app se puede buscar por dentro, o "".

    Dos formas de que valga: que este en la tabla de atajos, o que sea una
    app instalada de verdad. Lo segundo es lo que permite "busca X en epic":
    Epic no tiene atajo conocido, pero si esta instalada la orden tiene
    sentido y el plan B mira la pantalla.

    Lo que NO vale es un nombre que no sea ninguna app: ahi devuelve "" y el
    router lo manda a la web, que es lo correcto para "busca vuelos a bogota".
    """
    clave = _canonizar_app(nombre_app or "")
    if clave in APPS_CON_BUSCADOR:
        return clave

    try:
        from tools import catalogo
        candidatos = catalogo.buscar(clave)
        if candidatos and candidatos[0]["nota"] >= 0.85:
            return clave
    except Exception as e:
        log.debug("El catalogo no respondio al comprobar %r: %s", clave, e)

    return ""


def _enfocar_si_esta_abierta(clave: str) -> bool:
    """Trae al frente la ventana de esa app, si existe. No la abre."""
    try:
        import pygetwindow
    except ImportError:
        return False

    objetivo = clave.replace(".exe", "").lower()
    try:
        for ventana in pygetwindow.getAllWindows():
            titulo = (ventana.title or "").lower()
            if not titulo or objetivo not in titulo:
                continue
            try:
                if ventana.isMinimized:
                    ventana.restore()
                ventana.activate()
            except Exception:
                # activate() falla a menudo en Windows si otra ventana tiene
                # el foco "pegado". Minimizar y restaurar la trae igual.
                try:
                    ventana.minimize()
                    ventana.restore()
                except Exception:
                    return False
            time.sleep(0.4)
            log.info("Ventana enfocada: %r", ventana.title)
            return True
    except Exception as e:
        log.debug("No pude enfocar %s: %s", clave, e)
    return False


def buscar_en_app(consulta: str, nombre_app: str) -> str:
    """
    Busca algo DENTRO de una aplicacion, no en internet.

    Devuelve None si la app no tiene buscador conocido, para que quien llame
    pueda caer a la busqueda web sin haber roto nada por el camino.
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return "¿Qué quieres que busque?"

    clave = app_tiene_buscador(nombre_app)
    if not clave:
        return None

    from tools import entrada

    ya_estaba = _enfocar_si_esta_abierta(clave)
    if not ya_estaba:
        respuesta_apertura = abrir_aplicacion(clave)
        if "no " in respuesta_apertura.lower()[:12]:
            return respuesta_apertura
        # Una app recien lanzada no acepta atajos hasta que pinta su ventana.
        # Spotify y Teams tardan lo suyo; esperamos a ver la ventana en vez de
        # dormir a ciegas una cantidad fija.
        limite = time.monotonic() + 6
        while time.monotonic() < limite:
            time.sleep(0.5)
            if _enfocar_si_esta_abierta(clave):
                break
        else:
            return (f"Abrí {clave}, pero tardó demasiado en aparecer. "
                    f"Dime otra vez que busques {consulta} y ya lo hago.")

    atajo = ATAJOS_BUSQUEDA.get(clave)

    if atajo:
        try:
            pyautogui = entrada._obtener_pyautogui()
            if pyautogui is None:
                return f"Abrí {clave}, pero no tengo control del teclado para buscar."

            pyautogui.hotkey(*atajo)
            time.sleep(0.5)
            entrada.escribir_texto(consulta, pulsar_enter=True)
        except Exception as e:
            log.warning("Falló la búsqueda dentro de %s: %s", clave, e)
            return f"Abrí {clave} pero no pude escribir la búsqueda: {e}"

        log.info("Busqueda dentro de %s: %r", clave, consulta)
        return f"Buscando {consulta} en {clave}."

    # Sin atajo conocido: se mira la pantalla y se busca algo que sirva para
    # buscar. Es lo que harias tu al abrir una app que no conoces: localizas
    # la lupa. Cada app pone una palabra distinta, asi que se busca por lo
    # que HACE y no por como se llama.
    try:
        from tools import pantalla
        respuesta = pantalla.buscar_dentro_de_lo_que_veo(consulta)
    except Exception as e:
        log.warning("El OCR no pudo buscar dentro de %s: %s", clave, e)
        respuesta = ""

    if respuesta:
        return respuesta.replace("en lo que tienes abierto", f"en {clave}")

    return (f"Abrí {clave}, pero no encontré su buscador en la pantalla. "
            f"Dime dónde está y le doy clic.")


# =========================================================================
# QUE ARCHIVO OCUPA MAS
# =========================================================================
def archivos_mas_grandes(cantidad: int = 5, carpeta: str = "") -> str:
    """
    Los archivos que mas espacio ocupan.

    "qué archivo tiene más memoria" contestaba con el uso de RAM. Se
    entendia "memoria" como el modulo de memoria y no como el disco, que es
    lo que quiere saber cualquiera que pregunte eso.
    """
    from config import DESCARGAS, DOCUMENTOS, ESCRITORIO

    if carpeta:
        from tools.archivos import _base_desde_alias
        try:
            raices = [_base_desde_alias(carpeta)]
        except Exception:
            raices = [ESCRITORIO]
    else:
        raices = [ESCRITORIO, DESCARGAS, DOCUMENTOS]

    encontrados: list[tuple[int, str]] = []
    limite_archivos = 40000       # cortafuegos: el reloj de Alexa manda
    vistos = 0

    for raiz in raices:
        try:
            if not raiz or not os.path.isdir(raiz):
                continue
            for actual, _dirs, ficheros in os.walk(raiz):
                # Nos saltamos lo que no es del usuario: pesa mucho y no se
                # puede borrar sin romper cosas.
                if any(p in actual.lower() for p in
                       ("\\node_modules", "\\.git", "\\appdata", "\\venv", "\\__pycache__")):
                    continue
                for fichero in ficheros:
                    vistos += 1
                    if vistos > limite_archivos:
                        break
                    ruta = os.path.join(actual, fichero)
                    try:
                        encontrados.append((os.path.getsize(ruta), ruta))
                    except OSError:
                        continue
                if vistos > limite_archivos:
                    break
        except Exception as e:
            log.debug("No pude recorrer %s: %s", raiz, e)

    if not encontrados:
        return "No encontré archivos que medir en Escritorio, Descargas ni Documentos."

    encontrados.sort(reverse=True)
    cantidad = max(1, min(int(cantidad or 5), 8))

    partes = []
    for tamano, ruta in encontrados[:cantidad]:
        mb = tamano / (1024 * 1024)
        medida = f"{mb / 1024:.1f} gigas" if mb >= 1024 else f"{mb:.0f} megas"
        partes.append(f"{os.path.basename(ruta)}, {medida}")

    if cantidad == 1:
        return f"El que más ocupa es {partes[0]}."
    return "Los que más ocupan: " + "; ".join(partes) + "."


def arrancar_partida(nombre: str = "") -> str:
    """
    Abre el lanzador y ADEMAS le da a jugar.

    "abre valorant" abria Riot Client y ahi se quedaba, esperando a que
    alguien pinchara JUGAR. Un lanzador no es el juego: es una tienda con un
    boton. Como cada lanzador pone una palabra distinta en ese boton (JUGAR,
    PLAY, INICIAR, y Epic a veces solo un icono), no se busca la palabra sino
    lo que hace, y de eso se encarga el OCR.

    Se espera a que el lanzador termine de pintar: pulsar antes de que la
    ficha del juego este en pantalla es pinchar en el vacio.
    """
    crudo = (nombre or "").strip()
    if crudo:
        apertura = abrir_juego(crudo)
        if apertura.lower().startswith("no "):
            return apertura
    else:
        apertura = ""

    try:
        from tools import pantalla
    except ImportError:
        return apertura or "No tengo el OCR disponible para darle a jugar."

    # Los lanzadores tardan lo suyo en pintar la ficha. Se mira varias veces
    # en vez de dormir a ciegas una cantidad fija: con el disco frio Epic
    # tarda ocho segundos y con el caliente dos.
    limite = time.monotonic() + 12
    while time.monotonic() < limite:
        time.sleep(1.5)
        try:
            objetivo = pantalla.encontrar_por_intencion("jugar")
        except Exception as e:
            log.debug("El OCR fallo buscando el boton de jugar: %s", e)
            objetivo = None

        if objetivo:
            resultado = pantalla.clic_por_intencion("jugar")
            log.info("Partida arrancada en %r: %s", crudo or "lo que habia", resultado)
            return f"{apertura} Le di a {objetivo['texto']}.".strip()

    return (f"{apertura} Pero no encontré el botón de jugar en la pantalla. "
            "Dime dónde está y le doy clic.").strip()
