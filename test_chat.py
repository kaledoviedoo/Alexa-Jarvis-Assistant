"""Pruebas de la Fase 6.1: el chat local.

Que /chat no exista para quien llega por el Funnel, que pida la llave, que
se vincule el celular con un codigo de un solo uso, y que una orden muestre
sus pasos en vivo, su confirmacion y lo que deja al fondo. Con el servidor en
memoria y una orden falsa: no hace falta Ollama ni Windows.

Ejecutar:  py test_chat.py
"""

import os
import sys
import tempfile
import time
import types
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ["JARVIS_VERIFICAR_FIRMA"] = "false"
os.environ["JARVIS_AVISOS"] = "0"
os.environ.setdefault("JARVIS_TOKEN_LOCAL", "llave-de-prueba-1234567890")

TMP = Path(tempfile.mkdtemp(prefix="jarvis-chat-"))

import tareas            # noqa: E402
tareas.usar_base(TMP / "jarvis.db")

import config            # noqa: E402
if not config.TOKEN_LOCAL:
    config.TOKEN_LOCAL = os.environ["JARVIS_TOKEN_LOCAL"]
import chat              # noqa: E402
chat.TOKEN_LOCAL = config.TOKEN_LOCAL
import confirmaciones    # noqa: E402
import logging           # noqa: E402
import ollama_client     # noqa: E402
import server            # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

LLAVE = config.TOKEN_LOCAL
fallos: list[str] = []


def comprobar(condicion: bool, texto: str) -> None:
    print(f"  {'OK   ' if condicion else 'FALLO'}  {texto}")
    if not condicion:
        fallos.append(texto)


def cliente(ip: str) -> TestClient:
    return TestClient(server.app, client=(ip, 50000))


class _Peticion:
    def __init__(self, ip, cabeceras=None):
        self.headers = {k.lower(): v for k, v in (cabeceras or {}).items()}
        self.client = types.SimpleNamespace(host=ip)


def seguir(c: TestClient, ident: str, segundos: float = 10) -> list[dict]:
    eventos, desde, fin = [], 0, time.time() + segundos
    while time.time() < fin:
        r = c.get(f"/chat/eventos/{ident}?desde={desde}").json()
        eventos += r["eventos"]
        desde = r["siguiente"]
        if r["terminado"]:
            break
    return eventos


def main() -> int:
    print("=" * 70)
    print("  FASE 6.1: EL CHAT LOCAL")
    print("=" * 70)

    print("\nSOLO DESDE ESTE PC O DESDE TU RED DE TAILSCALE")
    dd = chat.desde_donde
    comprobar(dd(_Peticion("127.0.0.1")) == "local", "127.0.0.1 es este PC")
    comprobar(dd(_Peticion("::1")) == "local", "::1 tambien")
    comprobar(dd(_Peticion("100.101.102.103")) == "tailnet", "100.x es la red de Tailscale")
    comprobar(dd(_Peticion("fd7a:115c:a1e0::1")) == "tailnet", "y su IPv6")
    comprobar(dd(_Peticion("192.168.1.20")) is None, "el wifi de la casa no")
    comprobar(dd(_Peticion("8.8.8.8")) is None, "internet no")
    for cab in chat.CABECERAS_DE_TUNEL:
        comprobar(dd(_Peticion("127.0.0.1", {cab: "x"})) is None, f"por el Funnel no (cabecera {cab})")
    comprobar(set(chat.CABECERAS_DE_TUNEL) == set(server._CABECERAS_DE_TUNEL),
              "mira las mismas cabeceras que /probar")

    print("\nEL FUNNEL NI SABE QUE EXISTE")
    pc = cliente("127.0.0.1")
    for metodo, ruta in (("get", "/chat"), ("get", f"/chat?llave={LLAVE}"), ("post", "/chat/orden"),
                         ("get", "/chat/estado"), ("post", "/chat/vincular")):
        r = getattr(pc, metodo)(ruta, headers={"Tailscale-Funnel-Request": "?1", "X-Jarvis-Chat": "1"},
                                **({"json": {}} if metodo == "post" else {}))
        comprobar(r.status_code == 404, f"{metodo.upper()} {ruta.split('?')[0]} por el Funnel -> 404 ({r.status_code})")
    r = cliente("8.8.8.8").get("/chat")
    comprobar(r.status_code == 404, "desde internet sin tunel tambien 404")

    print("\nSIN LLAVE NO SE ENTRA")
    r = pc.get("/chat")
    comprobar(r.status_code == 401 and "Vincular con Jarvis" in r.text, "sin cookie: la pagina de vincular")
    comprobar(pc.get("/chat/estado").status_code == 401, "y la API dice 401")
    r = pc.get("/chat?llave=otra-cosa", follow_redirects=False)
    comprobar(r.status_code == 401 and "jarvis_chat" not in r.headers.get("set-cookie", ""), "una llave falsa no da cookie")
    r = pc.get(f"/chat?llave={LLAVE}", follow_redirects=False)
    galleta = r.headers.get("set-cookie", "")
    comprobar(r.status_code == 303 and "jarvis_chat=" in galleta, "con la llave del .env: cookie y a /chat")
    comprobar("httponly" in galleta.lower() and "samesite=strict" in galleta.lower(), "cookie HttpOnly y SameSite=Strict")
    comprobar(LLAVE not in galleta, "la cookie no es la llave del .env")
    r = pc.get("/chat")
    comprobar(r.status_code == 200 and "<title>Jarvis</title>" in r.text, "ya dentro: la pagina del chat")
    comprobar("frame-ancestors 'none'" in r.headers.get("content-security-policy", ""), "no se puede meter en otra pagina")
    comprobar(pc.post("/chat/orden", json={"texto": "hola"}).status_code == 403,
              "una orden sin la cabecera del chat se rechaza (otra pagina no puede mandarla)")

    print("\nVINCULAR EL CELULAR CON UN CODIGO")
    celular = cliente("100.70.1.2")
    comprobar(celular.post("/chat/codigo", headers={"X-Jarvis-Chat": "1"}).status_code == 401,
              "el celular sin vincular no puede pedir codigos")
    original_ip = chat.ip_tailnet
    chat.ip_tailnet = lambda: "100.99.88.77"
    r = pc.post("/chat/codigo", headers={"X-Jarvis-Chat": "1"}).json()
    chat.ip_tailnet = original_ip
    codigo = r["codigo"]
    comprobar(len(codigo) == 6 and codigo.isdigit() and r["segundos"] == 300, "el PC pide un codigo de 6 cifras, 5 minutos")
    comprobar(r["direccion"] == "http://100.99.88.77:8000/chat", f"y la direccion para el celular ({r['direccion']})")
    h = {"X-Jarvis-Chat": "1"}
    comprobar(celular.post("/chat/vincular", json={"codigo": "000000" if codigo != "000000" else "111111"},
                           headers=h).status_code == 401, "un codigo equivocado no vale")
    r = celular.post("/chat/vincular", json={"codigo": codigo}, headers=h)
    comprobar(r.status_code == 200 and "jarvis_chat=" in r.headers.get("set-cookie", ""), "el bueno vincula el celular")
    comprobar(celular.get("/chat").status_code == 200, "y el celular ya entra")
    otro = cliente("100.70.1.3")
    comprobar(otro.post("/chat/vincular", json={"codigo": codigo}, headers=h).status_code == 401,
              "el mismo codigo no vale dos veces")
    comprobar(celular.post("/chat/codigo", headers=h).status_code == 403, "los codigos solo se piden desde el PC")
    nuevo = chat.nuevo_codigo()
    for _ in range(chat.INTENTOS_CODIGO):
        chat.gastar_codigo("999999" if nuevo != "999999" else "888888")
    comprobar(not chat.gastar_codigo(nuevo), "tras 5 intentos fallidos, el codigo se invalida")

    print("\nUNA ORDEN SE VE PASO A PASO")
    log = logging.getLogger("jarvis.ollama")

    def falso(texto, datos, presupuesto, en_este_hilo=False):
        comprobar(en_este_hilo and presupuesto >= 600, "el chat no tiene el limite de Alexa")
        comprobar(ollama_client.canal_actual() == "chat", "el modelo sabe que es el chat")
        log.info("Ofrezco 3 herramientas (por significado): listar_ventanas, obsidian_agregar_al_diario, x")
        log.info("Ejecutando herramienta listar_ventanas con {}")
        tareas.registrar_paso("listar_ventanas", {}, "Tienes 2 ventanas: Steam y Claude.")
        log.warning("FRENO: enviar_mensaje con {}. No ejecuto enviar_mensaje.")
        tareas.registrar_paso("obsidian_agregar_al_diario", {"contenido": "Steam y Claude"},
                              "Añadido a tu nota de hoy.\nVERIFICACION FALLIDA: no aparece")
        import threading
        threading.Thread(target=lambda: logging.getLogger("jarvis.otro").info("de otro hilo")).start()
        return "Listo, apunté Steam y Claude en tu diario.", "modelo"

    chat.configurar(falso)
    ident = pc.post("/chat/orden", json={"texto": "mira mis ventanas y apúntalas"}, headers=h).json()["id"]
    ev = seguir(pc, ident)
    tipos = [e["tipo"] for e in ev]
    comprobar(tipos[0] == "inicio" and tipos[-1] == "respuesta", f"empieza y acaba ({tipos[0]} ... {tipos[-1]})")
    clases = [e.get("clase") for e in ev if e["tipo"] == "log"]
    comprobar("ofrecidas" in clases and "ejecutando" in clases and "freno" in clases,
              f"se ven las herramientas ofrecidas, la que usa y el freno ({clases})")
    pasos = [e for e in ev if e["tipo"] == "paso"]
    comprobar([p["herramienta"] for p in pasos] == ["listar_ventanas", "obsidian_agregar_al_diario"]
              and "Steam y Claude" in pasos[0]["resultado"], "cada paso con lo que devolvio")
    comprobar("VERIFICACION FALLIDA" in pasos[1]["resultado"], "y si la comprobacion fallo, se ve")
    comprobar(not any("de otro hilo" in e.get("mensaje", "") for e in ev), "lo de otros hilos no se cuela")
    resp = ev[-1]
    comprobar(resp["texto"].startswith("Listo, apunté") and resp["origen"] == "modelo" and resp["confirmacion"] is None,
              "la respuesta, quien la resolvio y sin confirmacion pendiente")
    comprobar(ollama_client.canal_actual() == "voz", "fuera del chat, el canal vuelve a voz")

    print("\nLO QUE PIDE CONFIRMACION SE APRUEBA CON UN BOTON")
    hecho = []

    def con_confirmacion(texto, *a, **k):
        if texto == "sí":
            return confirmaciones.resolver("sí") or "nada", "router"
        return confirmaciones.pedir("mandarle a familia: llego en 10", lambda: hecho.append(1) or "Enviado."), "modelo"

    chat.configurar(con_confirmacion)
    ident = pc.post("/chat/orden", json={"texto": "mandale a familia que llego en 10"}, headers=h).json()["id"]
    resp = seguir(pc, ident)[-1]
    c = resp["confirmacion"]
    comprobar(c is not None and c["descripcion"] == "mandarle a familia: llego en 10" and 40 <= c["segundos"] <= 45,
              f"la respuesta trae la confirmacion y cuanto le queda ({c})")
    ident = pc.post("/chat/orden", json={"texto": "sí"}, headers=h).json()["id"]
    resp = seguir(pc, ident)[-1]
    comprobar(hecho == [1] and "Enviado" in resp["texto"], "el boton 'Sí' la ejecuta")
    confirmaciones.olvidar()

    print("\nLO QUE QUEDA AL FONDO SE SIGUE HASTA QUE ACABA")

    def con_fondo(texto, *a, **k):
        tareas.lanzar_en_segundo_plano("investigar BGP", lambda: time.sleep(0.4) or "BGP elige rutas entre redes.")
        return "Buscando sobre BGP. Pregúntame cómo quedó lo último.", "router"

    chat.configurar(con_fondo)
    ident = pc.post("/chat/orden", json={"texto": "investiga bgp"}, headers=h).json()["id"]
    ev = seguir(pc, ident)
    tipos = [e["tipo"] for e in ev]
    comprobar("fondo" in tipos and tipos[-1] == "fondo_hecho", f"avisa que sigue y trae el resultado ({tipos})")
    comprobar(ev[-1]["resultado"] == "BGP elige rutas entre redes.", "con el texto de la tarea")
    comprobar(tareas.novedades() == "", "visto en el chat, no se repite como 'Por cierto'")

    print("\nEL ESTADO Y LA CONVERSACION SE RECUPERAN AL RECARGAR")
    s = pc.get("/chat/estado").json()
    comprobar(s["lugar"] == "local" and s["modo"] and s["modelo"], "modo y modelo")
    comprobar([c["texto"] for c in s["conversacion"]][-1] == "investiga bgp", "la conversacion de la sesion")
    comprobar(any(t["descripcion"] == "investigar BGP" for t in s["tareas"]), "y las tareas recientes")
    comprobar(celular.get("/chat/estado").json()["lugar"] == "tailnet", "el celular sabe que es el celular")

    print("\nEL MODELO SE EXTIENDE EN EL CHAT Y NO EN LA VOZ")
    voz_prompt = ollama_client.construir_prompt_sistema()
    chat_prompt = ollama_client.en_canal("chat", ollama_client.construir_prompt_sistema)
    comprobar(ollama_client.AVISO_CHAT not in voz_prompt and ollama_client.AVISO_CHAT in chat_prompt,
              "solo el chat le dice que puede extenderse")
    comprobar(ollama_client.TOKENS_CHAT > ollama_client.TOKENS_VOZ, "y le da mas espacio")

    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS")
        for f in fallos:
            print("   -", f)
        return 1
    print("  TODO CORRECTO. El chat solo existe en casa y enseña cada paso.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
