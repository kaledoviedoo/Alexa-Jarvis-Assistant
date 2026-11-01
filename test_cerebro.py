"""Pruebas de la Fase 4: el cerebro.

Verificar despues de actuar, parar tras dos fallos, no dejar partes de la
orden sin hacer, afinar las busquedas con el contexto del usuario, apagar
el "pensamiento" de qwen3 y puntuar el banco de modelos. Con un Ollama
falso: no hace falta GPU ni modelos.

Ejecutar:  py test_cerebro.py
"""

import os
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

os.environ["JARVIS_VERIFICAR_FIRMA"] = "false"

import ollama_client   # noqa: E402
import registro        # noqa: E402
import verificar       # noqa: E402

fallos: list[str] = []


def comprobar(condicion: bool, texto: str) -> None:
    print(f"  {'OK   ' if condicion else 'FALLO'}  {texto}")
    if not condicion:
        fallos.append(texto)


guion: list[dict] = []
pedidos: list[dict] = []


def _chat(**kwargs):
    pedidos.append(kwargs)
    return guion.pop(0) if guion else {"message": {"content": "Listo."}}


def _llamada(_herramienta, **args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": _herramienta, "arguments": args}}]}}


def _sin_vectores(*a, **k):
    raise RuntimeError("sin ollama")


sys.modules["ollama"] = types.SimpleNamespace(chat=_chat, embeddings=_sin_vectores,
                                              list=lambda: {"models": []}, generate=lambda **k: None)
registro._vectores = None


def main() -> int:
    print("=" * 70)
    print("  FASE 4: EL CEREBRO")
    print("=" * 70)

    print("\nQWEN3 CONTESTA SIN 'PENSAR' EN VOZ ALTA")
    pedidos.clear()
    ollama_client.chat_modelo(model="qwen3:8b", messages=[])
    ollama_client.chat_modelo(model="llama3.2:3b", messages=[])
    comprobar(pedidos[0].get("think") is False, "a qwen3 se le pide think=False")
    comprobar("think" not in pedidos[1], "a llama no se le toca nada")

    print("\nMAS PASOS EN MODO DEDICADO")
    import modes
    comprobar(modes.PERFILES["dedicado"]["max_pasos"] == 8 and modes.PERFILES["normal"]["max_pasos"] == 4,
              "dedicado 8 pasos, normal 4")

    print("\nLA BUSQUEDA SE AFINA CON EL CONTEXTO, SIN CAMBIAR LO PEDIDO")
    from tools import avanzado, investigar
    original_ctx, original_chat = avanzado.leer_contexto, investigar._chat
    avanzado.leer_contexto = lambda: "Estudiante de Ingenieria de Sistemas / Software."
    try:
        respuestas = iter(["como funciona un transformer inteligencia artificial",
                           "transformadores electricos",
                           "recetas de arepas"])
        investigar._chat = lambda **k: {"message": {"content": next(respuestas)}}
        a = investigar.afinar_consulta("como funciona un transformer")
        comprobar(a == "como funciona un transformer inteligencia artificial", f"'transformer' -> '{a}'")
        b = investigar.afinar_consulta("como funciona un transformer")
        comprobar(b == "como funciona un transformer", "si el modelo cambia la consulta, se ignora")
        c = investigar.afinar_consulta("recetas de arepas")
        comprobar(c == "recetas de arepas", "si no hace falta, queda igual")
        largo = "cuanto cuesta una rtx 4060 de 8 gigas en colombia hoy"
        comprobar(investigar.afinar_consulta(largo) == largo, "una consulta larga ni se toca")
    finally:
        avanzado.leer_contexto, investigar._chat = original_ctx, original_chat

    print("\nVERIFICAR EN EL DISCO DE VERDAD")
    from tools import archivos, obsidian
    tmp = Path(tempfile.mkdtemp())
    original_resolver, original_vault = archivos.resolver_ruta, obsidian.vault
    archivos.resolver_ruta = lambda nombre, base=None: tmp / nombre
    obsidian.vault = lambda: tmp
    try:
        (tmp / "compras.txt").write_text("leche", encoding="utf-8")
        comprobar(verificar.comprobar("crear_archivo", {"nombre_archivo": "compras.txt"}, "Listo, creé compras.txt") == "",
                  "el archivo existe: verificado")
        motivo = verificar.comprobar("crear_archivo", {"nombre_archivo": "fantasma.txt"}, "Listo, creé fantasma.txt")
        comprobar("no esta" in motivo, f"el archivo no existe: '{motivo}'")
        comprobar(verificar.comprobar("crear_archivo", {"nombre_archivo": "x.txt"}, "No pude crear el archivo") == "",
                  "si la herramienta ya dijo que fallo, no se verifica")
        hoy = tmp / f"{datetime.now():%Y-%m-%d}.md"
        hoy.write_text("- 10:00 — parcial de redes el jueves\n", encoding="utf-8")
        comprobar(verificar.comprobar("obsidian_agregar_al_diario", {"contenido": "parcial de redes el jueves"}, "Añadido") == "",
                  "el diario de hoy tiene el texto: verificado")
        comprobar("no tiene" in verificar.comprobar("obsidian_agregar_al_diario", {"contenido": "comprar pan"}, "Añadido"),
                  "el diario no tiene el texto: falla")
    finally:
        archivos.resolver_ruta, obsidian.vault = original_resolver, original_vault

    print("\nDOS VERIFICACIONES FALLIDAS: SE PARA Y SE DICE QUE FALLO")

    @registro.herramienta("prueba_crear", descripcion="Crea algo (prueba).", riesgo="escritura",
                          args={"nombre": "Nombre"}, palabras=("crea",), corroboran=("crea",))
    def _crear(nombre: str) -> str:
        return f"Listo, creé {nombre}."

    @verificar.comprueba("prueba_crear")
    def _nunca(args, resultado):
        return "no aparece en el disco"

    guion[:] = [_llamada("prueba_crear", nombre="a.txt"), _llamada("prueba_crear", nombre="a.txt"),
                {"message": {"content": "Listo, ya está creado."}}]
    respuesta = ollama_client._conversar("crea el archivo a.txt")
    comprobar(respuesta.startswith("No lo conseguí: prueba crear: no aparece"),
              f"para y lo dice ('{respuesta[:60]}')")
    comprobar(len(guion) == 1, "y no deja que el modelo diga 'listo'")

    print("\nUNA PARTE DE LA ORDEN SIN HACER: SE INSISTE")
    apuntado = []

    @registro.herramienta("prueba_diario", descripcion="Apunta en el diario (prueba).", riesgo="escritura",
                          args={"contenido": "Que apuntar"}, palabras=("diario",), corroboran=("apunta",))
    def _diario(contenido: str) -> str:
        apuntado.append(contenido)
        return "Apuntado."

    @registro.herramienta("prueba_ventanas", descripcion="Lista ventanas (prueba).", palabras=("ventanas",))
    def _ventanas() -> str:
        return "Tienes 2 ventanas: Steam y Claude."

    pedidos.clear()
    guion[:] = [_llamada("prueba_ventanas"),
                {"message": {"content": "Tienes Steam y Claude abiertos."}},
                _llamada("prueba_diario", contenido="Ventanas: Steam y Claude"),
                {"message": {"content": "Apuntado en tu diario."}}]
    respuesta = ollama_client._conversar("mira que ventanas tengo y apunta en el diario cuales son")
    comprobar(apuntado == ["Ventanas: Steam y Claude"], f"tras insistir, apunta ({apuntado})")
    insistencia = [m for m in pedidos[-1]["messages"] if m.get("role") == "user"][-1]["content"]
    comprobar(insistencia.startswith("Te falta hacer esto de la orden: apunta en el diario"),
              "el modelo oye que parte le falta")
    comprobar(ollama_client.pasos_sin_hacer("mira que ventanas tengo y apunta en el diario cuales son",
                                            ["prueba_diario"]) == [], "hecha la parte, no falta nada")

    print("\nINSISTIDO Y SIGUE SIN HACERLO: LO HACE JARVIS O LO DICE")
    # con dos diarios (el real y el de prueba) no elegiria: se aparta el real
    diario_real = registro.cargar().pop("obsidian_agregar_al_diario", None)
    apuntado.clear()
    guion[:] = [_llamada("prueba_ventanas"),
                {"message": {"content": "Tienes Steam y Claude abiertos."}},
                {"message": {"content": "Ya lo hice en tu diario."}}]
    respuesta = ollama_client._conversar("mira que ventanas tengo y apunta en el diario cuales son")
    comprobar(apuntado == ["Tienes 2 ventanas: Steam y Claude."], f"apunta lo que se leyo ({apuntado})")
    comprobar("Steam y Claude" in respuesta and "Apuntado" in respuesta and "Ya lo hice" not in respuesta,
              f"dice lo que de verdad paso ('{respuesta[:80]}')")

    apuntado.clear()
    guion[:] = [{"message": {"content": "Listo."}}, {"message": {"content": "Ya lo hice en tu diario."}}]
    respuesta = ollama_client._conversar("mira que ventanas tengo y apunta en el diario cuales son")
    comprobar(apuntado == [] and respuesta.startswith("Me faltó esta parte: «apunta en el diario"),
              f"sin nada leido no inventa: dice que falto ('{respuesta[:70]}')")
    if diario_real is not None:
        registro.cargar()["obsidian_agregar_al_diario"] = diario_real
    comprobar(ollama_client.pasos_sin_hacer("abre spotify y busca tame impala", []) == [],
              "'...y busca' no es una parte que cambie algo")

    for n in ("prueba_crear", "prueba_diario", "prueba_ventanas"):
        registro._registro.pop(n, None)
    verificar._COMPROBACIONES.pop("prueba_crear", None)

    print("\nEL BANCO DE MODELOS PUNTUA BIEN")
    import banco_modelos as banco
    caso = ("anota en el diario que mañana tengo parcial de redes", {"obsidian_agregar_al_diario"},
            {"contenido": "parcial"})
    bien = banco.evaluar(caso, ["obsidian_agregar_al_diario"],
                         [{"function": {"name": "obsidian_agregar_al_diario",
                                        "arguments": {"contenido": "mañana tengo parcial de redes"}}}], "")
    comprobar(bien["acierto"] and bien["args_ok"] and bien["ofrecida"], "herramienta y argumentos bien")
    hueco = banco.evaluar(caso, ["obsidian_agregar_al_diario"],
                          [{"function": {"name": "obsidian_agregar_al_diario",
                                         "arguments": {"contenido": "[resultado de listar]"}}}], "")
    comprobar(hueco["acierto"] and not hueco["args_ok"], "un hueco en el argumento cuenta como fallo")
    charla = ("hola, cómo vas", {banco.NINGUNA}, {})
    comprobar(banco.evaluar(charla, [], [], "Todo bien, ¿y tú?")["acierto"], "charla sin herramientas: bien")
    comprobar(not banco.evaluar(charla, ["estado_sistema"], [], "Puedes usar la función estado_sistema")["acierto"],
              "describir una herramienta no cuenta como contestar")
    mal = banco.evaluar(caso, ["listar_ventanas"], [{"function": {"name": "listar_ventanas", "arguments": {}}}], "")
    comprobar(not mal["acierto"] and not mal["ofrecida"], "si no se le ofrecio la buena, se anota aparte")
    comprobar(len(banco.CASOS) >= 40, f"el banco tiene {len(banco.CASOS)} ordenes")
    nombres = set(registro.cargar())
    raras = {h for _, validas, _ in banco.CASOS for h in validas} - nombres - {banco.NINGUNA}
    comprobar(not raras, f"todas las herramientas esperadas existen ({sorted(raras) or 'si'})")

    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS")
        for f in fallos:
            print("   -", f)
        return 1
    print("  TODO CORRECTO. Comprueba lo que hace y dice lo que no le salio.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
