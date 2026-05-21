"""Configuration central de Bee-Smart IoT.

Toda constante numérica/textual del proyecto vive aquí:
    - Calibración de hardware  (HX711, BME280).
    - Constantes biológicas    (set-point del nido, meta de cosecha, umbrales).
    - Parámetros de simulación (ventanas de eventos, ruido, semillas).
    - Hiperparámetros del Random Forest y de la regresión de cosecha.
    - Defaults del servidor Flask.

Los módulos `data_generator`, `ia_engine` y `app_flask` solo deben importar
desde este archivo; ningún número "mágico" debería quedar enterrado en una
función — si necesitas tunearlo, edita aquí.

Variables de entorno reconocidas (se leen una sola vez al importar):
    BEE_DATA_DIR        carpeta donde residen `bee_history.csv` y `bee_model.joblib`
    BEE_SERVER_HOST     dirección de bind para Flask (default 0.0.0.0)
    BEE_SERVER_PORT     puerto de bind para Flask (default 5000)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Etiquetas de salud (ground truth y predicción comparten estos IDs)
# ---------------------------------------------------------------------------
LABEL_SANA: int = 0
LABEL_ENJAMBRAZON: int = 1
LABEL_ESTRES_TERMICO: int = 2
LABEL_RESERVAS_BAJAS: int = 3

LABEL_MAP: dict[int, str] = {
    LABEL_SANA: "SANA",
    LABEL_ENJAMBRAZON: "ENJAMBRAZON",
    LABEL_ESTRES_TERMICO: "ESTRES_TERMICO",
    LABEL_RESERVAS_BAJAS: "RESERVAS_BAJAS",
}


# ---------------------------------------------------------------------------
# Hardware: HX711 + BME280 + rangos de validación de entradas en vivo
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HardwareConfig:
    # HX711 — celda de carga de 1 kg/40 kg. `scale_factor` es la pendiente
    # counts/kg producto de la calibración manual con una pesa patrón.
    hx711_scale_factor: float = 21_500.0
    hx711_offset: int = 84_230  # counts de tara con la colmena vacía

    # Profundidad del entero firmado de 24 bits que produce el ADC.
    hx711_raw_min: int = -(1 << 23)
    hx711_raw_max: int = (1 << 23) - 1

    # Rangos físicamente plausibles para descartar lecturas defectuosas.
    weight_min_kg: float = 0.0
    weight_max_kg: float = 200.0
    temp_min_c: float = -10.0
    temp_max_c: float = 60.0
    humidity_min_pct: float = 0.0
    humidity_max_pct: float = 100.0
    pressure_min_hpa: float = 850.0
    pressure_max_hpa: float = 1_080.0


# ---------------------------------------------------------------------------
# Biología: parámetros de la colmena y umbrales de diagnóstico
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BiologyConfig:
    nido_setpoint_c: float = 35.0          # temperatura objetivo del nido de cría
    harvest_goal_kg: float = 40.0          # masa total al iniciar cosecha

    # Umbrales usados tanto por el labeller sintético como por el dashboard
    # (gauge de temperatura, badges, etc.).
    stress_temp_c: float = 37.0            # sostenido => ESTRES_TERMICO
    swarm_min_loss_kg: float = 3.0         # caída en <1h => ENJAMBRAZON
    low_reserves_days: int = 7             # pérdida sostenida => RESERVAS_BAJAS

    # Ventanas visuales del gauge de temperatura en el dashboard.
    gauge_temp_min_c: float = 33.0
    gauge_temp_max_c: float = 39.0


# ---------------------------------------------------------------------------
# Simulación del generador sintético (data_generator.py)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SimulationConfig:
    days: int = 30
    seed: int = 42

    # Anclaje temporal arbitrario para que el CSV sea reproducible.
    anchor_year: int = 2026
    anchor_month: int = 4
    anchor_day: int = 21

    # Pesos iniciales y comportamiento de forrajeo.
    # El flujo diurno = `daytime_base_kg` + gauss(jitter_mean, jitter_std).
    # Con los defaults: ~0.05 + N(0.04, 0.02) ≈ 0.09 kg/h promedio diurno.
    start_weight_kg: float = 25.0
    daytime_base_kg: float = 0.05
    daytime_jitter_mean_kg: float = 0.04
    daytime_jitter_std_kg: float = 0.02
    nighttime_loss_mean_kg: float = -0.005
    nighttime_loss_std_kg: float = 0.004

    # Termorregulación normal (oscilación diurna alrededor del set-point).
    diurnal_temp_amp_c: float = 0.8
    diurnal_temp_noise_c: float = 0.15

    # Humedad ambiente costera Ensenada.
    base_humidity_pct: float = 65.0
    diurnal_humidity_amp_pct: float = 25.0
    humidity_noise_pct: float = 3.0

    # Presión atmosférica.
    base_pressure_hpa: float = 1_013.0
    pressure_monthly_amp_hpa: float = 3.0
    pressure_noise_hpa: float = 0.4

    # Evento Santa Ana (vientos cálidos secos de offshore).
    # Ventanas de evento pueden ser múltiples. Cada tupla (start_day, end_day) usa días relativos al ancla.
    santa_ana_windows: tuple[tuple[int, int], ...] = ((7, 10),)  # por defecto una ventana
    # Se mantiene la compatibilidad con los campos antiguos (solo el primero).
    santa_ana_start_day: int = 7  # primera ventana start (para código legado)
    santa_ana_end_day: int = 10   # primera ventana end (para código legado)
    santa_ana_peak_temp_offset_c: float = 3.5
    santa_ana_min_humidity_pct: float = 4.0
    santa_ana_humidity_drop_pct: float = 4.0
    santa_ana_pressure_drop_hpa: float = 2.5
    santa_ana_temp_noise_c: float = 0.25
    santa_ana_humidity_noise_pct: float = 1.2

    # Evento de enjambrazón.
    swarm_day: int = 15
    swarm_hour: int = 13
    swarm_pre_post_window_h: int = 3       # horas etiquetadas antes/después
    swarm_recovery_h: int = 24             # ventana de termoestabilización
    swarm_loss_kg: float = 3.6             # pérdida brusca al partir la reina
    swarm_cluster_temp_c: float = 37.6     # cluster restante calentando
    swarm_pre_temp_c: float = 36.4
    swarm_post_temp_c: float = 37.1
    swarm_temp_noise_c: float = 0.3

    # Sequía / dearth nectar — produce el patrón RESERVAS_BAJAS.
    low_reserves_start_day: int = 23
    low_reserves_end_day: int = 30
    drought_daily_loss_kg: float = 0.06
    drought_loss_jitter_kg: float = 0.03
    low_reserves_grace_hours: int = 48     # antes de empezar a etiquetar

    # Piso de seguridad para evitar pesos absurdos por ruido encadenado.
    min_weight_kg: float = 5.0


# ---------------------------------------------------------------------------
# Modelo: hiperparámetros del Random Forest + regresión lineal de cosecha
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelConfig:
    # Random Forest. `class_weight="balanced"` compensa el desbalance entre
    # la clase mayoritaria (SANA, ~85% del dataset) y las minoritarias.
    rf_n_estimators: int = 200
    rf_max_depth: int = 12
    rf_class_weight: str = "balanced"
    rf_random_state: int = 7
    rf_n_jobs: int = -1                    # paraleliza la construcción de árboles

    # Train/test split estratificado: garantiza que cada clase aparezca en
    # ambos folds proporcionalmente a su prevalencia.
    test_size: float = 0.2
    split_random_state: int = 7

    # Ingeniería de features sobre la serie temporal.
    delta_short_hours: int = 1             # Δpeso a 1 h  (detecta enjambrazón)
    delta_long_hours: int = 24             # Δpeso a 24 h (detecta sequía)

    # Ventana de inferencia: cuántas horas de historia se cargan para
    # calcular las features del último punto. Debe ser >= delta_long_hours
    # para que `delta_peso_24h` no quede en 0 por NaN.
    rolling_prediction_hours: int = 48

    # Regresor de cosecha (LinearRegression). Solo usa observaciones SANA
    # recientes para que la pendiente refleje el flujo de néctar actual,
    # no el promedio histórico de los 30 días.
    harvest_recent_days: int = 7
    harvest_min_rows: int = 24             # fallback al dataset completo si no alcanza


# ---------------------------------------------------------------------------
# Servidor Flask
# ---------------------------------------------------------------------------
def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw) if raw else default


@dataclass(frozen=True)
class ServerConfig:
    data_dir: Path = field(
        default_factory=lambda: _env_path("BEE_DATA_DIR", Path(__file__).parent)
    )
    history_csv_name: str = "bee_history.csv"
    model_joblib_name: str = "bee_model.joblib"
    host: str = field(
        default_factory=lambda: os.environ.get("BEE_SERVER_HOST", "0.0.0.0")
    )
    port: int = field(
        default_factory=lambda: int(os.environ.get("BEE_SERVER_PORT", "5000"))
    )
    # Presets aceptados por `/api/window?preset=...`
    preset_hours: tuple[tuple[str, int], ...] = (
        ("24h", 24),
        ("7d", 24 * 7),
        ("30d", 24 * 30),
    )

    @property
    def csv_path(self) -> Path:
        return self.data_dir / self.history_csv_name

    @property
    def model_path(self) -> Path:
        return self.data_dir / self.model_joblib_name

    @property
    def preset_map(self) -> dict[str, int]:
        return dict(self.preset_hours)


# ---------------------------------------------------------------------------
# Instancias singleton — el resto del código importa estas
# ---------------------------------------------------------------------------
HARDWARE = HardwareConfig()
BIOLOGY = BiologyConfig()
SIMULATION = SimulationConfig()
MODEL = ModelConfig()
SERVER = ServerConfig()

# Lista canónica de features que recibe el clasificador. Cualquier cambio
# debe reflejarse simultáneamente en `ia_engine.engineer_features`.
FEATURE_COLUMNS: tuple[str, ...] = (
    "temperatura_c",
    "humedad_pct",
    "presion_hpa",
    "peso_kg",
    "delta_peso_1h",
    "delta_peso_24h",
    "variacion_temp",
)
