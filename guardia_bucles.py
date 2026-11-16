"""Guardia de bucles: que el modelo no se quede llamando a lo mismo una y otra vez.

Un modelo pequeno a veces repite la misma herramienta con los mismos datos
("mira las ventanas", "mira las ventanas"...) o alterna dos sin avanzar
(buscar, leer, buscar, leer...). Cada vuelta gasta segundos y GPU, y al final
se acaban los pasos sin respuesta. La guardia lo nota pronto:

- la primera vez avisa al modelo ("eso ya lo hiciste, contesta con lo que
  tienes") sin volver a ejecutar la herramienta;
- si insiste, corta la orden y Jarvis dice lo que alcanzo a hacer.

Adaptado de OpenJarvis (https://github.com/open-jarvis/OpenJarvis), licencia
Apache-2.0: src/openjarvis/agents/loop_guard.py. Cambios: sin backend de
Rust, sin bus de eventos ni compresion de contexto (Jarvis ya limita los
pasos), argumentos normalizados antes de comparar, y el aviso no ejecuta la
llamada repetida. Ver NOTICE y licencias/OpenJarvis-LICENSE.txt.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass

MAX_IGUALES = 2          # la 3a llamada identica ya no se ejecuta
VENTANA_PING_PONG = 6    # A-B-A-B-A-B o A-B-C-A-B-C
PRESUPUESTO_POR_HERRAMIENTA = 5


@dataclass
class Veredicto:
    ejecutar: bool = True
    avisar: bool = False
    cortar: bool = False
    motivo: str = ""


def _huella(nombre: str, argumentos) -> str:
    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos)
        except Exception:
            pass
    if isinstance(argumentos, dict):
        argumentos = {k: (v.strip().lower() if isinstance(v, str) else v) for k, v in argumentos.items()}
    texto = json.dumps(argumentos, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{nombre}:{texto}".encode()).hexdigest()[:16]


class GuardiaBucles:
    """Una por orden: se crea al empezar el turno y se tira al acabar."""

    def __init__(self, max_iguales: int = MAX_IGUALES, ventana: int = VENTANA_PING_PONG,
                 presupuesto: int = PRESUPUESTO_POR_HERRAMIENTA):
        self.max_iguales = max_iguales
        self.ventana = ventana
        self.presupuesto = presupuesto
        self._iguales: dict[str, int] = {}
        self._por_herramienta: dict[str, int] = {}
        self._secuencia: deque[str] = deque(maxlen=ventana * 2)
        self._avisos = 0

    def revisar(self, nombre: str, argumentos) -> Veredicto:
        """Antes de ejecutar una herramienta: ¿va, se avisa o se corta?"""
        motivo = self._motivo(nombre, argumentos)
        if not motivo:
            return Veredicto()
        self._avisos += 1
        if self._avisos == 1:
            return Veredicto(ejecutar=False, avisar=True, motivo=motivo)
        return Veredicto(ejecutar=False, cortar=True, motivo=motivo)

    def _motivo(self, nombre: str, argumentos) -> str:
        huella = _huella(nombre, argumentos)
        self._iguales[huella] = self._iguales.get(huella, 0) + 1
        if self._iguales[huella] > self.max_iguales:
            return f"ya llamé a {nombre} {self.max_iguales} veces con lo mismo"

        self._por_herramienta[nombre] = self._por_herramienta.get(nombre, 0) + 1
        if self._por_herramienta[nombre] > self.presupuesto:
            return f"ya usé {nombre} {self.presupuesto} veces en esta orden"

        self._secuencia.append(nombre)
        if len(self._secuencia) >= self.ventana and self._ping_pong():
            return "estoy alternando las mismas herramientas sin avanzar"
        return ""

    def _ping_pong(self) -> bool:
        seq = list(self._secuencia)
        for periodo in (2, 3):
            if len(seq) >= periodo * 2:
                cola = seq[-periodo * 2:]
                patron = cola[:periodo]
                if len(set(patron)) > 1 and all(cola[i] == patron[i % periodo] for i in range(len(cola))):
                    return True
        return False


def aviso_para_el_modelo(motivo: str) -> str:
    """Lo que ve el modelo en lugar del resultado repetido."""
    return (f"No lo repito: {motivo}. El resultado no va a cambiar. Contesta ya al usuario "
            "con lo que tienes, o usa otra herramienta distinta si de verdad falta algo.")
