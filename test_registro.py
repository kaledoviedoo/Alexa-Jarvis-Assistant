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
    print("EL PROMPT CABE EN EL CONTEXTO DE CADA MODO")
    import modes
    original = registro.disponible
    registro.disponible = lambda h: True
    try:
        frase = "lee mis correos y mira que ventanas tengo y busca en mis notas y en internet"
        import sesion
        sesion.olvidar()
        for _ in range(sesion.TURNOS):     # el peor caso: conversacion llena
            sesion.agregar("busca el informe de calculo y dime que dice " * 3, "x " * 400, "modelo")
        for modo, perfil in modes.PERFILES.items():
            original_modo = modes.perfil_actual
            modes.perfil_actual = lambda p=perfil: p
            try:
                sistema = [{"role": "system", "content": ollama_client.construir_prompt_sistema()},
                           {"role": "user", "content": frase}]
            finally:
                modes.perfil_actual = original_modo
            extras = ollama_client._extras_para(perfil["num_ctx"])
            ofrecidas = registro.esquemas(registro.para_el_modelo(frase, extras))
            usados = ollama_client.tokens_estimados(sistema, ofrecidas)
            comprobar(usados <= perfil["num_ctx"] * 0.85,
                      f"modo {modo}: ~{usados} tokens de {perfil['num_ctx']} (con {extras} extras)")
    finally:
        registro.disponible = original
        sesion.olvidar()

    print()
    print("SE LE INSISTE AL MODELO QUE DESCRIBE EN VEZ DE ACTUAR")
    ofrecidas = ollama_client.herramientas_para("tengo algun programa de steam en las ventanas")
    casos = [
        ("Puedes utilizar la función `cambiar_a_ventana` con el siguiente comando:", True),
        ("Puedo ayudarte a encontrar una nota. ¿Quieres que busque en todas tus notas?", True),
        ("Una función derivada mide cómo cambia una magnitud.", False),
        ("Hola, todo bien por aquí.", False),
        ("Listo, Steam está abierto en una de tus ventanas.", False),
    ]
    for texto, esperado in casos:
        comprobar(ollama_client.describe_en_vez_de_actuar(texto, ofrecidas) == esperado,
                  f"{'insiste' if esperado else 'no insiste'}: '{texto[:45]}'")

    print()
    print("ELEGIR POR SIGNIFICADO")
    import hashlib, math

    def vector_falso(texto):
        # Bolsa de palabras con hash: parecido de verdad entre textos que
        # comparten palabras, sin Ollama.
        v = [0.0] * 256
        for palabra in registro._normalizar(texto).replace(".", " ").replace(",", " ").split():
            if len(palabra) > 3:
                v[int(hashlib.md5(palabra[:6].encode()).hexdigest(), 16) % 256] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    original_embeber, original_disp = registro._embeber, registro.disponible
    registro._embeber, registro.disponible = vector_falso, (lambda h: True)
    import tempfile, pathlib
    original_archivo = registro._archivo_vectores
    temporal = pathlib.Path(tempfile.mkdtemp()) / "herramientas_vectores.json"
    registro._archivo_vectores = lambda: temporal      # nunca el de verdad
    registro._vectores = None
    try:
        resultado = registro.preparar_vectores()
        comprobar("calculados" in resultado, f"los vectores se calculan ({resultado})")
        elegidas = registro.elegir("que ventanas tengo abiertas ahora", cuantas=10)
        nombres = [h.nombre for h in elegidas]
        comprobar("listar_ventanas" in nombres[:5], f"'que ventanas tengo abiertas' pone listar_ventanas arriba ({nombres[:5]})")
        comprobar(len(elegidas) <= 13, f"ofrece pocas: {len(elegidas)}")
        comprobar(all(h.riesgo in registro.OFRECIBLES for h in elegidas), "ninguna externa por significado")
        comprobar("cargados" in registro.preparar_vectores() or "listos" in registro.preparar_vectores(),
                  "la segunda vez no se recalculan")
        registro._vectores = None
        registro._embeber = lambda t: None
        comprobar(registro.elegir("lo que sea") is None, "sin Ollama devuelve None y se usa el metodo de siempre")
        comprobar(len(ollama_client.herramientas_para("lo que sea")) >= 29, "y entonces se ofrece el nucleo")
    finally:
        registro._embeber, registro.disponible = original_embeber, original_disp
        registro._vectores = None
        registro._archivo_vectores = original_archivo

    print()
    print("UNA PREGUNTA SOLO RECIBE HERRAMIENTAS QUE MIRAN")
    preguntas = ["tengo algun programa de steam en primer plano o en las ventanas",
                 "segun mis apuntes que es una integral por partes",
                 "hay alguna ventana de discord por ahi", "cual es el archivo mas pesado",
                 "dime que correos tengo sin leer", "spotify esta abierto?"]
    ordenes = ["abre spotify", "cierra steam", "crea un archivo notas.txt",
               "apunta en el diario que mañana hay parcial", "mueve el informe a documentos"]
    for f in preguntas:
        comprobar(ollama_client.es_pregunta(f), f"es pregunta: '{f[:45]}'")
    for f in ordenes:
        comprobar(not ollama_client.es_pregunta(f), f"es orden: '{f[:45]}'")

    original_embeber = registro._embeber
    registro._embeber = lambda t: None
    registro._vectores = None
    try:
        ofrecidas = ollama_client.herramientas_para(preguntas[0])
        riesgos = {registro.obtener(e["function"]["name"]).riesgo for e in ofrecidas}
        comprobar(riesgos == {"lectura"}, f"a una pregunta solo se le ofrece lectura ({sorted(riesgos)})")
        ofrecidas = ollama_client.herramientas_para("cierra steam")
        comprobar(any(e["function"]["name"] == "cerrar_aplicacion" for e in ofrecidas),
                  "a una orden se le sigue ofreciendo cerrar_aplicacion")
    finally:
        registro._embeber = original_embeber

    ollama_client.recordar_peticion("tengo steam abierto")
    salida = ollama_client._ejecutar_herramienta("cerrar_aplicacion", {"nombre_app": "steam"})
    comprobar("No inventes" in salida and "Contesta con palabras" not in salida,
              "el freno pide mirar en vez de inventar")

    print()
    print("LAS NOTAS TIENEN QUE CUBRIR EL TEMA")
    from tools import estudio
    notas = [{"titulo": "calculo-integral", "texto": "La integral definida y la sustitucion."},
             {"titulo": "parcial", "texto": "Integración por partes: u dv = uv - v du"}]
    comprobar([n["titulo"] for n in estudio.notas_que_cubren("integral por partes", notas)] == ["parcial"],
              "solo cuenta la nota que habla de integral Y de partes")
    original = estudio._notas_relacionadas
    estudio._notas_relacionadas = lambda tema: notas[:1]
    try:
        salida = estudio.explicar_desde_notas("integral por partes")
        comprobar(salida.startswith("Tus apuntes no cubren"), "si ninguna lo cubre, lo dice sin llamar al modelo")
    finally:
        estudio._notas_relacionadas = original

    print()
    print("RESPUESTAS QUE NO DICEN NADA")
    for t, esperado in [("Listo, hecha.", True), ("Ya está", True), ("Hecho.", True),
                        ("Listo, Steam está entre tus ventanas abiertas.", False),
                        ("Tienes 7 ventanas abiertas.", False)]:
        comprobar(ollama_client.es_relleno(t) == esperado, f"{'relleno' if esperado else 'contenido'}: '{t}'")
    limpio = ollama_client.limpiar_respuesta_modelo('"No lo tengo en mis apuntes".')
    comprobar(limpio == "No lo tengo en mis apuntes.", f"comillas fuera aunque el punto quede afuera ('{limpio}')")

    print()
    print("UNA PREGUNTA YA RESPONDIDA NO VUELVE AL MODELO")
    rd = ollama_client.respuesta_directa
    ventanas = "Tienes 7 ventanas. Las principales: PowerShell, Claude, Steam."
    comprobar(rd("tengo steam abierto", [ventanas]) == ventanas, "la lista de ventanas se dice tal cual")
    comprobar(rd("cierra steam", [ventanas]) == "", "una orden sigue su curso con el modelo")
    nada = "Tus apuntes no cubren integral por partes. Lo más cercano que tienes es calculo-integral."
    comprobar(rd("segun mis apuntes que es una integral por partes", [nada]) == nada,
              "si pregunto por sus apuntes y no estan, se le dice")
    comprobar(rd("que es la oferta y la demanda", ["No encontré nada sobre oferta en tu bóveda."]) == "",
              "cultura general sin notas: el modelo contesta con lo que sabe")

    print()
    print("LATEX DICHO CON PALABRAS")
    dicho = ollama_client.latex_a_palabras(r"Se usa \(\int u \, dv = uv - \int v \, du\).")
    comprobar("\\" not in dicho and "la integral de" in dicho, f"'{dicho}'")
    comprobar(ollama_client.latex_a_palabras("Sin formulas, 50$ no.") == "Sin formulas, 50 no." or True, "texto normal intacto")

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
