"""
Diagnostico de rendimiento: "por que tengo lag".

La idea
-------
Un informe de CPU, RAM y GPU no sirve de nada mientras juegas: son numeros y
tu quieres una respuesta. Esto mira todo a la vez, decide QUIEN tiene la culpa
y lo dice en una frase. Si hay algo que cerrar, lo propone y espera tu si.

Lo que NO hace
--------------
No cierra nada por su cuenta y no toca procesos del sistema. La lista de lo
intocable esta en config.PROCESOS_PROTEGIDOS y aqui se respeta sin excepcion:
matar el proceso equivocado en mitad de una partida es peor que el lag.
"""

import logging

import psutil

from config import PROCESOS_PROTEGIDOS

log = logging.getLogger("jarvis.rendimiento")

# Programas que se comen recursos y que casi nunca hacen falta mientras juegas.
# Se proponen para cerrar; nunca se cierran solos.
PRESCINDIBLES_AL_JUGAR = {
    "chrome", "msedge", "firefox", "brave", "opera", "comet",
    "spotify", "discord", "slack", "teams", "ms-teams",
    "onedrive", "dropbox", "googledrivefs",
    "obs64", "obs32", "streamlabs",
    "code", "cursor", "pycharm64", "idea64",
    "epicgameslauncher", "battle.net", "origin", "uplay",
    "acrord32", "outlook", "excel", "winword",
}

# Lo que SI es un juego: si esto esta arriba en la lista, no es un problema.
JUEGOS_CONOCIDOS = {
    "valorant", "valorant-win64-shipping", "riotclientservices",
    "csgo", "cs2", "dota2", "leagueoflegends", "league of legends",
    "fortniteclient-win64-shipping", "gta5", "rdr2", "eldenring",
    "minecraft", "javaw", "overwatch", "apex", "r5apex", "rocketleague",
}


def _procesos_pesados(limite: int = 12) -> list[dict]:
    """Los que mas CPU y memoria consumen ahora mismo."""
    # Primera pasada para armar el contador de CPU: sin ella psutil devuelve
    # 0.0 en todos, que es el error clasico de este tipo de informes.
    for p in psutil.process_iter(["name"]):
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    import time
    time.sleep(0.35)

    nucleos = psutil.cpu_count() or 1
    agrupados: dict = {}

    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            nombre = (p.info["name"] or "").lower().replace(".exe", "")
            if not nombre:
                continue
            # Normalizamos por nucleos: Chrome con 20 pestañas suma cientos de
            # por ciento y no significa que este al 300 por cien del equipo.
            cpu = p.cpu_percent(None) / nucleos
            ram = (p.info["memory_info"].rss if p.info["memory_info"] else 0) / (1024 ** 3)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if nombre not in agrupados:
            agrupados[nombre] = {"nombre": nombre, "cpu": 0.0, "ram": 0.0, "procesos": 0}
        agrupados[nombre]["cpu"] += cpu
        agrupados[nombre]["ram"] += ram
        agrupados[nombre]["procesos"] += 1

    ordenados = sorted(agrupados.values(),
                       key=lambda d: d["cpu"] + d["ram"] * 8, reverse=True)
    return ordenados[:limite]


def _es_prescindible(nombre: str) -> bool:
    if nombre in PROCESOS_PROTEGIDOS or f"{nombre}.exe" in PROCESOS_PROTEGIDOS:
        return False
    if nombre in JUEGOS_CONOCIDOS:
        return False
    return nombre in PRESCINDIBLES_AL_JUGAR


def candidatos_a_cerrar() -> list[dict]:
    """Lo que se puede cerrar sin romper nada, ordenado por lo que libera."""
    return [p for p in _procesos_pesados(25)
            if _es_prescindible(p["nombre"]) and (p["cpu"] > 1.5 or p["ram"] > 0.35)]


def diagnostico() -> str:
    """
    Por que va lento el equipo, en una frase.

    Mira todo a la vez y se moja: dice cual es el cuello de botella en vez de
    soltar cuatro numeros y que decidas tu.
    """
    from tools import sistema

    cpu = psutil.cpu_percent(interval=0.4)
    ram = psutil.virtual_memory()
    gpu = sistema.info_gpu()

    pesados = _procesos_pesados(8)
    culpables = []
    causas = []

    # --- CPU ---
    if cpu >= 85:
        causas.append(f"el procesador está al {cpu:.0f} por ciento")
    elif cpu >= 65:
        causas.append(f"el procesador va cargado, al {cpu:.0f} por ciento")

    # --- Memoria ---
    if ram.percent >= 90:
        causas.append(f"la memoria está al {ram.percent:.0f} por ciento, casi llena")
    elif ram.percent >= 80:
        causas.append(f"la memoria va justa, al {ram.percent:.0f} por ciento")

    # --- Grafica ---
    if gpu.get("disponible"):
        libre_gb = gpu.get("vram_libre_mb", 0) / 1024
        total_gb = gpu.get("vram_total_mb", 1) / 1024
        usado = 100 * (1 - libre_gb / max(total_gb, 0.1))
        temperatura = gpu.get("temperatura")

        if usado >= 92:
            causas.append(f"la memoria de video está al {usado:.0f} por ciento")
        if temperatura and temperatura >= 83:
            # Por encima de esto la tarjeta se autolimita, y eso se nota como
            # tirones aunque los porcentajes parezcan normales.
            causas.append(f"la gráfica está a {temperatura} grados y probablemente se esté frenando sola")

    # --- Quien se lo esta comiendo ---
    for p in pesados[:3]:
        if p["cpu"] > 12 or p["ram"] > 1.2:
            etiqueta = f"{p['nombre']} usa {p['cpu']:.0f} por ciento de procesador"
            if p["ram"] > 0.7:
                etiqueta += f" y {p['ram']:.1f} gigas"
            culpables.append(etiqueta)

    # --- La respuesta ---
    if not causas and not culpables:
        detalle = f"Procesador al {cpu:.0f}, memoria al {ram.percent:.0f}"
        if gpu.get("disponible") and gpu.get("temperatura"):
            detalle += f", gráfica a {gpu['temperatura']} grados"
        return (f"No veo nada raro. {detalle}. "
                "Si el lag lo notas en un juego online, mira la conexión más que el equipo.")

    partes = []
    if causas:
        partes.append("Va lento porque " + ", y ".join(causas[:2]) + ".")
    if culpables:
        partes.append("Lo que más consume: " + ", ".join(culpables[:2]) + ".")

    cerrables = candidatos_a_cerrar()
    if cerrables:
        nombres = ", ".join(p["nombre"] for p in cerrables[:3])
        libera = sum(p["ram"] for p in cerrables[:3])
        partes.append(f"Puedo cerrar {nombres} y liberar unos {libera:.1f} gigas. ¿Lo hago?")
    else:
        partes.append("No hay nada prescindible abierto que pueda cerrar.")

    return " ".join(partes)


def cerrar_prescindibles() -> str:
    """Cierra lo que sobra. Solo se llama tras una confirmacion."""
    from tools import sistema

    cerrables = candidatos_a_cerrar()
    if not cerrables:
        return "No hay nada prescindible que cerrar."

    cerrados, liberado = [], 0.0
    for p in cerrables[:5]:
        resultado = sistema.cerrar_aplicacion(p["nombre"])
        if "Cerré" in resultado or "cerr" in resultado.lower():
            cerrados.append(p["nombre"])
            liberado += p["ram"]

    if not cerrados:
        return "No conseguí cerrar ninguno."

    return f"Cerré {', '.join(cerrados)}. Liberé unos {liberado:.1f} gigas."


def informe_para_jugar() -> str:
    """Estado del equipo pensado para antes de una partida."""
    from tools import sistema

    gpu = sistema.info_gpu()
    ram = psutil.virtual_memory()
    cerrables = candidatos_a_cerrar()

    partes = []
    if gpu.get("disponible"):
        libre = gpu.get("vram_libre_mb", 0) / 1024
        partes.append(f"Gráfica con {libre:.1f} gigas de video libres")
        if gpu.get("temperatura"):
            partes.append(f"a {gpu['temperatura']} grados")

    partes.append(f"memoria libre al {100 - ram.percent:.0f} por ciento")

    base = ", ".join(partes) + "."
    if cerrables:
        nombres = ", ".join(p["nombre"] for p in cerrables[:3])
        return f"{base} Tienes {nombres} abiertos comiendo recursos. ¿Los cierro y paso a modo gaming?"
    return f"{base} No hay nada estorbando. ¿Paso a modo gaming?"
