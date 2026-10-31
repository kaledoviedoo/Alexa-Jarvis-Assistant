"""Pruebas de los archivos y de la seleccion por voz, tocando disco de verdad.

Estos dos modulos son los unicos que escriben, mueven y borran cosas tuyas, y
hasta ahora solo estaban probados por el lado del router: que la frase llegara
a la funcion. Que la funcion hiciera lo correcto no lo comprobaba nadie.

Todo pasa dentro de una carpeta temporal: se fabrica un escritorio, unas
descargas y unos documentos de mentira, se apunta ahi el sandbox de rutas, y
al terminar se borra. No se toca ni un archivo tuyo.

Lo que de verdad se vigila aqui son las tres promesas del proyecto: que no se
sale de las carpetas autorizadas, que nada se borra de verdad, y que mover
archivos nunca pisa uno que ya estaba.

Ejecutar:  py test_archivos.py
"""

import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from tools import archivos, seleccion

fallos: list[str] = []


def comprobar(etiqueta: str, condicion: bool, detalle: str = "") -> None:
    if condicion:
        print(f"  OK     {etiqueta}")
    else:
        fallos.append(f"{etiqueta}{': ' + detalle if detalle else ''}")
        print(f"  FALLO  {etiqueta}" + (f"  ({detalle})" if detalle else ""))


def titulo(texto: str) -> None:
    print()
    print("-" * 70)
    print(f"  {texto}")
    print("-" * 70)


@contextmanager
def sandbox():
    """Un escritorio de mentira, con sus descargas, documentos y papelera.

    Se parchean los nombres dentro de los modulos y no en config, porque los
    dos hacen `from config import ESCRITORIO`: reescribir config a estas
    alturas no cambiaria lo que ellos ya tienen cogido.
    """
    raiz = Path(tempfile.mkdtemp(prefix="jarvis_pruebas_"))
    escritorio = raiz / "Escritorio"
    descargas = raiz / "Descargas"
    documentos = raiz / "Documentos"
    papelera = raiz / "papelera"
    for carpeta in (escritorio, descargas, documentos, papelera):
        carpeta.mkdir(parents=True)

    guardado_archivos = (archivos.ESCRITORIO, archivos.DESCARGAS,
                         archivos.DOCUMENTOS, archivos.PAPELERA,
                         archivos.CARPETAS_PERMITIDAS, dict(archivos.ALIAS_CARPETAS))
    guardado_seleccion = (seleccion.ESCRITORIO, seleccion.DESCARGAS,
                          seleccion.DOCUMENTOS, seleccion._carpeta)

    try:
        archivos.ESCRITORIO = escritorio
        archivos.DESCARGAS = descargas
        archivos.DOCUMENTOS = documentos
        archivos.PAPELERA = papelera
        archivos.CARPETAS_PERMITIDAS = [escritorio, descargas, documentos]
        archivos.ALIAS_CARPETAS = {
            "escritorio": escritorio, "el escritorio": escritorio, "desktop": escritorio,
            "descargas": descargas, "las descargas": descargas, "downloads": descargas,
            "documentos": documentos, "los documentos": documentos, "documents": documentos,
        }
        seleccion.ESCRITORIO = escritorio
        seleccion.DESCARGAS = descargas
        seleccion.DOCUMENTOS = documentos
        seleccion._carpeta = escritorio
        seleccion.olvidar()

        yield escritorio, descargas, documentos, papelera
    finally:
        (archivos.ESCRITORIO, archivos.DESCARGAS, archivos.DOCUMENTOS,
         archivos.PAPELERA, archivos.CARPETAS_PERMITIDAS,
         archivos.ALIAS_CARPETAS) = guardado_archivos
        (seleccion.ESCRITORIO, seleccion.DESCARGAS,
         seleccion.DOCUMENTOS, seleccion._carpeta) = guardado_seleccion
        seleccion.olvidar()
        shutil.rmtree(raiz, ignore_errors=True)


# ---------------------------------------------------------------------------
# El sandbox de rutas
# ---------------------------------------------------------------------------
def probar_sandbox_de_rutas() -> None:
    """Que no haya forma de salir de las tres carpetas autorizadas.

    Jarvis esta detras de un tunel publico. Si un dia alguien consigue que
    ejecute una orden que no dictaste tu, esto es lo unico que hay entre esa
    orden y el resto del disco.
    """
    titulo("RUTAS QUE TIENEN QUE RECHAZARSE")

    intentos = [
        ("subir dos carpetas",          "../../secreto.txt"),
        ("subir con barra invertida",   "..\\..\\Windows\\system.ini"),
        ("una unidad del sistema",      "C:\\Windows\\system.ini"),
        ("barra codificada en URL",     "carpeta%2F..%2Fsecreto.txt"),
        ("barra invertida codificada",  "carpeta%5C..%5Csecreto.txt"),
        ("una absoluta de fuera",       "/etc/passwd"),
        ("tres puntos",                 ".../secreto.txt"),
        ("sin nombre",                  ""),
        ("solo espacios",               "   "),
    ]

    with sandbox():
        for etiqueta, intento in intentos:
            try:
                resuelta = archivos.resolver_ruta(intento)
                comprobar(f"{etiqueta:<30}", False, f"la dejó pasar a {resuelta}")
            except archivos.RutaNoPermitida:
                comprobar(f"{etiqueta:<30}", True)

    titulo("RUTAS QUE TIENEN QUE PASAR")

    with sandbox() as (escritorio, descargas, _, _):
        casos = [
            ("un nombre suelto",      "notas.txt",          escritorio / "notas.txt"),
            ("una subcarpeta",        "clase/apuntes.md",   escritorio / "clase" / "apuntes.md"),
            ("con barra invertida",   "clase\\apuntes.md",  escritorio / "clase" / "apuntes.md"),
            ("entrecomillado",        '"notas.txt"',        escritorio / "notas.txt"),
        ]
        for etiqueta, entrada, esperada in casos:
            try:
                salio = archivos.resolver_ruta(entrada)
                comprobar(f"{etiqueta:<30}", salio == esperada.resolve(),
                          f"salió {salio}")
            except archivos.RutaNoPermitida as e:
                comprobar(f"{etiqueta:<30}", False, str(e))

        # Un punto de partida distinto sigue siendo legal mientras este dentro.
        try:
            salio = archivos.resolver_ruta("factura.pdf", descargas)
            comprobar(f"{'con otra carpeta de base':<30}",
                      salio == (descargas / "factura.pdf").resolve(), f"salió {salio}")
        except archivos.RutaNoPermitida as e:
            comprobar(f"{'con otra carpeta de base':<30}", False, str(e))


# ---------------------------------------------------------------------------
# Crear, leer, editar
# ---------------------------------------------------------------------------
def probar_crear_leer_editar() -> None:
    titulo("CREAR, LEER Y EDITAR")

    with sandbox() as (escritorio, _, _, papelera):
        archivos.crear_archivo("apuntes", "primera línea")
        comprobar(f"{'sin extensión se guarda como .txt':<44}",
                  (escritorio / "apuntes.txt").is_file())

        archivos.crear_archivo("json/config.json", '{"a": 1}')
        comprobar(f"{'crea la subcarpeta que falte':<44}",
                  (escritorio / "json" / "config.json").is_file())

        leido = archivos.leer_archivo("apuntes.txt")
        comprobar(f"{'lee lo que acaba de escribir':<44}",
                  "primera línea" in leido, leido[:60])

        comprobar(f"{'avisa si el archivo no existe':<44}",
                  "No encontré" in archivos.leer_archivo("fantasma.txt"))

        archivos.editar_archivo("apuntes.txt", "agregar", "segunda línea")
        texto = (escritorio / "apuntes.txt").read_text(encoding="utf-8")
        comprobar(f"{'agregar conserva lo que ya había':<44}",
                  "primera línea" in texto and "segunda línea" in texto, repr(texto))

        # Editar hace copia antes de tocar nada: si la orden se entendio mal,
        # el original sigue estando.
        comprobar(f"{'editar deja un respaldo en la papelera':<44}",
                  any(p.name.startswith("apuntes_") for p in papelera.iterdir()))

        (escritorio / "repetido.txt").write_text("hola hola hola", encoding="utf-8")
        respuesta = archivos.editar_archivo("repetido.txt", "reemplazar",
                                            buscar="hola", reemplazar="adiós")
        comprobar(f"{'reemplazar cuenta las coincidencias':<44}",
                  "3 coincidencias" in respuesta, respuesta)
        comprobar(f"{'reemplazar de verdad cambia el texto':<44}",
                  (escritorio / "repetido.txt").read_text(encoding="utf-8")
                  == "adiós adiós adiós")

        comprobar(f"{'no inventa nada si no encuentra el texto':<44}",
                  "No encontré" in archivos.editar_archivo(
                      "repetido.txt", "reemplazar", buscar="zzz", reemplazar="x"))

        comprobar(f"{'una acción que no existe no toca el archivo':<44}",
                  "No entendí" in archivos.editar_archivo(
                      "repetido.txt", "trocear", contenido="x"))


# ---------------------------------------------------------------------------
# Eliminar y mover
# ---------------------------------------------------------------------------
def probar_eliminar_y_mover() -> None:
    titulo("ELIMINAR (QUE NO ES BORRAR) Y MOVER")

    with sandbox() as (escritorio, descargas, _, papelera):
        (escritorio / "borrable.txt").write_text("algo", encoding="utf-8")
        respuesta = archivos.eliminar_archivo("borrable.txt")

        comprobar(f"{'eliminar lo saca del escritorio':<44}",
                  not (escritorio / "borrable.txt").exists())
        # La promesa del proyecto es que nada se borra de verdad. Si esto
        # falla, deja de ser cierta.
        comprobar(f"{'eliminar lo deja en la papelera':<44}",
                  any(p.name.endswith("borrable.txt") for p in papelera.iterdir()),
                  respuesta)

        (escritorio / "informe.txt").write_text("v1", encoding="utf-8")
        (descargas / "informe.txt").write_text("el que ya estaba", encoding="utf-8")
        archivos.mover_archivo("informe.txt", "descargas")

        comprobar(f"{'mover no pisa el que ya estaba':<44}",
                  (descargas / "informe.txt").read_text(encoding="utf-8")
                  == "el que ya estaba")
        comprobar(f"{'al chocar, el nuevo entra con sufijo':<44}",
                  (descargas / "informe_1.txt").is_file())

        respuesta = archivos.mover_archivo("informe_1.txt", "../../fuera",
                                           carpeta="descargas")
        comprobar(f"{'mover fuera del sandbox se rechaza':<44}",
                  "Moví" not in respuesta and (descargas / "informe_1.txt").is_file(),
                  respuesta)

        (escritorio / "copiable.txt").write_text("contenido", encoding="utf-8")
        archivos.copiar_archivo("copiable.txt", "documentos")
        comprobar(f"{'copiar deja el original donde estaba':<44}",
                  (escritorio / "copiable.txt").is_file())


# ---------------------------------------------------------------------------
# La seleccion por voz
# ---------------------------------------------------------------------------
def _poblar(carpeta: Path, nombres: list[str]) -> None:
    for i, nombre in enumerate(nombres):
        ruta = carpeta / nombre
        ruta.write_text("x" * (i + 1), encoding="utf-8")
        # Fechas distintas y en orden inverso al alfabetico, para que "los
        # recientes" y "los primeros" no puedan salir bien por casualidad.
        marca = time.time() - (len(nombres) - i) * 60
        import os
        os.utime(ruta, (marca, marca))


def probar_seleccion() -> None:
    titulo("SELECCIONAR ARCHIVOS HABLANDO")

    with sandbox() as (escritorio, descargas, documentos, _):
        _poblar(descargas, [
            "Apuntes cálculo.pdf",
            "beca.docx",
            "Corte 2.pdf",
            "diagrama.png",
            "energia.pdf",
            "foto.jpg",
        ])

        respuesta = seleccion.entrar_en("descargas")
        comprobar(f"{'entrar en una carpeta por su alias':<44}",
                  seleccion.carpeta_actual() == descargas.resolve(), respuesta)
        comprobar(f"{'y contar lo que hay dentro':<44}",
                  "6 archivos" in respuesta, respuesta)

        # El orden es el del explorador (alfabetico, sin distinguir
        # mayusculas): "los tres primeros" tiene que significar lo mismo
        # mirando la pantalla que hablando.
        seleccion.seleccionar(cantidad=3)
        nombres = [r.name for r in seleccion.seleccionados()]
        comprobar(f"{'los 3 primeros van en orden alfabético':<44}",
                  nombres == ["Apuntes cálculo.pdf", "beca.docx", "Corte 2.pdf"],
                  str(nombres))

        seleccion.seleccionar("pdf")
        extensiones = {r.suffix.lower() for r in seleccion.seleccionados()}
        comprobar(f"{'todos los pdf son solo pdf':<44}",
                  extensiones == {".pdf"} and seleccion.contar() == 3,
                  f"{extensiones}, {seleccion.contar()}")

        seleccion.seleccionar("imagenes")
        comprobar(f"{'una familia hablada coge sus extensiones':<44}",
                  seleccion.contar() == 2, str(seleccion.contar()))

        # Alexa transcribe sin tildes la mitad de las veces.
        seleccion.seleccionar("calculo")
        nombres = [r.name for r in seleccion.seleccionados()]
        comprobar(f"{'busca por nombre sin importar las tildes':<44}",
                  nombres == ["Apuntes cálculo.pdf"], str(nombres))

        seleccion.seleccionar(recientes=True, cantidad=2)
        nombres = [r.name for r in seleccion.seleccionados()]
        comprobar(f"{'los recientes van por fecha, no por nombre':<44}",
                  nombres == ["foto.jpg", "energia.pdf"], str(nombres))

        # Una palabra corta parece una extension (.beca), y mirarlo asi antes
        # que el nombre dejaba "los de beca" sin encontrar beca.docx.
        seleccion.seleccionar("beca")
        nombres = [r.name for r in seleccion.seleccionados()]
        comprobar(f"{'una palabra corta se busca por nombre':<44}",
                  nombres == ["beca.docx"], str(nombres))

        # Y una extension que no es de ninguna familia conocida sigue
        # valiendo dicha suelta.
        (descargas / "ajustes.ini").write_text("x", encoding="utf-8")
        seleccion.seleccionar("ini")
        nombres = [r.name for r in seleccion.seleccionados()]
        comprobar(f"{'una extensión suelta sigue valiendo':<44}",
                  nombres == ["ajustes.ini"], str(nombres))

        comprobar(f"{'un criterio sin resultados no selecciona nada':<44}",
                  "no hay ninguno" in seleccion.seleccionar("zzzz"))

        # Caducidad: mover archivos que ya no recuerdas cuales eran es la
        # forma de mover los que no eran.
        seleccion.seleccionar("pdf")
        seleccion._sellado = time.monotonic() - seleccion.VIDA_SEGUNDOS - 1
        comprobar(f"{'una selección vieja se suelta sola':<44}",
                  seleccion.seleccionados() == [])
        comprobar(f"{'y lo dice en vez de callárselo':<44}",
                  "caducó" in seleccion.que_hay())

        # Uno que desaparece por detras no se arrastra a la siguiente orden.
        seleccion.seleccionar("pdf")
        (descargas / "Corte 2.pdf").unlink()
        comprobar(f"{'no arrastra un archivo que ya no está':<44}",
                  seleccion.contar() == 2, str(seleccion.contar()))


def probar_mover_seleccion() -> None:
    titulo("MOVER LO SELECCIONADO")

    with sandbox() as (escritorio, descargas, documentos, _):
        _poblar(descargas, ["a.pdf", "b.pdf", "c.pdf"])
        (documentos / "b.pdf").write_text("el que ya estaba", encoding="utf-8")

        seleccion.entrar_en("descargas")
        seleccion.seleccionar("pdf")
        respuesta = seleccion.mover_a("documentos")

        comprobar(f"{'mueve los que puede':<44}",
                  (documentos / "a.pdf").is_file() and (documentos / "c.pdf").is_file(),
                  respuesta)
        comprobar(f"{'no pisa el que ya estaba allí':<44}",
                  (documentos / "b.pdf").read_text(encoding="utf-8") == "el que ya estaba")
        comprobar(f"{'y deja el que chocó donde estaba':<44}",
                  (descargas / "b.pdf").is_file())
        comprobar(f"{'lo cuenta en la respuesta':<44}",
                  "2" in respuesta and "ya existían" in respuesta, respuesta)
        comprobar(f"{'después de mover, suelta la selección':<44}",
                  seleccion.contar() == 0)

        comprobar(f"{'un destino que no existe no mueve nada':<44}",
                  "No tengo nada seleccionado" in seleccion.mover_a("marte"))

        seleccion.seleccionar("pdf")
        comprobar(f"{'un destino fuera del sandbox se rechaza':<44}",
                  "No encuentro" in seleccion.mover_a("../../fuera"))

        # Cambiar de carpeta invalida lo que tuvieras cogido: si no, dices
        # "muévelos" pensando en otra cosa.
        seleccion.seleccionar("pdf")
        seleccion.entrar_en("escritorio")
        comprobar(f"{'cambiar de carpeta suelta la selección':<44}",
                  seleccion.contar() == 0)


def main() -> int:
    print("=" * 70)
    print("  PRUEBAS DE ARCHIVOS Y SELECCIÓN")
    print("=" * 70)

    probar_sandbox_de_rutas()
    probar_crear_leer_editar()
    probar_eliminar_y_mover()
    probar_seleccion()
    probar_mover_seleccion()

    print()
    print("=" * 70)
    if fallos:
        print(f"  {len(fallos)} FALLOS:")
        print("=" * 70)
        for f in fallos:
            print("   -", f)
        return 1

    print("  TODO CORRECTO. El sandbox aguanta y la selección hace lo que dice.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
