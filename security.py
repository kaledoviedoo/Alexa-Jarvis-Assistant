"""
Verificación de peticiones de Alexa.

Tu túnel de ngrok es una URL pública que ejecuta comandos reales en tu PC.
Sin esta verificación, cualquiera que descubra la URL puede crear archivos,
cerrar programas y escribir con tu teclado. Amazon firma criptográficamente
cada petición; aquí comprobamos esa firma.

Implementa el procedimiento oficial de Amazon:
  1. La URL del certificado debe apuntar a s3.amazonaws.com/echo.api/
  2. El certificado debe estar vigente y contener echo-api.amazon.com en su SAN
  3. La firma (SHA1withRSA) debe validar contra el cuerpo CRUDO de la petición
  4. El timestamp no puede tener más de 150 segundos
  5. El applicationId debe coincidir con tu skill

Docs: https://developer.amazon.com/docs/custom-skills/host-a-custom-skill-as-a-web-service.html
"""

import base64
import datetime as _dt
import logging
import posixpath
import threading
import urllib.parse
import urllib.request

from config import (
    ALEXA_SKILL_ID,
    TOLERANCIA_TIMESTAMP_SEGUNDOS,
    VERIFICAR_FIRMA,
)

log = logging.getLogger("jarvis.security")

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import ExtensionOID

    CRYPTO_DISPONIBLE = True
except ImportError:  # pragma: no cover
    CRYPTO_DISPONIBLE = False
    log.warning(
        "La librería 'cryptography' no está instalada: "
        "la verificación de firma quedará deshabilitada. "
        "Instálala con:  py -m pip install cryptography"
    )


class ErrorVerificacion(Exception):
    """La petición no proviene de Amazon (o no es válida)."""


# -------------------------------------------------------------------------
# Caché de certificados
# -------------------------------------------------------------------------
# Amazon reutiliza el mismo certificado durante días. Descargarlo en cada
# petición añadiría ~200 ms a cada orden de voz, que es tiempo que no tenemos.
_cache_certificados: dict = {}
_lock_cache = threading.Lock()


def _validar_url_certificado(url: str) -> None:
    """Amazon exige comprobar la forma de la URL ANTES de descargar nada."""
    partes = urllib.parse.urlparse(url)

    if partes.scheme.lower() != "https":
        raise ErrorVerificacion(f"El esquema del certificado no es https: {partes.scheme}")

    if partes.hostname is None or partes.hostname.lower() != "s3.amazonaws.com":
        raise ErrorVerificacion(f"Host de certificado no autorizado: {partes.hostname}")

    if partes.port not in (None, 443):
        raise ErrorVerificacion(f"Puerto de certificado no autorizado: {partes.port}")

    # Hay que NORMALIZAR la ruta antes de comprobarla. Sin esto, una URL como
    #   https://s3.amazonaws.com/echo.api/../../malicioso/cert.pem
    # pasaría el filtro (empieza por /echo.api/) pero en realidad apunta a
    # /malicioso/cert.pem. posixpath.normpath resuelve los '..' primero.
    ruta_normalizada = posixpath.normpath(urllib.parse.unquote(partes.path))
    if not ruta_normalizada.startswith("/echo.api/"):
        raise ErrorVerificacion(f"Ruta de certificado no autorizada: {partes.path}")


def _descargar_certificado(url: str):
    """Descarga y valida la cadena de certificados de Amazon (con caché)."""
    with _lock_cache:
        if url in _cache_certificados:
            return _cache_certificados[url]

    _validar_url_certificado(url)

    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            pem = resp.read()
    except Exception as e:
        raise ErrorVerificacion(f"No se pudo descargar el certificado: {e}") from e

    try:
        cadena = x509.load_pem_x509_certificates(pem)
    except AttributeError:
        # cryptography < 39 no tiene load_pem_x509_certificates
        cadena = [x509.load_pem_x509_certificate(pem)]
    except Exception as e:
        raise ErrorVerificacion(f"Certificado ilegible: {e}") from e

    if not cadena:
        raise ErrorVerificacion("La cadena de certificados venía vacía.")

    cert = cadena[0]

    # ---- Vigencia ----
    ahora = _dt.datetime.now(_dt.timezone.utc)
    try:
        no_antes = cert.not_valid_before_utc
        no_despues = cert.not_valid_after_utc
    except AttributeError:  # cryptography antiguo
        no_antes = cert.not_valid_before.replace(tzinfo=_dt.timezone.utc)
        no_despues = cert.not_valid_after.replace(tzinfo=_dt.timezone.utc)

    if not (no_antes <= ahora <= no_despues):
        raise ErrorVerificacion("El certificado de Amazon está vencido o aún no es válido.")

    # ---- SAN debe contener echo-api.amazon.com ----
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        nombres = san.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        raise ErrorVerificacion("El certificado no tiene extensión SAN.")

    if "echo-api.amazon.com" not in nombres:
        raise ErrorVerificacion(f"SAN no contiene echo-api.amazon.com: {nombres}")

    clave_publica = cert.public_key()
    if not isinstance(clave_publica, rsa.RSAPublicKey):
        raise ErrorVerificacion("La clave pública del certificado no es RSA.")

    with _lock_cache:
        _cache_certificados[url] = clave_publica
        # No dejamos crecer la caché indefinidamente.
        if len(_cache_certificados) > 8:
            _cache_certificados.pop(next(iter(_cache_certificados)))

    return clave_publica


def _verificar_firma(cuerpo_crudo: bytes, firma_b64: str, url_cert: str) -> None:
    clave_publica = _descargar_certificado(url_cert)

    try:
        firma = base64.b64decode(firma_b64)
    except Exception as e:
        raise ErrorVerificacion(f"Firma no decodificable: {e}") from e

    try:
        # Amazon firma con SHA1withRSA (PKCS#1 v1.5). Sí, SHA-1: lo define su
        # protocolo, no es una decisión nuestra.
        clave_publica.verify(firma, cuerpo_crudo, padding.PKCS1v15(), hashes.SHA1())
    except Exception as e:
        raise ErrorVerificacion(f"La firma no coincide con el cuerpo: {e}") from e


def _verificar_timestamp(cuerpo: dict) -> None:
    """Bloquea ataques de repetición: una petición vieja se rechaza."""
    marca = cuerpo.get("request", {}).get("timestamp")
    if not marca:
        raise ErrorVerificacion("La petición no trae timestamp.")

    try:
        texto = marca.replace("Z", "+00:00")
        momento = _dt.datetime.fromisoformat(texto)
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=_dt.timezone.utc)
    except Exception as e:
        raise ErrorVerificacion(f"Timestamp ilegible: {marca}") from e

    diferencia = abs((_dt.datetime.now(_dt.timezone.utc) - momento).total_seconds())
    if diferencia > TOLERANCIA_TIMESTAMP_SEGUNDOS:
        raise ErrorVerificacion(
            f"Timestamp fuera de tolerancia ({diferencia:.0f}s). "
            "Revisa que la hora de tu PC esté sincronizada."
        )


def _verificar_skill_id(cuerpo: dict) -> None:
    """Confirma que la petición sea de TU skill y no de otra."""
    if not ALEXA_SKILL_ID:
        log.warning(
            "ALEXA_SKILL_ID está vacío en la configuración: no se puede verificar "
            "el origen de la skill. Añádelo a tu archivo .env."
        )
        return

    recibido = (
        cuerpo.get("session", {}).get("application", {}).get("applicationId")
        or cuerpo.get("context", {}).get("System", {}).get("application", {}).get("applicationId")
    )

    if recibido != ALEXA_SKILL_ID:
        raise ErrorVerificacion(f"Skill ID no coincide. Recibido: {recibido}")


def verificar_peticion(cuerpo_crudo: bytes, cuerpo: dict, cabeceras) -> None:
    """
    Punto de entrada. Lanza ErrorVerificacion si la petición no es legítima.

    `cabeceras` debe permitir acceso tipo diccionario insensible a mayúsculas
    (los headers de FastAPI/Starlette ya lo son).
    """
    _verificar_skill_id(cuerpo)

    if not VERIFICAR_FIRMA:
        log.warning("VERIFICAR_FIRMA=False — modo de pruebas, sin validación criptográfica.")
        return

    if not CRYPTO_DISPONIBLE:
        raise ErrorVerificacion(
            "Se exige verificar la firma pero falta la librería 'cryptography'. "
            "Ejecuta: py -m pip install cryptography"
        )

    _verificar_timestamp(cuerpo)

    url_cert = cabeceras.get("signaturecertchainurl")
    firma = cabeceras.get("signature")

    if not url_cert or not firma:
        raise ErrorVerificacion("Faltan las cabeceras de firma de Amazon.")

    _verificar_firma(cuerpo_crudo, firma, url_cert)
