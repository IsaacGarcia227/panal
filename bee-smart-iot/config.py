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
    nido_setpoint_c: float = 27.0          # temperatura ambiente del sensor (demo académico)
    harvest_goal_kg: float = 40.0          # masa total al iniciar cosecha

    # Umbrales usados tanto por el labeller sintético como por el dashboard
    # (gauge de temperatura, badges, etc.).
    stress_temp_c: float = 30.0            # sostenido => ESTRES_TERMICO
    swarm_min_loss_kg: float = 3.0         # caída en <1h => ENJAMBRAZON
    low_reserves_days: int = 7             # pérdida sostenida => RESERVAS_BAJAS

    # Ventanas visuales del gauge de temperatura en el dashboard.
    gauge_temp_min_c: float = 24.0
    gauge_temp_max_c: float = 32.0


# ---------------------------------------------------------------------------
# Simulación del generador sintético (data_generator.py)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SimulationConfig:
    days: int = 182
    seed: int = 42

    # Anclaje temporal: 22 nov 2025 → 182 días terminan el 22 may 2026 (hoy).
    anchor_year: int = 2025
    anchor_month: int = 11
    anchor_day: int = 22

    # Pesos iniciales y comportamiento de forrajeo.
    start_weight_kg: float = 18.0
    daytime_base_kg: float = 0.02
    daytime_jitter_mean_kg: float = 0.05
    daytime_jitter_std_kg: float = 0.03
    nighttime_loss_mean_kg: float = -0.006
    nighttime_loss_std_kg: float = 0.005

    # Termorregulación normal — centrada en temperatura ambiente ~27°C.
    diurnal_temp_amp_c: float = 1.0
    diurnal_temp_noise_c: float = 0.2

    # Humedad costera Ensenada.
    base_humidity_pct: float = 62.0
    diurnal_humidity_amp_pct: float = 28.0
    humidity_noise_pct: float = 4.0

    # Presión atmosférica.
    base_pressure_hpa: float = 1_013.0
    pressure_monthly_amp_hpa: float = 4.5
    pressure_noise_hpa: float = 0.5

    # Dos eventos Santa Ana (ESTRES_TERMICO):
    #   1º — días 30-35  (22-27 dic 2025): ola de calor invernal
    #   2º — días 100-106 (2-8 mar 2026):  ola de calor primaveral
    santa_ana_windows: tuple[tuple[int, int], ...] = ((30, 35), (100, 106))
    santa_ana_start_day: int = 30
    santa_ana_end_day: int = 35
    santa_ana_peak_temp_offset_c: float = 3.0
    santa_ana_min_humidity_pct: float = 3.0
    santa_ana_humidity_drop_pct: float = 5.0
    santa_ana_pressure_drop_hpa: float = 2.8
    santa_ana_temp_noise_c: float = 0.3
    santa_ana_humidity_noise_pct: float = 1.5

    # Enjambrazón (ENJAMBRAZON) — día 65, hora 11 (26 ene 2026, pico primaveral).
    swarm_day: int = 65
    swarm_hour: int = 11
    swarm_pre_post_window_h: int = 4
    swarm_recovery_h: int = 30
    swarm_loss_kg: float = 4.1
    swarm_cluster_temp_c: float = 29.8
    swarm_pre_temp_c: float = 28.6
    swarm_post_temp_c: float = 29.2
    swarm_temp_noise_c: float = 0.3

    # Sequía (RESERVAS_BAJAS) — días 145-175 (16 abr - 16 may 2026).
    low_reserves_start_day: int = 145
    low_reserves_end_day: int = 175
    drought_daily_loss_kg: float = 0.05
    drought_loss_jitter_kg: float = 0.04
    low_reserves_grace_hours: int = 36

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
        ("5min", 5 / 60),
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
