"""Calculadora segura: cuentas dichas en espanol, sin pedirselas al modelo.

Un modelo de 3B se equivoca con 17 por 23. Esta herramienta pasa la frase a
una expresion ("quince por cuatro" -> 15*4) y la evalua recorriendo el arbol
de Python con solo operaciones permitidas: nada de eval, nada de nombres
raros, exponentes acotados para que nadie cuelgue el equipo con 9^9^9.

El evaluador esta adaptado de OpenJarvis (https://github.com/open-jarvis/OpenJarvis),
licencia Apache-2.0: src/openjarvis/tools/calculator.py. Cambios: frases en
espanol, porcentajes, limites de tamano, division por cero como error y
respuesta pensada para la voz. Ver NOTICE y licencias/OpenJarvis-LICENSE.txt.
"""

from __future__ import annotations

import ast
import math
import operator
import re

from registro import herramienta

MAX_EXPONENTE = 1000
MAX_RESULTADO = 1e300
MAX_LONGITUD = 200

_BINARIOS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARIOS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCIONES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "ceil": math.ceil, "floor": math.floor,
}
_CONSTANTES = {"pi": math.pi, "e": math.e}


class ErrorDeCuenta(ValueError):
    pass


def _evaluar(nodo: ast.AST):
    if isinstance(nodo, ast.Expression):
        return _evaluar(nodo.body)
    if isinstance(nodo, ast.Constant):
        if isinstance(nodo.value, (int, float)) and not isinstance(nodo.value, bool):
            return nodo.value
        raise ErrorDeCuenta("solo números")
    if isinstance(nodo, ast.BinOp):
        tipo = type(nodo.op)
        if tipo not in _BINARIOS:
            raise ErrorDeCuenta("operación no permitida")
        a, b = _evaluar(nodo.left), _evaluar(nodo.right)
        if tipo is ast.Pow:
            if abs(b) > MAX_EXPONENTE:
                raise ErrorDeCuenta("el exponente es demasiado grande")
            if abs(a) > 1 and abs(b) > 64:
                a = float(a)          # en flotante se desborda en vez de colgarse
        if tipo in (ast.Div, ast.FloorDiv, ast.Mod) and b == 0:
            raise ErrorDeCuenta("no se puede dividir entre cero")
        try:
            r = _BINARIOS[tipo](a, b)
        except OverflowError:
            raise ErrorDeCuenta("el resultado es demasiado grande")
        if isinstance(r, complex):
            raise ErrorDeCuenta("el resultado no es un número real")
        if abs(r) > MAX_RESULTADO:
            raise ErrorDeCuenta("el resultado es demasiado grande")
        return r
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _UNARIOS:
        return _UNARIOS[type(nodo.op)](_evaluar(nodo.operand))
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) and not nodo.keywords:
        funcion = _FUNCIONES.get(nodo.func.id)
        if funcion is None:
            raise ErrorDeCuenta(f"no conozco la función {nodo.func.id}")
        try:
            return funcion(*[_evaluar(a) for a in nodo.args])
        except (ValueError, TypeError) as e:
            raise ErrorDeCuenta(f"{nodo.func.id} no admite eso ({e})")
    if isinstance(nodo, ast.Name) and nodo.id in _CONSTANTES:
        return _CONSTANTES[nodo.id]
    raise ErrorDeCuenta("eso no es una cuenta")


def evaluar(expresion: str) -> float:
    """Evalua una expresion ya en simbolos (15*4, sqrt(16), 2^10)."""
    expresion = (expresion or "").strip().replace("^", "**")
    if not expresion or len(expresion) > MAX_LONGITUD:
        raise ErrorDeCuenta("la cuenta está vacía o es demasiado larga")
    try:
        arbol = ast.parse(expresion, mode="eval")
    except SyntaxError:
        raise ErrorDeCuenta("no entendí la cuenta")
    return _evaluar(arbol)


# ---- De palabras a simbolos ------------------------------------------------

_UNIDADES = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
_DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
            "setenta": 70, "ochenta": 80, "noventa": 90}
_CENTENAS = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300,
             "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600,
             "setecientos": 700, "ochocientos": 800, "novecientos": 900}
_PALABRAS_NUMERO = set(_UNIDADES) | set(_DECENAS) | set(_CENTENAS) | {"mil", "millon", "millones", "y"}


def _numero_de_palabras(palabras: list[str]) -> int | None:
    total, actual, visto = 0, 0, False
    for p in palabras:
        if p in _UNIDADES:
            actual += _UNIDADES[p]
        elif p in _DECENAS:
            actual += _DECENAS[p]
        elif p in _CENTENAS:
            actual += _CENTENAS[p]
        elif p == "mil":
            actual = (actual or 1) * 1000
        elif p in ("millon", "millones"):
            total += (actual or 1) * 1_000_000
            actual = 0
        elif p == "y":
            continue
        else:
            return None
        visto = True
    if not visto:
        return None
    return total + actual


def _numeros_en_palabras(texto: str) -> str:
    """'quince por cuatro' -> '15 por 4'. Junta los numeros de varias palabras.

    La "y" solo es parte del numero despues de una decena (treinta y dos);
    en "dos y dos" es una suma.
    """
    salida, grupo = [], []

    def soltar():
        if grupo:
            n = _numero_de_palabras(grupo)
            salida.append(str(n) if n is not None else " ".join(grupo))
            grupo.clear()

    palabras = texto.split()
    for i, palabra in enumerate(palabras):
        if palabra == "y":
            siguiente = palabras[i + 1] if i + 1 < len(palabras) else ""
            if grupo and grupo[-1] in _DECENAS and siguiente in _UNIDADES and _UNIDADES[siguiente] < 10:
                grupo.append(palabra)
                continue
            soltar()
            salida.append("y")
        elif palabra in _PALABRAS_NUMERO:
            grupo.append(palabra)
        else:
            soltar()
            salida.append(palabra)
    soltar()
    return " ".join(salida)


_SIN_TILDES = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")

_REEMPLAZOS = [
    (r"\braiz\s+cuadrada\s+(?:de\s+)?(\S+)", r"sqrt(\1)"),
    (r"\braiz\s+cubica\s+(?:de\s+)?(\S+)", r"(\1)**(1/3)"),
    (r"\blogaritmo\s+(?:natural\s+)?(?:de\s+)?(\S+)", r"log(\1)"),
    (r"\bvalor\s+absoluto\s+(?:de\s+)?(\S+)", r"abs(\1)"),
    (r"(\S+)\s*%\s+de\s+(\S+)", r"(\1/100*\2)"),
    (r"(\S+)\s*%", r"(\1/100)"),
    (r"\bal\s+cuadrado\b", "**2"),
    (r"\bal\s+cubo\b", "**3"),
    (r"\b(?:elevado\s+(?:a\s+la|al|a)|a\s+la\s+potencia(?:\s+de)?)\s+", "**"),
    (r"\b(?:dividido|divido|partido)\s+(?:entre|por|en)\b", "/"),
    (r"\bentre\b", "/"),
    (r"\b(?:multiplicado\s+por|por|veces)\b", "*"),
    (r"\b(?:mas|sumado\s+a|sumale)\b", "+"),
    (r"\b(?:menos|restado)\b", "-"),
    (r"\bmodulo\b|\bmod\b", "%"),
    (r"\bx\b|×", "*"),
    (r"÷", "/"),
    (r"\babre\s+parentesis\b", "("),
    (r"\bcierra\s+parentesis\b", ")"),
    (r"\bpi\b", "pi"),
]

_RELLENO = re.compile(r"^(?:jarvis\s*,?\s*)?(?:(?:me\s+)?(?:puedes\s+)?(?:dime|decir|calcula(?:me|r)?|"
                      r"cuanto\s+(?:es|son|da|seria|serian|sale)|cual\s+es(?:\s+el\s+resultado\s+de)?|resuelve|"
                      r"haz\s+la\s+cuenta(?:\s+de)?|hazme\s+la\s+cuenta(?:\s+de)?)\s+)+")


def a_expresion(frase: str) -> str:
    """Pasa una cuenta dicha en espanol a una expresion evaluable."""
    t = (frase or "").translate(_SIN_TILDES).lower().strip()
    t = re.sub(r"[¿?¡!=]+", " ", t)
    t = _RELLENO.sub("", t.strip())
    # 1.234,5 (miles con punto y decimales con coma) -> 1234.5 ; "3,5" -> 3.5
    t = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", "", t)
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)
    t = re.sub(r"(?<=\d)\s+coma\s+(?=\d)", ".", t)
    t = re.sub(r"\s*\b(?:por\s+ciento|porciento)\b", " %", t)   # antes: "ciento" es un numero
    t = _numeros_en_palabras(t)
    t = re.sub(r"(?<=\d)\s+coma\s+(?=\d)", ".", t)
    t = re.sub(r"(?<=[\d)])\s+y\s+(?=[\d(]|raiz|sqrt)", " + ", t)
    for patron, reemplazo in _REEMPLAZOS:
        t = re.sub(patron, reemplazo, t)
    t = re.sub(r"\b(?:el|la|los|las|de|del|a|y)\b", " ", t)   # restos sueltos
    return re.sub(r"\s+", "", t)


def formatear(numero) -> str:
    """60 -> '60'; 3.14159 -> '3,1416'; 1234567 -> '1.234.567'."""
    if isinstance(numero, float) and numero.is_integer() and abs(numero) < 1e15:
        numero = int(numero)
    if isinstance(numero, int):
        return f"{numero:,}".replace(",", ".")
    if abs(numero) >= 1e15 or (0 < abs(numero) < 1e-4):
        return f"{numero:.4e}".replace(".", ",")
    texto = f"{numero:,.4f}".rstrip("0").rstrip(".")
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def calcular_frase(frase: str) -> str:
    """'cuanto es quince por cuatro' -> 'Son 60.' Lanza ErrorDeCuenta si no es una cuenta."""
    expresion = a_expresion(frase)
    resto = re.sub(r"sqrt|log10|log2|log|abs|pi", "", expresion)
    if not re.search(r"\d", expresion) or re.search(r"[a-z]", resto):
        raise ErrorDeCuenta("no es una cuenta")
    return f"Son {formatear(evaluar(expresion))}."


@herramienta(
    descripcion="Hace una cuenta exacta: sumas, restas, multiplicaciones, divisiones, potencias, "
                "raíces y porcentajes. Úsala siempre que haya que calcular algo.",
    args={"expresion": "La cuenta, en números o en palabras: '17*23', 'raíz cuadrada de 81', '15 por ciento de 80000'"},
    palabras=("cuanto es", "cuanto son", "calcula", "por ciento", "porcentaje", "raiz", "multiplica",
              "divide", "suma", "resta", "elevado"),
    en_plan=True,
)
def calcular(expresion: str) -> str:
    """Resultado exacto de una cuenta."""
    try:
        return calcular_frase(expresion)
    except ErrorDeCuenta as e:
        return f"No pude hacer la cuenta: {e}."
