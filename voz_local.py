"""Voz sin Alexa (Fase 6.2): oir con Whisper y contestar con Piper, todo en el PC.

Pulsas el atajo (Ctrl+Alt+J por defecto), suena un pitido, hablas y te
callas: Jarvis nota el silencio, transcribe con Whisper, resuelve la orden por
el mismo camino que Alexa y el chat, y te contesta en voz alta. Nada sale de
casa ni pasa por Amazon, y no hay limite de 8 segundos.

- Oir: faster-whisper, en el procesador (int8) para no quitarle grafica al
  modelo. El modelo "small" pesa ~460 MB y se baja una vez.
- Hablar: Piper con una voz en espanol (~60 MB). Si Piper no esta, la voz
  de Windows, que ya viene instalada.
- El chat del navegador usa lo mismo: boton de microfono y respuesta en voz
  alta.

Todo es opcional: sin estas librerias Jarvis funciona igual, solo que sin voz
local. scripts/instalar_voz.ps1 lo instala.
"""

import base64
import io
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path

log = logging.getLogger("jarvis.voz_local")


def _env(nombre: str, defecto: str) -> str:
    return (os.environ.get(nombre) or defecto).strip()


ACTIVA = _env("JARVIS_VOZ_LOCAL", "1") not in ("0", "no", "false")
ATAJO = _env("JARVIS_VOZ_ATAJO", "ctrl+alt+j")
MODELO_WHISPER = _env("JARVIS_WHISPER_MODELO", "small")
VOZ_PIPER = _env("JARVIS_VOZ_PIPER", "es_MX-claude-high")
FRECUENCIA = 16000
MAX_SEGUNDOS = 20
MAX_CARACTERES_HABLADOS = 900
PRESUPUESTO = 300.0

# Palabras que Whisper no conoce y que aqui salen a diario: se las damos como
# pista para que escriba "Obsidian" y no "obsidiana".
PISTA = ("Jarvis, Obsidian, WhatsApp, Spotify, Valorant, Steam, Epic Games, Outlook, Teams, "
         "Chrome, GitHub, Python, PowerShell, Descargas, Documentos, modo dedicado, modo gaming.")

_procesar = None
_lock = threading.RLock()
_whisper = None
_piper = None
_estado = {"grabando": False, "hablando": False, "pensando": False}
_detener_grabacion = threading.Event()


def configurar(procesar) -> None:
    global _procesar
    _procesar = procesar


def _carpeta() -> Path:
    from config import CARPETA_DATOS
    carpeta = Path(CARPETA_DATOS) / "voz"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


# ---------------------------------------------------------------------------
# Que hay instalado
# ---------------------------------------------------------------------------
def _hay(modulo: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(modulo) is not None
    except Exception:
        return False


def puede_oir() -> bool:
    return _hay("faster_whisper")


def _ruta_voz_piper() -> Path | None:
    try:
        ruta = _carpeta() / f"{VOZ_PIPER}.onnx"
    except Exception:
        return None
    return ruta if ruta.exists() else None


def motor_de_habla() -> str | None:
    """'piper', 'windows' o None."""
    if _hay("piper") and _ruta_voz_piper():
        return "piper"
    if os.name == "nt":
        return "windows"
    return None


def faltan() -> list[str]:
    """Lo que falta para el atajo de teclado (oir, grabar y el propio atajo)."""
    return [p for p, m in (("faster-whisper", "faster_whisper"), ("sounddevice", "sounddevice"),
                           ("keyboard", "keyboard")) if not _hay(m)]


def estado() -> dict:
    return {"oir": puede_oir(), "hablar": motor_de_habla(), "atajo": ATAJO if ACTIVA else None,
            "modelo": MODELO_WHISPER, "faltan": faltan(), **_estado}


# ---------------------------------------------------------------------------
# Oir
# ---------------------------------------------------------------------------
def _modelo_whisper():
    global _whisper
    with _lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            inicio = time.time()
            _whisper = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8",
                                    download_root=str(_carpeta() / "whisper"))
            log.info("Whisper '%s' listo en %.1f s.", MODELO_WHISPER, time.time() - inicio)
        return _whisper


def transcribir(audio) -> str:
    """Audio (array float32 a 16 kHz, o bytes de cualquier formato) a texto."""
    modelo = _modelo_whisper()
    temporal = None
    try:
        if isinstance(audio, (bytes, bytearray)):
            # Lo que manda el navegador (webm/ogg): PyAV, que viene con
            # faster-whisper, lo decodifica desde un archivo.
            temporal = tempfile.NamedTemporaryFile(delete=False, suffix=".audio")
            temporal.write(audio)
            temporal.close()
            audio = temporal.name
        inicio = time.time()
        segmentos, _info = modelo.transcribe(audio, language="es", beam_size=1, vad_filter=True,
                                             initial_prompt=PISTA, condition_on_previous_text=False)
        texto = " ".join(s.text.strip() for s in segmentos).strip()
        log.info("Oí en %.1f s: %r", time.time() - inicio, texto)
        return limpiar_transcripcion(texto)
    finally:
        if temporal is not None:
            try:
                os.unlink(temporal.name)
            except OSError:
                pass


def limpiar_transcripcion(texto: str) -> str:
    """Whisper a veces inventa cuando solo hay ruido ("Gracias por ver el video")."""
    texto = (texto or "").strip()
    if re.fullmatch(r"[\s.,¡!¿?…-]*", texto):
        return ""
    alucinaciones = ("gracias por ver", "suscríbete", "subtítulos realizados", "amara.org", "gracias.")
    if any(a in texto.lower() for a in alucinaciones) and len(texto) < 60:
        return ""
    return texto.strip(" .")


# ---------------------------------------------------------------------------
# Hablar
# ---------------------------------------------------------------------------
def texto_para_hablar(texto: str) -> str:
    """Sin markdown, sin enlaces, y sin monologos interminables."""
    texto = re.sub(r"https?://\S+", "un enlace", texto or "")
    texto = re.sub(r"[*_`#>]+", "", texto)
    texto = re.sub(r"^\s*[-•]\s+", "", texto, flags=re.M)
    texto = re.sub(r"\s*\n+\s*", ". ", texto)
    texto = re.sub(r"\.(\s*\.)+", ".", texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip()
    if len(texto) > MAX_CARACTERES_HABLADOS:
        corte = texto[:MAX_CARACTERES_HABLADOS]
        punto = max(corte.rfind(". "), corte.rfind("? "), corte.rfind("! "))
        texto = (corte[:punto + 1] if punto > 200 else corte.rsplit(" ", 1)[0] + "…") + \
            " El resto lo tienes en el chat."
    return texto


def _voz_piper():
    global _piper
    with _lock:
        if _piper is None:
            from piper import PiperVoice
            _piper = PiperVoice.load(str(_ruta_voz_piper()))
            log.info("Voz Piper '%s' lista.", VOZ_PIPER)
        return _piper


def _sintetizar_windows(texto: str) -> bytes | None:
    """La voz de Windows (System.Speech), sin instalar nada."""
    salida = Path(tempfile.gettempdir()) / f"jarvis-voz-{uuid.uuid4().hex[:8]}.wav"
    codificado = base64.b64encode(texto.encode("utf-8")).decode("ascii")
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'es-*' } | Select-Object -First 1;"
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
        f"$s.SetOutputToWaveFile('{salida}');"
        f"$s.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{codificado}')));"
        "$s.Dispose()"
    )
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       timeout=60, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return salida.read_bytes() if salida.exists() else None
    finally:
        try:
            salida.unlink()
        except OSError:
            pass


def sintetizar(texto: str) -> bytes | None:
    """Texto a WAV. None si no hay con que hablar."""
    texto = texto_para_hablar(texto)
    if not texto:
        return None
    motor = motor_de_habla()
    try:
        if motor == "piper":
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                _voz_piper().synthesize_wav(texto, wav)
            return buffer.getvalue()
        if motor == "windows":
            return _sintetizar_windows(texto)
    except Exception as e:
        log.warning("No pude sintetizar con %s: %s", motor, e)
        if motor == "piper" and os.name == "nt":
            return _sintetizar_windows(texto)
    return None


def reproducir(wav: bytes) -> None:
    """Suena por los altavoces del PC. Bloquea hasta que acaba o se corta."""
    if not wav or os.name != "nt":
        return
    import winsound
    _estado["hablando"] = True
    try:
        winsound.PlaySound(wav, winsound.SND_MEMORY)
    finally:
        _estado["hablando"] = False


def callar() -> None:
    if os.name == "nt":
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
    _estado["hablando"] = False


def pitido(tipo: str) -> None:
    if os.name != "nt":
        return
    try:
        import winsound
        frecuencia, ms = {"escucho": (880, 90), "listo": (660, 90), "error": (300, 220)}[tipo]
        winsound.Beep(frecuencia, ms)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Grabar hasta que te callas
# ---------------------------------------------------------------------------
class Silencio:
    """Decide cuando dejar de grabar mirando el volumen de cada trozo de 50 ms.

    - Antes de que hables: si en 6 s no dices nada, se cancela.
    - Cuando ya hablaste: 1,2 s seguidos de silencio y se termina.
    - Nunca mas de MAX_SEGUNDOS.
    """

    TROZO = 0.05

    def __init__(self, umbral: float = 0.012, silencio_final: float = 1.2, espera_inicial: float = 6.0,
                 voz_minima: float = 0.25, maximo: float = MAX_SEGUNDOS):
        self.umbral = umbral
        self.silencio_final = silencio_final
        self.espera_inicial = espera_inicial
        self.voz_minima = voz_minima
        self.maximo = maximo
        self.t = self.voz = self.callado = 0.0
        self._n = self._nv = self._nc = 0

    def alimentar(self, volumen: float) -> str:
        """'sigue', 'fin' (habló y se calló) o 'nada' (nunca habló)."""
        # Se cuentan trozos enteros: sumar 0,05 s ciento veinte veces no da 6,0.
        self._n += 1
        if volumen >= self.umbral:
            self._nv += 1
            self._nc = 0
        else:
            self._nc += 1
        self.t = round(self._n * self.TROZO, 6)
        self.voz = round(self._nv * self.TROZO, 6)
        self.callado = round(self._nc * self.TROZO, 6)
        if self.voz >= self.voz_minima and self.callado >= self.silencio_final:
            return "fin"
        if self.voz < self.voz_minima and self.t >= self.espera_inicial:
            return "nada"
        if self.t >= self.maximo:
            return "fin" if self.voz >= self.voz_minima else "nada"
        return "sigue"


def grabar():
    """Graba del microfono hasta el silencio. Devuelve el audio o None."""
    import numpy as np
    import sounddevice as sd
    trozos, decision = [], {"valor": "sigue"}
    juez = Silencio()
    listo = threading.Event()
    _detener_grabacion.clear()

    def _callback(datos, _frames, _tiempo, _estado_sd):
        mono = datos[:, 0].copy()
        trozos.append(mono)
        volumen = float(np.sqrt(np.mean(mono ** 2))) if len(mono) else 0.0
        decision["valor"] = juez.alimentar(volumen)
        if decision["valor"] != "sigue":
            listo.set()

    _estado["grabando"] = True
    try:
        with sd.InputStream(samplerate=FRECUENCIA, channels=1, dtype="float32",
                            blocksize=int(FRECUENCIA * Silencio.TROZO), callback=_callback):
            while not listo.wait(0.05):
                if _detener_grabacion.is_set():
                    decision["valor"] = "fin" if juez.voz >= juez.voz_minima else "nada"
                    break
    finally:
        _estado["grabando"] = False
    if decision["valor"] == "nada" or not trozos:
        return None
    return np.concatenate(trozos)


# ---------------------------------------------------------------------------
# Una orden por voz, de principio a fin
# ---------------------------------------------------------------------------
def atender(audio, oir=None, hablar=None, sonar=None, procesar=None) -> dict:
    """Transcribe, resuelve y contesta. Los parametros se cambian en las pruebas."""
    import confirmaciones
    import ollama_client
    import tareas
    oir = oir or transcribir
    hablar = hablar or sintetizar
    sonar = sonar or reproducir
    procesar = procesar or _procesar

    _estado["pensando"] = True
    try:
        texto = oir(audio)
        if not texto:
            pitido("error")
            return {"texto": "", "respuesta": None}
        pitido("listo")
        inicio = time.time()
        respuesta, origen = ollama_client.en_canal("voz", procesar, texto, None, PRESUPUESTO, en_este_hilo=True)
        tareas.guardar_chat(uuid.uuid4().hex[:10], texto, respuesta, origen, round((time.time() - inicio) * 1000))
    finally:
        _estado["pensando"] = False

    wav = hablar(respuesta)
    if wav:
        sonar(wav)
    # Si quedo algo por confirmar ("¿lo envio?"), se escucha el si o el no
    # sin tener que volver a pulsar el atajo.
    return {"texto": texto, "respuesta": respuesta, "origen": origen,
            "escuchar_otra_vez": confirmaciones.pendiente() is not None}


def _turno_completo() -> None:
    try:
        while True:
            pitido("escucho")
            audio = grabar()
            if audio is None:
                pitido("error")
                return
            resultado = atender(audio)
            if not resultado.get("escuchar_otra_vez"):
                return
    except Exception as e:
        log.exception("La voz local fallo: %s", e)
        pitido("error")


_hilo_turno: threading.Thread | None = None


def al_atajo() -> None:
    """Pulsar el atajo: si habla, se calla y escucha; si escucha, para; si no, escucha."""
    global _hilo_turno
    if _estado["grabando"]:
        _detener_grabacion.set()
        return
    if _estado["hablando"]:
        callar()
    if _estado["pensando"]:
        return
    if _hilo_turno and _hilo_turno.is_alive() and not _estado["hablando"]:
        return
    _hilo_turno = threading.Thread(target=_turno_completo, daemon=True, name="voz-local")
    _hilo_turno.start()


def arrancar() -> str:
    """Registra el atajo y precarga Whisper. Devuelve una linea para el log de arranque."""
    if not ACTIVA:
        return "apagada (JARVIS_VOZ_LOCAL=0)"
    falta = faltan()
    if falta:
        return f"sin instalar (falta {', '.join(falta)}): scripts\\instalar_voz.ps1"
    try:
        import keyboard
        keyboard.add_hotkey(ATAJO, al_atajo, suppress=False)
    except Exception as e:
        return f"no pude registrar el atajo {ATAJO}: {e}"
    threading.Thread(target=_modelo_whisper, daemon=True, name="whisper-carga").start()
    return f"atajo {ATAJO} · whisper {MODELO_WHISPER} · habla con {motor_de_habla() or 'nada'}"
