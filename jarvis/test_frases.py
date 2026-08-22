"""
Prueba de TOLERANCIA AL FRASEO.

test_router.py comprueba que cada frase acabe en el comando correcto.
Este archivo comprueba algo distinto y complementario: que la MISMA orden se
entienda dicha de muchas formas, para que no haya que memorizar una formula.

Cada grupo son variantes que deben resolverse todas localmente, sin tocar el
modelo. Si añades una forma nueva de decir algo, añádela aquí primero: la
prueba fallará, y eso te dice exactamente qué patrón ampliar.

Ejecutar:  py test_frases.py
"""

import sys
from types import SimpleNamespace

import nlu

# -------------------------------------------------------------------------
# Dobles: solo nos interesa QUE se enrute, no lo que devuelva la herramienta.
# -------------------------------------------------------------------------
_llamadas: list[str] = []


def _doble(nombre):
    def fn(*args, **kwargs):
        _llamadas.append(nombre)
        return f"[{nombre}]"
    return fn


for _mod, _nombres in {
    "archivos": ["crear_archivo", "leer_archivo", "editar_archivo", "mover_archivo",
                 "copiar_archivo", "eliminar_archivo", "listar_archivos",
                 "buscar_archivo", "crear_carpeta", "eliminar_varios"],
    "sistema": ["uso_cpu", "uso_ram", "uso_disco", "uso_gpu", "estado_general",
                "procesos_pesados", "bateria", "abrir_aplicacion", "cerrar_aplicacion",
                "bloquear_equipo", "suspender_equipo", "apagar_equipo",
                "cancelar_apagado", "reiniciar_equipo", "cerrar_todo"],
    "navegador": ["buscar_en_navegador", "abrir_sitio", "abrir_navegador",
                  "reproducir_en_youtube"],
    "entrada": ["escribir_texto", "ejecutar_atajo", "captura_pantalla",
                "desplazar", "pulsar_tecla"],
    "avanzado": ["informe_completo", "info_equipo", "buscar_en_contenido",
                 "explorar_carpeta", "archivos_recientes", "editar_contexto",
                 "donde_esta_contexto"],
    "obsidian": ["crear_nota", "agregar_a_nota", "agregar_al_diario",
                 "buscar_en_vault", "estado_vault"],
    "modes": ["cambiar_modo", "describir_modo"],
    "tareas": ["consultar_pendiente"],
}.items():
    setattr(nlu, _mod, SimpleNamespace(**{n: _doble(n) for n in _nombres}))


# -------------------------------------------------------------------------
# Grupos de variantes. Todas las de un grupo significan lo mismo.
# -------------------------------------------------------------------------
GRUPOS: dict[str, list[str]] = {
    "Crear archivo": [
        "crea un archivo llamado prueba punto py con el codigo print hola",
        "cree un archivo llamado prueba punto py con el codigo print hola",
        "crear un archivo llamado prueba punto py con el codigo print hola",
        "creame un archivo llamado prueba punto py con print hola",
        "hazme un archivo llamado prueba punto py con print hola",
        "genera un archivo llamado prueba punto py con print hola",
        "genereme un archivo llamado prueba punto py",
        "guarda un archivo llamado notas punto txt que diga hola",
        "construye un archivo llamado notas punto txt con hola",
        "nuevo archivo llamado notas punto txt",
        "crea un archivo de nombre notas punto txt",
        "crea un archivo con el nombre notas punto txt",
        "crea un archivo que se llame notas punto txt",
        "crea un archivo nombrado notas punto txt",
        "crea un archivo notas punto txt en descargas",
        "crea un archivo notas punto txt dentro de documentos",
        "crea un archivo punto py con hola escrito dentro",
        "crea 1 archivo.py con hola dentro",
    ],
    "Leer archivo": [
        "lee el archivo notas punto txt",
        "lea el archivo notas punto txt",
        "leeme el archivo notas punto txt",
        "muestrame el archivo notas punto txt",
        "muestra el contenido del archivo notas punto txt",
        "que dice notas punto txt",
        "que contiene notas punto txt",
    ],
    "Listar carpeta": [
        "lista los archivos del escritorio",
        "que archivos hay en el escritorio",
        "muestrame las cosas que hay en descargas",
        "dime que archivos hay en documentos",
        "dame el contenido del escritorio",
    ],
    "Buscar archivo": [
        "busca el archivo llamado informe",
        "encuentra el archivo informe",
        "localiza el documento informe",
        "ubica el archivo informe",
    ],
    "Editar archivo": [
        "agrega una linea al archivo notas punto txt",
        "agregue una linea al archivo notas punto txt",
        "aniade una linea al archivo notas punto txt",
        "mete una linea al final del archivo notas punto txt",
        "escribe una linea en el archivo notas punto txt",
        "reemplaza hola por adios en notas punto txt",
        "sustituye hola por adios en notas punto txt",
        "cambia hola por adios en notas punto txt",
    ],
    "Mover y copiar": [
        "mueve reporte punto docx a documentos",
        "mueva reporte punto docx a documentos",
        "manda reporte punto docx a documentos",
        "envia reporte punto docx a documentos",
        "pasa reporte punto docx a documentos",
        "lleva reporte punto docx a documentos",
        "copia prueba punto py a descargas",
        "duplica prueba punto py en descargas",
    ],
    "Eliminar": [
        "elimina el archivo basura punto txt",
        "borra el archivo basura punto txt",
        "quita el archivo basura punto txt",
    ],
    "Estado del sistema": [
        "como esta el cpu", "dime el cpu", "que tal el procesador", "uso del micro",
        "cuanta ram queda", "como esta la memoria", "memoria",
        "como esta la grafica", "cuanta vram queda", "estado de la gpu",
        "como va la nvidia", "cuanto espacio libre queda", "como esta el disco",
        "dame el estado general del equipo", "como va todo", "resumen del sistema",
        "que programas consumen mas", "que aplicaciones estan ocupando memoria",
    ],
    "Modos": [
        "modo gaming", "activa el modo gaming", "active el modo gaming",
        "pon el modo gaming", "cambia a modo gaming", "voy a jugar", "me voy a jugar",
        "modo dedicado", "pon el modo dedicado", "usa la grafica", "modo inteligente",
        "modo normal", "modo rapido", "sal del modo juego", "quita el modo gaming",
        "en que modo estas", "que modo tienes",
    ],
    "Abrir y cerrar apps": [
        "abre spotify", "abra spotify", "abrir spotify", "inicia spotify",
        "ejecuta spotify", "lanza spotify", "arranca spotify", "pon spotify",
        "cierra chrome", "cierre chrome", "mata chrome", "termina chrome",
        "quita chrome",
    ],
    "Navegador": [
        "busca en internet el clima", "investiga el clima", "averigua el clima",
        "consulta el clima en internet", "googlea el clima",
        "pon musica en youtube", "reproduce musica en youtube",
        "abre la pagina github", "abre el sitio github",
    ],
    "Teclado y raton": [
        "toma una captura de pantalla", "haz una captura", "saca un pantallazo",
        "captura la pantalla", "screenshot",
        "escribe hola que tal", "teclea hola", "redacta hola que tal",
        "presiona el atajo copiar", "pulsa copiar", "aprieta el atajo pegar",
        "minimiza todo", "sube el volumen", "baja el volumen", "silencia", "mutea",
    ],
    "Energia": [
        "bloquea el equipo", "bloquee la pantalla",
        "apaga el equipo en 5 minutos", "cancela el apagado", "no apagues",
    ],
    "Cerrar todo y apps mal oidas": [
        "cierra todo", "cierre todas las ventanas", "cierra todos los programas",
        "cierra comet", "cierre cometa", "cierre la ventana de comer",
    ],
    "Eliminar (uno y varios)": [
        "elimine archivo.py", "elimina el archivo prueba.py", "borra notas.txt",
        "elimine las 2 capturas del escritorio",
        "elimina todas las capturas del escritorio",
    ],
    "Busqueda dentro de archivos": [
        "busca contrasena dentro de los archivos",
        "que archivo contiene mi presupuesto",
        "explora la carpeta descargas",
        "revisa el escritorio",
        "que archivos modifique en los ultimos 3 dias",
        "en que estaba trabajando",
        "archivos recientes",
    ],
    "Informe y equipo": [
        "informe completo del equipo", "reporte detallado", "guarda un informe",
        "que equipo es este", "que hardware tengo", "que sabes de mi equipo",
        "caracteristicas del equipo",
    ],
    "Obsidian": [
        "apunta en el diario que termine el proyecto",
        "anota en el diario que tengo reunion manana",
        "crea una nota llamada ideas de proyecto",
        "busca python en mis notas",
        "cuantas notas tengo",
        "mi vault",
    ],
    "Contexto personal": [
        "recuerda que trabajo con python",
        "ten en cuenta que uso obsidian a diario",
        "que sabes de mi",
        "tu contexto",
    ],
}

# Despedidas: NO son ordenes, cierran la sesion. El servidor las intercepta
# antes del router, asi que aqui comprobamos el detector directamente.
DESPEDIDAS = [
    "pausa", "para", "detente", "descansa", "basta", "ya esta", "eso es todo",
    "gracias", "muchas gracias", "adios", "hasta luego", "chao",
    "suelta", "libera a alexa", "cierra la sesion", "modo espera",
    "duerme", "silencio", "callate",
]

# Estas SI son ordenes aunque empiecen parecido a una despedida.
NO_SON_DESPEDIDAS = [
    "para el modo gaming",
    "gracias por abrir spotify",
    "espera un archivo nuevo",
    "crea un archivo llamado pausa punto txt",
    "abre spotify",
]

# Estas NO deben resolverse localmente: son para el modelo.
AL_MODELO = [
    "resume el contenido del informe y dime si vale la pena",
    "explicame que es la fotosintesis",
    "que opinas de mi codigo",
    "escribeme un correo formal para pedir vacaciones y guardalo",
    "traduce este texto al ingles",
]


def main() -> int:
    total = fallos = 0
    detalle: list[str] = []

    print("=" * 70)
    print("  TOLERANCIA AL FRASEO")
    print("=" * 70)
    print()

    for grupo, frases in GRUPOS.items():
        sin_ruta = []
        for frase in frases:
            _llamadas.clear()
            try:
                if nlu.enrutar(frase) is None:
                    sin_ruta.append(frase)
            except Exception as e:
                sin_ruta.append(f"{frase}  [excepcion: {e}]")

        total += len(frases)
        fallos += len(sin_ruta)
        aciertos = len(frases) - len(sin_ruta)
        marca = "OK" if not sin_ruta else f"{len(sin_ruta)} FALLOS"
        print(f"  {marca:>9}  {grupo:<24} {aciertos}/{len(frases)}")
        for f in sin_ruta:
            detalle.append(f"{grupo}: no casa {f!r}")
            print(f"              -> {f}")

    print()
    print("-" * 70)
    print("  DEBEN IR AL MODELO")
    print("-" * 70)
    for frase in AL_MODELO:
        total += 1
        if nlu.enrutar(frase) is not None:
            fallos += 1
            detalle.append(f"no debio enrutar: {frase!r}")
            print(f"     FALLO  {frase}")
        else:
            print(f"        OK  {frase[:58]}")

    # ---- Despedidas ----
    print()
    print("-" * 70)
    print("  DESPEDIDAS (cierran la sesión)")
    print("-" * 70)
    for frase in DESPEDIDAS:
        total += 1
        if nlu.es_despedida(frase):
            print(f"        OK  {frase}")
        else:
            fallos += 1
            detalle.append(f"no detectada como despedida: {frase!r}")
            print(f"     FALLO  {frase}")

    print()
    print("-" * 70)
    print("  NO son despedidas (siguen siendo órdenes)")
    print("-" * 70)
    for frase in NO_SON_DESPEDIDAS:
        total += 1
        if not nlu.es_despedida(frase):
            print(f"        OK  {frase}")
        else:
            fallos += 1
            detalle.append(f"confundida con despedida: {frase!r}")
            print(f"     FALLO  {frase}")

    print()
    print("=" * 70)
    if fallos:
        print(f"  {total - fallos}/{total} correctos, {fallos} fallos")
        print("=" * 70)
        for d in detalle:
            print("   -", d)
        return 1

    print(f"  {total}/{total} correctos. El fraseo es tolerante.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
