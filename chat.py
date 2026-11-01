"""El chat local: Jarvis sin el limite de Alexa (Fase 6.1).

Una pagina en el mismo servidor, /chat, para hablarle por escrito desde el
PC o desde el celular. Usa el mismo camino que Alexa (router, capa
semantica, modelo), la misma conversacion, el mismo registro de herramientas
y las mismas reglas de riesgo. Lo que cambia es el canal:

- Sin los 8 segundos: la orden espera al modelo lo que haga falta.
- Sin recortar a 600 caracteres, y el modelo puede extenderse mas.
- Se ve cada paso en vivo: que herramientas tuvo a mano, cual uso, que
  devolvio, si el freno paro algo, si la comprobacion fallo.
- Lo que pide confirmacion se aprueba con un boton.

Quien puede entrar:
- Solo desde este PC o desde tu red de Tailscale (100.64.0.0/10). Lo que
  llega por el Funnel publico trae sus cabeceras y recibe un 404, como si
  la ruta no existiera.
- Y solo con la llave: el PC entra con la del .env (chat.ps1 abre el enlace);
  el celular, con un codigo de 6 cifras que el PC muestra durante 5 minutos.
"""

import hashlib
import hmac
import ipaddress
import logging
import secrets
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import confirmaciones
import modes
import tareas
from config import TOKEN_LOCAL

log = logging.getLogger("jarvis.chat")
router = APIRouter()

COOKIE = "jarvis_chat"
PAGINA = Path(__file__).with_name("chat.html")
PRESUPUESTO_CHAT = 900.0            # 15 min: aqui nadie corta a los 8 s
SEGUNDOS_ESPERA_EVENTOS = 20
MINUTOS_FONDO = 10                  # cuanto se sigue una tarea de fondo en la pagina
SEGUNDOS_CODIGO = 300
INTENTOS_CODIGO = 5
MAX_CORRIDAS = 60

# Cabeceras que pone un proxy delante (Tailscale Funnel, tailscale serve,
# ngrok). Las mismas que mira /probar.
CABECERAS_DE_TUNEL = ("tailscale-funnel-request", "x-forwarded-for", "x-forwarded-host", "forwarded")
REDES_LOCALES = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))
REDES_TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))

# Lo que el log dice en cada orden y no aporta nada en pantalla.
_RUIDO = ("Comando recibido", "Resuelto por", "Respuesta lista", "Respondemos", "HTTP Request")

_procesar = None                    # server.procesar_con_origen, lo inyecta el servidor
_lock = threading.Lock()
_corridas: dict = {}
_codigos: dict = {}


def configurar(procesar) -> None:
    global _procesar
    _procesar = procesar


# ---------------------------------------------------------------------------
# Quien entra
# ---------------------------------------------------------------------------
def desde_donde(request) -> str | None:
    """'local', 'tailnet' o None (de fuera, o detras de un proxy)."""
    for cabecera in CABECERAS_DE_TUNEL:
        if cabecera in request.headers:
            return None
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if any(ip in red for red in REDES_LOCALES):
        return "local"
    if any(ip in red for red in REDES_TAILNET):
        return "tailnet"
    return None


def _llave_chat() -> str:
    """Lo que guarda la cookie: sale de la llave del .env pero no es ella."""
    if not TOKEN_LOCAL:
        return ""
    return hmac.new(TOKEN_LOCAL.encode("utf-8"), b"jarvis-chat-v1", hashlib.sha256).hexdigest()


def autorizado(request) -> bool:
    llave = _llave_chat()
    return bool(llave and desde_donde(request)
                and hmac.compare_digest(request.cookies.get(COOKIE, "").encode(), llave.encode()))


def _exigir(request, escribir: bool = False) -> str:
    lugar = desde_donde(request)
    if not lugar:
        log.warning("Alguien pidio %s desde fuera (%s). Rechazado.", request.url.path,
                    request.client.host if request.client else "?")
        raise HTTPException(status_code=404)
    if not autorizado(request):
        raise HTTPException(status_code=401, detail="Falta vincular este navegador.")
    # Contra que otra pagina mande ordenes en tu nombre: el navegador no deja
    # poner esta cabecera desde otro sitio sin permiso.
    if escribir and request.headers.get("x-jarvis-chat") != "1":
        raise HTTPException(status_code=403)
    return lugar


def _con_cookie(respuesta):
    respuesta.set_cookie(COOKIE, _llave_chat(), max_age=365 * 86400, httponly=True, samesite="strict")
    return respuesta


def nuevo_codigo() -> str:
    codigo = f"{secrets.randbelow(10**6):06d}"
    with _lock:
        ahora = time.time()
        for c in [c for c, v in _codigos.items() if v["expira"] < ahora]:
            _codigos.pop(c, None)
        _codigos[codigo] = {"expira": ahora + SEGUNDOS_CODIGO, "intentos": 0}
    return codigo


def gastar_codigo(codigo: str) -> bool:
    """Un codigo vale una vez, 5 minutos. Cinco fallos y se invalidan todos."""
    codigo = (codigo or "").strip().replace(" ", "")
    with _lock:
        ahora = time.time()
        if codigo in _codigos and _codigos[codigo]["expira"] >= ahora:
            _codigos.pop(codigo)
            return True
        for v in _codigos.values():
            v["intentos"] += 1
        for c in [c for c, v in _codigos.items() if v["intentos"] >= INTENTOS_CODIGO]:
            _codigos.pop(c, None)
    return False


def ip_tailnet() -> str | None:
    try:
        salida = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ip = (salida.stdout or "").strip().splitlines()[0].strip()
        ipaddress.ip_address(ip)
        return ip
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Una orden y lo que va pasando
# ---------------------------------------------------------------------------
class _Corrida:
    def __init__(self, texto: str):
        self.id = uuid.uuid4().hex[:10]
        self.texto = texto
        self.eventos: list[dict] = []
        self.terminado = False
        self.respuesta = ""
        self.creado = time.time()
        self.cond = threading.Condition()

    def emitir(self, tipo: str, **datos) -> None:
        with self.cond:
            self.eventos.append({"tipo": tipo, "t": round(time.time() - self.creado, 2), **datos})
            self.cond.notify_all()

    def terminar(self) -> None:
        with self.cond:
            self.terminado = True
            self.cond.notify_all()

    def leer(self, desde: int, espera: float) -> tuple[list, bool]:
        fin = time.time() + espera
        with self.cond:
            while len(self.eventos) <= desde and not self.terminado:
                restante = fin - time.time()
                if restante <= 0:
                    break
                self.cond.wait(restante)
            return list(self.eventos[desde:]), self.terminado


def _clase(mensaje: str, modulo: str) -> str:
    m = mensaje
    if m.startswith("FRENO") or "No ejecuto" in m:
        return "freno"
    if "VERIFICACION FALLIDA" in m:
        return "verificacion"
    if "Insisto" in m or "insistir" in m or "lo hago yo" in m:
        return "insiste"
    if m.startswith("Ofrezco"):
        return "ofrecidas"
    if m.startswith("Ejecutando herramienta"):
        return "ejecutando"
    if "Intent resuelto" in m or modulo == "jarvis.intencion":
        return "router"
    return "info"


class _Captura(logging.Handler):
    """Lo que Jarvis apunta en el log mientras resuelve ESTA orden, y nada mas."""

    def __init__(self, corrida: _Corrida, hilo: int):
        super().__init__(level=logging.INFO)
        self.corrida = corrida
        self.hilo = hilo

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.hilo or record.name == "jarvis.chat":
            return
        try:
            mensaje = record.getMessage()
        except Exception:
            return
        if any(mensaje.startswith(r) for r in _RUIDO):
            return
        self.corrida.emitir("log", clase=_clase(mensaje, record.name), modulo=record.name,
                            nivel=record.levelname, mensaje=mensaje[:700])


def _corriendo_desde(inicio: float) -> list[dict]:
    limite = datetime.fromtimestamp(inicio - 1).isoformat(timespec="seconds")
    return [t for t in tareas.historial(20)
            if t["inicio"] >= limite and t["estado"] in ("corriendo", "en_cola") and t["origen"] != "chat"]


def _correr(corrida: _Corrida) -> None:
    import ollama_client
    raiz = logging.getLogger("jarvis")
    captura = _Captura(corrida, threading.get_ident())
    raiz.addHandler(captura)
    inicio = time.time()
    corrida.emitir("inicio", texto=corrida.texto, modo=modes.modo_actual(),
                   modelo=modes.perfil_actual()["modelo"])

    def _paso(herramienta, argumentos, resultado):
        corrida.emitir("paso", herramienta=herramienta, argumentos=argumentos,
                       resultado=str(resultado)[:2000])

    try:
        tareas.observar_hilo(_paso)
        respuesta, origen = ollama_client.en_canal(
            "chat", _procesar, corrida.texto, None, PRESUPUESTO_CHAT, en_este_hilo=True)
        corrida.respuesta = respuesta
        corrida.emitir("respuesta", texto=respuesta, origen=origen,
                       ms=round((time.time() - inicio) * 1000), confirmacion=confirmaciones.pendiente())
    except Exception as e:
        log.exception("Fallo la orden del chat")
        corrida.respuesta = f"Tuve un problema con esa orden: {e}"
        corrida.emitir("error", texto=corrida.respuesta)
    finally:
        tareas.observar_hilo(None)
        raiz.removeHandler(captura)

    # Lo que la orden dejo trabajando al fondo se sigue aqui hasta que acabe.
    try:
        pendientes = {t["id"]: t["descripcion"] for t in _corriendo_desde(inicio)}
        for descripcion in pendientes.values():
            corrida.emitir("fondo", descripcion=descripcion)
        fin = time.time() + MINUTOS_FONDO * 60
        while pendientes and time.time() < fin:
            time.sleep(1)
            for ident in list(pendientes):
                t = tareas.obtener(ident)
                if t and t["estado"] not in ("corriendo", "en_cola"):
                    corrida.emitir("fondo_hecho", descripcion=pendientes.pop(ident),
                                   resultado=t.get("resultado") or "", estado=t["estado"])
                    tareas.marcar_leida(ident)      # ya se vio aqui: no hace falta "Por cierto"
    except Exception as e:
        log.debug("No pude seguir la tarea de fondo: %s", e)
    corrida.terminar()


def lanzar(texto: str) -> _Corrida:
    corrida = _Corrida(texto)
    with _lock:
        _corridas[corrida.id] = corrida
        for viejo in sorted(_corridas, key=lambda k: _corridas[k].creado)[:-MAX_CORRIDAS]:
            _corridas.pop(viejo, None)
    threading.Thread(target=_correr, args=(corrida,), daemon=True, name=f"chat-{corrida.id}").start()
    return corrida


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
_SEGURIDAD = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                "connect-src 'self'; frame-ancestors 'none'"),
}

_SIN_LLAVE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jarvis · vincular</title>
<style>
:root{color-scheme:light dark;--fondo:#f6f5f2;--texto:#1d1d1b;--suave:#6b6a66;--borde:#d9d7d0;--acento:#2f6f5e}
@media (prefers-color-scheme:dark){:root{--fondo:#161615;--texto:#ecebe7;--suave:#9b9a95;--borde:#33322f;--acento:#6fc2a9}}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--fondo);color:var(--texto);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
main{max-width:340px;width:100%}h1{font-size:22px;margin:0 0 8px}p{color:var(--suave);margin:0 0 20px}
input{width:100%;box-sizing:border-box;font:600 28px/1 ui-monospace,Consolas,monospace;letter-spacing:.3em;
text-align:center;padding:14px;border:1px solid var(--borde);border-radius:10px;background:transparent;color:inherit}
button{margin-top:12px;width:100%;padding:13px;border:0;border-radius:10px;background:var(--acento);color:#fff;font:600 16px system-ui}
#error{color:#c0392b;min-height:1.5em;margin-top:10px}
</style></head><body><main>
<h1>Vincular con Jarvis</h1>
<p>En el chat del PC pulsa <b>Vincular celular</b> y escribe aquí el código de 6 cifras.</p>
<form id="f"><input id="c" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required autofocus>
<button>Vincular</button></form><div id="error"></div>
<script>
document.getElementById('f').onsubmit=async e=>{e.preventDefault();
 const r=await fetch('/chat/vincular',{method:'POST',headers:{'Content-Type':'application/json','X-Jarvis-Chat':'1'},
  body:JSON.stringify({codigo:document.getElementById('c').value})});
 if(r.ok){location.href='/chat'}else{document.getElementById('error').textContent='Código no válido o caducado. Pide otro en el PC.'}};
</script></main></body></html>"""


@router.get("/chat")
def pagina(request: Request):
    lugar = desde_donde(request)
    if not lugar:
        raise HTTPException(status_code=404)
    llave = request.query_params.get("llave")
    if llave:
        if TOKEN_LOCAL and hmac.compare_digest(llave.encode(), TOKEN_LOCAL.encode()):
            log.info("Chat vinculado desde %s (%s) con la llave del .env.", lugar, request.client.host)
            return _con_cookie(RedirectResponse("/chat", status_code=303))
        log.warning("Llave del chat no valida desde %s.", request.client.host)
    if not autorizado(request):
        return HTMLResponse(_SIN_LLAVE, status_code=401, headers=_SEGURIDAD)
    try:
        html = PAGINA.read_text(encoding="utf-8")
    except Exception as e:
        return HTMLResponse(f"No encuentro chat.html: {e}", status_code=500)
    return HTMLResponse(html, headers=_SEGURIDAD)


@router.post("/chat/vincular")
def vincular(request: Request, cuerpo: dict):
    lugar = desde_donde(request)
    if not lugar:
        raise HTTPException(status_code=404)
    if request.headers.get("x-jarvis-chat") != "1":
        raise HTTPException(status_code=403)
    if not gastar_codigo(str((cuerpo or {}).get("codigo", ""))):
        log.warning("Codigo de vinculacion no valido desde %s.", request.client.host)
        raise HTTPException(status_code=401)
    log.info("Chat vinculado desde %s (%s) con codigo.", lugar, request.client.host)
    return _con_cookie(JSONResponse({"ok": True}))


@router.post("/chat/codigo")
def codigo(request: Request):
    """El PC pide un codigo para vincular el celular."""
    lugar = _exigir(request, escribir=True)
    if lugar != "local":
        raise HTTPException(status_code=403, detail="El código se pide desde el PC.")
    ip = ip_tailnet()
    puerto = request.url.port or 8000
    return {"codigo": nuevo_codigo(), "segundos": SEGUNDOS_CODIGO,
            "direccion": f"http://{ip}:{puerto}/chat" if ip else None}


@router.post("/chat/orden")
def orden(request: Request, cuerpo: dict):
    _exigir(request, escribir=True)
    if _procesar is None:
        raise HTTPException(status_code=503, detail="El chat no está listo.")
    texto = str((cuerpo or {}).get("texto", "")).strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Falta el texto.")
    corrida = lanzar(texto[:2000])
    return {"id": corrida.id}


@router.get("/chat/eventos/{ident}")
def eventos(request: Request, ident: str, desde: int = 0):
    _exigir(request)
    corrida = _corridas.get(ident)
    if corrida is None:
        raise HTTPException(status_code=404)
    nuevos, terminado = corrida.leer(max(0, desde), SEGUNDOS_ESPERA_EVENTOS)
    return {"eventos": nuevos, "siguiente": desde + len(nuevos), "terminado": terminado}


@router.get("/chat/estado")
def estado(request: Request):
    lugar = _exigir(request)
    perfil = modes.perfil_actual()
    recientes = []
    for t in tareas.historial(8):
        recientes.append({"descripcion": t["descripcion"], "estado": t["estado"], "origen": t["origen"],
                          "inicio": t["inicio"], "resultado": (t.get("resultado") or "")[:300]})
    with _lock:
        conversacion = [{"id": c.id, "texto": c.texto, "respuesta": c.respuesta, "terminado": c.terminado}
                        for c in sorted(_corridas.values(), key=lambda c: c.creado)][-30:]
    return {"modo": modes.modo_actual(), "modelo": perfil["modelo"], "lugar": lugar,
            "tareas": recientes, "confirmacion": confirmaciones.pendiente(), "conversacion": conversacion}
