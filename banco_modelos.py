"""Banco de pruebas de modelos: que modelo elige mejor las herramientas.

Pasa unas 45 ordenes (las reales que llegaron al modelo estos dias y otras
que cubren todas las herramientas) por cada modelo instalado en Ollama, con
el mismo prompt y las mismas herramientas que usa Jarvis, y mide:

- si eligio la herramienta correcta (o ninguna, cuando no hacia falta),
- si los argumentos son validos (los obligatorios, sin huecos),
- si la herramienta correcta estaba entre las ofrecidas (para separar los
  fallos de la seleccion por significado de los del modelo),
- cuanto tardo y cuanta VRAM ocupo.

NO ejecuta ninguna herramienta: solo mira que haria el modelo. Todo es local
y gratis. Conviene correrlo con Jarvis quieto, porque comparten la grafica.

    py banco_modelos.py                       # todos los modelos instalados
    py banco_modelos.py qwen3:4b llama3.2:3b  # solo estos
    py banco_modelos.py --rapido              # 12 ordenes, para probar

El resultado queda en ~/.jarvis/banco/ como tabla markdown.
"""

import json
import sys
import time
from datetime import datetime

NINGUNA = "(ninguna)"

# (orden, herramientas validas como primer paso, {argumento: texto que debe contener})
CASOS = [
    # ---- Reales: llegaron al modelo estos dias ----
    ("qué programas hay abiertos", {"listar_ventanas"}, {}),
    ("tengo algun programa de steam en primer plano o en las ventanas", {"listar_ventanas"}, {}),
    ("segun mis apuntes que es una integral por partes",
     {"explicar_desde_notas", "encontrar_nota", "que_tengo_de", "obsidian_buscar", "buscar_notas",
      "buscar_significado"}, {}),
    ("me qué es la fotosíntesis", {NINGUNA}, {}),
    ("mi hermano me manda mensajes todo el dia", {NINGUNA}, {}),
    ("fijate que ventanas tengo abiertas y apunta en el diario cuales son", {"listar_ventanas"}, {}),
    ("mira que ventanas tengo abiertas y mandale a familia por whatsapp cuales son", {"listar_ventanas"}, {}),
    ("por qué consume tantos recursos",
     {"diagnostico_rendimiento", "estado_sistema", "informe_completo", "informe_para_jugar"}, {}),
    ("qué puedo hacer con un sticker NFC", {NINGUNA, "investigar", "buscar_en_navegador"}, {}),
    ("pega el texto copiado", {"ejecutar_atajo"}, {"nombre_atajo": "peg"}),

    # ---- Archivos ----
    ("crea un archivo llamado compras.txt que diga leche y pan", {"crear_archivo"},
     {"nombre_archivo": "compras"}),
    ("léeme el archivo notas.txt", {"leer_archivo"}, {"nombre_archivo": "notas"}),
    ("mueve el informe.docx a documentos", {"mover_archivo"}, {"origen": "informe"}),
    ("busca en mis archivos dónde escribí sobre subredes", {"buscar_en_contenido", "buscar_significado"}, {}),
    ("qué archivos modifiqué esta semana", {"archivos_recientes"}, {}),
    ("qué hay en la carpeta descargas", {"listar_archivos", "explorar_carpeta", "entrar_en_carpeta"}, {}),
    ("entra a descargas y selecciona los pdf", {"entrar_en_carpeta"}, {}),

    # ---- Obsidian y contexto ----
    ("anota en el diario que mañana tengo parcial de redes", {"obsidian_agregar_al_diario"},
     {"contenido": "parcial"}),
    ("crea una nota llamada ideas del proyecto", {"obsidian_crear_nota"}, {"titulo": "ideas"}),
    ("recuerda que prefiero las respuestas cortas", {"recordar_en_contexto"}, {"contenido": "cort"}),
    ("hazme preguntas de repaso de cálculo", {"preguntas_de_repaso"}, {}),

    # ---- Correo, WhatsApp, Teams ----
    ("qué correos tengo sin leer", {"correos_sin_leer", "ultimos_correos"}, {}),
    ("lee el último correo", {"leer_correo", "ultimos_correos"}, {}),
    ("mándale a familia por whatsapp que llego en 10", {"enviar_mensaje", "abrir_chat"},
     {"destinatario": "familia"}),
    ("qué tengo pendiente en teams", {"actividad_teams", "leer_teams"}, {}),

    # ---- Pantalla y ventanas ----
    ("qué dice la pantalla ahora", {"leer_pantalla", "describir_pantalla"}, {}),
    ("pulsa el botón de aceptar", {"clic_por_intencion"}, {}),
    ("pasa a la ventana de spotify", {"cambiar_a_ventana"}, {}),
    ("minimiza todo", {"minimizar_todo", "ejecutar_atajo"}, {}),
    ("toma una captura de pantalla", {"captura_pantalla"}, {}),
    ("escribe hola qué tal", {"escribir_texto"}, {"texto": "hola"}),

    # ---- Internet ----
    ("investiga qué es un transformer en inteligencia artificial", {"investigar"}, {}),
    ("compara la rtx 4060 con la rx 7600", {"comparar", "investigar"}, {}),
    ("cuál es la mejor silla gamer barata", {"mejor_opcion", "investigar"}, {}),
    ("busca en internet recetas de arepas", {"buscar_en_navegador", "investigar"}, {}),
    ("abre la página de github", {"abrir_sitio"}, {"nombre": "github"}),

    # ---- Equipo y programas ----
    ("por qué está lento el computador", {"diagnostico_rendimiento", "estado_sistema"}, {}),
    ("cuánta ram me queda", {"estado_sistema", "info_equipo", "informe_completo"}, {}),
    ("abre spotify", {"abrir_aplicacion"}, {"nombre_app": "spotify"}),
    ("cierra chrome", {"cerrar_aplicacion"}, {"nombre_app": "chrome"}),
    ("cambia al modo gaming", {"cambiar_modo"}, {"nuevo_modo": "gaming"}),

    # ---- Charla: no hay que tocar nada ----
    ("hola, cómo vas", {NINGUNA}, {}),
    ("qué es la oferta y la demanda", {NINGUNA}, {}),
    ("cuánto es 15 por 4", {NINGUNA}, {}),
    ("ayer me acosté tardísimo y hoy no rindo", {NINGUNA}, {}),
]

RAPIDOS = [0, 1, 2, 3, 4, 5, 10, 17, 21, 31, 38, 41]


def evaluar(caso, ofrecidas: list[str], llamadas: list[dict], contenido: str) -> dict:
    """Puntua una respuesta. Separado para poder probarlo sin Ollama."""
    import ollama_client
    import registro

    frase, validas, args_esperados = caso
    primera = llamadas[0] if llamadas else None
    nombre = (primera or {}).get("function", {}).get("name") or NINGUNA
    argumentos = ollama_client._desenvolver_argumentos(
        (primera or {}).get("function", {}).get("arguments", {}) or {})

    acierto = nombre in validas
    # Contestar con texto describiendo una herramienta no cuenta como "ninguna".
    if nombre == NINGUNA and NINGUNA in validas and ollama_client.describe_en_vez_de_actuar(
            contenido, [{"function": {"name": n}} for n in ofrecidas]):
        acierto = False

    args_ok = True
    if acierto and nombre != NINGUNA:
        h = registro.obtener(nombre)
        requeridos = h.requeridos if h else ()
        if any(not str(argumentos.get(r, "")).strip() for r in requeridos):
            args_ok = False
        if any(ollama_client._HUECO.search(str(v)) for v in argumentos.values()):
            args_ok = False
        for clave, texto in args_esperados.items():
            if texto.lower() not in str(argumentos.get(clave, "")).lower():
                args_ok = False

    ofrecida = NINGUNA in validas or bool(set(validas) & set(ofrecidas))
    return {"frase": frase, "eligio": nombre, "acierto": acierto, "args_ok": args_ok,
            "ofrecida": ofrecida, "argumentos": argumentos}


def _vram_gb(ollama, modelo: str) -> float:
    try:
        datos = ollama.ps()
        modelos = datos.get("models") if isinstance(datos, dict) else getattr(datos, "models", [])
        for m in modelos or []:
            nombre = m.get("name") if isinstance(m, dict) else getattr(m, "name", "")
            if nombre and nombre.split(":")[0] == modelo.split(":")[0]:
                vram = m.get("size_vram") if isinstance(m, dict) else getattr(m, "size_vram", 0)
                return round((vram or 0) / 1e9, 1)
    except Exception:
        pass
    return 0.0


def probar_modelo(ollama, modelo: str, casos: list) -> dict:
    import modes
    import ollama_client
    import registro
    import sesion

    perfil = dict(modes.PERFILES["dedicado"], modelo=modelo, num_ctx=8192, num_gpu=-1)
    original = modes.perfil_actual
    modes.perfil_actual = lambda: perfil
    filas, tiempos = [], []
    try:
        registro.preparar_vectores()
        for i, caso in enumerate(casos, 1):
            sesion.olvidar()
            frase = caso[0]
            ofrecidas = ollama_client.herramientas_para(frase)
            nombres = [e["function"]["name"] for e in ofrecidas]
            mensajes = [{"role": "system", "content": ollama_client.construir_prompt_sistema()},
                        {"role": "user", "content": frase}]
            inicio = time.perf_counter()
            try:
                r = ollama_client.chat_modelo(
                    model=modelo, messages=mensajes, tools=ofrecidas, keep_alive="10m",
                    options={"num_ctx": 8192, "num_gpu": -1, "temperature": 0.2, "num_predict": 200})
                mensaje = r.get("message", {}) if isinstance(r, dict) else r.message
                if not isinstance(mensaje, dict):
                    mensaje = mensaje.model_dump() if hasattr(mensaje, "model_dump") else dict(mensaje)
                llamadas = mensaje.get("tool_calls") or []
                contenido = mensaje.get("content") or ""
            except Exception as e:
                if "does not support tools" in str(e):
                    print("   este modelo no sabe usar herramientas: se salta")
                    return None
                llamadas, contenido = [], f"ERROR: {e}"
            segundos = time.perf_counter() - inicio
            if i > 1:                     # la primera incluye cargar el modelo
                tiempos.append(segundos)
            fila = evaluar(caso, nombres, llamadas, contenido)
            fila["segundos"] = round(segundos, 1)
            filas.append(fila)
            marca = "OK " if fila["acierto"] and fila["args_ok"] else ("arg" if fila["acierto"] else "MAL")
            print(f"   [{marca}] {segundos:4.1f}s  {frase[:52]:<52} -> {fila['eligio']}"
                  + ("" if fila["ofrecida"] else "  (no se le ofrecio la buena)"))
        vram = _vram_gb(ollama, modelo)
    finally:
        modes.perfil_actual = original

    total = len(filas)
    return {
        "modelo": modelo,
        "aciertos": sum(f["acierto"] and f["args_ok"] for f in filas),
        "herramienta_bien": sum(f["acierto"] for f in filas),
        "no_ofrecida": sum(not f["ofrecida"] for f in filas),
        "total": total,
        "segundos": round(sum(tiempos) / len(tiempos), 1) if tiempos else 0.0,
        "vram_gb": vram,
        "filas": filas,
    }


def _tabla(resultados: list[dict]) -> str:
    lineas = ["| Modelo | Acierto completo | Herramienta bien | Seleccion fallo | Seg/orden | VRAM |",
              "| --- | --- | --- | --- | --- | --- |"]
    for r in sorted(resultados, key=lambda r: -r["aciertos"]):
        t = r["total"] or 1
        lineas.append(f"| {r['modelo']} | {100 * r['aciertos'] // t} % ({r['aciertos']}/{t}) | "
                      f"{100 * r['herramienta_bien'] // t} % | {r['no_ofrecida']} | {r['segundos']} | "
                      f"{r['vram_gb']} GB |")
    return "\n".join(lineas)


def main() -> int:
    import logging
    logging.disable(logging.WARNING)

    try:
        import ollama
    except ImportError:
        print("Falta el paquete ollama: py -m pip install ollama")
        return 1

    import ollama_client
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    casos = [CASOS[i] for i in RAPIDOS] if "--rapido" in sys.argv else CASOS
    modelos = args or [m for m in ollama_client.modelos_instalados() if "embed" not in m]
    if not modelos:
        print("No hay modelos en Ollama.")
        return 1

    print(f"Banco de modelos: {len(casos)} ordenes x {len(modelos)} modelos. No se ejecuta nada.\n")
    resultados = []
    for modelo in modelos:
        print(f"== {modelo}")
        r = probar_modelo(ollama, modelo, casos)
        if r is None:
            print()
            continue
        resultados.append(r)
        print(f"   -> {r['aciertos']}/{r['total']} completos, {r['segundos']} s/orden, {r['vram_gb']} GB\n")
        try:
            ollama.generate(model=modelo, prompt="", keep_alive=0)   # soltar la VRAM
        except Exception:
            pass

    tabla = _tabla(resultados)
    print(tabla)

    from config import CARPETA_DATOS
    carpeta = CARPETA_DATOS / "banco"
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / f"resultados-{datetime.now():%Y-%m-%d-%H%M}.md"
    detalle = []
    for r in resultados:
        detalle.append(f"\n## {r['modelo']}\n\n| Orden | Eligio | Bien | Args | Ofrecida | Seg |\n| --- | --- | --- | --- | --- | --- |")
        for f in r["filas"]:
            detalle.append(f"| {f['frase']} | {f['eligio']} | {'si' if f['acierto'] else 'no'} | "
                           f"{'si' if f['args_ok'] else 'no'} | {'si' if f['ofrecida'] else 'no'} | {f['segundos']} |")
    archivo.write_text(f"# Banco de modelos {datetime.now():%Y-%m-%d %H:%M}\n\n{tabla}\n" + "\n".join(detalle),
                       encoding="utf-8")
    (carpeta / "ultimo.json").write_text(json.dumps(
        [{k: v for k, v in r.items() if k != "filas"} for r in resultados], ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\nGuardado en {archivo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
