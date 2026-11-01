"""Avisos de Windows (las notificaciones de la esquina) cuando algo termina.

No hay que instalar nada: PowerShell de Windows ya sabe mostrar una
notificacion. Jarvis le pasa el texto codificado, asi que da igual que lleve
comillas, tildes o simbolos.

Se apagan con JARVIS_AVISOS=0 en el .env. Fuera de Windows (las pruebas)
solo se apuntan en ENVIADOS.
"""

import base64
import logging
import os
import subprocess
from collections import deque
from xml.sax.saxutils import escape

log = logging.getLogger("jarvis.avisos")

# El identificador de PowerShell: Windows ya lo conoce, asi que la
# notificacion sale sin registrar ninguna aplicacion.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
MAX_TITULO = 60
MAX_TEXTO = 240

ENVIADOS: deque = deque(maxlen=20)


def activos() -> bool:
    return os.environ.get("JARVIS_AVISOS", "1").strip() not in ("0", "no", "false")


def _recortar(texto: str, maximo: int) -> str:
    texto = " ".join(str(texto or "").split())
    if len(texto) <= maximo:
        return texto
    return texto[:maximo].rsplit(" ", 1)[0] + "…"


def _escapar(texto: str) -> str:
    # Sin comillas simples en el XML: el texto va dentro de '...' en PowerShell.
    return escape(texto, {"'": "&apos;", '"': "&quot;"})


def script(titulo: str, texto: str) -> str:
    """El PowerShell que muestra la notificacion."""
    xml = ("<toast><visual><binding template='ToastGeneric'>"
           f"<text>{_escapar(_recortar(titulo, MAX_TITULO))}</text>"
           f"<text>{_escapar(_recortar(texto, MAX_TEXTO))}</text>"
           "</binding></visual></toast>").replace("'", '"')
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] > $null\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] > $null\n"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
        f"$x.LoadXml('{xml}')\n"
        "$t = [Windows.UI.Notifications.ToastNotification]::new($x)\n"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($t)\n"
    )


def avisar(titulo: str, texto: str) -> bool:
    """Muestra una notificacion. Nunca bloquea ni lanza: si falla, se apunta en el log."""
    ENVIADOS.append((titulo, texto))
    if not activos() or os.name != "nt":
        log.info("Aviso (sin mostrar): %s | %s", titulo, _recortar(texto, 120))
        return False
    try:
        codificado = base64.b64encode(script(titulo, texto).encode("utf-16-le")).decode("ascii")
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-EncodedCommand", codificado],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.info("Aviso: %s | %s", titulo, _recortar(texto, 120))
        return True
    except Exception as e:
        log.warning("No pude mostrar el aviso: %s", e)
        return False


if __name__ == "__main__":
    # py avisos.py  -> deberia salir una notificacion en la esquina
    logging.basicConfig(level=logging.INFO)
    print("Mostrado" if avisar("Jarvis", "Prueba de aviso: si ves esto, los avisos funcionan.")
          else "No se mostró (mira el log)")
