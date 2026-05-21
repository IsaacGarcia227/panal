"""Generador sintético de la serie horaria de la colmena.

Produce `bee_history.csv` con N días × 24 horas de lecturas (peso, temperatura
interna, humedad ambiente, presión) que sirven como ground truth para entrenar
el clasificador Random Forest. Tres eventos están inyectados deterministicamente:

    - Vientos de Santa Ana   → ESTRES_TERMICO
    - Enjambrazón             → ENJAMBRAZON  (caída brusca de peso + cluster caliente)
    - Sequía / dearth nectar  → RESERVAS_BAJAS (pérdida sostenida)

Todos los números — tiempos de evento, amplitudes, ruido, calibración HX711 —
se importan desde `config.py`; este módulo solo orquesta su composición.

Ecuación de calibración del HX711:
        peso_kg = (raw_counts - offset) / scale_factor

Para escribir el CSV se invierte la fórmula (`kg_to_raw`) para que cada fila
incluya tanto el dato físico como el dato crudo que generaría el ADC.
"""
from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from config import (
    BIOLOGY,
    HARDWARE,
    LABEL_ENJAMBRAZON,
    LABEL_ESTRES_TERMICO,
    LABEL_RESERVAS_BAJAS,
    LABEL_SANA,
    SERVER,
    SIMULATION,
)

# Re-exports usados por otros módulos para no acoplarlos directamente a config.
NIDO_SETPOINT_C: float = BIOLOGY.nido_setpoint_c
HARVEST_GOAL_KG: float = BIOLOGY.harvest_goal_kg
HX711_SCALE_FACTOR: float = HARDWARE.hx711_scale_factor
HX711_OFFSET: int = HARDWARE.hx711_offset


# ---------------------------------------------------------------------------
# Calibración HX711
# ---------------------------------------------------------------------------
def raw_to_kg(
    raw_counts: int,
    offset: int = HARDWARE.hx711_offset,
    scale_factor: float = HARDWARE.hx711_scale_factor,
) -> float:
    """Conversión counts → kg usando la ecuación de la celda de carga."""
    return (raw_counts - offset) / scale_factor


def kg_to_raw(
    weight_kg: float,
    offset: int = HARDWARE.hx711_offset,
    scale_factor: float = HARDWARE.hx711_scale_factor,
) -> int:
    """Inverso de `raw_to_kg`. Útil para fabricar `raw_counts` sintéticos."""
    return int(round(weight_kg * scale_factor + offset))


# ---------------------------------------------------------------------------
# Registro de una observación horaria
# ---------------------------------------------------------------------------
@dataclass
class HourReading:
    timestamp: str
    peso_kg: float
    temperatura_c: float
    humedad_pct: float
    presion_hpa: float
    label: int


# ---------------------------------------------------------------------------
# Submodelos ambientales
# ---------------------------------------------------------------------------
def diurnal_internal_temp(hour: int) -> float:
    """Temperatura del nido — bien regulada, con leve oscilación diurna."""
    return (
        BIOLOGY.nido_setpoint_c
        + SIMULATION.diurnal_temp_amp_c * math.sin(math.pi * (hour - 6) / 12)
        + random.gauss(0, SIMULATION.diurnal_temp_noise_c)
    )


def diurnal_ambient_humidity(hour: int) -> float:
    """Humedad costera: noches húmedas, tardes secas."""
    value = (
        SIMULATION.base_humidity_pct
        - SIMULATION.diurnal_humidity_amp_pct * math.sin(math.pi * (hour - 6) / 12)
        + random.gauss(0, SIMULATION.humidity_noise_pct)
    )
    return max(HARDWARE.humidity_min_pct + 15.0, value)


def baseline_pressure(day_idx: int) -> float:
    """Deriva suave de la presión a lo largo del mes."""
    return (
        SIMULATION.base_pressure_hpa
        + SIMULATION.pressure_monthly_amp_hpa
        * math.sin(2 * math.pi * day_idx / SIMULATION.days)
        + random.gauss(0, SIMULATION.pressure_noise_hpa)
    )


def nectar_flow_kg(hour: int) -> float:
    """Mass-balance horario por forrajeo.

    Las pecoreadoras vuelan principalmente entre 09:00 y 17:00; de noche hay
    evaporación neta minúscula.
    """
    if 9 <= hour <= 17:
        return max(
            0.0,
            SIMULATION.daytime_base_kg
            + random.gauss(
                SIMULATION.daytime_jitter_mean_kg,
                SIMULATION.daytime_jitter_std_kg,
            ),
        )
    return random.gauss(
        SIMULATION.nighttime_loss_mean_kg, SIMULATION.nighttime_loss_std_kg
    )


# ---------------------------------------------------------------------------
# Ventanas de evento (en horas desde el ancla)
# ---------------------------------------------------------------------------
# Support multiple Santa Ana windows (list of [start_day, end_day] pairs)
_SANTA_ANA_WINDOWS = [
    (start * 24, end * 24) for start, end in getattr(SIMULATION, "santa_ana_windows", [(SIMULATION.santa_ana_start_day, SIMULATION.santa_ana_end_day)])
]
_SWARM_HOUR = SIMULATION.swarm_day * 24 + SIMULATION.swarm_hour
_SWARM_RECOVERY_END = _SWARM_HOUR + SIMULATION.swarm_recovery_h
_LOW_RESERVES_WINDOW = (
    SIMULATION.low_reserves_start_day * 24,
    SIMULATION.low_reserves_end_day * 24,
)


def _in_window(hour_idx: int, window: tuple[int, int]) -> bool:
    return window[0] <= hour_idx < window[1]


# ---------------------------------------------------------------------------
# Generador
# ---------------------------------------------------------------------------
def generate_history(
    days: int = SIMULATION.days,
    start: datetime | None = None,
    seed: int = SIMULATION.seed,
) -> Iterator[HourReading]:
    """Yieldea una `HourReading` por hora durante `days` días.

    La función es determinista para una semilla dada — útil para tests y para
    que el dataset de entrenamiento sea estable entre ejecuciones.
    """
    random.seed(seed)
    if start is None:
        start = datetime(
            SIMULATION.anchor_year, SIMULATION.anchor_month, SIMULATION.anchor_day
        )

    weight = SIMULATION.start_weight_kg
    pre_window = SIMULATION.swarm_pre_post_window_h

    for h in range(days * 24):
        ts = start + timedelta(hours=h)
        hour_of_day = ts.hour
        day_idx = h // 24

        temp_int = diurnal_internal_temp(hour_of_day)
        hum = diurnal_ambient_humidity(hour_of_day)
        pres = baseline_pressure(day_idx)
        label = LABEL_SANA

        # ---- Santa Ana: calor seco offshore ----------------------------
        if any(_in_window(h, w) for w in _SANTA_ANA_WINDOWS):
            heat_factor = max(0.0, math.sin(math.pi * (hour_of_day - 6) / 14))
            temp_int = (
                BIOLOGY.nido_setpoint_c
                + 0.5
                + SIMULATION.santa_ana_peak_temp_offset_c * heat_factor
                + random.gauss(0, SIMULATION.santa_ana_temp_noise_c)
            )
            hum = max(
                SIMULATION.santa_ana_min_humidity_pct,
                8.0
                - SIMULATION.santa_ana_humidity_drop_pct * heat_factor
                + random.gauss(0, SIMULATION.santa_ana_humidity_noise_pct),
            )
            pres -= SIMULATION.santa_ana_pressure_drop_hpa
            if temp_int > BIOLOGY.stress_temp_c:
                label = LABEL_ESTRES_TERMICO

        # ---- Enjambrazón: reina parte con ~½ de la colonia ------------
        # Se etiqueta una ventana pre/post para que el clasificador vea
        # más de una muestra positiva (estratificación 80/20).
        if h == _SWARM_HOUR:
            weight -= SIMULATION.swarm_loss_kg
            temp_int = SIMULATION.swarm_cluster_temp_c + random.gauss(0, 0.2)
            label = LABEL_ENJAMBRAZON
        elif _SWARM_HOUR - pre_window <= h < _SWARM_HOUR:
            temp_int = SIMULATION.swarm_pre_temp_c + random.gauss(
                0, SIMULATION.swarm_temp_noise_c
            )
            label = LABEL_ENJAMBRAZON
        elif _SWARM_HOUR < h <= _SWARM_HOUR + pre_window:
            temp_int = SIMULATION.swarm_post_temp_c + random.gauss(
                0, SIMULATION.swarm_temp_noise_c
            )
            label = LABEL_ENJAMBRAZON
        elif _SWARM_HOUR + pre_window < h < _SWARM_RECOVERY_END:
            temp_int = 36.0 + random.gauss(0, SIMULATION.swarm_temp_noise_c)

        # ---- Mass-balance de forrajeo / sequía ------------------------
        if not _in_window(h, _LOW_RESERVES_WINDOW):
            weight += nectar_flow_kg(hour_of_day)
        else:
            weight -= SIMULATION.drought_daily_loss_kg + random.uniform(
                0.0, SIMULATION.drought_loss_jitter_kg
            )
            if h - _LOW_RESERVES_WINDOW[0] > SIMULATION.low_reserves_grace_hours:
                label = LABEL_RESERVAS_BAJAS

        weight = max(weight, SIMULATION.min_weight_kg)

        yield HourReading(
            timestamp=ts.strftime("%Y-%m-%d %H:%M:%S"),
            peso_kg=round(weight, 3),
            temperatura_c=round(temp_int, 2),
            humedad_pct=round(hum, 2),
            presion_hpa=round(pres, 2),
            label=label,
        )


# ---------------------------------------------------------------------------
# Escritura del CSV
# ---------------------------------------------------------------------------
def write_csv(
    out_path: Path,
    days: int = SIMULATION.days,
    seed: int = SIMULATION.seed,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(generate_history(days=days, seed=seed))
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))
    return len(rows)


def main() -> None:
    target = SERVER.csv_path
    n = write_csv(target)
    print(f"[bee_history] wrote {n} hourly rows to {target}")


if __name__ == "__main__":
    main()
