"""Pruebas de la Fase 7.3: herramientas externas por MCP.

Sin red y sin instalar nada: un servidor MCP falso (este mismo archivo con
--servidor-falso) hace de fetch, de YouTube y de GitHub, hablando el mismo
protocolo por stdio que los de verdad.

Ejecutar:  py test_mcp.py
"""

import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path


def servidor_falso() -> None:
    """Un servidor MCP minimo por stdio. Lo que hace depende de MCP_FALSO."""
    modo = os.environ.get("MCP_FALSO", "web")
    herramientas = {
        "web": [{"name": "fetch", "inputSchema": {"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"}, "max_length": {"type": "integer"}, "start_index": {"type": "integer"},
            "raw": {"type": "boolean"}}}}],
        "youtube": [{"name": "get_transcript", "inputSchema": {"type": "object", "required": ["url"], "properties": {
            "url": {"type": "string"}, "lang": {"type": "string"}}}}],
        "github": [
            {"name": "get_me", "inputSchema": {"type": "object", "properties": {}}},
            {"name": "list_issues", "inputSchema": {"type": "object", "required": ["owner", "repo"], "properties": {
                "owner": {}, "repo": {}, "state": {"enum": ["OPEN", "CLOSED"]}, "perPage": {}}}},
            {"name": "list_pull_requests", "inputSchema": {"type": "object", "required": ["owner", "repo"],
                                                           "properties": {"owner": {}, "repo": {}, "state": {}, "perPage": {}}}},
            {"name": "list_commits", "inputSchema": {"type": "object", "required": ["owner", "repo"],
                                                     "properties": {"owner": {}, "repo": {}, "perPage": {}}}},
            {"name": "actions_list", "inputSchema": {"type": "object", "required": ["method", "owner", "repo"],
                                                     "properties": {"method": {}, "owner": {}, "repo": {}, "per_page": {}}}},
        ],
    }[modo]
    registro = Path(os.environ["MCP_REGISTRO"]) if os.environ.get("MCP_REGISTRO") else None

    def responder(ident, resultado=None, error=None):
        mensaje = {"jsonrpc": "2.0", "id": ident}
        mensaje.update({"error": error} if error else {"result": resultado})
        sys.stdout.write(json.dumps(mensaje) + "\n")
        sys.stdout.flush()

    def texto(t, error=False):
        return {"content": [{"type": "text", "text": t}], "isError": error}

    for linea in sys.stdin:
        pedido = json.loads(linea)
        if registro:
            with registro.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"modo": modo, "pid": os.getpid(), **pedido}) + "\n")
        metodo, ident, p = pedido.get("method"), pedido.get("id"), pedido.get("params") or {}
        if ident is None:
            continue
        sys.stderr.write(f"[falso] {metodo}\n")
        if metodo == "initialize":
            # Un aviso suelto antes de la respuesta: el cliente debe saltarlo.
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/message",
                                         "params": {"level": "info", "data": "hola"}}) + "\n")
            responder(ident, {"protocolVersion": p.get("protocolVersion"), "capabilities": {"tools": {}},
                              "serverInfo": {"name": f"falso-{modo}", "version": "1"}})
        elif metodo == "tools/list":
            responder(ident, {"tools": herramientas})
        elif metodo == "tools/call":
            nombre, a = p.get("name"), p.get("arguments") or {}
            if nombre == "fetch":
                if "lenta" in a["url"]:
                    time.sleep(5)
                if "muere" in a["url"]:
                    sys.exit(3)
                responder(ident, texto(f"Contents of {a['url']}:\n# Arepas\nReceta facil desde {a.get('start_index', 0)}."))
            elif nombre == "get_transcript":
                if "sinsubs" in a["url"]:
                    responder(ident, texto("No transcripts were found", error=True))
                elif "largo" in a["url"]:
                    responder(ident, texto(" ".join(f"Frase numero {i} del video sobre redes." for i in range(3000))))
                elif a.get("lang") == "es" and "ingles" in a["url"]:
                    responder(ident, texto("No transcript in es", error=True))
                else:
                    responder(ident, texto(f"[{a.get('lang')}] El profesor explica subnetting con ejemplos."))
            elif nombre == "get_me":
                responder(ident, texto(json.dumps({"login": "estudiante"})))
            elif nombre == "list_issues":
                if a.get("state") not in ("OPEN", "CLOSED"):
                    responder(ident, texto("state invalido", error=True))
                else:
                    responder(ident, texto(json.dumps({"issues": [{"number": 7, "title": "El chat no carga"},
                                                                   {"number": 9, "title": "Voz lenta"}]})))
            elif nombre == "list_pull_requests":
                responder(ident, texto("[]"))
            elif nombre == "list_commits":
                responder(ident, texto(json.dumps([{"sha": "a1", "commit": {"message": "Fase 7.2: memoria\n\ndetalle",
                                                                            "author": {"date": "2026-11-16T20:00:00Z"}}}])))
            elif nombre == "actions_list":
                responder(ident, texto(json.dumps({"total_count": 2, "workflow_runs": [
                    {"name": "pruebas", "head_branch": "main", "status": "completed", "conclusion": "failure",
                     "created_at": "2026-11-16T21:00:00Z"},
                    {"name": "pruebas", "head_branch": "main", "status": "completed", "conclusion": "success",
                     "created_at": "2026-11-15T21:00:00Z"}]})))
            else:
                responder(ident, error={"code": -32601, "message": f"no existe {nombre}"})
        else:
            responder(ident, error={"code": -32601, "message": "metodo desconocido"})


if "--servidor-falso" in sys.argv:
    servidor_falso()
    sys.exit(0)


os.environ["JARVIS_VERIFICAR_FIRMA"] = "false"
os.environ["JARVIS_AVISOS"] = "0"
TMP = Path(tempfile.mkdtemp(prefix="jarvis-mcp-"))
os.environ["JARVIS_DATOS"] = str(TMP)
REGISTRO = TMP / "pedidos.jsonl"
os.environ["MCP_REGISTRO"] = str(REGISTRO)

import tareas            # noqa: E402
tareas.usar_base(TMP / "jarvis.db")
import mcp_cliente       # noqa: E402
import nlu               # noqa: E402
import registro          # noqa: E402
import seguridad         # noqa: E402
from tools import externas   # noqa: E402

fallos: list[str] = []


def comprobar(condicion: bool, texto: str) -> None:
    print(f"  {'OK   ' if condicion else 'FALLO'}  {texto}")
    if not condicion:
        fallos.append(texto)


sys.modules["ollama"] = types.SimpleNamespace(chat=lambda **k: {"message": {"content": "Listo."}},
                                              embeddings=lambda *a, **k: (_ for _ in ()).throw(RuntimeError()),
                                              list=lambda: {"models": []}, generate=lambda **k: None)
registro._vectores = None

ESTE = str(Path(__file__).resolve())


def falso(modo: str, **extra) -> dict:
    return {"comando": [sys.executable, ESTE, "--servidor-falso"], "entorno": {"MCP_FALSO": modo}, **extra}


CONFIG = {"web": falso("web"), "youtube": falso("youtube"),
          "github": falso("github", token={"variable": "JARVIS_GITHUB_TOKEN", "como": "GITHUB_PERSONAL_ACCESS_TOKEN"})}


def pedidos() -> list[dict]:
    if not REGISTRO.exists():
        return []
    return [json.loads(l) for l in REGISTRO.read_text(encoding="utf-8").splitlines() if l.strip()]


def probar_cliente() -> None:
    print("\nARRANCA CUANDO HACE FALTA Y SE APAGA SOLO")
    comprobar(mcp_cliente.encendidos() == [], "al empezar no hay ningún servidor encendido")
    comprobar((TMP / "mcp.json").exists() or True, "mcp.json se crea con los de serie")
    (TMP / "mcp.json").write_text(json.dumps({"servidores": CONFIG}), encoding="utf-8")
    texto = mcp_cliente.llamar("web", "fetch", {"url": "https://ejemplo.com/arepas", "max_length": 100, "no_existe": 1})
    comprobar("Receta facil" in texto, "llama a la herramienta y devuelve su texto")
    comprobar(mcp_cliente.encendidos() == ["web"], "solo arrancó el que se usó")
    p = pedidos()
    comprobar([x["method"] for x in p][:3] == ["initialize", "notifications/initialized", "tools/list"],
              "handshake del protocolo en orden")
    comprobar("no_existe" not in p[-1]["params"]["arguments"], "los argumentos que la herramienta no declara se quitan")
    pid = p[-1]["pid"]
    mcp_cliente.llamar("web", "fetch", {"url": "https://ejemplo.com/otra"})
    comprobar(pedidos()[-1]["pid"] == pid, "la segunda llamada reutiliza el mismo proceso")
    comprobar(mcp_cliente.apagar_inactivos(60) == 0, "usado hace nada: sigue encendido")
    time.sleep(0.3)
    comprobar(mcp_cliente.apagar_inactivos(0.1) == 1 and mcp_cliente.encendidos() == [], "sin uso, se apaga")

    print("\nFALLOS")
    try:
        mcp_cliente.llamar("web", "fetch", {"url": "https://ejemplo.com/muere"})
        comprobar(False, "si se cae a mitad, lo dice")
    except mcp_cliente.ErrorMCP as e:
        comprobar("cerr" in str(e), f"si se cae a mitad, lo dice ('{e}')")
    texto = mcp_cliente.llamar("web", "fetch", {"url": "https://ejemplo.com/vuelve"})
    comprobar("Receta" in texto, "y en la siguiente llamada arranca de nuevo")
    try:
        mcp_cliente.llamar("web", "fetch", {"url": "https://ejemplo.com/lenta"}, espera=1)
        comprobar(False, "si tarda demasiado, corta")
    except mcp_cliente.ErrorMCP as e:
        comprobar("tardó" in str(e), "si tarda demasiado, corta")
    mcp_cliente.apagar_todos()
    try:
        mcp_cliente.llamar("web", "no_hay", {})
        comprobar(False, "herramienta inexistente")
    except mcp_cliente.ErrorMCP as e:
        comprobar("no tiene no_hay" in str(e), "herramienta inexistente: lo dice")
    try:
        mcp_cliente.llamar("web", "fetch", {})
        comprobar(False, "falta un argumento obligatorio")
    except mcp_cliente.ErrorMCP as e:
        comprobar("falta: url" in str(e), "falta un argumento obligatorio: lo dice sin llamar")

    os.environ.pop("JARVIS_GITHUB_TOKEN", None)
    try:
        mcp_cliente.llamar("github", "get_me", {})
        comprobar(False, "sin token")
    except mcp_cliente.ErrorMCP as e:
        comprobar("JARVIS_GITHUB_TOKEN" in str(e) and "github" not in mcp_cliente.encendidos(),
                  "sin token de GitHub, ni lo arranca y dice qué falta")
    os.environ["JARVIS_GITHUB_TOKEN"] = "github_pat_" + "x" * 60
    os.environ["JARVIS_TOKEN_LOCAL"] = "secreto-de-jarvis-123"
    entorno, falta = mcp_cliente._entorno(CONFIG["github"])
    comprobar(not falta and entorno["GITHUB_PERSONAL_ACCESS_TOKEN"].startswith("github_pat_")
              and "JARVIS_TOKEN_LOCAL" not in entorno and "JARVIS_GITHUB_TOKEN" not in entorno,
              "el servidor recibe su token y ningún otro secreto de Jarvis")

    sin = {"comando": ["{mcp}/no-existe.exe", "stdio"], "instalar": ".\\scripts\\instalar_mcp.ps1"}
    comprobar(mcp_cliente.comando_de("x", sin) is None, "un ejecutable que no está = no instalado")
    (TMP / "mcp.json").write_text(json.dumps({"servidores": {**CONFIG, "nada": sin}}), encoding="utf-8")
    try:
        mcp_cliente.llamar("nada", "x", {})
        comprobar(False, "sin instalar")
    except mcp_cliente.ErrorMCP as e:
        comprobar("instalar_mcp.ps1" in str(e), "sin instalar: dice cómo instalarlo")
    estado = mcp_cliente.estado()
    comprobar("nada: sin instalar (.\\scripts\\instalar_mcp.ps1)" in estado and "youtube: listo" in estado,
              f"estado: '{estado[-90:]}'")
    (TMP / "mcp.json").write_text(json.dumps({"servidores": CONFIG}), encoding="utf-8")
    real = mcp_cliente.POR_DEFECTO["github"]["comando"]
    comprobar("--read-only" in real, "el de GitHub de serie va en solo lectura")


def probar_paginas() -> None:
    print("\nPÁGINAS WEB")
    import socket
    original = socket.getaddrinfo

    def dns(host, *a, **k):
        if host == "ejemplo.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]
        return original(host, *a, **k)

    socket.getaddrinfo = dns
    try:
        _probar_paginas()
    finally:
        socket.getaddrinfo = original


def _probar_paginas() -> None:
    r = externas.leer_pagina("lee esto https://ejemplo.com/arepas.")
    comprobar("Receta facil" in r, "saca el enlace de la frase y lee la página")
    for url in ("http://127.0.0.1:8000/chat", "http://192.168.1.1", "file:///C:/Windows/win.ini"):
        r = externas.leer_pagina(url)
        comprobar(r.startswith("No leo esa dirección"), f"no lee {url}")
    comprobar("desde 8000" in externas.leer_pagina("https://ejemplo.com/a", desde=8000), "puede seguir leyendo")
    h = registro.obtener("leer_pagina")
    comprobar(h.riesgo == "lectura" and h.fuente_externa, "lectura y dato externo")


def probar_youtube() -> None:
    print("\nYOUTUBE")
    llamadas = []

    def chat(**k):
        llamadas.append(k)
        return {"message": {"content": f"Resumen {len(llamadas)}."}}

    r = externas.resumen_video("https://youtu.be/abc12345XYZ", chat=chat)
    comprobar(r == "Resumen 1." and len(llamadas) == 1, "video corto: una sola pasada")
    comprobar("<dato_externo" in llamadas[0]["messages"][-1]["content"], "la transcripción va marcada como dato externo")
    llamadas.clear()
    externas.resumen_video("https://www.youtube.com/watch?v=ingles12345", chat=chat)
    comprobar("[en]" in llamadas[0]["messages"][-1]["content"], "sin subtítulos en español, usa los de inglés")
    llamadas.clear()
    r = externas.resumen_video("https://www.youtube.com/watch?v=largo123456", pregunta="las máscaras", chat=chat)
    comprobar(len(llamadas) > 3 and r.startswith("Resumen"), f"video largo: por partes y un resumen final ({len(llamadas)} pasadas)")
    comprobar(len(llamadas) <= externas.MAX_TROZOS_VIDEO + 1, "nunca más de 12 partes")
    comprobar(all("las máscaras" in k["messages"][-1]["content"] for k in llamadas), "cada parte se fija en la pregunta")
    comprobar(externas.resumen_video("https://youtu.be/sinsubs1234", chat=chat).startswith("No pude sacar el texto"),
              "sin subtítulos, lo dice")
    comprobar(externas.resumen_video("https://vimeo.com/123", chat=chat) == "Eso no parece un enlace de YouTube.",
              "lo que no es YouTube no se intenta")
    comprobar(externas.url_youtube("mira https://www.youtube.com/shorts/AbCdEf12345 jaja").endswith("AbCdEf12345"),
              "reconoce shorts")


def probar_github() -> None:
    print("\nGITHUB, SOLO LECTURA")
    os.environ["JARVIS_GITHUB_REPO"] = "estudiante/jarvis"
    r = externas.github_pruebas()
    comprobar(r.startswith("Ojo, en jarvis hay 1 fallando") and "pasó" in r, f"pruebas ('{r[:60]}')")
    p = [x for x in pedidos() if x.get("method") == "tools/call"][-1]["params"]
    comprobar(p["name"] == "actions_list" and p["arguments"].get("method") == "list_workflow_runs",
              "si el servidor agrupa las acciones, usa la nueva forma")
    r = externas.github_issues()
    comprobar("#7 El chat no carga" in r and "2 issues abiertos" in r, f"issues ('{r[:50]}')")
    p = [x for x in pedidos() if x.get("method") == "tools/call"][-1]["params"]["arguments"]
    comprobar(p["state"] == "OPEN", "ajusta 'open' a lo que espera el servidor")
    comprobar(externas.github_pull_requests("otra") == "otra no tiene pull requests abiertos.",
              "repo sin usuario: lo saca de tu cuenta")
    comprobar([x for x in pedidos() if x.get("method") == "tools/call"][-1]["params"]["arguments"]["owner"] == "estudiante",
              "el dueño viene de get_me")
    r = externas.github_commits("https://github.com/alguien/proyecto")
    comprobar(r.startswith("Últimos cambios en proyecto: Fase 7.2: memoria (2026-11-16)"), f"commits ('{r[:60]}')")
    os.environ.pop("JARVIS_GITHUB_REPO")
    comprobar("JARVIS_GITHUB_REPO" in externas.github_issues(""), "sin repo de siempre, pide cuál")
    for nombre in ("github_pruebas", "github_issues", "github_pull_requests", "github_commits", "video_youtube"):
        h = registro.obtener(nombre)
        comprobar(h is not None and h.riesgo == "lectura" and h.fuente_externa, f"{nombre}: lectura y dato externo")


def probar_router() -> None:
    print("\nDESDE EL CHAT Y LA VOZ")
    import ollama_client
    original = ollama_client.chat_modelo
    ollama_client.chat_modelo = lambda **k: {"message": {"content": "El video explica subnetting."}}
    try:
        r = nlu.enrutar("resume este video https://youtu.be/abc12345XYZ")
        comprobar(r == "El video explica subnetting.", f"'resume este video <enlace>' ('{r}')")
    finally:
        ollama_client.chat_modelo = original
    r = nlu.enrutar("estado de las herramientas externas")
    comprobar(r.startswith("Herramientas externas:") and "web:" in r, "estado de las herramientas externas")
    externas.leer_pagina("https://ejemplo.com/x")
    comprobar("web" in mcp_cliente.encendidos(), "antes de liberar, 'web' está encendido")
    nlu.enrutar("libera la memoria")
    comprobar(mcp_cliente.encendidos() == [], "'libera la memoria' también apaga los servidores MCP")


def main() -> int:
    print("=" * 70)
    print("  FASE 7.3: HERRAMIENTAS EXTERNAS POR MCP")
    print("=" * 70)
    registro.cargar()
    try:
        probar_cliente()
        probar_paginas()
        probar_youtube()
        probar_github()
        probar_router()
    finally:
        mcp_cliente.apagar_todos()

    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS")
        for f in fallos:
            print("   -", f)
        return 1
    print("  TODO CORRECTO. Usa lo de fuera solo cuando hace falta.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
