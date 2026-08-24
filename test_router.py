"""
Batería de pruebas del router determinista.

Sustituye las herramientas reales por dobles que solo registran qué se llamó,
así podemos comprobar el enrutado sin crear archivos ni cerrar programas.

Ejecutar:  py test_router.py
"""

import sys
from types import SimpleNamespace

import foco
import nlu

# -------------------------------------------------------------------------
# Dobles de prueba
# -------------------------------------------------------------------------
registro: list[tuple] = []


def _doble(nombre):
    def fn(*args, **kwargs):
        registro.append((nombre, args, kwargs))
        return f"[{nombre}]"
    return fn


nlu.archivos = SimpleNamespace(
    crear_archivo=_doble("crear_archivo"),
    leer_archivo=_doble("leer_archivo"),
    editar_archivo=_doble("editar_archivo"),
    mover_archivo=_doble("mover_archivo"),
    copiar_archivo=_doble("copiar_archivo"),
    eliminar_archivo=_doble("eliminar_archivo"),
    listar_archivos=_doble("listar_archivos"),
    buscar_archivo=_doble("buscar_archivo"),
    crear_carpeta=_doble("crear_carpeta"),
    eliminar_varios=_doble("eliminar_varios"),
)
nlu.sistema = SimpleNamespace(
    uso_cpu=_doble("cpu"),
    uso_ram=_doble("ram"),
    uso_disco=_doble("disco"),
    uso_gpu=_doble("gpu"),
    estado_general=_doble("estado"),
    procesos_pesados=_doble("procesos"),
    bateria=_doble("bateria"),
    abrir_aplicacion=_doble("abrir_app"),
    cerrar_aplicacion=_doble("cerrar_app"),
    cerrar_todo=_doble("cerrar_todo"),
    bloquear_equipo=_doble("bloquear"),
    suspender_equipo=_doble("suspender"),
    apagar_equipo=_doble("apagar"),
    cancelar_apagado=_doble("cancelar_apagado"),
    reiniciar_equipo=_doble("reiniciar"),
)
nlu.obsidian = SimpleNamespace(
    crear_nota=_doble("obs_crear"),
    agregar_a_nota=_doble("obs_agregar"),
    agregar_al_diario=_doble("obs_diario"),
    buscar_en_vault=_doble("obs_buscar"),
    estado_vault=_doble("obs_estado"),
)
nlu.avanzado = SimpleNamespace(
    informe_completo=_doble("informe"),
    info_equipo=_doble("info_equipo"),
    buscar_en_contenido=_doble("buscar_contenido"),
    explorar_carpeta=_doble("explorar"),
    archivos_recientes=_doble("recientes"),
    donde_esta_contexto=_doble("donde_contexto"),
    editar_contexto=_doble("recordar"),
)
nlu.tareas = SimpleNamespace(
    consultar_pendiente=_doble("pendiente"),
)
nlu.navegador = SimpleNamespace(
    buscar_en_navegador=_doble("buscar_web"),
    abrir_sitio=_doble("abrir_sitio"),
    abrir_navegador=_doble("abrir_navegador"),
    reproducir_en_youtube=_doble("youtube"),
)
nlu.entrada = SimpleNamespace(
    escribir_texto=_doble("escribir"),
    ejecutar_atajo=_doble("atajo"),
    captura_pantalla=_doble("captura"),
    desplazar=_doble("desplazar"),
    pulsar_tecla=_doble("pulsar"),
)
nlu.modes = SimpleNamespace(
    cambiar_modo=_doble("cambiar_modo"),
    describir_modo=_doble("describir_modo"),
)
nlu.tareas = SimpleNamespace(consultar_pendiente=_doble("pendiente"))

# Estas tres llegaron con los arreglos del registro del 22 de agosto.
# app_tiene_buscador NO es un doble tonto: su valor decide si la orden se
# queda dentro de la app o cae a la busqueda web, asi que la prueba tiene
# que ver la misma decision que tomaria el sistema real.
nlu.sistema.app_tiene_buscador = lambda nombre: (
    (nombre or "").strip().lower()
    if (nombre or "").strip().lower() in
    {"spotify", "spoti", "obsidian", "teams", "discord", "steam", "code", "whatsapp"}
    else ""
)
nlu.sistema.buscar_en_app = _doble("buscar_en_app")
nlu.sistema.archivos_mas_grandes = _doble("archivos_grandes")

# Sin este doble, "investiga X" reventaba dentro de _al_fondo y el error se
# leia como "no llamó ninguna herramienta", que despistaba mucho.
nlu.tareas.lanzar_en_segundo_plano = _doble("al_fondo")

nlu.ventanas = SimpleNamespace(
    listar=_doble("ventanas_listar"),
    cambiar_a=_doble("ventana_cambiar"),
    minimizar_todo=_doble("minimizar_todo"),
    maximizar_actual=_doble("maximizar"),
)

nlu.seleccion = SimpleNamespace(
    entrar_en=_doble("sel_entrar"),
    seleccionar=_doble("sel_coger"),
    que_hay=_doble("sel_que_hay"),
    leer_titulos=_doble("sel_titulos"),
    mover_a=_doble("sel_mover"),
    olvidar=_doble("sel_soltar"),
    contar=lambda: 3,
    archivar_en_boveda=lambda: "archivado",
)

nlu.pantalla = SimpleNamespace(
    leer_pantalla=_doble("pantalla_leer"),
    buscar_en_pantalla=_doble("pantalla_buscar"),
    clic_en=_doble("pantalla_clic"),
    describir_pantalla=_doble("pantalla_describir"),
    clic_por_intencion=_doble("clic_intencion"),
    encontrar_por_intencion=_doble("busca_intencion"),
    buscar_dentro_de_lo_que_veo=_doble("ocr_buscador"),
)
nlu.sistema.arrancar_partida = _doble("arrancar_partida")


# -------------------------------------------------------------------------
# Casos:  (frase, herramienta esperada, comprobación opcional de argumentos)
# -------------------------------------------------------------------------
CASOS = [
    # ---- El caso original que fallaba ----
    ("por favor crea el archivo llamado prueba punto py con el codigo print hola",
     "crear_archivo", lambda a, k: a[0] == "prueba.py" and a[1] == 'print("hola")'),
    ("crea un archivo llamado prueba.py con el código print hola",
     "crear_archivo", lambda a, k: a[0] == "prueba.py" and a[1] == 'print("hola")'),
    ("créame un archivo llamado notas punto txt que diga hola mundo",
     "crear_archivo", lambda a, k: a[0] == "notas.txt" and a[1] == "hola mundo"),
    ("hazme un archivo python llamado calculadora punto py con el codigo print resultado",
     "crear_archivo", lambda a, k: a[0] == "calculadora.py"),
    ("crea un archivo llamado mis notas punto txt",
     "crear_archivo", lambda a, k: a[0] == "mis_notas.txt"),
    ("crea un documento word llamado informe",
     "crear_archivo", lambda a, k: a[0] == "informe.docx"),
    ("crea un archivo excel llamado datos",
     "crear_archivo", lambda a, k: a[0] == "datos.xlsx"),
    ("crea un archivo llamado config punto json en descargas",
     "crear_archivo", lambda a, k: a[0] == "config.json" and a[2] == "descargas"),
    ("crea una carpeta llamada proyectos", "crear_carpeta", None),

    # ---- Modos ----
    ("activa el modo gaming", "cambiar_modo", lambda a, k: a[0] == "gaming"),
    ("modo juego", "cambiar_modo", lambda a, k: a[0] == "gaming"),
    ("me voy a jugar", "cambiar_modo", lambda a, k: a[0] == "gaming"),
    ("pon el modo dedicado", "cambiar_modo", lambda a, k: a[0] == "dedicado"),
    ("usa el modelo grande", "cambiar_modo", lambda a, k: a[0] == "dedicado"),
    ("vuelve al modo normal", "cambiar_modo", lambda a, k: a[0] == "normal"),
    ("sal del modo juego", "cambiar_modo", lambda a, k: a[0] == "normal"),
    ("en qué modo estás", "describir_modo", None),

    # ---- Métricas ----
    ("cómo está el cpu", "cpu", None),
    ("dime el uso del procesador", "cpu", None),
    ("cuánta memoria ram queda", "ram", None),
    ("cómo está la gráfica", "gpu", None),
    ("cuánta vram queda libre", "gpu", None),
    ("cuánto espacio libre tengo en el disco", "disco", None),
    ("dame el estado general del equipo", "estado", None),
    ("qué programas están consumiendo más", "procesos", None),

    # ---- Archivos: leer, editar, mover ----
    ("lee el archivo notas punto txt", "leer_archivo", lambda a, k: a[0] == "notas.txt"),
    ("qué dice el archivo config punto json", "leer_archivo", lambda a, k: a[0] == "config.json"),
    ("agrega una línea nueva al archivo notas punto txt",
     "editar_archivo", lambda a, k: a[0] == "notas.txt" and a[1] == "agregar"),
    ("reemplaza hola por adiós en notas punto txt",
     "editar_archivo", lambda a, k: a[0] == "notas.txt" and a[1] == "reemplazar"),
    ("mueve el archivo reporte punto docx a documentos",
     "mover_archivo", lambda a, k: a[0] == "reporte.docx" and a[1] == "documentos"),
    ("copia prueba punto py a descargas", "copiar_archivo", None),
    ("elimina el archivo basura punto txt", "eliminar_archivo", None),
    ("qué archivos hay en el escritorio", "listar_archivos", lambda a, k: a[0] == "escritorio"),
    ("muéstrame los archivos que hay en descargas", "listar_archivos", None),
    ("busca el archivo llamado informe", "buscar_archivo", None),

    # ---- Aplicaciones ----
    ("abre spotify", "abrir_app", lambda a, k: a[0] == "spotify"),
    ("inicia visual studio code", "abrir_app", None),
    ("cierra chrome", "cerrar_app", lambda a, k: a[0] == "chrome"),

    # ---- Navegador ----
    ("busca en internet el clima de bogotá", "buscar_web", None),
    ("búscame recetas de arepas", "buscar_web", None),
    # Ya no es una busqueda web de una linea: "investiga" dispara la
    # investigacion larga, que se lanza al fondo porque tarda mas de lo que
    # Alexa espera. La prueba decia "buscar_web" desde antes de ese cambio.
    ("investiga cómo funciona un transformer", "al_fondo", None),
    ("pon música relajante en youtube", "youtube", None),
    ("abre la página github", "abrir_sitio", None),

    # ---- Teclado y mouse ----
    ("toma una captura de pantalla", "captura", None),
    ("escribe hola qué tal", "escribir", lambda a, k: "hola" in a[0]),
    ("presiona el atajo copiar", "atajo", None),
    # Antes era el atajo win+d a ciegas; ahora lo hace el modulo de ventanas,
    # que ademas sabe decirte cuantas minimizo.
    ("minimiza todo", "minimizar_todo", None),
    ("sube el volumen", "atajo", None),

    # ---- Obsidian y contexto: nunca se habian probado ----
    # No habia dobles para estos modulos, asi que sus handlers podian estar
    # rotos sin que ninguna prueba se enterase.
    ("apunta en el diario que termine la configuracion", "obs_diario", None),
    ("busca en mis notas el proyecto", "obs_buscar", None),
    ("cuantas notas tengo", "obs_estado", None),
    ("dame un informe completo del equipo", "informe", None),
    ("que equipo tengo", "info_equipo", None),
    ("donde esta el contexto", "donde_contexto", None),
    ("que archivos he tocado hoy", "recientes", None),
    ("como quedo lo ultimo", "pendiente", None),

    # ---- Energía ----
    ("bloquea el equipo", "bloquear", None),
    ("cancela el apagado", "cancelar_apagado", None),

    # ---- Subjuntivos: la muestra "que {comando}" de Alexa los induce ----
    # "dile a mi asistente QUE CREE un archivo..." llega como "cree", no "crea".
    ("cree un archivo llamado prueba punto py con el codigo print hola",
     "crear_archivo", lambda a, k: a[0] == "prueba.py" and a[1] == 'print("hola")'),
    ("me diga como esta la grafica", "gpu", None),
    ("active el modo gaming", "cambiar_modo", lambda a, k: a[0] == "gaming"),
    ("abra spotify", "abrir_app", lambda a, k: a[0] == "spotify"),
    ("cierre chrome", "cerrar_app", lambda a, k: a[0] == "chrome"),
    ("lea el archivo notas punto txt", "leer_archivo", lambda a, k: a[0] == "notas.txt"),
    ("mueva reporte punto docx a documentos", "mover_archivo", None),
    ("busque en internet el clima de bogota", "buscar_web", None),
    ("tome una captura de pantalla", "captura", None),
    ("escriba hola que tal", "escribir", None),
    ("elimine el archivo basura punto txt", "eliminar_archivo", None),
    ("agregue una linea al archivo notas punto txt", "editar_archivo", None),
    ("bloquee el equipo", "bloquear", None),

    # ---- Alexa convierte "un" en el digito 1 ----
    # Cadena real capturada del log: el slot llego con "crea 1 archivo...".
    ("crea 1 archivo llamado prueba.py con el codigo print hola",
     "crear_archivo", lambda a, k: a[0] == "prueba.py" and a[1] == 'print("hola")'),
    ("cree 1 archivo llamado notas punto txt", "crear_archivo", lambda a, k: a[0] == "notas.txt"),
    ("crea 1 carpeta llamada proyectos", "crear_carpeta", None),
    ("toma 1 captura de pantalla", "captura", None),

    # ---- Sin nombre: Alexa fusiona el sustantivo con la extension ----
    # Cadena real del log: "cree un archivo punto py" llego como "1 archivo.py".
    ("cree 1 archivo.py con hola escrito dentro",
     "crear_archivo", lambda a, k: a[0] == "archivo.py" and a[1] == 'print("hola")'),
    ("crea un archivo.py con hola dentro",
     "crear_archivo", lambda a, k: a[0] == "archivo.py"),
    ("crea notas.txt con mis apuntes adentro",
     "crear_archivo", lambda a, k: a[0] == "notas.txt" and a[1] == "mis apuntes"),
    ("crea un archivo.py", "crear_archivo", lambda a, k: a[0] == "archivo.py"),

    # ---- Meta ----
    ("cómo quedó lo último", "pendiente", None),

    # ---------------------------------------------------------------
    # ORDENES QUE SE ENTENDIAN AL REVES  (registro real del 22-08)
    # ---------------------------------------------------------------
    # Estas ocho no fallaban: hacian otra cosa y sonaban convincentes,
    # que es el fallo peor. Cada una lleva al lado lo que hacia antes.

    # Hacia: buscar "tame impala" en Google.
    ("busca tame impala en spotify",
     "buscar_en_app", lambda a, k: a[0] == "tame impala" and a[1] == "spotify"),
    # Hacia: abrir Spotify y ADEMAS buscar en Google.
    ("abre spotify y busca tame impala",
     "buscar_en_app", lambda a, k: a[0] == "tame impala" and a[1] == "spotify"),
    ("abre obsidian y busca redes neuronales",
     "buscar_en_app", lambda a, k: a[1] == "obsidian"),
    ("busca calculo en obsidian", "buscar_en_app", lambda a, k: a[1] == "obsidian"),

    # Hacia: "Abriendo un pestaña y entra a cloud" (y no abria nada).
    ("abre 1 pestaña y entra a cloud", "abrir_sitio", lambda a, k: a[0] == "cloud"),
    ("abre una pestaña y entra a github", "abrir_sitio", lambda a, k: a[0] == "github"),
    ("abre una pestaña en comet y ve a youtube",
     "abrir_sitio", lambda a, k: a[0] == "youtube"),

    # Hacia: dar el porcentaje de RAM.
    ("qué archivo tiene más memoria", "archivos_grandes", None),
    ("cuál es el archivo que ocupa más espacio", "archivos_grandes", None),
    ("archivos más grandes", "archivos_grandes", None),

    # Hacia: irse al modelo, que se puso a explorar carpetas.
    ("le click donde dice equipos",
     "pantalla_clic", lambda a, k: a[0] == "equipos"),
    ("haz clic donde dice aceptar", "pantalla_clic", lambda a, k: a[0] == "aceptar"),
    ("dale click donde aparece descargar",
     "pantalla_clic", lambda a, k: a[0] == "descargar"),

    # Hacia: intentar cerrar un programa llamado "voz baja".
    ("apaga el modo voz baja", "describir_modo", None),
    ("quita el modo silencioso", "describir_modo", None),

    # "comic" es como Alexa escribe "comet". Hacia: abrir una app
    # llamada "1 pestaña en comic".
    ("abre 1 pestaña en comic", "abrir_navegador", None),
    ("abre comic", "abrir_app", lambda a, k: a[0] in ("comic", "comet")),

    # ---------------------------------------------------------------
    # SELECCION DE ARCHIVOS
    # ---------------------------------------------------------------
    # El equivalente hablado de arrastrar el raton sobre varios archivos.
    ("entra a descargas", "sel_entrar", lambda a, k: a[0] == "descargas"),
    ("entra a la carpeta parciales", "sel_entrar", lambda a, k: a[0] == "parciales"),
    ("métete en escritorio", "sel_entrar", lambda a, k: a[0] == "escritorio"),

    # "los 3 primeros" es alfabetico y "los 3 mas recientes" es por fecha.
    # En Descargas los dos ordenes no se parecen en nada, asi que la
    # diferencia tiene que sobrevivir al router.
    ("selecciona los 3 primeros", "sel_coger", lambda a, k: a[1] == 3 and a[2] is False),
    ("coge los cinco primeros archivos", "sel_coger", lambda a, k: a[1] == 5),
    ("selecciona los 3 más recientes", "sel_coger", lambda a, k: a[1] == 3 and a[2] is True),
    ("selecciona todos los pdf", "sel_coger", lambda a, k: a[0] == "pdf"),
    ("selecciona los word", "sel_coger", lambda a, k: a[0] == "word"),
    ("selecciona los que digan calculo", "sel_coger", lambda a, k: a[0] == "calculo"),

    ("qué tengo seleccionado", "sel_que_hay", None),
    ("léeme los títulos", "sel_titulos", None),
    ("cómo se llaman", "sel_titulos", None),
    ("muévelos a documentos", "sel_mover", lambda a, k: a[0] == "documentos"),
    ("pásalos a la carpeta parciales", "sel_mover", lambda a, k: a[0] == "parciales"),
    ("archívalos en la bóveda", "al_fondo", None),
    ("olvida la selección", "sel_soltar", None),

    # ---------------------------------------------------------------
    # RAZONAR SOBRE LO QUE SE VE
    # ---------------------------------------------------------------
    # Un lanzador no es el juego: "abre valorant" dejaba Riot Client abierto
    # esperando a que alguien pinchara JUGAR.
    ("juega valorant", "arrancar_partida", lambda a, k: "valorant" in a[0]),
    ("ponte a jugar lol", "arrancar_partida", None),
    ("abre epic games y dale a jugar", "arrancar_partida", None),

    # Por lo que HACE el boton, no por como se llama: en Epic pone JUGAR, en
    # Steam PLAY, y en algunos sitios es solo un icono.
    ("dale a jugar", "clic_intencion", lambda a, k: a[0] == "jugar"),
    ("dale al buscador", "clic_intencion", lambda a, k: a[0] == "buscar"),
    ("pincha en aceptar", "clic_intencion", lambda a, k: a[0] == "aceptar"),
    ("dale a cancelar", "clic_intencion", lambda a, k: a[0] == "cerrar"),

    # ---------------------------------------------------------------
    # MEMORIA DEL PROPIO CODIGO
    # ---------------------------------------------------------------
    # "donde esta X" a secas NO entra aqui: es ambiguo y se llevaba por
    # delante "donde esta el contexto". Hace falta decir codigo o archivo.
    ("indexa el código", "al_fondo", None),
    ("indexa el proyecto", "al_fondo", None),
    ("dónde está el contexto", "donde_contexto", None),
]

# Frases que SI se resuelven aqui, pero contestando en vez de ejecutar.
# No caben en CASOS porque alli se exige que se llame a una herramienta.
SIN_HERRAMIENTA = [
    # Alexa corto la frase. Buscar la palabra "que" en internet, que es lo
    # que hacia antes, es peor que pedir que la repita.
    "busca qué",
    "busca eso",
    "búscame algo",
]

# Frases que NO deben resolverse localmente: tienen que ir al modelo.
DEBEN_IR_AL_MODELO = [
    "resume el contenido del informe y dime si vale la pena",
    "explícame qué es la fotosíntesis",
    "qué opinas de mi código",
    "escríbeme un correo formal para pedir vacaciones y guárdalo",
]


def probar_intenciones() -> list[str]:
    """
    Que cada frase canonica de la capa semantica siga siendo una orden real.

    La capa de intencion no ejecuta nada: traduce lo que dijiste a una frase
    que el router entiende y la vuelve a pasar por el. Toda su utilidad
    depende de que esas frases canonicas SIGAN estando en nlu.INTENTS.

    Si alguien cambia un patron y una canonica deja de casar, la capa no da
    error: devuelve una traduccion que no lleva a ninguna parte y la orden se
    va al modelo, o sea al comportamiento lento de antes, sin que nadie se
    entere. Esta prueba es lo unico que lo hace visible.

    No se comprueban los parecidos porque eso necesita el modelo de vectores
    corriendo; aqui solo se valida la parte que puede romperse en frio.
    """
    from tools import intencion
    fallos = []

    print()
    print("-" * 70)
    print("  INTENCIONES: LAS CANONICAS SIGUEN SIENDO ORDENES")
    print("-" * 70)

    for canonica in intencion.EJEMPLOS:
        registro.clear()
        foco.olvidar()
        try:
            respuesta = nlu.enrutar(canonica)
        except Exception as e:
            fallos.append(f"CANONICA {canonica!r} revienta: {e}")
            print(f"  FALLO  {canonica[:44]:<46} -> excepción: {e}")
            continue

        if not respuesta:
            fallos.append(f"CANONICA {canonica!r} ya no la reconoce el router")
            print(f"  FALLO  {canonica[:44]:<46} -> el router no la reconoce")
        else:
            quien = registro[0][0] if registro else "(sin herramienta)"
            print(f"  OK     {canonica[:44]:<46} -> {quien}")

    # Y que ningun ejemplo este duplicado entre dos ordenes distintas: eso
    # seria un empate garantizado, y los empates se descartan a proposito.
    vistos = {}
    for canonica, variantes in intencion.EJEMPLOS.items():
        for frase in variantes:
            if frase in vistos:
                fallos.append(f"EJEMPLO duplicado {frase!r}: {vistos[frase]} y {canonica}")
                print(f"  FALLO  ejemplo repetido: {frase!r}")
            vistos[frase] = canonica

    return fallos


def probar_keep_warm() -> list[str]:
    """
    Que la grafica descanse cuando no la usas, sin que se note al volver.

    El keep-warm tocaba el modelo cada 90 segundos las 24 horas. No gastaba
    calculo, pero dejaba 2 GB clavados en la VRAM todo el dia. Ahora solo se
    toca dentro de la ventana de gracia, y al abrir la skill se precalienta
    mientras suena el saludo.
    """
    import mantener_caliente
    fallos = []

    print()
    print("-" * 70)
    print("  KEEP-WARM: LA GRAFICA SOLO CALIENTE CUANDO HACE FALTA")
    print("-" * 70)

    original = mantener_caliente._ultima_actividad
    tocados = []
    modes_falso = SimpleNamespace(precalentar_modelo=lambda: tocados.append(1))
    sys.modules["modes"] = modes_falso
    try:
        casos = [
            ("recien arrancado, nadie ha hablado", None, False),
            ("acabas de dar una orden", 0, True),
            (f"{mantener_caliente.MINUTOS_DE_GRACIA - 1} min sin hablar",
             (mantener_caliente.MINUTOS_DE_GRACIA - 1) * 60, True),
            (f"{mantener_caliente.MINUTOS_DE_GRACIA + 5} min sin hablar",
             (mantener_caliente.MINUTOS_DE_GRACIA + 5) * 60, False),
        ]

        for etiqueta, hace_cuanto, debe_tocar in casos:
            if hace_cuanto is None:
                mantener_caliente._ultima_actividad = 0.0
            else:
                mantener_caliente.marcar_actividad()
                mantener_caliente._ultima_actividad -= hace_cuanto

            tocados.clear()
            mantener_caliente._tocar_modelo()
            toco = bool(tocados)

            if toco == debe_tocar:
                estado = "calienta" if toco else "deja enfriar"
                print(f"  OK     {etiqueta:<42} -> {estado}")
            else:
                fallos.append(f"KEEP-WARM {etiqueta}: {'tocó' if toco else 'no tocó'} y no tocaba")
                print(f"  FALLO  {etiqueta:<42} -> {'tocó' if toco else 'no tocó'}")
    finally:
        sys.modules.pop("modes", None)
        mantener_caliente._ultima_actividad = original

    return fallos


def probar_freno_de_mano() -> list[str]:
    """
    Que la conversacion normal no acabe tocando el equipo.

    Del registro real, hablando sin dar ninguna orden: escribio texto en la
    ventana que hubiera al frente, intento cerrar Alexa, y llamo a cerrar
    con 'nada' y con el nombre de otra herramienta como argumento.

    Contestar mal se nota y se repite. Escribir en una ventana que no
    mirabas o cerrar algo con trabajo sin guardar, no.
    """
    import ollama_client
    fallos = []

    print()
    print("-" * 70)
    print("  FRENO: CONVERSACION QUE NO DEBE EJECUTAR NADA")
    print("-" * 70)

    casos = [
        # (lo que dijo, herramienta, argumentos, debe ejecutarse)
        ("estoy en la cama y no tengo ganas de levantarme pon carajo entonces le digo",
         "escribir_texto", {"texto": "carajo entonces le digo"}, False),
        ("que no le puedo hablar aca porque no me ha detectado lo que estoy "
         "diciendo yo simplemente ella escucha todo lo que digo",
         "cerrar_aplicacion", {"nombre_app": "Alexa"}, False),
        ("dime como va todo", "cerrar_aplicacion", {"nombre_app": "nada"}, False),
        ("que hora es", "cerrar_aplicacion", {"nombre_app": "estado_sistema"}, False),
        ("hablame de la tarea", "eliminar_archivo", {"nombre_archivo": "prueba"}, False),
        ("cierra alexa", "cerrar_aplicacion", {"nombre_app": "alexa"}, False),

        # Y las ordenes de verdad, que tienen que seguir pasando. Un freno
        # que ademas bloquea lo legitimo no sirve de nada.
        ("cierra spotify", "cerrar_aplicacion", {"nombre_app": "spotify"}, True),
        ("escribe hola que tal", "escribir_texto", {"texto": "hola que tal"}, True),
        ("elimina el archivo basura.txt",
         "eliminar_archivo", {"nombre_archivo": "basura.txt"}, True),
        ("mueve informe.docx a documentos",
         "mover_archivo", {"origen": "informe.docx", "destino": "documentos"}, True),
        ("activa el modo gaming", "cambiar_modo", {"modo": "gaming"}, True),

        # Las que solo miran pasan siempre: equivocarse ahi no cuesta nada.
        ("estaba pensando en que no se muy bien que archivos tengo por ahi "
         "tirados en el escritorio",
         "listar_archivos", {"carpeta": "escritorio"}, True),
    ]

    for frase, herramienta, argumentos, debe_pasar in casos:
        ollama_client.recordar_peticion(frase)
        veto = ollama_client._por_que_no(herramienta, argumentos)
        paso = (veto == "")
        etiqueta = "pasa" if paso else "FRENADO"
        if paso == debe_pasar:
            print(f"  OK     {herramienta:<19} {etiqueta:<8} {frase[:34]!r}")
        else:
            fallos.append(f"FRENO {herramienta} con {argumentos}: {etiqueta} y no tocaba")
            print(f"  FALLO  {herramienta:<19} {etiqueta:<8} {frase[:34]!r}")

    ollama_client.recordar_peticion("")
    return fallos


def probar_modelos_instalados() -> list[str]:
    """
    Que Jarvis vea los modelos que de verdad tienes descargados.

    Esto no da error cuando se rompe: devuelve una lista vacia, que se lee
    como "no tienes nada instalado". Por eso el arranque decia que faltaba
    nomic-embed-text con el modelo ya descargado. Se prueban las dos formas
    de respuesta de la biblioteca de Ollama, la vieja y la nueva.
    """
    import sys as _sys
    import types as _types
    fallos = []

    print()
    print("-" * 70)
    print("  MODELOS INSTALADOS EN OLLAMA")
    print("-" * 70)

    class _Modelo:
        def __init__(self, m):
            self.model = m

    class _Respuesta:
        def __init__(self, ms):
            self.models = ms

    falso = _types.ModuleType("ollama")
    anterior = _sys.modules.get("ollama")
    _sys.modules["ollama"] = falso
    try:
        import ollama_client

        casos = [
            ("dict (biblioteca vieja)",
             {"models": [{"model": "llama3.2:3b"}, {"name": "nomic-embed-text:latest"}]},
             ["llama3.2:3b", "nomic-embed-text:latest"]),
            ("objeto (biblioteca nueva)",
             _Respuesta([_Modelo("llama3.2:3b"), _Modelo("nomic-embed-text:latest")]),
             ["llama3.2:3b", "nomic-embed-text:latest"]),
            ("objeto con dicts dentro",
             _Respuesta([{"model": "nomic-embed-text:latest"}]),
             ["nomic-embed-text:latest"]),
            ("sin modelos", {"models": []}, []),
        ]

        for etiqueta, respuesta, esperado in casos:
            falso.list = lambda r=respuesta: r
            salida = ollama_client.modelos_instalados()
            if salida == esperado:
                print(f"  OK     {etiqueta:<32} -> {salida}")
            else:
                fallos.append(f"MODELOS {etiqueta}: {salida} en vez de {esperado}")
                print(f"  FALLO  {etiqueta:<32} -> {salida}")

        # Lo que decide de verdad si la memoria semantica arranca.
        falso.list = lambda: _Respuesta([_Modelo("nomic-embed-text:latest")])
        ve = any("nomic-embed-text" in m for m in ollama_client.modelos_instalados())
        if ve:
            print(f"  OK     {'reconoce nomic-embed-text':<32} -> sí")
        else:
            fallos.append("MODELOS no reconoce nomic-embed-text")
            print(f"  FALLO  {'reconoce nomic-embed-text':<32} -> no")
    finally:
        if anterior is None:
            _sys.modules.pop("ollama", None)
        else:
            _sys.modules["ollama"] = anterior

    return fallos


def probar_limpieza_modelo() -> list[str]:
    """
    Lo que el modelo escribe de mas y Alexa acaba leyendo en voz alta.

    Del registro: el modelo contesto 'listar_archivos \\nEn Desktop hay 108
    archivos.' y el altavoz dijo "listar guion bajo archivos" antes de la
    frase util. No es un fallo del router: la respuesta era correcta, venia
    con el nombre de la herramienta pegado delante.
    """
    import ollama_client
    fallos = []

    print()
    print("-" * 70)
    print("  LIMPIEZA DE LA RESPUESTA DEL MODELO")
    print("-" * 70)

    casos = [
        # (entrada, lo que debe quedar)
        ("listar_archivos \nEn Desktop hay 108 archivos.",
         "En Desktop hay 108 archivos."),
        ("estado_sistema: El procesador está al 12 por ciento.",
         "El procesador está al 12 por ciento."),
        ("abrir_aplicacion()  Abriendo Spotify.", "Abriendo Spotify."),
        # Y lo que NO se debe tocar: un nombre de archivo real con guion bajo
        # al principio de la frase se parece muchisimo a una herramienta.
        ("notas_clase.md está en Documentos.", "notas_clase.md está en Documentos."),
        ("En Desktop hay 108 archivos.", "En Desktop hay 108 archivos."),
    ]

    for entrada, esperado in casos:
        salida = ollama_client.limpiar_respuesta_modelo(entrada)
        if salida == esperado:
            print(f"  OK     {entrada[:44]!r:<48} -> {salida[:26]!r}")
        else:
            fallos.append(f"LIMPIEZA {entrada!r} -> {salida!r}")
            print(f"  FALLO  {entrada[:44]!r:<48} -> {salida[:40]!r}")

    # El nombre a secas no se lee: se reconoce el fallo.
    solo = ollama_client.limpiar_respuesta_modelo("listar_archivos")
    if "listar_archivos" in solo:
        fallos.append(f"LIMPIEZA nombre suelto -> {solo!r}")
        print(f"  FALLO  {'nombre de herramienta a secas':<48} -> {solo!r}")
    else:
        print(f"  OK     {'nombre de herramienta a secas':<48} -> {solo[:26]!r}")

    return fallos


def probar_confirmaciones() -> list[str]:
    """
    Las ordenes sin vuelta atras no se ejecutan a la primera.

    Se comprueba aqui y no arriba porque no son un "patron -> funcion": son
    dos turnos. La primera vez preguntan, y solo el "si" del turno siguiente
    dispara la accion. Un fallo en esto significa que Alexa puede apagarte el
    equipo por haber oido mal, que ya paso una vez.
    """
    import confirmaciones
    fallos = []

    print()
    print("-" * 70)
    print("  ORDENES DESTRUCTIVAS: CONFIRMACION EN DOS TURNOS")
    print("-" * 70)

    casos = [
        ("apaga el equipo en 5 minutos", "apagar", (5,)),
        ("reinicia el equipo", "reiniciar", ()),
        ("cierra todo", "cerrar_todo", ()),
    ]

    for frase, esperado, args_esperados in casos:
        confirmaciones.olvidar()
        registro.clear()

        primera = nlu.enrutar(frase)
        if registro:
            fallos.append(f"{frase!r} se ejecuto SIN confirmar")
            print(f"  FALLO  {frase[:40]:<40} -> se ejecuto sin preguntar")
            continue
        if not primera or "onfirmas" not in primera:
            fallos.append(f"{frase!r} no pidio confirmacion")
            print(f"  FALLO  {frase[:40]:<40} -> no pregunto")
            continue

        nlu.enrutar("si")
        if not registro or registro[-1][0] != esperado:
            fallos.append(f"{frase!r}: el 'si' no disparo {esperado}")
            print(f"  FALLO  {frase[:40]:<40} -> el 'si' no ejecuto {esperado}")
            continue
        if args_esperados and registro[-1][1] != args_esperados:
            fallos.append(f"{frase!r}: argumentos {registro[-1][1]} != {args_esperados}")
            print(f"  FALLO  {frase[:40]:<40} -> argumentos incorrectos")
            continue

        print(f"  OK     {frase[:40]:<40} -> pregunta, y el 'si' lo ejecuta")

    # Un "no" tiene que cancelar de verdad.
    confirmaciones.olvidar(); registro.clear()
    nlu.enrutar("apaga el equipo")
    nlu.enrutar("no")
    if registro:
        fallos.append("el 'no' no cancelo el apagado")
        print("  FALLO  el 'no' no cancelo el apagado")
    else:
        print("  OK     el 'no' cancela")

    # Cambiar de tema tambien cancela: la orden no puede quedarse armada
    # esperando un "si" que llegue veinte minutos despues por otra cosa.
    confirmaciones.olvidar(); registro.clear()
    nlu.enrutar("apaga el equipo")
    nlu.enrutar("cuanto uso de cpu tengo")
    nlu.enrutar("si")
    if any(l[0] == "apagar" for l in registro):
        fallos.append("un 'si' posterior a otro tema apago el equipo")
        print("  FALLO  cambiar de tema dejo el apagado armado")
    else:
        print("  OK     cambiar de tema descarta lo pendiente")

    confirmaciones.olvidar()
    return fallos


def main() -> int:
    fallos = []
    print("=" * 70)
    print("  PRUEBAS DEL ROUTER DETERMINISTA")
    print("=" * 70)

    fallos.extend(probar_confirmaciones())
    fallos.extend(probar_limpieza_modelo())
    fallos.extend(probar_modelos_instalados())
    fallos.extend(probar_freno_de_mano())
    fallos.extend(probar_keep_warm())
    fallos.extend(probar_intenciones())

    for frase, esperado, comprobar in CASOS:
        registro.clear()
        # Cada frase parte de cero. Sin esto, un caso que deje un
        # destinatario pendiente hace que el SIGUIENTE lo capture el patron
        # de "texto del mensaje", que es amplisimo a proposito. Paso: dos
        # casos daban por buenos enrutados que en realidad no ocurrian.
        foco.olvidar()
        try:
            resultado = nlu.enrutar(frase)
        except Exception as e:
            fallos.append(f"EXCEPCIÓN  {frase!r}: {e}")
            print(f"  ERROR  {frase[:52]:<52} -> excepción: {e}")
            continue

        if resultado is None:
            fallos.append(f"SIN RUTA   {frase!r} (esperaba {esperado})")
            print(f"  FALLO  {frase[:52]:<52} -> no coincidió ningún patrón")
            continue

        if not registro:
            fallos.append(f"SIN LLAMADA {frase!r}")
            print(f"  FALLO  {frase[:52]:<52} -> no llamó ninguna herramienta")
            continue

        nombre, args, kwargs = registro[0]

        if nombre != esperado:
            fallos.append(f"RUTA MALA  {frase!r}: {nombre} en vez de {esperado}")
            print(f"  FALLO  {frase[:52]:<52} -> {nombre} (esperaba {esperado})")
            continue

        if comprobar and not comprobar(args, kwargs):
            fallos.append(f"ARGS MALOS {frase!r}: {args}")
            print(f"  FALLO  {frase[:52]:<52} -> argumentos {args}")
            continue

        detalle = str(args[0])[:28] if args else ""
        print(f"  OK     {frase[:52]:<52} -> {nombre}({detalle})")

    print()
    print("-" * 70)
    print("  FRASES QUE SE CONTESTAN SIN TOCAR NADA")
    print("-" * 70)

    for frase in SIN_HERRAMIENTA:
        registro.clear()
        respuesta = nlu.enrutar(frase)
        if not respuesta:
            fallos.append(f"SIN RESPUESTA {frase!r} (se fue al modelo)")
            print(f"  FALLO  {frase[:52]:<52} -> no la resolvió el router")
        elif registro:
            fallos.append(f"EJECUTO ALGO {frase!r} -> {registro[0][0]}")
            print(f"  FALLO  {frase[:52]:<52} -> llamó a {registro[0][0]}")
        else:
            print(f"  OK     {frase[:52]:<52} -> {respuesta[:30]!r}")

    print()
    print("-" * 70)
    print("  FRASES QUE DEBEN DELEGARSE AL MODELO")
    print("-" * 70)

    for frase in DEBEN_IR_AL_MODELO:
        registro.clear()
        resultado = nlu.enrutar(frase)
        if resultado is None:
            print(f"  OK     {frase[:52]:<52} -> al modelo")
        else:
            fallos.append(f"NO DEBIÓ ENRUTAR {frase!r} -> {registro}")
            print(f"  FALLO  {frase[:52]:<52} -> lo capturó {registro[0][0] if registro else '?'}")

    print()
    print("=" * 70)
    total = len(CASOS) + len(SIN_HERRAMIENTA) + len(DEBEN_IR_AL_MODELO) + 6 + 5 + 12 + 4 + len(__import__('tools.intencion', fromlist=['x']).EJEMPLOS)
    if fallos:
        print(f"  RESULTADO: {total - len(fallos)}/{total} correctos, {len(fallos)} fallos")
        print("=" * 70)
        for fallo in fallos:
            print("   -", fallo)
        return 1

    print(f"  RESULTADO: {total}/{total} correctos. Router validado.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
