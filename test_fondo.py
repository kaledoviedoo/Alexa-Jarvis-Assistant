"""Pruebas de la Fase 5: tareas que sobreviven, avisos y rutinas.

Sin Windows ni Ollama: la base de tareas y las rutinas viven en una carpeta
temporal, los avisos solo se apuntan y los procesos de los juegos son falsos.

Ejecutar:  py test_fondo.py
"""

import json
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

os.environ["JARVIS_VERIFICAR_FIRMA"] = "false"
os.environ["JARVIS_AVISOS"] = "0"

TMP = Path(tempfile.mkdtemp(prefix="jarvis-fondo-"))

import avisos          # noqa: E402
import rutinas         # noqa: E402
import tareas          # noqa: E402

tareas.usar_base(TMP / "jarvis.db")
rutinas.usar_carpeta(TMP)

import nlu             # noqa: E402
import ollama_client   # noqa: E402
import registro        # noqa: E402

fallos: list[str] = []


def comprobar(condicion: bool, texto: str) -> None:
    print(f"  {'OK   ' if condicion else 'FALLO'}  {texto}")
    if not condicion:
        fallos.append(texto)


def esperar(condicion, segundos: float = 3.0) -> bool:
    fin = time.time() + segundos
    while time.time() < fin:
        if condicion():
            return True
        time.sleep(0.02)
    return condicion()


def main() -> int:
    print("=" * 70)
    print("  FASE 5: TAREAS, AVISOS Y RUTINAS")
    print("=" * 70)

    # ------------------------------------------------------------------
    print("\nLAS TAREAS SOBREVIVEN A UN REINICIO")
    tareas.registrar_inicio("a1", "investigar transformers")
    tareas.registrar_inicio("a2", "preparar examen de redes")
    tareas.registrar_resultado("a2", "preparar examen de redes", "Repasa subnetting y VLAN.")
    # Reinicio: se cierra la conexion y se vuelve a abrir el mismo archivo.
    tareas.usar_base(TMP / "jarvis.db")
    n = tareas.al_arrancar()
    comprobar(n == 1, f"al arrancar, la que corria queda interrumpida ({n})")
    comprobar(tareas.obtener("a1")["estado"] == "interrumpida", "y su estado lo dice")
    comprobar(tareas.obtener("a2")["estado"] == "hecha", "la que termino sigue terminada")
    texto = tareas.novedades()
    comprobar(texto.startswith("Por cierto, terminé «preparar examen de redes»")
              and "Repasa subnetting" in texto, f"se cuenta lo terminado ('{texto[:70]}')")
    comprobar("a medias «investigar transformers»" in texto, "y lo que quedo a medias")
    comprobar(tareas.novedades() == "", "contado una vez, no se repite")

    print("\nLO QUE YA SE OYO NO SALTA COMO AVISO")
    avisos.ENVIADOS.clear()
    tareas.registrar_inicio("b1", "abrir spotify")
    tareas.registrar_resultado("b1", "abrir spotify", "Abrí Spotify.", avisar=False)
    comprobar(not avisos.ENVIADOS, "respuesta a tiempo: sin aviso de Windows")
    comprobar(tareas.novedades() == "", "ni queda sin leer")
    ident = tareas.lanzar_en_segundo_plano("investigar algo largo", lambda: "Listo el resumen.")
    comprobar(esperar(lambda: (tareas.obtener(ident) or {}).get("estado") == "hecha"), "la de fondo termina")
    comprobar(esperar(lambda: bool(avisos.ENVIADOS)) and "Listo el resumen." in avisos.ENVIADOS[-1][1],
              "y salta el aviso con su resultado")
    ident = tareas.lanzar_en_segundo_plano("algo que falla", lambda: 1 / 0)
    comprobar(esperar(lambda: (tareas.obtener(ident) or {}).get("estado") == "fallo"),
              "una que lanza un error queda como fallo")
    ident = tareas.lanzar_en_segundo_plano("rutina sin nada que decir", lambda: None)
    comprobar(esperar(lambda: tareas.obtener(ident) is None), "la que no tiene nada que decir no deja rastro")
    comprobar(tareas.consultar_pendiente().startswith("La tarea falló"),
              "'como quedo lo ultimo' lee la base")

    print("\nCADA HERRAMIENTA QUEDA COMO UN PASO")
    tareas.registrar_inicio("c1", "orden de prueba")
    tareas.con_tarea("c1", lambda: (tareas.registrar_paso("listar_ventanas", {}, "2 ventanas"),
                                    tareas.registrar_paso("obsidian_agregar_al_diario",
                                                          {"contenido": "x"}, "Apuntado.")))
    p = tareas.pasos("c1")
    comprobar([x["herramienta"] for x in p] == ["listar_ventanas", "obsidian_agregar_al_diario"],
              f"los pasos, en orden ({[x['herramienta'] for x in p]})")
    tareas.registrar_paso("suelto", {}, "x")
    comprobar(len(tareas.pasos("c1")) == 2, "fuera de una tarea no se apunta nada")

    # ------------------------------------------------------------------
    print("\nEL AVISO DE WINDOWS NO SE ROMPE CON COMILLAS NI SIMBOLOS")
    s = avisos.script("Jarvis · O'Brien & <amigos>", "Dijo \"hola\" y 50 % de 'todo'")
    xml = s.split("LoadXml('", 1)[1].split("')", 1)[0]
    comprobar("'" not in xml, "ni una comilla simple dentro del texto para PowerShell")
    comprobar("&amp;" in xml and "&lt;amigos&gt;" in xml and "&apos;" in xml, "el XML va escapado")
    comprobar(avisos.APP_ID in s, "usa el identificador de PowerShell, sin registrar nada")
    largo = avisos.script("t", "palabra " * 200)
    comprobar(len(largo) < 1500 and "…" in largo, "un resultado largo se recorta")

    # ------------------------------------------------------------------
    print("\nLAS RUTINAS DE SERIE")
    lista = rutinas.cargar()
    nombres = [r["nombre"] for r in lista]
    comprobar(nombres == ["buenos dias", "descargas", "disco lleno", "modo juego", "memoria al dia"],
              f"se crean las 5 del plan ({nombres})")
    comprobar(rutinas.ruta_rutinas().exists(), "y quedan en rutinas.yaml para editarlas")
    comprobar(all(not set(r["riesgos"]) & {"externa", "irreversible"} for r in lista),
              "ninguna puede enviar ni hacer nada irreversible")
    mala = rutinas.validar({"nombre": "x", "disparador": {"tipo": "hora", "hora": "08:00"},
                            "orden": "manda un correo", "riesgos": ["lectura", "externa", "irreversible"]})
    comprobar(mala["riesgos"] == ["lectura"], "si alguien le pone externa a mano, se quita")
    comprobar(rutinas.validar({"nombre": "y", "disparador": {"tipo": "luna"}, "aviso": "x"}) is None,
              "un disparador que no existe se ignora")

    print("\nSE PUEDE EDITAR A MANO (Y SIN PYYAML TAMBIEN)")
    rutinas.ruta_rutinas().write_text(json.dumps([
        {"nombre": "agua", "activa": True, "disparador": {"tipo": "hora", "dias": [0], "hora": "10:00"},
         "aviso": "tomar agua"}]), encoding="utf-8")
    time.sleep(0.05)
    os.utime(rutinas.ruta_rutinas(), None)
    comprobar([r["nombre"] for r in rutinas.cargar()] == ["agua"], "un archivo en JSON tambien se lee")
    rutinas.guardar([dict(r) for r in rutinas.PREDETERMINADAS])

    print("\nCUANDO TOCA")
    lunes7 = datetime(2026, 11, 2, 7, 1)          # lunes
    bd = next(r for r in rutinas.cargar() if r["nombre"] == "buenos dias")
    comprobar(rutinas.toca(bd, lunes7, {}), "buenos dias, el lunes a las 7:01")
    comprobar(not rutinas.toca(bd, lunes7, {"ultima": lunes7.isoformat()}), "pero solo una vez")
    comprobar(rutinas.toca(bd, lunes7.replace(hour=9), {}), "si el PC estaba apagado, a las 9 aun cuenta")
    comprobar(not rutinas.toca(bd, lunes7.replace(hour=11), {}), "a las 11 ya no")
    comprobar(not rutinas.toca(bd, datetime(2026, 11, 1, 7, 1), {}), "el domingo no")
    comprobar(not rutinas.toca(dict(bd, activa=False), lunes7, {}), "apagada, no")
    mem = next(r for r in rutinas.cargar() if r["nombre"] == "memoria al dia")
    original = rutinas.pc_libre
    rutinas.pc_libre = lambda: False
    comprobar(not rutinas.toca(mem, lunes7.replace(hour=3, minute=5), {}), "memoria al dia espera al PC libre")
    rutinas.pc_libre = lambda: True
    comprobar(rutinas.toca(mem, lunes7.replace(hour=3, minute=5), {}), "y corre cuando lo esta")
    rutinas.pc_libre = original

    # ------------------------------------------------------------------
    print("\nORDENES PROGRAMADAS POR VOZ")
    domingo = datetime(2026, 11, 1, 15, 0)
    casos = [
        ("cada día a las 7 investiga noticias de inteligencia artificial",
         "investiga noticias de inteligencia artificial", "todos los días a las 7:00"),
        ("todos los lunes a las 8 revisa mis correos sin leer", "revisa mis correos sin leer",
         "los lunes a las 8:00"),
        ("de lunes a viernes a las 6 y media de la tarde dime cómo está el equipo",
         "dime cómo está el equipo", "de lunes a viernes a las 18:30"),
        ("los lunes y miércoles a las 9:30 busca ofertas de rtx 4060", "busca ofertas de rtx 4060",
         "los lunes y miércoles a las 9:30"),
    ]
    for frase, que, cuando in casos:
        r = rutinas.interpretar(frase, domingo)
        ok = isinstance(r, dict) and r.get("orden") == que and rutinas.describir(r, domingo) == cuando
        comprobar(ok, f"'{frase}' -> {que} | {cuando}"
                  + ("" if ok else f"  (salio {r if isinstance(r, str) else (r.get('orden'), rutinas.describir(r, domingo)) if r else None})"))
    comprobar(rutinas.interpretar(casos[0][0], domingo)["riesgos"] == ["lectura", "escritura"],
              "una orden creada por voz puede mirar y cambiar cosas que se deshacen, nunca enviar")

    print("\nACCIONES A UNA HORA, DICHAS COMO SALGAN")
    lunes = datetime(2026, 11, 2, 10, 35)
    for frase, orden, cuando in [
        ("quieor que a las 10:36 am de hoy abras valorant y le des a jugar", "juega valorant", "hoy a las 10:36"),
        ("quiero que a las 10:38 abras valorant y le des al boton que dice jugar", "juega valorant", "hoy a las 10:38"),
        ("a las 5 abre spotify", "abre spotify", "hoy a las 17:00"),
        ("abre spotify a las 9 de la noche", "abre spotify", "hoy a las 21:00"),
        ("en 20 minutos minimiza todo", "minimiza todo", "hoy a las 10:55"),
        ("necesito que a las 3 me digas como esta el equipo", "dime como esta el equipo", "hoy a las 15:00"),
    ]:
        r = rutinas.interpretar(frase, lunes)
        ok = isinstance(r, dict) and r["orden"] == orden and rutinas.describir(r, lunes) == cuando
        comprobar(ok, f"'{frase}' -> {orden} | {cuando}" + ("" if ok else f"  (salio {r})"))
    comprobar("necesita tu «sí» en vivo" in rutinas.interpretar("a las 6 mandale a familia que llego tarde", lunes),
              "enviar a una hora no se programa: necesita tu si")
    for frase in ("apaga el equipo a las 11", "en 5 minutos apaga el equipo", "que tengo a las 3"):
        comprobar(rutinas.interpretar(frase, lunes) is None, f"'{frase}' sigue su camino de siempre")
    comprobar(rutinas.a_imperativo("abras valorant y le des a jugar") == "abre valorant y dale a jugar",
              "del subjuntivo a la orden")
    comprobar("¿A qué hora?" in rutinas.interpretar("todos los lunes revisa mis correos", domingo),
              "sin hora, la pregunta")
    for frase in ("cada vez que abro chrome va lento", "todos los archivos pdf muévelos a documentos",
                  "recuerda que prefiero las respuestas cortas", "todos los lunes a las 8 recuérdame revisar GPIN",
                  "recuérdame a las 5 llamar a mamá"):
        comprobar(rutinas.interpretar(frase, domingo) is None, f"'{frase}' no es una rutina")

    print("\nLOS RECORDATORIOS SON COSA DE ALEXA")
    for frase in ("recuérdame en 20 minutos sacar la ropa", "todos los lunes a las 8 recuérdame revisar GPIN",
                  "qué recordatorios tengo"):
        respuesta = nlu.enrutar(frase) or ""
        comprobar("con Alexa" in respuesta and "Listo" not in respuesta,
                  f"'{frase}' -> lo manda a Alexa, sin decir 'listo'")

    print("\nEL ROUTER LAS ENTIENDE")
    respuesta = nlu.enrutar("todos los lunes a las 8 revisa mis correos sin leer")
    comprobar(respuesta == "Listo: los lunes a las 8:00 haré esto: revisa mis correos sin leer. Te aviso con el resultado.",
              f"crear ('{respuesta}')")
    comprobar(any(r.get("orden") == "revisa mis correos sin leer" for r in rutinas.cargar()), "y queda guardada")
    respuesta = nlu.enrutar("qué rutinas tengo")
    comprobar(respuesta.startswith("Tienes 6 rutinas activas") and "revisa mis correos" in respuesta,
              f"listar ('{respuesta[:60]}')")
    respuesta = nlu.enrutar("apaga la rutina de buenos días")
    comprobar(respuesta == "Apagué la rutina buenos dias.", f"apagar ('{respuesta}')")
    comprobar(not next(r for r in rutinas.cargar() if r["nombre"] == "buenos dias")["activa"], "y queda apagada")
    respuesta = nlu.enrutar("enciende la rutina de buenos días")
    comprobar(respuesta == "Encendí la rutina buenos dias.", f"encender ('{respuesta}')")
    respuesta = nlu.enrutar("borra la rutina de correos")
    comprobar(respuesta.startswith("Borré la rutina revisa mis correos"), f"borrar ('{respuesta}')")
    respuesta = nlu.enrutar("apaga todas las rutinas")
    comprobar(respuesta.startswith("Apagué las rutinas") and not any(r["activa"] for r in rutinas.cargar()),
              "apagar todas")
    nlu.enrutar("enciende todas las rutinas")
    respuesta = nlu.enrutar("apaga el equipo")
    comprobar("rutina" not in (respuesta or "").lower() and "?" in (respuesta or ""),
              f"'apaga el equipo' sigue pidiendo confirmacion de apagar ('{(respuesta or '')[:50]}')")
    import confirmaciones
    confirmaciones.resolver("no")

    # ------------------------------------------------------------------
    print("\nDESCARGAS: PROPONE DE NOCHE, MUEVE SOLO SI DICES QUE SI")
    desc = TMP / "Descargas"
    desc.mkdir()
    viejo = time.time() - 3600
    for nombre in ("taller.pdf", "foto.jpg", "foto2.png", "setup.exe", "bajando.pdf.crdownload", "raro.xyz"):
        (desc / nombre).write_text("x")
        os.utime(desc / nombre, (viejo, viejo))
    (desc / "recien.pdf").write_text("x")              # de hace un momento: no se toca
    (desc / "Boveda").mkdir()                           # carpetas: nunca
    rutinas._descargas = lambda: desc
    plan = dict(rutinas.plan_de_descargas(desc))
    comprobar(plan == {"foto.jpg": "Imagenes", "foto2.png": "Imagenes", "setup.exe": "Instaladores",
                       "taller.pdf": "PDF"}, f"propone solo lo suelto y terminado ({plan})")
    r = next(r for r in rutinas.cargar() if r["nombre"] == "descargas")
    texto = rutinas.proponer_descargas(r, {})
    comprobar("4 archivos" in texto and "No he movido nada" in texto, f"la propuesta ('{texto[:60]}')")
    comprobar((desc / "taller.pdf").exists(), "proponer no mueve nada")
    comprobar(rutinas.propuesta_pendiente() is not None, "la propuesta queda guardada para mañana")
    (desc / "Imagenes").mkdir()
    (desc / "Imagenes" / "foto.jpg").write_text("ya habia una")
    respuesta = nlu.enrutar("ordena las descargas")
    comprobar(respuesta.startswith("Ordené 4 archivos") and "No borré nada" in respuesta, f"'ordena las descargas' ('{respuesta}')")
    comprobar((desc / "PDF" / "taller.pdf").exists() and (desc / "Imagenes" / "foto (1).jpg").exists()
              and (desc / "Imagenes" / "foto.jpg").read_text() == "ya habia una",
              "mueve a subcarpetas sin sobrescribir nada")
    comprobar((desc / "recien.pdf").exists() and (desc / "raro.xyz").exists() and (desc / "Boveda").is_dir(),
              "lo reciente, lo desconocido y las carpetas se quedan donde estaban")
    comprobar(rutinas.propuesta_pendiente() is None, "y la propuesta se gasta")

    print("\nMODO JUEGO")
    import modes
    estado_modo = {"modo": "normal"}
    cambios = []
    modes_original = (modes.modo_actual,)
    modes.modo_actual = lambda: estado_modo["modo"]
    import dataclasses
    h = registro.obtener("cambiar_modo")
    registro.cargar()["cambiar_modo"] = dataclasses.replace(
        h, funcion=lambda nuevo_modo: (cambios.append(nuevo_modo), estado_modo.update(modo=nuevo_modo)) and "ok")
    try:
        r = next(r for r in rutinas.cargar() if r["nombre"] == "modo juego")
        est = {}
        nada = [("explorer.exe", r"C:\Windows\explorer.exe"), ("steam.exe", r"C:\Program Files (x86)\Steam\steam.exe")]
        valorant = nada + [("VALORANT-Win64-Shipping.exe", r"C:\Riot Games\VALORANT\live\ShooterGame\Binaries\Win64\VALORANT-Win64-Shipping.exe")]
        comprobar(rutinas.modo_juego(r, est, nada) is None and not cambios, "Steam abierto sin juego: nada")
        texto = rutinas.modo_juego(r, est, valorant)
        comprobar(cambios == ["gaming"] and texto.startswith("Abriste Valorant: pasé a modo gaming"),
                  f"al abrir Valorant, a gaming ('{(texto or '')[:40]}')")
        comprobar("cierra lo que sobra" in texto and "cerrar_prescindibles" not in str(cambios),
                  "cerrar programas solo lo sugiere: es irreversible")
        comprobar(rutinas.modo_juego(r, est, valorant) is None, "mientras juegas no repite")
        comprobar(rutinas.modo_juego(r, est, nada) is None, "un parpadeo sin el juego no cuenta")
        texto = rutinas.modo_juego(r, est, nada)
        comprobar(cambios == ["gaming", "normal"] and texto == "Cerraste el juego: volví al modo normal.",
                  f"al cerrarlo, vuelve al modo de antes ('{texto}')")
        fondo = [("wallpaper64.exe", r"C:\Program Files (x86)\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe")]
        comprobar(rutinas.juego_abierto(fondo) is None, "Wallpaper Engine no es un juego")
        comprobar(rutinas.juego_abierto([("x.exe", r"D:\steamapps\common\Mi Herramienta\x.exe")],
                                        ignorar=["mi herramienta"]) is None, "y se puede ignorar otra desde el YAML")
        steam = [("game.exe", r"D:\SteamLibrary\steamapps\common\Hollow Knight\hollow_knight.exe")]
        comprobar(rutinas.juego_abierto(steam) == "Hollow Knight", "reconoce un juego de Steam por su carpeta")
    finally:
        registro.cargar()["cambiar_modo"] = h
        modes.modo_actual = modes_original[0]

    print("\nUNA RUTINA NUNCA SE SALTA SU LIMITE DE RIESGO")
    r = {"nombre": "prueba", "riesgos": ["lectura"]}
    salida = rutinas.usar("crear_archivo", r, nombre_archivo="x.txt")
    comprobar(salida.startswith("No usé crear_archivo"), "una de solo lectura no escribe")
    salida = rutinas.usar("enviar_mensaje", {"nombre": "p", "riesgos": ["lectura", "escritura", "externa"]},
                          destinatario="familia", mensaje="hola")
    comprobar(salida.startswith("No usé enviar_mensaje"), "ni aunque se le ponga externa")
    ollama_client._empezar_turno()
    ollama_client.recordar_peticion("crea un archivo llamado x.txt")
    salida = ollama_client.con_riesgos(["lectura"], ollama_client._ejecutar_herramienta,
                                       "crear_archivo", {"nombre_archivo": "x.txt"})
    comprobar("viene de una rutina" in salida, "una orden de rutina que pasa por el modelo tampoco")
    salida = ollama_client.con_riesgos(["lectura", "escritura", "externa"], ollama_client._ejecutar_herramienta,
                                       "enviar_mensaje", {"destinatario": "familia", "mensaje": "hola"})
    comprobar("viene de una rutina" in salida, "y enviar sigue fuera aunque la rutina lo pida")
    salida = ollama_client._ejecutar_herramienta("inventada", {})
    comprobar("No existe" in salida, "fuera de la rutina, todo vuelve a la normalidad")

    print("\nEL PLANIFICADOR CORRE LO QUE TOCA Y BORRA LO DE UNA VEZ")
    rutinas.guardar([
        {"nombre": "sacar la ropa", "activa": True, "riesgos": ["lectura"], "aviso": "sacar la ropa",
         "disparador": {"tipo": "una_vez", "cuando": (datetime.now() - timedelta(minutes=1)).isoformat(timespec="minutes")}},
        {"nombre": "luego", "activa": True, "riesgos": ["lectura"], "aviso": "luego",
         "disparador": {"tipo": "una_vez", "cuando": (datetime.now() + timedelta(hours=2)).isoformat(timespec="minutes")}},
    ])
    avisos.ENVIADOS.clear()
    corridas = rutinas.tick()
    comprobar(corridas == ["sacar la ropa"], f"corre la que ya toco ({corridas})")
    comprobar(esperar(lambda: any("Recordatorio: sacar la ropa." in t for _, t in avisos.ENVIADOS)),
              "y salta el aviso")
    comprobar([r["nombre"] for r in rutinas.cargar()] == ["luego"], "la de una vez desaparece al cumplirse")
    comprobar(rutinas.tick() == [], "y no se repite")

    print("\nEL PLANIFICADOR NO ESCRIBE AL DISCO SI NADA CAMBIO")
    rutinas.guardar([{"nombre": "lejana", "activa": True, "riesgos": ["lectura"], "aviso": "x",
                      "disparador": {"tipo": "hora", "dias": [0], "hora": "04:00", "margen": 1}}])
    rutinas.tick()
    marca = rutinas._ruta_estado().stat().st_mtime_ns if rutinas._ruta_estado().exists() else None
    time.sleep(0.05)
    rutinas.tick()
    despues = rutinas._ruta_estado().stat().st_mtime_ns if rutinas._ruta_estado().exists() else None
    comprobar(marca == despues, "una vuelta sin novedades no toca el disco")
    comprobar(rutinas.SEGUNDOS_TICK >= 60, "y mira una vez por minuto, no cada 30 s")

    print("\nUNA ORDEN PROGRAMADA VA POR EL CAMINO DE SIEMPRE Y NO CONFIRMA SOLA")
    vistos = []

    def procesar(texto, datos, presupuesto, en_este_hilo=False):
        vistos.append((texto, ollama_client.canal_actual(), sorted(ollama_client._limite_riesgo.valor)))
        if "familia" in texto:
            return confirmaciones.pedir("mandarle a familia", lambda: "Enviado."), "modelo"
        return "Abrí Valorant y le di a JUGAR.", "router"

    rutinas.configurar(procesar)
    r = {"nombre": "juego", "orden": "juega valorant", "riesgos": ["lectura", "escritura"],
         "disparador": {"tipo": "una_vez", "cuando": "2026-11-02T10:38"}}
    salida = rutinas._orden(r, {})
    comprobar(salida == "Abrí Valorant y le di a JUGAR." and vistos[-1][0] == "juega valorant",
              "pasa por el router/modelo como cualquier orden")
    comprobar(vistos[-1][2] == ["escritura", "lectura"], "con el riesgo limitado a lectura y escritura")
    salida = rutinas._orden(dict(r, orden="mandale a familia que ya voy"), {})
    p = confirmaciones.pendiente()
    comprobar(p is not None and p["segundos"] > 100 and "Contéstame «sí» o «no»" in salida,
              f"si algo pide confirmacion, queda 2 minutos para contestar por voz o en el chat ({p})")
    confirmaciones.olvidar()
    rutinas.configurar(None)

    print("\nVALORANT ENTRA DIRECTO Y EL 'SI' DE CERRAR LO QUE SOBRA FUNCIONA")
    from tools import sistema, rendimiento
    lanzados = []
    originales = (sistema.cliente_riot, sistema.subprocess.Popen, rendimiento.candidatos_a_cerrar,
                  rendimiento.cerrar_prescindibles)
    sistema.cliente_riot = lambda: r"C:\Riot Games\Riot Client\RiotClientServices.exe"
    sistema.subprocess.Popen = lambda args, **k: lanzados.append(args)
    rendimiento.candidatos_a_cerrar = lambda: [{"nombre": "comet", "ram": 1.2, "cpu": 3}]
    cerrado = []
    rendimiento.cerrar_prescindibles = lambda: cerrado.append(1) or "Cerré comet."
    import modes
    modo_original = (modes.modo_actual, modes.cambiar_modo)
    modes.modo_actual = lambda: "gaming"
    try:
        texto = sistema.arrancar_partida("valorant")
        comprobar(lanzados and lanzados[-1][1:] == ["--launch-product=valorant", "--launch-patchline=live"],
                  f"se lanza como su acceso directo ({lanzados[-1] if lanzados else None})")
        comprobar(texto.startswith("Abriendo valorant: entra directo al juego") and "Di sí o no" in texto,
                  f"sin buscar el boton de jugar, y con la pregunta ('{texto[:70]}')")
        comprobar(confirmaciones.pendiente() is not None, "la pregunta queda pendiente de verdad")
        respuesta = confirmaciones.resolver("sí")
        comprobar(cerrado == [1] and "Cerré comet" in (respuesta or ""), "y el 'sí' cierra lo que sobra")
    finally:
        (sistema.cliente_riot, sistema.subprocess.Popen, rendimiento.candidatos_a_cerrar,
         rendimiento.cerrar_prescindibles) = originales
        modes.modo_actual, modes.cambiar_modo = modo_original
        confirmaciones.olvidar()

    print("\nSI EL PC ESTABA APAGADO A LA HORA, NO LO HACE HORAS DESPUES")
    avisos.ENVIADOS.clear()
    rutinas.guardar([{"nombre": "tarde", "activa": True, "riesgos": ["lectura", "escritura"], "orden": "juega valorant",
                      "disparador": {"tipo": "una_vez", "cuando": (datetime.now() - timedelta(hours=3)).isoformat(timespec="minutes")}}])
    comprobar(rutinas.tick() == ["tarde"], "la ve vencida")
    comprobar(any("el PC estaba apagado" in t for _, t in avisos.ENVIADOS), "y avisa que no la hizo")
    comprobar(rutinas.cargar() == [], "y la quita")

    print("\nEL DIARIO DE HOY SE PUEDE LEER")
    h = registro.obtener("obsidian_leer_diario")
    comprobar(h is not None and h.riesgo == "lectura", "obsidian_leer_diario existe y solo mira")

    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS")
        for f in fallos:
            print("   -", f)
        return 1
    print("  TODO CORRECTO. Termina lo que empieza, avisa y hace lo programado.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
