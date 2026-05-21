"""Núcleo de inteligencia artificial: entrenamiento y persistencia.

Este módulo es la "fábrica" de modelos del proyecto. Toma el CSV de
`data_generator.py` y produce dos artefactos serializados en un único bundle
`bee_model.joblib`:

    1. `classifier`        — RandomForestClassifier que diagnostica el estado
                             de salud (SANA / ENJAMBRAZON / ESTRES_TERMICO /
                             RESERVAS_BAJAS).
    2. `harvest_regressor` — LinearRegression que estima la pendiente de
                             ganancia de peso (kg/día), usada por Flask para
                             pronosticar cuántos días faltan para la meta de
                             cosecha.

¿Por qué Random Forest?
-----------------------
* Maneja sin escalado features con magnitudes muy distintas (peso en kg,
  presión en hPa, humedad en %).
* Es robusto a outliers — útil porque los eventos extremos (Santa Ana,
  enjambrazón) son justamente outliers físicos que queremos detectar.
* Produce probabilidades calibradas por voto entre árboles (200), lo que
  alimenta directamente la sección "Diagnóstico Random Forest" del dashboard.
* No requiere ajuste fino de hiperparámetros para un problema tabular
  de tamaño moderado (~720 filas, 7 features).

¿Por qué regresión lineal para cosecha?
---------------------------------------
La ganancia diaria de néctar tiene una pendiente local aproximadamente
constante (la naturaleza del flujo es estacional, no caótico día a día).
Una línea es la herramienta correcta para extrapolar "días hasta meta" a
partir de la pendiente reciente.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from config import BIOLOGY, FEATURE_COLUMNS, LABEL_MAP, MODEL, SERVER

# Re-exports para compatibilidad con `app_flask.py` (que también los importa).
__all__ = [
    "FEATURE_COLUMNS",
    "LABEL_MAP",
    "engineer_features",
    "build_bundle",
    "save_bundle",
    "load_bundle",
]

# Tipo alias del bundle. Se persiste como `dict` (no como dataclass) para
# evitar problemas de pickling cuando este archivo se ejecuta como script
# (`__main__.HiveModelBundle` se rompería al deserializar desde Flask).
HiveBundle = dict[str, Any]


# ---------------------------------------------------------------------------
# Feature engineering — preparación de las columnas que ve el clasificador
# ---------------------------------------------------------------------------
# Columnas que el pipeline garantiza como float64. Si llega un string
# (p.ej. "30.05" desde un JSON exterior) el modelo recibiría dtype=object
# y `predict_proba` produciría basura — por eso se castean explícitamente.
_NUMERIC_INPUT_COLS: tuple[str, ...] = (
    "peso_kg",
    "temperatura_c",
    "humedad_pct",
    "presion_hpa",
)


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Fuerza columnas numéricas a `float64`, marcando valores no parseables.

    Pandas a veces deja una columna como `object` si en algún punto se
    mezclaron strings con números (p.ej. tras un round-trip a JSON sin
    cuidado). `pd.to_numeric(..., errors="coerce")` convierte lo parseable
    y reemplaza el resto por `NaN`; los NaN se propagan a las features
    derivadas y son visibles en el classification report.
    """
    for col in _NUMERIC_INPUT_COLS:
        if col in df.columns and df[col].dtype != "float64":
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    return df


def _weight_at_lag(
    ts_sec: np.ndarray, weights: np.ndarray, lag_hours: int
) -> np.ndarray:
    """Peso interpolado linealmente en `t - lag_hours` para cada fila.

    Args:
        ts_sec     segundos relativos desde una referencia común (e.g. la
                   primera observación). Trabajar en segundos evita los
                   bugs de precisión de `.astype('int64')` sobre datetime64,
                   cuyo resultado depende de la unidad interna (us vs ns).
        weights    pesos alineados a `ts_sec`.
        lag_hours  ventana a mirar atrás.

    Cuando dos lecturas consecutivas están separadas por un hueco (p.ej.
    21 h entre la última fila del CSV y un POST en vivo), `np.interp` traza
    la recta entre ambos puntos y devuelve el valor a `t - lag`. En la
    malla 1 h del entrenamiento `t - 1h` cae exactamente sobre el punto
    anterior, así que el resultado es idéntico a `.diff(periods=N)`.

    Retorna `NaN` cuando el objetivo cae antes del primer timestamp.
    """
    target = ts_sec - lag_hours * 3600.0
    return np.interp(target, ts_sec, weights, left=np.nan)


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula las tres features derivadas que el Random Forest necesita.

    Las features brutas (`temp_c`, `hum_pct`, `pres_hpa`, `weight_kg`) no son
    suficientes por sí solas: el diagnóstico depende de **derivadas
    temporales** del peso y de la **desviación** de la temperatura respecto
    al set-point del nido. Por eso engineering produce:

        delta_peso_1h   peso(t) - peso(t-1h)
                        → spike negativo grande es la firma de enjambrazón.

        delta_peso_24h  peso(t) - peso(t-24h)
                        → tendencia diaria; valores fuertemente negativos
                          sostenidos disparan RESERVAS_BAJAS.

        variacion_temp  |temp(t) - 35.0|
                        → magnitud de la desviación térmica; combinada con
                          la temperatura cruda permite separar estrés
                          (>37 °C) de simple oscilación diurna.

    Las deltas se computan por **lag temporal real**, no por posición de
    fila: para cada `t` se interpola linealmente el peso en `t - 1h` y en
    `t - 24h`. Esto blinda el cálculo cuando las lecturas no llegan a una
    cadencia uniforme (p.ej. POSTs cada 5-30 min, o un hueco de horas tras
    un reinicio del ESP32). Con la malla 1 h del CSV de entrenamiento, la
    interpolación coincide exactamente con `.diff(periods=N)`, así que el
    modelo entrenado conserva su validez.

    El DataFrame de entrada se preserva (se opera sobre una copia ordenada
    temporalmente). Antes de derivar nada se fuerzan los dtypes a numérico
    — esto evita que un valor llegado como string desde JSON contamine el
    cálculo. Las filas cuya ventana de lag cae antes del inicio de la
    serie reciben 0.0 (valor neutral: no hay historia con la cual comparar).
    """
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"])
    out = out.sort_values("timestamp").reset_index(drop=True)
    out = _coerce_numeric(out)

    if len(out) == 0:
        out["delta_peso_1h"] = []
        out["delta_peso_24h"] = []
        out["variacion_temp"] = []
        return out

    t0 = out["timestamp"].iloc[0]
    ts_sec = (out["timestamp"] - t0).dt.total_seconds().to_numpy()
    w = out["peso_kg"].to_numpy(dtype="float64")

    w_short = _weight_at_lag(ts_sec, w, MODEL.delta_short_hours)
    w_long = _weight_at_lag(ts_sec, w, MODEL.delta_long_hours)

    out["delta_peso_1h"] = np.where(np.isnan(w_short), 0.0, w - w_short)
    out["delta_peso_24h"] = np.where(np.isnan(w_long), 0.0, w - w_long)
    out["variacion_temp"] = (out["temperatura_c"] - BIOLOGY.nido_setpoint_c).abs()
    return out


# ---------------------------------------------------------------------------
# Entrenamiento del clasificador Random Forest
# ---------------------------------------------------------------------------
def train_classifier(df: pd.DataFrame) -> RandomForestClassifier:
    """Entrena el RandomForestClassifier y reporta métricas.

    Pasos:
        1. Extrae `X` (las 7 features) y `y` (las etiquetas 0..3).
        2. `train_test_split` **estratificado** — la opción `stratify=y`
           preserva la proporción de cada clase en los folds; sin eso, una
           clase rara como ENJAMBRAZON (~4 muestras en 720) podría quedar
           ausente del train set.
        3. Construye el bosque con `class_weight="balanced"`, que escala
           internamente el peso de cada muestra de modo inversamente
           proporcional a la frecuencia de su clase. Sin esto el modelo
           tendería a predecir SANA siempre (mayoría aplastante).
        4. Evalúa contra el hold-out y emite el classification report
           (precision/recall/F1 por clase) por consola.
    """
    X = df[list(FEATURE_COLUMNS)].to_numpy()
    y = df["label"].to_numpy()

    X_tr, X_te, y_tr, y_te = train_test_split(
        X,
        y,
        test_size=MODEL.test_size,
        stratify=y,
        random_state=MODEL.split_random_state,
    )

    clf = RandomForestClassifier(
        n_estimators=MODEL.rf_n_estimators,
        max_depth=MODEL.rf_max_depth,
        class_weight=MODEL.rf_class_weight,
        random_state=MODEL.rf_random_state,
        n_jobs=MODEL.rf_n_jobs,
    )
    clf.fit(X_tr, y_tr)

    preds = clf.predict(X_te)
    print("\n[Random Forest — classification report]")
    print(
        classification_report(
            y_te,
            preds,
            target_names=[LABEL_MAP[i] for i in sorted(LABEL_MAP)],
            zero_division=0,
        )
    )
    return clf


# ---------------------------------------------------------------------------
# Regresor lineal de cosecha
# ---------------------------------------------------------------------------
def train_harvest_regressor(df: pd.DataFrame) -> LinearRegression:
    """Ajusta una recta `peso = slope·t + intercept` sobre el período sano reciente.

    Decisión clave: NO usamos todo el dataset. Si lo hiciéramos, eventos como
    la sequía final de 7 días o la enjambrazón distorsionarían la pendiente
    hacia un valor negativo o caótico. Lo que nos interesa proyectar es el
    régimen NORMAL de ganancia de peso por flujo de néctar, así que:

        1. Filtramos solo filas con `label == SANA`.
        2. Tomamos las últimas `harvest_recent_days` (7) de ese subconjunto.
        3. Caemos al dataset completo solo si no hay suficientes muestras
           sanas recientes (caso degenerado).

    El regresor opera en escala de **horas**, no días — lo importante es la
    pendiente, y al consumirla en Flask se multiplica por 24 para convertir
    a kg/día antes de proyectar.
    """
    healthy = df[df["label"] == 0].copy()
    if len(healthy) < MODEL.harvest_min_rows:
        healthy = df.copy()  # fallback poco probable

    cutoff = healthy["timestamp"].max() - pd.Timedelta(days=MODEL.harvest_recent_days)
    recent = healthy[healthy["timestamp"] >= cutoff]
    if len(recent) < MODEL.harvest_min_rows:
        recent = healthy

    t0 = recent["timestamp"].min()
    hours = (recent["timestamp"] - t0).dt.total_seconds().to_numpy() / 3600.0
    X = hours.reshape(-1, 1)
    y = recent["peso_kg"].to_numpy()

    reg = LinearRegression().fit(X, y)
    slope_per_day = reg.coef_[0] * 24
    print(
        f"\n[Harvest regressor] slope = {slope_per_day:+.3f} kg/día "
        f"(intercept = {reg.intercept_:.2f} kg)"
    )
    return reg


# ---------------------------------------------------------------------------
# Construcción y persistencia del bundle
# ---------------------------------------------------------------------------
def build_bundle(csv_path: Path) -> HiveBundle:
    """Pipeline completo: lee CSV → engineering → entrena ambos modelos."""
    raw = pd.read_csv(csv_path)
    df = engineer_features(raw)
    clf = train_classifier(df)
    reg = train_harvest_regressor(df)

    last = df.iloc[-1]
    return {
        "classifier": clf,
        "harvest_regressor": reg,
        "feature_columns": list(FEATURE_COLUMNS),
        "label_map": dict(LABEL_MAP),
        "baseline_peso_kg": float(last["peso_kg"]),
        "baseline_timestamp": str(last["timestamp"]),
    }


def save_bundle(bundle: HiveBundle, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_path)
    print(f"\n[Persistence] model bundle saved to {out_path}")


def load_bundle(path: Path) -> HiveBundle:
    """Helper recíproco a `save_bundle` (usado por `app_flask.py`)."""
    return joblib.load(path)


def main() -> None:
    csv_path = SERVER.csv_path
    if not csv_path.exists():
        raise SystemExit(
            f"missing dataset: {csv_path}. Run `python data_generator.py` first."
        )
    bundle = build_bundle(csv_path)
    save_bundle(bundle, SERVER.model_path)


if __name__ == "__main__":
    main()
