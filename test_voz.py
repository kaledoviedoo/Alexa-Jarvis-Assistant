"""Pruebas de la Fase 6.2: voz sin Alexa.

Sin microfono, sin Whisper y sin Piper: se prueba la logica (cuando dejar de
grabar, que se dice en voz alta, el camino de una orden hablada, las rutas del
chat) con piezas falsas.

Ejecutar:  py test_voz.py
"""

import os
import sys
import tempfile
import types
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ["JARVIS_VERIFICAR_FIRMA"] = "false"
os.environ["JARVIS_AVISOS"] = "0"
os.environ.setdefault("JARVIS_TOKEN_LOCAL", "llave-de-prueba-1234567890")

TMP = Path(tempfile.mkdtemp(prefix="jarvis-voz-"))

import tareas            # noqa: E402
tareas.usar_base(TMP / "jarvis.db")
import config            # noqa: E402
if not config.TOKEN_LOCAL:
    config.TOKEN_LOCAL = os.environ["JARVIS_TOKEN_LOCAL"]
import chat              # noqa: E402
chat.TOKEN_LOCAL = config.TOKEN_LOCAL
import confirmaciones    # noqa: E402
import ollama_client     # noqa: E402
import server            # noqa: E402
import voz_local         # noqa: E402
from fastapi.testclient import TestClient   # noqa: E402

fallos: list[str] = []


def comprobar(condicion: bool, texto: str) -> None:
    print(f"  {'OK   ' if condicion else 'FALLO'}  {texto}")
    if not condicion:
        fallos.append(texto)


def main() -> int:
    print("=" * 70)
    print("  FASE 6.2: VOZ SIN ALEXA")
    print("=" * 70)

    print("\nDEJA DE GRABAR CUANDO TE CALLAS")
    juez = voz_local.Silencio()
    salida = [juez.alimentar(0.0) for _ in range(20)]               # 1 s callado
    salida += [juez.alimentar(0.08) for _ in range(30)]             # 1,5 s hablando
    salida += [juez.alimentar(0.002) for _ in range(30)]            # y te callas
    comprobar("fin" in salida, "hablar y callarse 1,2 s termina la grabacion")
    comprobar(salida.index("fin") == 20 + 30 + 23, f"justo a los 1,2 s de silencio (trozo {salida.index('fin')})")
    juez = voz_local.Silencio()
    salida = [juez.alimentar(0.003) for _ in range(200)]
    comprobar(salida[-1] == "nada" and salida.index("nada") == 119, "si en 6 s no dices nada, se cancela")
    juez = voz_local.Silencio()
    salida = [juez.alimentar(0.05) for _ in range(500)]
    comprobar("fin" in salida and salida.index("fin") == 399, "nunca mas de 20 s")
    juez = voz_local.Silencio()
    salida = [juez.alimentar(0.08) for _ in range(3)] + [juez.alimentar(0.0) for _ in range(150)]
    comprobar("fin" not in salida and "nada" in salida, "un golpe suelto no cuenta como hablar")

    print("\nLO QUE SE DICE EN VOZ ALTA")
    t = voz_local.texto_para_hablar("**Listo.** Mira https://github.com/x/y\n- uno\n- dos")
    comprobar(t == "Listo. Mira un enlace. uno. dos", f"sin markdown ni enlaces ('{t}')")
    largo = voz_local.texto_para_hablar("Una frase cualquiera. " * 100)
    comprobar(len(largo) < voz_local.MAX_CARACTERES_HABLADOS + 40 and largo.endswith("El resto lo tienes en el chat."),
              "lo muy largo se corta en una frase y avisa")
    comprobar(voz_local.limpiar_transcripcion(" Gracias por ver el video. ") == "", "Whisper inventando con ruido: se ignora")
    comprobar(voz_local.limpiar_transcripcion("...") == "", "solo puntos: nada")
    comprobar(voz_local.limpiar_transcripcion(" abre spotify. ") == "abre spotify", "una orden normal pasa limpia")

    print("\nUNA ORDEN HABLADA DE PRINCIPIO A FIN")
    sonado, canales = [], []

    def procesar(texto, datos, presupuesto, en_este_hilo=False):
        canales.append((ollama_client.canal_actual(), presupuesto, en_este_hilo))
        return f"Hecho: {texto}.", "router"

    r = voz_local.atender(b"audio", oir=lambda a: "abre spotify", hablar=lambda t: b"WAV:" + t.encode(),
                         sonar=sonado.append, procesar=procesar)
    comprobar(r["texto"] == "abre spotify" and r["respuesta"] == "Hecho: abre spotify.", "oye, resuelve y contesta")
    comprobar(canales == [("voz", voz_local.PRESUPUESTO, True)], f"como voz y sin el limite de Alexa ({canales})")
    comprobar(sonado == [b"WAV:Hecho: abre spotify."], "y lo dice por los altavoces")
    comprobar(tareas.historial_chat()[-1]["texto"] == "abre spotify", "queda en el historial del chat")
    comprobar(not r["escuchar_otra_vez"], "sin nada que confirmar, no vuelve a escuchar")

    sonado.clear()
    r = voz_local.atender(b"ruido", oir=lambda a: "", hablar=lambda t: b"x", sonar=sonado.append,
                         procesar=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no deberia")))
    comprobar(r["respuesta"] is None and not sonado, "si no entendio nada, no inventa una orden")

    def pide_confirmar(texto, *a, **k):
        return confirmaciones.pedir("mandarle a familia: llego en 10", lambda: "Enviado."), "modelo"

    r = voz_local.atender(b"a", oir=lambda a: "mandale a familia que llego en 10", hablar=lambda t: None,
                         sonar=sonado.append, procesar=pide_confirmar)
    comprobar(r["escuchar_otra_vez"], "si queda un '¿lo envio?', escucha el si sin pulsar el atajo")
    confirmaciones.olvidar()

    print("\nEL ATAJO: PULSAR PARA HABLAR, PULSAR PARA PARAR")
    voz_local._estado.update(grabando=True)
    voz_local._detener_grabacion.clear()
    voz_local.al_atajo()
    comprobar(voz_local._detener_grabacion.is_set(), "grabando: el atajo para la grabacion")
    voz_local._estado.update(grabando=False, pensando=True)
    antes = voz_local._hilo_turno
    voz_local.al_atajo()
    comprobar(voz_local._hilo_turno is antes, "pensando: el atajo no empieza otra orden encima")
    voz_local._estado.update(pensando=False)

    print("\nSIN LIBRERIAS, JARVIS SIGUE Y DICE QUE FALTA")
    original = voz_local._hay
    voz_local._hay = lambda m: False
    linea = voz_local.arrancar()
    comprobar("faster-whisper" in linea and "instalar_voz.ps1" in linea, f"arranque: '{linea}'")
    comprobar(voz_local.estado()["oir"] is False, "y el chat sabe que no puede oir")
    voz_local._hay = original

    print("\nEL MICROFONO DEL CHAT")
    pc = TestClient(server.app, client=("127.0.0.1", 50000))
    pc.get(f"/chat?llave={config.TOKEN_LOCAL}", follow_redirects=False)
    pc.cookies.set(chat.COOKIE, chat._llave_chat())
    h = {"X-Jarvis-Chat": "1", "Content-Type": "audio/webm"}
    funnel = TestClient(server.app, client=("127.0.0.1", 50001))
    comprobar(funnel.post("/chat/escuchar", content=b"x", headers={**h, "Tailscale-Funnel-Request": "?1"}).status_code == 404,
              "por el Funnel, el microfono no existe")
    comprobar(pc.post("/chat/escuchar", content=b"x", headers={"Content-Type": "audio/webm"}).status_code == 403,
              "sin la cabecera del chat, no")
    voz_local_original = (voz_local.puede_oir, voz_local.transcribir, voz_local.sintetizar)
    voz_local.puede_oir = lambda: False
    comprobar(pc.post("/chat/escuchar", content=b"x", headers=h).status_code == 503, "sin Whisper instalado: 503 que lo explica")
    voz_local.puede_oir = lambda: True
    voz_local.transcribir = lambda audio: f"oí {len(audio)} bytes"
    r = pc.post("/chat/escuchar", content=b"12345", headers=h)
    comprobar(r.status_code == 200 and r.json()["texto"] == "oí 5 bytes", "con Whisper: el audio pasa a texto")
    comprobar(pc.post("/chat/escuchar", content=b"0" * (chat.MAX_AUDIO + 1), headers=h).status_code == 413,
              "un audio enorme se rechaza")
    voz_local.sintetizar = lambda texto: b"RIFF....WAVE" + texto.encode()
    r = pc.post("/chat/hablar", json={"texto": "hola"}, headers={"X-Jarvis-Chat": "1"})
    comprobar(r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content.endswith(b"hola"),
              "la respuesta en voz alta llega como WAV")
    voz_local.sintetizar = lambda texto: None
    comprobar(pc.post("/chat/hablar", json={"texto": "hola"}, headers={"X-Jarvis-Chat": "1"}).status_code == 503,
              "sin voz instalada: 503")
    voz_local.puede_oir, voz_local.transcribir, voz_local.sintetizar = voz_local_original
    s = pc.get("/chat/estado").json()
    comprobar(set(s["voz"]) == {"oir", "hablar", "atajo"}, "el estado dice que voz hay")
    comprobar("media-src 'self' blob:" in pc.get("/chat").headers.get("content-security-policy", ""),
              "la pagina puede reproducir el audio que genera")

    print("\nWHISPER RECIBE LO QUE TIENE QUE RECIBIR")
    visto = {}

    class _Falso:
        def transcribe(self, audio, **k):
            visto.update(k, audio=audio)
            return iter([types.SimpleNamespace(text=" Abre "), types.SimpleNamespace(text="Obsidian. ")]), None

    voz_local._whisper = _Falso()
    texto = voz_local.transcribir(b"webm")
    comprobar(texto == "Abre Obsidian", f"une los trozos ('{texto}')")
    comprobar(visto["language"] == "es" and visto["vad_filter"] and "Obsidian" in visto["initial_prompt"],
              "en espanol, sin silencios, y con la pista de nombres propios")
    comprobar(isinstance(visto["audio"], str) and not Path(visto["audio"]).exists(),
              "el audio del navegador pasa por un archivo temporal que luego se borra")
    voz_local._whisper = None

    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS")
        for f in fallos:
            print("   -", f)
        return 1
    print("  TODO CORRECTO. Te oye y te contesta sin salir de casa.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
