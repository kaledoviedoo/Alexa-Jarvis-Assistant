"""Mantiene vivo el camino publico por el que entra Amazon.

Un ping cada noventa segundos al tunel para que no se duerma. El ping se
manda contra la IP publica resuelta por DNS-over-HTTPS y no contra el
nombre, porque MagicDNS resuelve los nombres .ts.net a la IP interna del
tailnet y el ping se quedaba dentro sin tocar el camino real.

El modelo solo se mantiene en la VRAM si has usado el asistente hace poco.
"""

import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request

from config import (
    INTERVALO_CALIENTE,
    MANTENER_CALIENTE,
    TUNEL_URL,
)

log = logging.getLogger("jarvis.caliente")

_hilo: threading.Thread | None = None
_parar = threading.Event()

# Estadisticas, para poder verlas en /salud y saber si esto sirve de algo.
estadisticas = {
    "pings": 0,
    "fallos": 0,
    "fallos_seguidos": 0,
    "ultimo_ms": None,
    "ultimo_error": None,
    "ruta": "sin estrenar",
}


fallos_externos = [0]

_ips_publicas: list[str] = []
_ips_caducan: float = 0.0


def _resolver_por_dns_publico(host: str) -> list[str]:
    """Resuelve un nombre saltandose el DNS del equipo."""
    url = f"https://dns.google/resolve?name={urllib.parse.quote(host)}&type=A"
    try:
        peticion = urllib.request.Request(url, headers={"accept": "application/dns-json"})
        with urllib.request.urlopen(peticion, timeout=8) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.debug("No pude resolver %s por DNS publico: %s", host, e)
        return []

    return [r["data"] for r in datos.get("Answer", [])
            if r.get("type") == 1 and r.get("data")]


def _ips_del_tunel(host: str) -> list[str]:
    global _ips_publicas, _ips_caducan

    if _ips_publicas and time.monotonic() < _ips_caducan:
        return _ips_publicas

    encontradas = _resolver_por_dns_publico(host)
    if encontradas:
        _ips_publicas = encontradas
        _ips_caducan = time.monotonic() + 3600

        try:
            local = socket.gethostbyname(host)
            if local not in encontradas:
                log.info(
                    "MagicDNS resuelve %s como %s, pero la IP publica es %s. "
                    "Caliento por la publica, que es por donde entra Amazon.",
                    host, local, encontradas[0],
                )
        except OSError as e:
            log.debug("MagicDNS no resolvio %s: %s", host, e)

    return _ips_publicas


def _ping_por_ip(host: str, ip: str, ruta: str = "/jarvis", timeout: int = 12) -> int:
    """Pide https://host/ruta forzando la conexion contra una IP concreta."""
    contexto = ssl.create_default_context()
    inicio = time.perf_counter()

    with socket.create_connection((ip, 443), timeout=timeout) as bruto:
        # server_hostname es lo importante: el Funnel enruta por SNI, asi que
        # sin esto la entrada de Tailscale no sabria a que nodo mandarnos.
        with contexto.wrap_socket(bruto, server_hostname=host) as tls:
            tls.settimeout(timeout)
            tls.sendall((
                f"GET {ruta} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                "User-Agent: Jarvis-KeepWarm/2.0\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"))

            respuesta = b""
            while len(respuesta) < 64:
                trozo = tls.recv(1024)
                if not trozo:
                    break
                respuesta += trozo

    if not respuesta.startswith(b"HTTP/"):
        raise OSError("la entrada del tunel no devolvio una respuesta HTTP")

    return round((time.perf_counter() - inicio) * 1000)


def _ping_publico() -> None:
    """Recorre el mismo camino que Amazon para dejarlo abierto."""
    if not TUNEL_URL:
        return

    host = urllib.parse.urlparse(TUNEL_URL).hostname or ""
    if not host:
        return

    # --- Camino bueno: contra la IP publica, como entra Amazon ---
    for ip in _ips_del_tunel(host):
        try:
            transcurrido = _ping_por_ip(host, ip)
        except Exception as e:
            fallos_externos[0] += 1
            if fallos_externos[0] in (1, 3) or fallos_externos[0] % 10 == 0:
                log.warning("El camino público por %s no responde: %s", ip, str(e)[:90])
            continue

        estadisticas["pings"] += 1
        estadisticas["ultimo_ms"] = transcurrido
        estadisticas["ultimo_error"] = None
        estadisticas["ruta"] = f"publica ({ip})"
        if fallos_externos[0]:
            log.info("El camino público vuelve tras %d fallos.", fallos_externos[0])
            fallos_externos[0] = 0

        if estadisticas["fallos_seguidos"]:
            log.warning("El tunel vuelve a responder tras %d intentos fallidos.",
                        estadisticas["fallos_seguidos"])
        estadisticas["fallos_seguidos"] = 0

        # Amazon se rinde a los 8 segundos. Un viaje de mas de 2 s significa
        # que el camino estaba frio de verdad, y que esto hace falta.
        if transcurrido > 2000:
            log.info("Camino frío: el ping externo tardó %d ms. Ya está caliente.", transcurrido)
        else:
            log.debug("Ping externo (%s): %d ms", ip, transcurrido)
        return

    url = f"{TUNEL_URL.rstrip('/')}/jarvis"
    inicio = time.perf_counter()
    try:
        peticion = urllib.request.Request(
            url, headers={"User-Agent": "Jarvis-KeepWarm/2.0",
                          "ngrok-skip-browser-warning": "1"})
        with urllib.request.urlopen(peticion, timeout=10) as resp:
            resp.read(200)

        estadisticas["pings"] += 1
        estadisticas["ultimo_ms"] = round((time.perf_counter() - inicio) * 1000)
        estadisticas["ultimo_error"] = None
        estadisticas["ruta"] = "interna (no calienta el camino de Amazon)"
        estadisticas["fallos_seguidos"] = 0

    except Exception as e:
        estadisticas["fallos"] += 1
        estadisticas["fallos_seguidos"] += 1
        estadisticas["ultimo_error"] = str(e)[:120]
        estadisticas["ruta"] = "ninguna"

        if estadisticas["fallos_seguidos"] >= 2:
            log.warning("El túnel no responde (%d fallos seguidos): %s",
                        estadisticas["fallos_seguidos"], str(e)[:120])
        else:
            log.debug("El ping al túnel falló: %s", e)


# Cuanto tiempo se considera que "sigues por aqui". Mientras dure, el modelo
# se mantiene cargado en la VRAM; pasado eso, se le deja caer.
MINUTOS_DE_GRACIA = 20

# Ultima vez que llego algo de Alexa. Lo marca el servidor en cada peticion.
_ultima_actividad = 0.0


def marcar_actividad() -> None:
    """El servidor avisa de que estas usando el asistente."""
    global _ultima_actividad
    _ultima_actividad = time.monotonic()


def hay_actividad_reciente() -> bool:
    if not _ultima_actividad:
        return False
    return (time.monotonic() - _ultima_actividad) < MINUTOS_DE_GRACIA * 60


def _tocar_modelo() -> None:
    """Renueva el keep_alive del modelo, pero SOLO si lo estas usando."""
    if not hay_actividad_reciente():
        return

    try:
        import modes

        modes.precalentar_modelo()
    except Exception as e:
        log.debug("No pude tocar el modelo: %s", e)


def _bucle() -> None:
    # Esperamos un poco antes del primer ping: al arrancar, el tunel puede no
    # estar levantado todavia y el fallo solo ensuciaria el registro.
    if _parar.wait(3):
        return

    for _ in range(6):
        _ping_publico()
        if estadisticas["ultimo_error"] is None and estadisticas["pings"]:
            break
        if _parar.wait(3):
            return

    while not _parar.is_set():
        _ping_publico()
        _tocar_modelo()

        if _parar.wait(INTERVALO_CALIENTE):
            break


def iniciar() -> bool:
    """Arranca el hilo. Devuelve True si quedó en marcha."""
    global _hilo

    if not MANTENER_CALIENTE:
        log.info("Mantener caliente: desactivado por configuración.")
        return False

    if not TUNEL_URL:
        log.info("Mantener caliente: no hay URL de túnel configurada, se omite.")
        return False

    if _hilo is not None and _hilo.is_alive():
        return True

    _parar.clear()
    _hilo = threading.Thread(target=_bucle, daemon=True, name="mantener-caliente")
    _hilo.start()

    log.info(
        "Mantener caliente: activo, un ping cada %d segundos a %s",
        INTERVALO_CALIENTE,
        TUNEL_URL,
    )
    return True


def detener() -> None:
    _parar.set()


def _funnel_publicado() -> str:
    """El nombre que Tailscale dice estar publicando ahora mismo, o "".

    Se lee de `tailscale funnel status` porque es la unica fuente que sabe la
    verdad: el .env puede haberse quedado viejo y el propio Jarvis no tiene
    forma de enterarse.
    """
    import re
    import subprocess

    try:
        salida = subprocess.run(
            ["tailscale", "funnel", "status"],
            capture_output=True, text=True, timeout=8,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        ).stdout
    except Exception as e:
        log.debug("No pude consultar el Funnel: %s", e)
        return ""

    publicados = nombres_publicados(salida)
    return publicados[0] if publicados else ""


def nombres_publicados(salida: str) -> list[str]:
    """Todos los nombres que publica el Funnel, sin repetir.

    Un equipo renombrado en Tailscale puede seguir publicandose con el nombre
    viejo y el nuevo a la vez, y los dos llegan aqui.
    """
    import re
    vistos = []
    for nombre in re.findall(r"https://([a-z0-9-]+\.[a-z0-9.-]+\.ts\.net)", salida or "", re.I):
        if nombre.lower() not in vistos:
            vistos.append(nombre.lower())
    return vistos


def _salida_funnel() -> str:
    import subprocess
    try:
        return subprocess.run(
            ["tailscale", "funnel", "status"],
            capture_output=True, text=True, timeout=8,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        ).stdout
    except Exception as e:
        log.debug("No pude consultar el Funnel: %s", e)
        return ""


def comprobar_camino_publico() -> tuple[bool, str]:
    """Comprueba que la direccion configurada exista de verdad y sea la buena.

    Devuelve (todo_bien, explicacion). La explicacion trae ya escrito lo que
    hay que hacer, porque el fallo tipico aqui es imposible de adivinar
    mirando el equipo: todo parece correcto y nada funciona.

    Existe por lo del 16 de septiembre. El equipo se renombro en Tailscale, la
    direccion vieja dejo de resolver, y Jarvis siguio arrancando tan contento
    y diciendo "listo y escuchando" mientras Amazon llamaba a una puerta que
    ya no existia. Cuarenta pings fallidos seguidos, cero peticiones, y la
    unica pista enterrada en una linea de aviso repetida.
    """
    if not TUNEL_URL:
        return False, ("No hay JARVIS_TUNEL_URL en el .env. Alexa no tiene "
                       "por donde entrar.")

    configurado = urllib.parse.urlparse(TUNEL_URL).hostname or ""
    if not configurado:
        return False, f"JARVIS_TUNEL_URL no parece una direccion: {TUNEL_URL}"

    resuelve = bool(_resolver_por_dns_publico(configurado))
    publicados = nombres_publicados(_salida_funnel())
    # Tailscale puede seguir listando el nombre viejo de un equipo renombrado
    # aunque ya no exista en internet. Solo se sugiere uno que resuelva: el
    # 1 de noviembre se sugirio el fantasma, se puso en la consola de Alexa y
    # el Echo dejo de llegar.
    vivos = [n for n in publicados if n == configurado.lower() and resuelve
             or n != configurado.lower() and _resolver_por_dns_publico(n)]
    publicado = (vivos or publicados or [""])[0]

    # El caso que nos costo la tarde: el nombre del .env ya no existe, pero
    # Tailscale esta publicando otro. Podemos decir exactamente cual. Si el
    # del .env esta entre los publicados y resuelve, no hay nada que arreglar.
    if publicados and configurado.lower() not in publicados:
        if not vivos:
            return False, (
                f"El .env apunta a {configurado} y Tailscale publica "
                f"{', '.join(publicados)}, pero ninguno resuelve en internet. "
                "Vuelve a lanzar: tailscale funnel --bg --https=443 localhost:8000"
            )
        return False, (
            f"El .env apunta a {configurado}, pero Tailscale esta publicando "
            f"{publicado}. Cambia JARVIS_TUNEL_URL a https://{publicado} y "
            f"actualiza tambien el endpoint en la consola de Alexa."
        )

    if not resuelve:
        if publicado:
            return False, (
                f"{configurado} no resuelve por DNS publico aunque el Funnel "
                "este activo. Suele tardar un par de minutos tras publicarlo; "
                "si sigue igual, vuelve a lanzarlo: "
                "tailscale funnel --bg --https=443 localhost:8000"
            )
        return False, (
            f"{configurado} no resuelve y Tailscale no esta publicando nada. "
            "Comprueba que Tailscale este corriendo y lanza: "
            "tailscale funnel --bg --https=443 localhost:8000"
        )

    return True, configurado
