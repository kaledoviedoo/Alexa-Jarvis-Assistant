"""
Calienta el camino publico del tunel y dice cuanto tarda.

    py scripts/calentar_tunel.py [intentos]

Por que no basta con `Invoke-WebRequest https://tu-nombre.ts.net/jarvis`:
mientras Tailscale corre, MagicDNS resuelve los nombres .ts.net a la IP
interna del tailnet. Esa peticion sale del equipo y vuelve a entrar sin tocar
el Funnel, asi que mide algo que a Amazon no le sirve de nada.

Este script resuelve por DNS publico y conecta contra la IP de la entrada de
Tailscale, que es por donde llega Amazon de verdad.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.parse  # noqa: E402

import mantener_caliente as mc  # noqa: E402
from config import TUNEL_URL  # noqa: E402


def main() -> int:
    if not TUNEL_URL:
        print("No hay JARVIS_TUNEL_URL en el .env.")
        return 1

    # Por defecto insistimos casi tres minutos. No es exageracion: en el
    # registro esta medido que tras reiniciar el servidor, el Funnel tarda
    # hasta 109 SEGUNDOS en volver a aceptar conexiones, fallando mientras
    # tanto con "SSL: UNEXPECTED_EOF_WHILE_READING". Si el script se rinde
    # antes, te devuelve el control creyendo que todo esta listo y las
    # primeras invocaciones a Alexa se pierden en ese agujero.
    intentos = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    host = urllib.parse.urlparse(TUNEL_URL).hostname or ""
    ips = mc._ips_del_tunel(host)

    if not ips:
        print(f"No pude resolver {host} por DNS publico.")
        print("Sin eso no se puede distinguir el camino de Amazon del interno.")
        return 1

    print(f"Host   : {host}")
    print(f"IP(s)  : {', '.join(ips)}")
    print()

    for numero in range(1, intentos + 1):
        for ip in ips:
            try:
                ms = mc._ping_por_ip(host, ip)
            except Exception as e:
                # Los primeros fallos son NORMALES tras un reinicio, asi que
                # no los pintamos todos: solo uno de cada cinco, para que se
                # vea que sigue vivo sin llenar la pantalla de ruido.
                if numero % 5 == 1:
                    breve = str(e).split("(")[0].strip()
                    print(f"  intento {numero}: aun no ({breve})")
                continue

            print(f"  intento {numero}: {ip} -> {ms} ms")

            # Amazon se rinde a los 8 s. Por debajo de 1,5 s hay margen de
            # sobra, y significa que la conexion ya esta hecha.
            if ms < 1500:
                print()
                print(f"Camino caliente en el intento {numero}. Alexa ya tiene por donde entrar.")
                return 0
            break

        import time as _t
        _t.sleep(3)

    print()
    print("El camino sigue lento. Comprueba que el Funnel siga publicado:")
    print("  tailscale funnel status")
    return 1


if __name__ == "__main__":
    sys.exit(main())
