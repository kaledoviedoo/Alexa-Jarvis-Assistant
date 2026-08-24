"""
Lectura del correo de Outlook, en local y sin permisos de nadie.

Por que COM y no la API de Microsoft
------------------------------------
Graph seria mas potente (historico, busqueda del servidor), pero exige
registrar una app en Azure y, para cuentas de trabajo o universidad, el
consentimiento de un administrador que casi nunca llega. COM habla con el
Outlook que ya tienes instalado y con la sesion que ya tienes iniciada: cero
configuracion, cero credenciales guardadas, cero trafico fuera del equipo.

Solo lectura, a proposito
-------------------------
Aqui no se envia, ni se borra, ni se marca nada. Un asistente de voz que se
equivoca al transcribir y manda un correo es un problema mucho mas caro que
uno que solo sabe leerlos. Si algun dia hace falta enviar, que sea una
funcion aparte y con confirmacion.
"""

import logging
import re
from datetime import datetime, timedelta

from config import CORREO_CARACTERES, CORREO_MAXIMO

log = logging.getLogger("jarvis.correo")

# Carpetas estandar de Outlook (olFolderInbox, olFolderSentMail).
_BANDEJA_ENTRADA = 6
_ENVIADOS = 5


def _outlook():
    """Devuelve el Outlook vivo, o None con el motivo escrito en el registro."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        log.warning("Falta pywin32. Ejecuta scripts/instalar_extras.ps1")
        return None

    try:
        # Imprescindible: esto corre en un hilo del servidor, y COM exige que
        # cada hilo se inicialice. Sin esto falla con un error críptico sobre
        # apartamentos ("CoInitialize has not been called").
        pythoncom.CoInitialize()
        return win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    except Exception as e:
        log.warning("No pude hablar con Outlook: %s", e)
        return None


def _limpiar(texto: str, limite: int) -> str:
    """Deja el cuerpo del correo en algo que se pueda escuchar."""
    if not texto:
        return ""

    # Fuera firmas, avisos legales y cadenas de respuestas: en voz alta son
    # ruido puro y se comen el tiempo de Alexa.
    corte = re.split(
        r"\n\s*(?:-{2,}|_{2,}|De:|From:|Enviado el:|Sent:|"
        r"Este mensaje.{0,40}confidencial|This (?:e-?mail|message))",
        texto, maxsplit=1,
    )[0]

    corte = re.sub(r"https?://\S+", "un enlace", corte)
    corte = re.sub(r"\s+", " ", corte).strip()

    if len(corte) > limite:
        corte = corte[:limite].rsplit(" ", 1)[0] + "..."
    return corte


def _cuando(fecha) -> str:
    """Fecha en lenguaje hablado: 'hace diez minutos', no un timestamp."""
    try:
        momento = datetime(fecha.year, fecha.month, fecha.day,
                           fecha.hour, fecha.minute, fecha.second)
    except Exception:
        return ""

    diferencia = datetime.now() - momento
    if diferencia < timedelta(minutes=2):
        return "ahora mismo"
    if diferencia < timedelta(hours=1):
        return f"hace {int(diferencia.total_seconds() // 60)} minutos"
    if diferencia < timedelta(hours=24):
        horas = int(diferencia.total_seconds() // 3600)
        return "hace una hora" if horas == 1 else f"hace {horas} horas"
    if diferencia < timedelta(days=2):
        return "ayer"
    if diferencia < timedelta(days=7):
        return f"hace {diferencia.days} días"
    return momento.strftime("el %d de %m")


def _mensajes(carpeta, cuantos: int, solo_sin_leer: bool = False) -> list:
    elementos = carpeta.Items
    elementos.Sort("[ReceivedTime]", True)   # los mas nuevos primero

    if solo_sin_leer:
        try:
            elementos = elementos.Restrict("[Unread] = True")
        except Exception:
            pass

    salida = []
    for i, mensaje in enumerate(elementos):
        if i >= cuantos:
            break
        try:
            salida.append({
                "de": getattr(mensaje, "SenderName", "") or "alguien",
                "asunto": (getattr(mensaje, "Subject", "") or "sin asunto").strip(),
                "cuerpo": getattr(mensaje, "Body", "") or "",
                "cuando": _cuando(getattr(mensaje, "ReceivedTime", None)),
                "sin_leer": bool(getattr(mensaje, "UnRead", False)),
            })
        except Exception as e:
            log.debug("Un mensaje dio problemas y lo salto: %s", e)
            continue
    return salida


# -------------------------------------------------------------------------
# ORDENES
# -------------------------------------------------------------------------
def ultimos_correos(cuantos: int = 3, con_cuerpo: bool = False) -> str:
    """Los ultimos correos recibidos."""
    espacio = _outlook()
    if espacio is None:
        return "No puedo llegar a Outlook. Comprueba que esté instalado y abierto."

    cuantos = max(1, min(int(cuantos or 3), CORREO_MAXIMO))

    try:
        mensajes = _mensajes(espacio.GetDefaultFolder(_BANDEJA_ENTRADA), cuantos)
    except Exception as e:
        log.exception("Fallo leyendo la bandeja")
        return f"No pude leer la bandeja: {e}"

    if not mensajes:
        return "No hay nada en la bandeja de entrada."

    partes = []
    for n, m in enumerate(mensajes, 1):
        trozo = f"{n}. De {m['de']}, {m['asunto']}"
        if m["cuando"]:
            trozo += f", {m['cuando']}"
        if con_cuerpo:
            cuerpo = _limpiar(m["cuerpo"], CORREO_CARACTERES)
            if cuerpo:
                trozo += f". Dice: {cuerpo}"
        partes.append(trozo)

    cabecera = "Tu último correo:" if len(partes) == 1 else f"Tus últimos {len(partes)} correos:"
    return cabecera + " " + " ".join(partes)


def correos_sin_leer(cuantos: int = 3) -> str:
    """Cuantos hay sin leer y de quien son los primeros."""
    espacio = _outlook()
    if espacio is None:
        return "No puedo llegar a Outlook. Comprueba que esté instalado y abierto."

    try:
        bandeja = espacio.GetDefaultFolder(_BANDEJA_ENTRADA)
        total = bandeja.UnReadItemCount
        mensajes = _mensajes(bandeja, max(1, min(int(cuantos or 3), CORREO_MAXIMO)),
                             solo_sin_leer=True)
    except Exception as e:
        log.exception("Fallo contando los no leídos")
        return f"No pude contar los correos: {e}"

    if not total:
        return "No tienes correos sin leer."

    if not mensajes:
        return f"Tienes {total} correos sin leer."

    detalle = " ".join(f"{m['de']}, {m['asunto']}" for m in mensajes)
    cuenta = "1 correo sin leer" if total == 1 else f"{total} correos sin leer"
    return f"Tienes {cuenta}. Los más recientes: {detalle}"


def leer_correo(cual: int = 1) -> str:
    """Lee un correo concreto por su posicion, con el cuerpo."""
    espacio = _outlook()
    if espacio is None:
        return "No puedo llegar a Outlook. Comprueba que esté instalado y abierto."

    posicion = max(1, min(int(cual or 1), CORREO_MAXIMO))

    try:
        mensajes = _mensajes(espacio.GetDefaultFolder(_BANDEJA_ENTRADA), posicion)
    except Exception as e:
        return f"No pude leer el correo: {e}"

    if len(mensajes) < posicion:
        return f"Solo tengo {len(mensajes)} correos a mano."

    m = mensajes[posicion - 1]
    cuerpo = _limpiar(m["cuerpo"], CORREO_CARACTERES * 2) or "No tiene texto legible."
    return f"De {m['de']}, {m['cuando']}. Asunto: {m['asunto']}. {cuerpo}"


def buscar_correos(quien: str, cuantos: int = 3) -> str:
    """Correos de un remitente concreto."""
    espacio = _outlook()
    if espacio is None:
        return "No puedo llegar a Outlook. Comprueba que esté instalado y abierto."

    quien = (quien or "").strip()
    if not quien:
        return "¿De quién quieres que busque los correos?"

    try:
        elementos = espacio.GetDefaultFolder(_BANDEJA_ENTRADA).Items
        elementos.Sort("[ReceivedTime]", True)

        encontrados = []
        for i, mensaje in enumerate(elementos):
            # Tope de barrido: sin esto, una bandeja de veinte mil correos
            # se comeria el plazo de Alexa buscando en toda la historia.
            if i > 400 or len(encontrados) >= cuantos:
                break
            try:
                remitente = (getattr(mensaje, "SenderName", "") or "").lower()
                if quien.lower() in remitente:
                    encontrados.append({
                        "de": getattr(mensaje, "SenderName", ""),
                        "asunto": (getattr(mensaje, "Subject", "") or "sin asunto").strip(),
                        "cuando": _cuando(getattr(mensaje, "ReceivedTime", None)),
                    })
            except Exception:
                continue
    except Exception as e:
        return f"No pude buscar: {e}"

    if not encontrados:
        return f"No encontré correos recientes de {quien}."

    detalle = " ".join(f"{m['asunto']}, {m['cuando']}" for m in encontrados)
    return f"De {encontrados[0]['de']}: {detalle}"


# -------------------------------------------------------------------------
# ENVIAR
# -------------------------------------------------------------------------
# Aqui hay una ventaja grande frente a WhatsApp: Outlook RESUELVE el
# destinatario contra tu libreta de direcciones. Le das "andres" y te devuelve
# "Andres Ramirez <a.ramirez@...>" o te dice que no lo encuentra. No hay que
# leer la pantalla ni adivinar: es el propio Outlook quien confirma a quien le
# vas a escribir, y eso es mucho mas fiable que un OCR.
#
# Aun asi se pide confirmacion, porque un correo enviado tampoco se recoge.

def _resolver_destinatario(mensaje, quien: str):
    """
    Anade el destinatario y deja que Outlook lo resuelva.

    Devuelve (nombre_resuelto, direccion) o (None, None).
    """
    try:
        destinatario = mensaje.Recipients.Add(quien)
        if not destinatario.Resolve():
            return None, None

        nombre = getattr(destinatario, "Name", "") or quien
        direccion = ""
        try:
            entrada = destinatario.AddressEntry
            direccion = getattr(entrada, "Address", "") or ""
            # Las cuentas internas devuelven una ruta X500 ilegible; la de
            # verdad esta en el objeto de usuario de Exchange.
            if direccion.startswith("/") and hasattr(entrada, "GetExchangeUser"):
                usuario = entrada.GetExchangeUser()
                if usuario:
                    direccion = getattr(usuario, "PrimarySmtpAddress", "") or direccion
        except Exception:
            pass

        return nombre, direccion
    except Exception as e:
        log.warning("No pude resolver %r: %s", quien, e)
        return None, None


def preparar_correo(quien: str, asunto: str, cuerpo: str):
    """
    Redacta el correo y lo deja listo para enviar.

    Devuelve (mensaje_com, nombre, direccion, error).
    """
    espacio = _outlook()
    if espacio is None:
        return None, None, None, "No puedo llegar a Outlook."

    quien = (quien or "").strip()
    cuerpo = (cuerpo or "").strip()

    if not quien:
        return None, None, None, "¿A quién le escribo?"
    if not cuerpo:
        return None, None, None, "¿Qué quieres que le diga?"

    try:
        import win32com.client
        aplicacion = win32com.client.Dispatch("Outlook.Application")
        mensaje = aplicacion.CreateItem(0)          # 0 = olMailItem
    except Exception as e:
        return None, None, None, f"No pude crear el correo: {e}"

    nombre, direccion = _resolver_destinatario(mensaje, quien)
    if nombre is None:
        return None, None, None, (
            f"No encuentro a {quien} en tus contactos de Outlook. "
            "Dime el correo completo o el nombre tal como lo tienes guardado."
        )

    mensaje.Subject = (asunto or "").strip() or "Sin asunto"
    mensaje.Body = cuerpo

    return mensaje, nombre, direccion, None


def enviar_correo(quien: str, cuerpo: str, asunto: str = "") -> str:
    """Redacta, confirma con el nombre que resolvio Outlook, y luego envia."""
    import confirmaciones

    mensaje, nombre, direccion, error = preparar_correo(quien, asunto, cuerpo)
    if error:
        return error

    def _enviar():
        try:
            mensaje.Send()
            log.info("Correo enviado a %s", nombre)
            return f"Enviado a {nombre}."
        except Exception as e:
            log.exception("Falló el envío")
            return f"No pude enviarlo: {e}"

    def _descartar():
        try:
            # Sin esto quedaria un borrador fantasma abierto en Outlook.
            mensaje.Close(1)        # 1 = olDiscard
        except Exception:
            pass
        return "Vale, lo descarté. No se envió nada."

    corto = cuerpo if len(cuerpo) <= 70 else cuerpo[:70] + "..."
    detalle = f"{nombre}" + (f", {direccion}" if direccion and "@" in direccion else "")

    return confirmaciones.pedir(
        f"enviar el correo a {nombre}",
        _enviar,
        al_rechazar=_descartar,
        pregunta=f"Va para {detalle}, y dice: {corto}. ¿Lo envío? Di sí o no.",
    )


def responder_ultimo(cuerpo: str) -> str:
    """Responde al ultimo correo recibido."""
    import confirmaciones

    espacio = _outlook()
    if espacio is None:
        return "No puedo llegar a Outlook."

    cuerpo = (cuerpo or "").strip()
    if not cuerpo:
        return "¿Qué quieres responder?"

    try:
        elementos = espacio.GetDefaultFolder(_BANDEJA_ENTRADA).Items
        elementos.Sort("[ReceivedTime]", True)
        original = elementos.GetFirst()
        if original is None:
            return "No hay ningún correo al que responder."

        de = getattr(original, "SenderName", "alguien")
        asunto = getattr(original, "Subject", "") or "sin asunto"
        respuesta = original.Reply()
        respuesta.Body = cuerpo + "\n\n" + (respuesta.Body or "")
    except Exception as e:
        return f"No pude preparar la respuesta: {e}"

    def _enviar():
        try:
            respuesta.Send()
            return f"Respondido a {de}."
        except Exception as e:
            return f"No pude enviarlo: {e}"

    def _descartar():
        try:
            respuesta.Close(1)
        except Exception:
            pass
        return "Vale, descarté la respuesta."

    corto = cuerpo if len(cuerpo) <= 70 else cuerpo[:70] + "..."
    return confirmaciones.pedir(
        f"responder a {de}",
        _enviar,
        al_rechazar=_descartar,
        pregunta=f"Respondo a {de}, sobre {asunto}, diciendo: {corto}. ¿Lo mando? Di sí o no.",
    )
