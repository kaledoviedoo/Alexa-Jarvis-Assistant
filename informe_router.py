"""Que reglas del router se usan y cuales podria cubrir la capa semantica.

El plan de la Fase 4 es adelgazar nlu.py (unas 2.200 lineas de regex) sin
perder nada. Antes de borrar una sola regla hay que saber, con datos de uso
real, cuales se usan, cuales nunca, y cuales la capa semantica resolveria
igual. Este informe lo dice; no borra ni cambia nada.

    py informe_router.py

Lee ~/.jarvis/aprendizaje.jsonl y deja ~/.jarvis/router-informe.md. La parte
semantica necesita Ollama con nomic-embed-text; sin el, sale solo el uso.
Con menos de 300 ordenes del router los numeros son orientativos.
"""

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime

MINIMO_FIABLE = 300


def regla_que_casa(texto: str):
    """Indice de la primera regla del router que casa, SIN ejecutarla."""
    import nlu
    limpio = nlu.limpiar_comando(texto)
    if not limpio:
        return None
    plano = nlu.sin_acentos(limpio)
    for i, (patron, _) in enumerate(nlu.INTENTS):
        if patron.search(plano):
            return i
    return None


def main() -> int:
    import logging
    logging.disable(logging.WARNING)
    import nlu
    from config import CARPETA_DATOS

    archivo = CARPETA_DATOS / "aprendizaje.jsonl"
    if not archivo.exists():
        print("Todavia no hay aprendizaje.jsonl: usa Jarvis unos dias.")
        return 1
    filas = [json.loads(l) for l in archivo.read_text(encoding="utf-8").splitlines() if l.strip()]
    del_router = [f["texto"] for f in filas if f.get("origen") == "router" and f.get("texto")]

    uso = Counter()
    ejemplos = defaultdict(list)
    for texto in del_router:
        i = regla_que_casa(texto)
        if i is not None:
            uso[i] += 1
            if len(ejemplos[i]) < 3 and texto not in ejemplos[i]:
                ejemplos[i].append(texto)

    # ¿La capa semantica llegaria a la misma regla sin el regex literal?
    cubre = {}
    try:
        from tools import intencion
        if intencion.disponible():
            for i, textos in ejemplos.items():
                aciertos = 0
                for t in textos:
                    canonica, _ = intencion.traducir(t)
                    if canonica and regla_que_casa(canonica) == i:
                        aciertos += 1
                cubre[i] = (aciertos, len(textos))
    except Exception as e:
        print(f"(sin capa semantica: {e})")

    total = len(nlu.INTENTS)
    nunca = [i for i in range(total) if i not in uso]
    lineas = [
        f"# Informe del router {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"{len(del_router)} ordenes resueltas por el router; {total} reglas; "
        f"{len(uso)} usadas y {len(nunca)} nunca usadas.",
    ]
    if len(del_router) < MINIMO_FIABLE:
        lineas.append(f"\n**Pocos datos** (menos de {MINIMO_FIABLE} ordenes): no borres nada todavia.")
    lineas += ["", "## Reglas usadas", "", "| # | Usos | Semantica la cubre | Ejemplos | Patron |",
               "| --- | --- | --- | --- | --- |"]
    for i, n in uso.most_common():
        patron = nlu.INTENTS[i][0].pattern[:60].replace("|", "\\\\|")
        semantica = f"{cubre[i][0]}/{cubre[i][1]}" if i in cubre else "-"
        lineas.append(f"| {i} | {n} | {semantica} | {'; '.join(ejemplos[i])} | `{patron}` |")
    lineas += ["", "## Reglas nunca usadas", "",
               "Candidatas a revisar (no a borrar a ciegas: algunas son para casos raros pero importantes).", ""]
    for i in nunca:
        manejadora = getattr(nlu.INTENTS[i][1], "__name__", "?")
        lineas.append(f"- {i} `{manejadora}`")

    salida = CARPETA_DATOS / "router-informe.md"
    salida.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas[:4]))
    print(f"\nInforme completo en {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
