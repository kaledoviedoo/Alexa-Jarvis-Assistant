"""Pruebas de la memoria de la conversacion (Fase 2).

Que se apunten los turnos los resuelva quien los resuelva, que caduquen, que
lo que hace el modelo deje el foco para el router, y que el historial quepa
en el contexto. No toca el equipo ni necesita Ollama.

Ejecutar:  py test_sesion.py
"""

import os
import sys

os.environ["JARVIS_VERIFICAR_FIRMA"] = "false"

import foco          # noqa: E402
import registro      # noqa: E402
import sesion        # noqa: E402

fallos: list[str] = []


def comprobar(condicion: bool, texto: str) -> None:
    print(f"  {'OK   ' if condicion else 'FALLO'}  {texto}")
    if not condicion:
        fallos.append(texto)


def main() -> int:
    print("=" * 70)
    print("  MEMORIA DE LA CONVERSACION")
    print("=" * 70)

    print("\nTURNOS")
    sesion.olvidar()
    for i in range(1, 9):
        sesion.agregar(f"orden {i}", f"respuesta {i}", "router")
    t = sesion.turnos()
    comprobar(len(t) == sesion.TURNOS, f"se guardan los ultimos {sesion.TURNOS} turnos ({len(t)})")
    comprobar(t[0].orden == "orden 3" and t[-1].orden == "orden 8", "en orden, sin los mas viejos")

    bloque = sesion.para_el_modelo()
    comprobar("El usuario dijo: orden 8" in bloque and "respuesta 8" in bloque,
              "el modelo ve lo ultimo que se hablo")
    comprobar(bloque.index("orden 3") < bloque.index("orden 8"), "de mas antiguo a mas reciente")

    print("\nCADUCIDAD")
    original = sesion.time.monotonic
    try:
        ahora = original()
        sesion.time.monotonic = lambda: ahora + sesion.SEGUNDOS_VIDA + 5
        comprobar(sesion.turnos() == [], "tras 10 minutos sin hablar se empieza de cero")
    finally:
        sesion.time.monotonic = original

    print("\nLO LARGO SE RECORTA Y CABE")
    sesion.olvidar()
    for i in range(6):
        sesion.agregar("busca el informe de calculo " * 10, "Encontre esto: " + "x" * 2000, "modelo")
    bloque = sesion.para_el_modelo(1200)
    comprobar(len(bloque) <= 1400, f"con limite de 1200 el bloque ocupa {len(bloque)} caracteres")
    comprobar("..." in bloque, "las respuestas largas se recortan")

    print("\nTAREAS DE FONDO")
    sesion.olvidar()
    sesion.agregar("resume mis notas de redes", "Estoy trabajando en eso. Pregúntame cómo quedó.", "modelo")
    sesion.completar("resume mis notas de redes", "Tus notas de redes hablan de subnetting.")
    comprobar(sesion.turnos()[-1].respuesta.startswith("Tus notas de redes"),
              "cuando termina la tarea, el turno guarda el resultado real")

    print("\nEL ESTADO ENTRE MANOS")
    foco.recordar("archivo", "informe-calculo.pdf", lista=["informe-calculo.pdf", "taller-3.pdf"])
    estado = "\n".join(sesion.estado_actual())
    comprobar("Archivo en foco: informe-calculo.pdf" in estado, "el archivo en foco aparece")
    comprobar("2. taller-3.pdf" in estado, "y la ultima lista, numerada para 'el segundo'")

    print("\nLO QUE HACE EL MODELO LO VE EL ROUTER")
    import ollama_client
    foco.olvidar()

    @registro.herramienta("prueba_leer_archivo", descripcion="Lee un archivo de prueba, sin tocar el disco.",
                          args={"nombre_archivo": "Nombre del archivo"}, palabras=("prueba",))
    def _leer_falso(nombre_archivo: str) -> str:
        return f"El archivo {nombre_archivo} dice: hola."

    ollama_client.recordar_peticion("lee el archivo notas.txt")
    ollama_client._ejecutar_herramienta("prueba_leer_archivo", {"nombre_archivo": "notas.txt"})
    datos = foco.actual("archivo")
    comprobar(bool(datos) and datos["valor"] == "notas.txt",
              "si el modelo lee notas.txt, 'abrelo' por el router sabe cual es")

    @registro.herramienta("prueba_falla", descripcion="Herramienta de prueba que no encuentra nada.",
                          args={"nombre_archivo": "Nombre"}, palabras=("prueba",))
    def _falla(nombre_archivo: str) -> str:
        return "No encontré ese archivo."

    ollama_client._ejecutar_herramienta("prueba_falla", {"nombre_archivo": "fantasma.txt"})
    comprobar(foco.actual("archivo")["valor"] == "notas.txt", "lo que no se encontro no pasa a foco")
    del registro._registro["prueba_leer_archivo"], registro._registro["prueba_falla"]

    print("\nEL SERVIDOR APUNTA CADA ORDEN")
    import server
    sesion.olvidar()
    server.procesar_comando("en qué modo estás")
    t = sesion.turnos()
    comprobar(len(t) == 1 and t[0].origen == "router" and t[0].respuesta,
              "una orden del router queda en la conversacion")
    prompt = ollama_client.construir_prompt_sistema()
    comprobar("El usuario dijo: en qué modo estás" in prompt,
              "y el modelo la ve en su prompt en la orden siguiente")

    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS")
        for f in fallos:
            print("   -", f)
        return 1
    print("  TODO CORRECTO. Jarvis recuerda de que se esta hablando.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
