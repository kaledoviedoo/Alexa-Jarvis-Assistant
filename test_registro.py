"""Pruebas del registro unico de herramientas.

Comprueba que cada herramienta esta bien declarada, que el modelo nunca
recibe ni ejecuta nada externo o irreversible, y que el planificador y el
freno salen del mismo sitio. No toca el equipo.

Ejecutar:  py test_registro.py
"""

import json
import re
import sys

import registro

# Las 29 que el modelo ya usaba antes del registro. Ninguna puede perderse.
DE_SIEMPRE = {
    "crear_archivo", "leer_archivo", "editar_archivo", "mover_archivo", "buscar_archivo",
    "listar_archivos", "abrir_aplicacion", "cerrar_aplicacion", "buscar_en_navegador",
    "abrir_sitio", "escribir_texto", "ejecutar_atajo", "captura_pantalla", "estado_sistema",
    "cambiar_modo", "eliminar_archivo", "eliminar_varios", "copiar_archivo", "crear_carpeta",
    "buscar_en_contenido", "explorar_carpeta", "archivos_recientes", "informe_completo",
    "info_equipo", "obsidian_crear_nota", "obsidian_agregar_a_nota",
    "obsidian_agregar_al_diario", "obsidian_buscar", "recordar_en_contexto",
}

fallos: list[str] = []


def comprobar(condicion: bool, texto: str) -> None:
    print(f"  {'OK   ' if condicion else 'FALLO'}  {texto}")
    if not condicion:
        fallos.append(texto)


def main() -> int:
    print("=" * 70)
    print("  REGISTRO DE HERRAMIENTAS")
    print("=" * 70)

    todas = registro.cargar()
    comprobar(len(todas) >= 60, f"hay al menos 60 herramientas registradas ({len(todas)})")
    comprobar(DE_SIEMPRE <= set(todas), "estan las 29 de siempre")
    nucleo = {n for n, h in todas.items() if h.nucleo}
    comprobar(nucleo == DE_SIEMPRE, "el nucleo son exactamente las 29 de siempre")

    print()
    print("CADA HERRAMIENTA BIEN DECLARADA")
    malas = []
    for n, h in todas.items():
        problemas = []
        if not re.fullmatch(r"[a-z][a-z0-9_]*", n):
            problemas.append("nombre no es snake_case")
        if h.riesgo not in registro.RIESGOS:
            problemas.append(f"riesgo {h.riesgo}")
        if not 10 <= len(h.descripcion) <= registro.LARGO_MAXIMO_DESCRIPCION:
            problemas.append(f"descripcion de {len(h.descripcion)} caracteres")
        esquema = h.esquema()
        try:
            json.dumps(esquema)
        except TypeError:
            problemas.append("esquema no serializable")
        props = esquema["function"]["parameters"]["properties"]
        if not set(esquema["function"]["parameters"]["required"]) <= set(props):
            problemas.append("requeridos fuera de properties")
        if any(not d for _, _, d in h.parametros):
            problemas.append("parametro sin descripcion")
        if not h.nucleo and not h.palabras and not h.en_plan:
            problemas.append("ni nucleo, ni palabras, ni plan: nunca se ofreceria")
        if h.riesgo in ("escritura",) and h.nucleo is False and h.corroboran == () and h.palabras:
            problemas.append("escritura sin pistas para el freno")
        if problemas:
            malas.append(f"{n}: {', '.join(problemas)}")
    for m in malas:
        print("         ", m)
    comprobar(not malas, f"las {len(todas)} tienen nombre, riesgo, descripcion y esquema validos")

    print()
    print("LO EXTERNO NUNCA LLEGA AL MODELO")
    # Para esta prueba todo cuenta como disponible: en Linux no hay Outlook.
    original = registro.disponible
    registro.disponible = lambda h: True
    try:
        frases = ["mandale un whatsapp a familia que llego en 10",
                  "envia un correo a mi jefe", "responde el ultimo correo",
                  "cierra lo que sobra y libera memoria", ""]
        for frase in frases:
            ofrecidas = registro.para_el_modelo(frase, extras=50)
            peligrosas = [h.nombre for h in ofrecidas if h.riesgo not in registro.OFRECIBLES]
            comprobar(not peligrosas, f"'{frase[:40]}' no ofrece externas ({peligrosas or 'ninguna'})")

        ofrecidas = {h.nombre for h in registro.para_el_modelo("lee mis correos sin leer", extras=4)}
        comprobar("correos_sin_leer" in ofrecidas, "'lee mis correos sin leer' ofrece correos_sin_leer")
        comprobar(len(ofrecidas) <= 29 + 4, f"respeta el tope de extras ({len(ofrecidas)} <= 33)")

        ofrecidas = {h.nombre for h in registro.para_el_modelo("que ventanas tengo abiertas", extras=4)}
        comprobar("listar_ventanas" in ofrecidas, "'que ventanas tengo abiertas' ofrece listar_ventanas")

        plan = registro.para_el_plan()
        comprobar(all(h.riesgo in registro.OFRECIBLES for h in plan.values()),
                  f"el plan solo usa lectura y escritura ({len(plan)} acciones)")
        comprobar({"buscar_notas", "buscar_significado", "investigar"} <= set(plan),
                  "el plan conserva buscar_notas, buscar_significado e investigar")
    finally:
        registro.disponible = original

    print()
    print("EL MODELO NO PUEDE SALTARSE EL REGISTRO")
    import ollama_client
    ollama_client.recordar_peticion("mandale un whatsapp a familia que llego en 10")
    salida = ollama_client._ejecutar_herramienta(
        "enviar_mensaje", {"destinatario": "familia", "mensaje": "llego en 10"})
    comprobar("No ejecuto enviar_mensaje" in salida, "enviar_mensaje pedido por nombre se rechaza")

    salida = ollama_client._ejecutar_herramienta("inventada", {})
    comprobar("No existe" in salida, "una herramienta inventada se rechaza")

    ollama_client.recordar_peticion("hola que tal")
    salida = ollama_client._ejecutar_herramienta("crear_archivo", {"nombre_archivo": "x.txt"})
    comprobar("No ejecuto crear_archivo" in salida, "el freno sigue vetando crear_archivo en un saludo")

    comprobar(set(ollama_client.DESPACHADOR) == set(todas), "DESPACHADOR sale del registro")
    comprobar(ollama_client.HERRAMIENTAS_QUE_TOCAN == registro.corroboracion(),
              "las pistas del freno salen del registro")

    print()
    print("EL PLANIFICADOR PARTE EL TEXTO POR LA FIRMA")
    vistos = {}
    h = registro.Herramienta(
        nombre="prueba", funcion=lambda titulo, contenido="": vistos.update(t=titulo, c=contenido) or "ok",
        descripcion="Herramienta de prueba", riesgo="lectura",
        parametros=(("titulo", "string", "t"), ("contenido", "string", "c")), requeridos=("titulo",))
    registro.llamar_con_texto(h, "Compras | leche y pan | con barra")
    comprobar(vistos == {"t": "Compras", "c": "leche y pan | con barra"},
              "'titulo | contenido' llega partido en su sitio")

    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS")
        for f in fallos:
            print("   -", f)
        return 1
    print("  TODO CORRECTO. Una sola fuente de verdad para las herramientas.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
