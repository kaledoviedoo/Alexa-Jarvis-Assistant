"""Tres defensas pequenas para lo que entra y sale de Jarvis.

1. escanear_inyeccion: mira si un texto de fuera (una web, un correo, un PDF)
   intenta darle ordenes al modelo ("ignora las instrucciones anteriores...").
2. quitar_credenciales: tapa claves y tokens antes de que acaben en el log,
   en el historial de tareas o en el del chat.
3. revisar_url / abrir_url: no deja que una peticion web acabe en la red de
   casa, en el propio equipo o en la tailnet (SSRF), tampoco tras un redirect.

Adaptado de OpenJarvis (https://github.com/open-jarvis/OpenJarvis), licencia
Apache-2.0: src/openjarvis/security/injection_scanner.py,
credential_stripper.py y ssrf.py. Cambios: patrones en espanol, sin el
backend de Rust ni el bus de eventos, aviso pensado para el prompt de Jarvis,
mas tipos de credencial (Google, Tailscale, GitHub fine-grained, JWT, claves
en .env) y comprobacion de cada redirect. Ver NOTICE y
licencias/OpenJarvis-LICENSE.txt.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("jarvis.seguridad")


# ============================================================================
#  1. Inyeccion de instrucciones
# ============================================================================

NIVELES = ("bajo", "medio", "alto", "critico")

# (patron, tipo, nivel, que es). Se buscan sobre el texto sin tildes.
_PATRONES_INYECCION: list[tuple[str, str, str, str]] = [
    # ---- Ingles (de OpenJarvis) ----
    (r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?|context)",
     "anular_instrucciones", "critico", "Intenta anular las instrucciones"),
    (r"you\s+are\s+now\s+(?:a|an|in)\s+", "cambiar_rol", "alto", "Intenta cambiarle el papel al asistente"),
    (r"(?:^|\n)\s*system\s*:\s*", "falso_sistema", "alto", "Se hace pasar por un mensaje del sistema"),
    (r"disregard\s+(?:all\s+)?(?:previous|prior|your)\s+(?:instructions?|programming|rules?)",
     "anular_instrucciones", "alto", "Intenta que se ignoren las instrucciones"),
    (r"(?:execute|run|eval)\s*\(\s*['\"]", "codigo", "alto", "Intenta ejecutar codigo"),
    (r"(?:;|\||&&)\s*(?:rm|curl|wget|nc|ncat|bash|sh|python|perl|powershell|iwr|del)\s",
     "comando", "alto", "Mete un comando de consola"),
    (r"(?:send|post|upload|exfiltrate|transmit)\s+(?:(?:to|data|all|everything)\s+)*(?:to\s+)?(?:https?://|my\s+server)",
     "exfiltracion", "alto", "Intenta sacar datos hacia fuera"),
    (r"base64\s+encode\s+(?:and\s+)?(?:send|include|append)", "exfiltracion", "medio",
     "Intenta sacar datos codificados"),
    (r"(?:\bDAN\b|do\s+anything\s+now)\s+(?:mode|prompt|jailbreak)", "jailbreak", "alto", "Jailbreak tipo DAN"),
    (r"pretend\s+(?:you\s+)?(?:have\s+)?no\s+(?:restrictions?|limitations?|rules?|filters?)",
     "jailbreak", "medio", "Pide actuar sin limites"),
    (r"```(?:system|assistant)\b", "delimitador", "medio", "Falso bloque de rol"),
    (r"<\|(?:im_start|im_end|system|assistant|user)\|>", "delimitador", "alto", "Plantilla de chat inyectada"),
    # ---- Espanol (nuevo) ----
    (r"\b(?:ignora|olvida|descarta|omite|pasa\s+de)\s+(?:todas?\s+)?(?:las?\s+|tus\s+|los\s+)?"
     r"(?:instrucciones|ordenes|reglas|indicaciones|normas)(?:\s+(?:anteriores|previas|de\s+arriba|que\s+te\s+dieron))?",
     "anular_instrucciones", "critico", "Intenta anular las instrucciones"),
    (r"\b(?:a\s+partir\s+de\s+ahora|desde\s+ahora)\s+(?:eres|seras|actua\s+como|te\s+llamas)\b",
     "cambiar_rol", "alto", "Intenta cambiarle el papel al asistente"),
    (r"\b(?:nuevas?\s+instrucciones|instrucciones\s+nuevas)\s*:", "falso_sistema", "alto",
     "Trae 'instrucciones nuevas' para el asistente"),
    (r"(?:^|\n)\s*(?:sistema|system\s+prompt|mensaje\s+del\s+sistema)\s*:", "falso_sistema", "alto",
     "Se hace pasar por un mensaje del sistema"),
    (r"\b(?:asistente|jarvis|ia|modelo)\s*,?\s+(?:reenvia|envia|manda|borra|elimina|ejecuta|apaga|desinstala|comparte)\b",
     "orden_directa", "alto", "Le da una orden directa al asistente"),
    (r"\b(?:envia|manda|reenvia|sube|comparte)\s+(?:todos?\s+)?(?:tus|los|mis)?\s*(?:contrasenas|claves|tokens|"
     r"credenciales|archivos|contactos|correos|datos)\s+(?:a|al|hacia)\b",
     "exfiltracion", "alto", "Intenta sacar datos hacia fuera"),
    (r"\b(?:finge|haz\s+como\s+si|actua\s+como\s+si)\s+no\s+tuvieras\s+(?:reglas|limites|restricciones|filtros)",
     "jailbreak", "medio", "Pide actuar sin limites"),
    (r"\bno\s+(?:le\s+)?(?:digas|cuentes|avises|menciones)\s+(?:nada\s+)?(?:al|a\s+tu)\s+usuario\b",
     "ocultar", "alto", "Pide ocultarle algo al usuario"),
    (r"</?\s*dato_externo\b", "delimitador", "critico", "Intenta cerrar la marca de dato externo"),
]

_COMPILADOS = [(re.compile(p, re.IGNORECASE), t, n, d) for p, t, n, d in _PATRONES_INYECCION]
MAX_TEXTO_HALLAZGO = 100


@dataclass
class Hallazgo:
    tipo: str
    texto: str
    nivel: str
    descripcion: str


@dataclass
class ResultadoEscaneo:
    limpio: bool
    hallazgos: list[Hallazgo] = field(default_factory=list)
    nivel: str = "bajo"

    @property
    def peligroso(self) -> bool:
        """Alto o critico: lo bastante claro como para avisar y frenar."""
        return self.nivel in ("alto", "critico")


def _sin_tildes(texto: str) -> str:
    tabla = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return texto.translate(tabla)


def escanear_inyeccion(texto: str) -> ResultadoEscaneo:
    """Busca en un texto de fuera intentos de darle ordenes al modelo."""
    plano = _sin_tildes(texto or "")
    hallazgos: list[Hallazgo] = []
    peor = -1
    for regex, tipo, nivel, descripcion in _COMPILADOS:
        for m in regex.finditer(plano):
            hallazgos.append(Hallazgo(tipo, m.group(0)[:MAX_TEXTO_HALLAZGO].strip(), nivel, descripcion))
            peor = max(peor, NIVELES.index(nivel))
    return ResultadoEscaneo(limpio=not hallazgos, hallazgos=hallazgos,
                            nivel=NIVELES[peor] if peor >= 0 else "bajo")


def neutralizar(texto: str) -> str:
    """Rompe las marcas con las que un texto podria salirse de su envoltorio."""
    texto = re.sub(r"<(/?)\s*dato_externo", r"‹\1dato_externo", texto or "", flags=re.IGNORECASE)
    return re.sub(r"<\|([a-z_]+)\|>", r"‹|\1|›", texto, flags=re.IGNORECASE)


# ============================================================================
#  2. Credenciales
# ============================================================================

_CREDENCIALES: list[tuple[str, re.Pattern]] = [
    ("api_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("github_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]+")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("tailscale_key", re.compile(r"\btskey-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.=]{20,}")),
    # CLAVE=valor al estilo .env o de un formulario, cuando el nombre lo delata.
    ("secreto", re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|LLAVE|CLAVE|CONTRASE[NÑ]A)"
                           r"[A-Z0-9_]*)(\s*[=:]\s*)([\"']?)[^\s\"'&]{6,}\3")),
    ("llave_en_url", re.compile(r"(?i)([?&](?:llave|token|key|api_key|access_token)=)[^&\s#]{6,}")),
]


def quitar_credenciales(texto):
    """Tapa claves y tokens. Lo que no es texto se devuelve tal cual."""
    if not isinstance(texto, str) or not texto:
        return texto
    for etiqueta, patron in _CREDENCIALES:
        if etiqueta == "secreto":
            texto = patron.sub(lambda m: f"{m.group(1)}{m.group(2)}[OCULTO:{etiqueta}]", texto)
        elif etiqueta == "llave_en_url":
            texto = patron.sub(lambda m: f"{m.group(1)}[OCULTO]", texto)
        else:
            texto = patron.sub(f"[OCULTO:{etiqueta}]", texto)
    return texto


class FiltroCredenciales(logging.Filter):
    """Filtro de logging: nada de lo que se escribe en el log lleva una clave."""

    def filter(self, registro: logging.LogRecord) -> bool:
        try:
            mensaje = registro.getMessage()
        except Exception:
            return True
        limpio = quitar_credenciales(mensaje)
        if limpio != mensaje:
            registro.msg, registro.args = limpio, None
        return True


def instalar_filtro_log(*manejadores: logging.Handler) -> None:
    """Pone el filtro en los manejadores dados (o en los del logger raiz)."""
    filtro = FiltroCredenciales()
    for manejador in manejadores or logging.getLogger().handlers:
        if not any(isinstance(f, FiltroCredenciales) for f in manejador.filters):
            manejador.addFilter(filtro)


# ============================================================================
#  3. SSRF: peticiones web que no salen a internet
# ============================================================================

_HOSTS_BLOQUEADOS = frozenset({
    "169.254.169.254", "metadata.google.internal", "metadata.google.com", "100.100.100.200",
    "localhost", "localhost.localdomain",
})

_REDES_BLOQUEADAS = [ipaddress.ip_network(r) for r in (
    "0.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16",
    "100.64.0.0/10",       # CGNAT: aqui viven las IP de Tailscale
    "192.0.0.0/24", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
    "::/128", "::1/128", "64:ff9b:1::/48", "fc00::/7", "fe80::/10", "ff00::/8",
)]
_IP_PUBLICAS_DE_PROTOCOLO = frozenset(ipaddress.ip_address(i) for i in ("192.0.0.9", "192.0.0.10"))
ESQUEMAS_PERMITIDOS = ("http", "https")


def _ipv4_dentro(addr: ipaddress.IPv6Address):
    """La IPv4 escondida en una IPv6 (::ffff:a.b.c.d, NAT64, 6to4, compatible)."""
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    b = addr.packed
    if b[:12] == bytes.fromhex("0064ff9b0000000000000000"):
        return ipaddress.IPv4Address(b[12:])
    if b[:2] == b"\x20\x02":
        return ipaddress.IPv4Address(b[2:6])
    if b[:12] == b"\x00" * 12 and addr not in (ipaddress.IPv6Address("::"), ipaddress.IPv6Address("::1")):
        return ipaddress.IPv4Address(b[12:])
    return None


def es_ip_privada(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        addr = _ipv4_dentro(addr) or addr
    return addr not in _IP_PUBLICAS_DE_PROTOCOLO and any(addr in red for red in _REDES_BLOQUEADAS)


def _ipv4_disfrazada(host: str):
    """127.1, 2130706433, 0x7f000001...: formas raras de escribir una IPv4."""
    try:
        addr = ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None
    return None if str(addr) == host else addr


def revisar_url(url: str) -> str | None:
    """None si la URL se puede pedir; si no, el motivo en una frase."""
    try:
        partes = urllib.parse.urlparse(url or "")
    except ValueError:
        return "URL mal formada"
    if partes.scheme.lower() not in ESQUEMAS_PERMITIDOS:
        return f"esquema no permitido: {partes.scheme or 'ninguno'}"
    host = (partes.hostname or "").strip().rstrip(".").lower()
    if not host:
        return "la URL no tiene servidor"
    if host in _HOSTS_BLOQUEADOS or host.endswith((".localhost", ".local", ".internal", ".ts.net", ".lan")):
        return f"{host} es de la red local o del propio equipo"

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = _ipv4_disfrazada(host)
    if literal is not None:
        if isinstance(literal, ipaddress.IPv6Address):
            dentro = _ipv4_dentro(literal)
            if dentro is not None and str(dentro) in _HOSTS_BLOQUEADOS:
                return f"{dentro} es un servicio de metadatos"
        return f"{literal} es una IP privada" if es_ip_privada(str(literal)) else None

    try:
        direcciones = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        if os.environ.get("JARVIS_SSRF_PERMISIVO", "").lower() in ("1", "true", "si"):
            return None
        return f"no pude resolver {host}"
    for *_, sockaddr in direcciones:
        if es_ip_privada(sockaddr[0]):
            return f"{host} apunta a una IP privada ({sockaddr[0]})"
    return None


class URLBloqueada(Exception):
    """La peticion iba (o la redirigian) a donde no debe."""


class _RedirectRevisado(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        motivo = revisar_url(newurl)
        if motivo:
            log.warning("SSRF: redirect bloqueado hacia %s (%s)", newurl[:120], motivo)
            raise URLBloqueada(f"me redirigian a un sitio no permitido: {motivo}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_abridor = urllib.request.build_opener(_RedirectRevisado())


def abrir_url(peticion, timeout: float = 12):
    """urlopen, pero solo hacia internet. Lanza URLBloqueada si no."""
    url = peticion.full_url if isinstance(peticion, urllib.request.Request) else str(peticion)
    motivo = revisar_url(url)
    if motivo:
        log.warning("SSRF: peticion bloqueada a %s (%s)", url[:120], motivo)
        raise URLBloqueada(motivo)
    return _abridor.open(peticion, timeout=timeout)
