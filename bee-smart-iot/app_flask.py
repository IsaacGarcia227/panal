"""Servidor Flask: dashboard + API de consulta/ingesta + inferencia en vivo.

Arquitectura
============
1. **Boot** (`create_app`):
   - Carga el bundle `bee_model.joblib` (RandomForest + LinearRegression).
   - Carga el CSV completo en una `HiveDataset` (DataFrame en memoria con
     `pandas` para queries por rango).
   - Registra handlers de error: `SensorError` → 400, `FilterError` → 400,
     todo lo demás → 500 con log en `app.logger`.

2. **Endpoints**:
       GET  /             → dashboard HTML
       GET  /api/meta     → metadata estática (rango del dataset, umbrales,
                            etiquetas) para que el frontend no hardcodee nada
       GET  /api/window   → slice filtrado del dataset + diagnóstico IA del
                            último punto del slice + pronóstico de cosecha
       POST /api/reading  → ingesta de una lectura cruda HX711/BME280, la
                            convierte a unidades físicas, valida rangos,
                            la agrega al dataset en memoria, y retorna la
                            misma estructura de diagnóstico que /api/window

3. **Pipeline de inferencia** (documentado abajo en `predict_health`):
   slice del dataset → engineer_features → última fila → predict_proba →
   {label dominante, distribución completa, deltas}.

Nada en este archivo contiene constantes literales relevantes al dominio;
todas viven en `config.py`. Lo único que queda como número son detalles de
formato (decimales mostrados, etc.).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import joblib
import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

from config import (
    BIOLOGY,
    FEATURE_COLUMNS,
    HARDWARE,
    LABEL_MAP,
    MODEL,
    SERVER,
)
from ia_engine import engineer_features, train_harvest_regressor

# Re-exports para los tests que importen del módulo histórico.
HARVEST_GOAL_KG = BIOLOGY.harvest_goal_kg
NIDO_SETPOINT_C = BIOLOGY.nido_setpoint_c


# ===========================================================================
# Tipos de dominio
# ===========================================================================
@dataclass
class SensorFrame:
    """Una observación validada lista para almacenarse."""

    timestamp: datetime
    peso_kg: float
    temperatura_c: float
    humedad_pct: float
    presion_hpa: float

    def as_row(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "peso_kg": self.peso_kg,
            "temperatura_c": self.temperatura_c,
            "humedad_pct": self.humedad_pct,
            "presion_hpa": self.presion_hpa,
        }


@dataclass
class TimeWindow:
    """Rango temporal resuelto a partir de los query-params del cliente."""

    start: datetime
    end: datetime
    label: str  # "24h" | "7d" | "30d" | "all" | "custom" | "YYYY-MM-DD"


# ===========================================================================
# Errores de dominio (cada uno mapea a un handler de Flask)
# ===========================================================================
class SensorError(ValueError):
    """Lectura inválida o fuera de rango — responde 400."""


class FilterError(ValueError):
    """Query-string mal formado — responde 400."""


# ===========================================================================
# Validación de payloads
# ===========================================================================
def _require_number(
    payload: dict[str, Any], key: str, lo: float, hi: float
) -> float:
    if key not in payload:
        raise SensorError(f"missing field: {key}")
    try:
        value = float(payload[key])
    except (TypeError, ValueError) as exc:
        raise SensorError(f"field {key!r} is not numeric") from exc
    if not (lo <= value <= hi):
        raise SensorError(f"field {key!r} out of range [{lo}, {hi}]: {value}")
    return value


def parse_frame(payload: dict[str, Any]) -> SensorFrame:
    """Convierte el JSON del firmware (peso kg + BME280) en `SensorFrame`.

    Acepta los nombres de campo que envía el firmware:
        peso        → peso_kg
        temperatura → temperatura_c
        humedad     → humedad_pct
        presion     → presion_hpa
    """
    if not isinstance(payload, dict):
        raise SensorError("payload must be a JSON object")

    # Peso: acepta 'peso' (firmware) o 'peso_kg'
    peso_raw = payload.get("peso") if "peso" in payload else payload.get("peso_kg")
    if peso_raw is None:
        raise SensorError("missing field: peso")
    try:
        peso_kg = float(peso_raw)
    except (TypeError, ValueError) as exc:
        raise SensorError("field 'peso' is not numeric") from exc
    if not (HARDWARE.weight_min_kg <= peso_kg <= HARDWARE.weight_max_kg):
        raise SensorError(f"peso out of range: {peso_kg:.2f} kg")

    # Temperatura: acepta 'temperatura' (firmware) o 'temperatura_c'
    temp_key = "temperatura" if "temperatura" in payload else "temperatura_c"
    temperatura_c = _require_number(payload, temp_key, HARDWARE.temp_min_c, HARDWARE.temp_max_c)

    # Humedad: acepta 'humedad' (firmware) o 'humedad_pct'
    hum_key = "humedad" if "humedad" in payload else "humedad_pct"
    humedad_pct = _require_number(payload, hum_key, HARDWARE.humidity_min_pct, HARDWARE.humidity_max_pct)

    # Presión: acepta 'presion' (firmware) o 'presion_hpa'
    pres_key = "presion" if "presion" in payload else "presion_hpa"
    presion_hpa = _require_number(payload, pres_key, HARDWARE.pressure_min_hpa, HARDWARE.pressure_max_hpa)

    ts_raw = payload.get("timestamp")
    if ts_raw:
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", ""))
        except ValueError as exc:
            raise SensorError("timestamp not ISO-8601") from exc
    else:
        ts = datetime.now()

    return SensorFrame(
        timestamp=ts,
        peso_kg=round(peso_kg, 3),
        temperatura_c=round(temperatura_c, 2),
        humedad_pct=round(humedad_pct, 2),
        presion_hpa=round(presion_hpa, 2),
    )


# ===========================================================================
# Parsing de filtros temporales (query params)
# ===========================================================================
def _parse_iso(value: str, *, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError as exc:
        raise FilterError(f"{field} is not ISO-8601: {value!r}") from exc


def parse_window(
    args: dict[str, str], dataset_end: datetime, dataset_start: datetime
) -> TimeWindow:
    """Resuelve los query-string filters en una ventana temporal concreta.

    Prioridad (de más específico a menos):
        `from`/`to`  >  `day`  >  `preset`  >  default 24h
    """
    frm = args.get("from")
    to = args.get("to")
    if frm or to:
        start = _parse_iso(frm, field="from") if frm else dataset_start
        end = _parse_iso(to, field="to") if to else dataset_end
        if end < start:
            raise FilterError("`to` must be after `from`")
        return TimeWindow(start=start, end=end, label="custom")

    day = args.get("day")
    if day:
        start = _parse_iso(day, field="day").replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return TimeWindow(start=start, end=start + timedelta(days=1), label=day)

    preset = (args.get("preset") or "24h").strip().lower()
    if preset == "all":
        return TimeWindow(start=dataset_start, end=dataset_end, label="all")

    if preset not in SERVER.preset_map:
        valid = sorted(SERVER.preset_map) + ["all"]
        raise FilterError(f"unknown preset {preset!r}; valid: {valid}")

    hours = SERVER.preset_map[preset]
    now = datetime.now()
    return TimeWindow(
        start=now - timedelta(hours=hours), end=now, label=preset
    )


# ===========================================================================
# Repositorio en memoria
# ===========================================================================
class HiveDataset:
    """Carga el CSV completo y expone slicing por ventana + append.

    Esta clase reemplaza a una base de datos real para esta primera versión.
    Toda operación está protegida por un lock interno (Flask sirve requests
    en hilos concurrentes vía Werkzeug).
    """

    # Columnas con su dtype esperado. Forzamos la coerción al cargar el CSV
    # para evitar que pandas deje algún campo numérico como `object` (lo
    # que rompería las operaciones aritméticas posteriores con strings).
    _SCHEMA = {
        "peso_kg": "float64",
        "temperatura_c": "float64",
        "humedad_pct": "float64",
        "presion_hpa": "float64",
        "label": "int64",
    }

    def __init__(self, csv_path) -> None:
        self._lock = threading.Lock()
        if not csv_path.exists():
            raise FileNotFoundError(f"dataset CSV not found: {csv_path}")
        df = pd.read_csv(csv_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
        for col, dtype in self._SCHEMA.items():
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype(dtype)
        self._df = df.sort_values("timestamp").reset_index(drop=True)

    @property
    def start(self) -> datetime:
        return self._df["timestamp"].iloc[0].to_pydatetime()

    @property
    def end(self) -> datetime:
        return self._df["timestamp"].iloc[-1].to_pydatetime()

    def slice(self, window: TimeWindow) -> pd.DataFrame:
        with self._lock:
            mask = (self._df["timestamp"] >= window.start) & (
                self._df["timestamp"] <= window.end
            )
            return self._df.loc[mask].copy()

    def trailing_hours(self, end: datetime, hours: int) -> pd.DataFrame:
        """Devuelve las últimas `hours` horas hasta `end` (inclusive).

        Lo usa el pipeline de inferencia para construir la ventana sobre la
        que se calculan las features derivadas — debe abarcar al menos
        `MODEL.delta_long_hours` para que `delta_peso_24h` sea calculable.
        """
        with self._lock:
            mask = (self._df["timestamp"] > end - timedelta(hours=hours)) & (
                self._df["timestamp"] <= end
            )
            return self._df.loc[mask].copy()

    def append_new_from_csv(self, csv_path) -> int:
        """Recarga el CSV completo y actualiza el dataset en memoria.

        Devuelve cuántas filas nuevas se agregaron (0 = sin cambios).
        """
        df = pd.read_csv(csv_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
        for col, dtype in self._SCHEMA.items():
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype(dtype)
        df = df.sort_values("timestamp").reset_index(drop=True)

        with self._lock:
            old_count = len(self._df)
            self._df = df
            return max(0, len(df) - old_count)

    def snapshot(self) -> pd.DataFrame:
        """Copia thread-safe del DataFrame completo."""
        with self._lock:
            return self._df.copy()

    def append(self, frame: SensorFrame) -> None:
        # Construimos el frame nuevo con dtypes idénticos al `_df` existente
        # para que `pd.concat` no degenere alguna columna a `object`.
        new_row = pd.DataFrame(
            [
                {
                    "timestamp": pd.to_datetime(frame.timestamp),
                    "peso_kg": float(frame.peso_kg),
                    "temperatura_c": float(frame.temperatura_c),
                    "humedad_pct": float(frame.humedad_pct),
                    "presion_hpa": float(frame.presion_hpa),
                    "label": 0,  # placeholder; el modelo predice, no es GT
                }
            ]
        ).astype({col: dtype for col, dtype in self._SCHEMA.items()})

        with self._lock:
            self._df = (
                pd.concat([self._df, new_row], ignore_index=True)
                .sort_values("timestamp")
                .reset_index(drop=True)
            )


def _start_csv_watcher(
    app: Flask, dataset: "HiveDataset", bundle: dict, interval: int = 60
) -> None:
    """Hilo daemon que cada `interval` segundos:
      1. Lee el CSV en disco y agrega filas nuevas al dataset en memoria.
      2. Re-entrena solo el regresor de cosecha (LinearRegression) con los datos actualizados.

    El Random Forest NO se re-entrena: los datos reales del ESP32 tienen
    label=0 de placeholder (sin etiqueta real de salud), por lo que reentrenarlo
    corrompería las fronteras de diagnóstico aprendidas con datos sintéticos.
    """
    import time

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                added = dataset.append_new_from_csv(SERVER.csv_path)
                if added > 0:
                    df_snap = dataset.snapshot()
                    bundle["harvest_regressor"] = train_harvest_regressor(df_snap)
                    app.logger.info("csv_watcher: +%d filas, regresor actualizado", added)
            except Exception:
                app.logger.exception("csv_watcher: error al recargar CSV")

    t = threading.Thread(target=_loop, daemon=True, name="csv-watcher")
    t.start()


def _df_to_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Serializa un slice a JSON-safe rows (timestamps como strings)."""
    if df.empty:
        return []
    out = df.copy()
    out["timestamp"] = out["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
    keep = ["timestamp", "peso_kg", "temperatura_c", "humedad_pct", "presion_hpa"]
    return out[keep].to_dict(orient="records")


# ===========================================================================
# Inferencia: pipeline de uso del modelo en producción
# ===========================================================================
def predict_health(
    bundle: dict[str, Any], rolling: list[dict[str, Any]]
) -> dict[str, Any]:
    """Diagnóstico de salud del último punto de `rolling`.

    Pipeline (igual al de entrenamiento — esto es esencial: si las features
    cambian acá pero no allá, el modelo recibe basura):

        rolling (≥48h)  ──►  engineer_features(...)  ──►  DataFrame
                                                          con 7 features
                                                                │
                                                                ▼
                                       última fila → predict_proba(X)
                                                                │
                              ┌──────────────────────────────────┘
                              ▼
            probabilidades por clase (suman 1.0)
                              │
                              ▼
            label dominante = argmax(probabilidades)

    Devuelve:
        label_id        — entero 0..3 según `LABEL_MAP`.
        label           — nombre legible.
        confidence      — máxima probabilidad entre las 4 clases.
        probabilities   — dict {nombre_clase: prob} (lo que pinta el grid
                          "Diagnóstico Random Forest" en el dashboard).
        delta_peso_1h / delta_peso_24h / variacion_temp
                        — las features derivadas crudas, expuestas para que
                          el dashboard las muestre como KPIs (no son salida
                          del modelo, son entradas que el cliente quiere ver).
    """
    if not rolling:
        return {"label_id": -1, "label": "SIN_DATOS", "confidence": 0.0}

    df = engineer_features(pd.DataFrame(rolling))
    # `to_numpy(dtype=float64)` blinda el caso degenerado en el que alguna
    # columna haya quedado como `object` (mezcla de string/numérico); sin
    # esto, sklearn podría producir una predicción incorrecta o lanzar.
    last_features = (
        df.iloc[[-1]][list(FEATURE_COLUMNS)].to_numpy(dtype=np.float64)
    )

    clf = bundle["classifier"]
    probs = clf.predict_proba(last_features)[0]
    classes = list(clf.classes_)

    dominant_idx = int(np.argmax(probs))
    label_id = int(classes[dominant_idx])

    return {
        "label_id": label_id,
        "label": LABEL_MAP.get(label_id, "DESCONOCIDO"),
        "confidence": round(float(probs.max()), 3),
        "probabilities": {
            LABEL_MAP[int(c)]: round(float(p), 3)
            for c, p in zip(classes, probs)
        },
        "delta_peso_1h": round(float(df.iloc[-1]["delta_peso_1h"]), 3),
        "delta_peso_24h": round(float(df.iloc[-1]["delta_peso_24h"]), 3),
        "variacion_temp": round(float(df.iloc[-1]["variacion_temp"]), 3),
    }


def _compute_delta_peso_7d(dataset: "HiveDataset", end_ts: datetime) -> float:
    """Cambio de peso en las últimas 168 h (7 días) por interpolación lineal.

    Es un early-warning para `RESERVAS_BAJAS`: una caída sostenida de varios
    kg en 7 días aparece como `Δ7d` muy negativo antes de que el clasificador
    acumule las 48 h de pérdida necesarias para etiquetar el estado.

    Devuelve 0.0 si no hay suficiente historia para mirar 7 días atrás.
    """
    week = dataset.trailing_hours(end_ts, hours=24 * 7 + 1)
    if len(week) < 2:
        return 0.0
    df = week.sort_values("timestamp").reset_index(drop=True)
    t0 = df["timestamp"].iloc[0]
    hours = (df["timestamp"] - t0).dt.total_seconds().to_numpy() / 3600.0
    target = hours[-1] - 24 * 7
    if target < hours[0]:
        return 0.0
    weights = df["peso_kg"].to_numpy(dtype="float64")
    return float(weights[-1] - float(np.interp(target, hours, weights)))


def _build_timeline(sliced: pd.DataFrame) -> list[dict[str, Any]]:
    """Detecta transiciones de `label` dentro del slice y las devuelve como eventos.

    Cada evento describe el cambio de un estado a otro y el timestamp donde
    ocurrió la transición. Útil para que el operador vea de un vistazo
    "cuándo cambió algo" sin tener que escudriñar 30 días de gráficas.
    """
    if len(sliced) < 2:
        return []
    events: list[dict[str, Any]] = []
    labels = sliced["label"].to_numpy()
    timestamps = sliced["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").to_numpy()
    for i in range(1, len(labels)):
        if int(labels[i]) != int(labels[i - 1]):
            events.append(
                {
                    "timestamp": str(timestamps[i]),
                    "from": LABEL_MAP.get(int(labels[i - 1]), "DESCONOCIDO"),
                    "to": LABEL_MAP.get(int(labels[i]), "DESCONOCIDO"),
                }
            )
    return events


def _human_age(seconds: float) -> str:
    """Formato compacto: '23h 14m', '4m 12s', '32s'."""
    seconds = int(abs(seconds))
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    mins, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    if mins:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _current_slope_kg_per_day(rolling: list[dict[str, Any]]) -> float | None:
    """Pendiente por mínimos cuadrados sobre el rolling window.

    Trabaja en horas relativas a la primera observación para evitar problemas
    de unidad/escala. Requiere al menos 2 puntos separados por más de una
    hora; de lo contrario devuelve `None` (el llamador caerá a la pendiente
    histórica del bundle).
    """
    if not rolling or len(rolling) < 2:
        return None
    df = pd.DataFrame(rolling)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    t0 = df["timestamp"].iloc[0]
    hours = (df["timestamp"] - t0).dt.total_seconds().to_numpy() / 3600.0
    if hours[-1] - hours[0] < 1.0:
        return None
    weights = df["peso_kg"].to_numpy(dtype="float64")
    slope_per_hour = float(np.polyfit(hours, weights, 1)[0])
    return slope_per_hour * 24


def forecast_harvest(
    bundle: dict[str, Any],
    current_weight: float,
    rolling: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Proyección a la meta usando la **pendiente actual** de la colmena.

    Antes este pronóstico usaba siempre la pendiente del `LinearRegression`
    entrenado en los 7 días sanos finales del dataset — un valor histórico
    fijo. Eso producía pronósticos engañosos cuando la colmena estaba en
    sequía o estrés (mostraba "67 días" mientras perdía peso).

    Ahora el cálculo es:
        1. Se ajusta una recta a las últimas observaciones (rolling window)
           para obtener el ritmo **actual** en kg/día.
        2. Si la pendiente actual es positiva → proyección de días a meta.
        3. Si es negativa o cero → mensaje "perdiendo X kg/día", `days_to_goal`
           queda `null` (la meta es inalcanzable al ritmo actual).
        4. Si la rolling window no tiene suficiente span (<1h), se cae al
           valor histórico del bundle como aproximación.

    Campos del response:
        slope_kg_per_day             pendiente USADA para el cálculo
        historical_slope_kg_per_day  pendiente del regresor entrenado (contexto)
        days_to_goal                 número o `null` si meta inalcanzable
        source                       "current" | "historical" (de dónde salió slope)
        message                      texto presentable para el dashboard
    """
    historical = float(bundle["harvest_regressor"].coef_[0]) * 24
    current = _current_slope_kg_per_day(rolling) if rolling else None
    slope = current if current is not None else historical
    source = "current" if current is not None else "historical"

    remaining = BIOLOGY.harvest_goal_kg - current_weight
    base = {
        "slope_kg_per_day": round(slope, 3),
        "historical_slope_kg_per_day": round(historical, 3),
        "goal_kg": BIOLOGY.harvest_goal_kg,
        "source": source,
    }

    if remaining <= 0:
        return {
            **base,
            "days_to_goal": 0,
            "message": f"Meta de {BIOLOGY.harvest_goal_kg} kg ya alcanzada.",
        }

    if slope <= 0:
        return {
            **base,
            "days_to_goal": None,
            "message": (
                f"Perdiendo {abs(slope):.2f} kg/día — meta inalcanzable al "
                f"ritmo actual. Ritmo sano histórico: {historical:+.2f} kg/día."
            ),
        }

    days = remaining / slope
    return {
        **base,
        "days_to_goal": round(days, 1),
        "message": (
            f"Al ritmo actual ({slope:+.2f} kg/día): {days:.1f} días "
            f"para alcanzar {BIOLOGY.harvest_goal_kg} kg."
        ),
    }


# ===========================================================================
# Construcción y wiring de Flask
# ===========================================================================
def create_app() -> Flask:
    """Factory de la aplicación — facilita testing y `WSGI` montaje externo."""
    app = Flask(__name__, template_folder="templates", static_folder="static")

    if not SERVER.model_path.exists():
        raise SystemExit(
            f"Model bundle not found at {SERVER.model_path}. "
            "Run `python ia_engine.py` to train first."
        )
    bundle = joblib.load(SERVER.model_path)
    dataset = HiveDataset(SERVER.csv_path)
    _start_csv_watcher(app, dataset, bundle, interval=5)

    # ---- error handlers --------------------------------------------------
    @app.errorhandler(SensorError)
    def _bad_sensor(err: SensorError):  # type: ignore[unused-ignore]
        return jsonify({"error": "sensor_error", "detail": str(err)}), 400

    @app.errorhandler(FilterError)
    def _bad_filter(err: FilterError):  # type: ignore[unused-ignore]
        return jsonify({"error": "filter_error", "detail": str(err)}), 400

    @app.errorhandler(Exception)
    def _unhandled(err: Exception):  # type: ignore[unused-ignore]
        app.logger.exception("unexpected error")
        return jsonify({"error": "internal", "detail": str(err)}), 500

    # ---- rutas -----------------------------------------------------------
    @app.get("/")
    def dashboard():
        return render_template(
            "dashboard.html",
            setpoint=BIOLOGY.nido_setpoint_c,
            goal_kg=BIOLOGY.harvest_goal_kg,
        )

    @app.get("/api/meta")
    def meta():
        """Constantes que el frontend necesita para evitar hardcodear nada."""
        return jsonify(
            {
                "dataset": {
                    "start": dataset.start.isoformat(),
                    "end": dataset.end.isoformat(),
                    "hours": int(
                        (dataset.end - dataset.start).total_seconds() // 3600
                    ),
                },
                "presets": [name for name, _ in SERVER.preset_hours] + ["all"],
                "biology": {
                    "setpoint_c": BIOLOGY.nido_setpoint_c,
                    "stress_temp_c": BIOLOGY.stress_temp_c,
                    "harvest_goal_kg": BIOLOGY.harvest_goal_kg,
                    "gauge_temp_min_c": BIOLOGY.gauge_temp_min_c,
                    "gauge_temp_max_c": BIOLOGY.gauge_temp_max_c,
                },
                "labels": LABEL_MAP,
                "feature_columns": list(FEATURE_COLUMNS),
            }
        )

    @app.get("/api/window")
    def window():
        """Slice filtrado + diagnóstico IA al final del slice + pronóstico.

        Para que `predict_health` tenga al menos `delta_long_hours` (24h) de
        historia, se construye `rolling` con `trailing_hours` desde el
        timestamp del último punto del slice — no del slice mismo. Es una
        sutileza importante: si el usuario filtra "1 día específico", el
        slice tiene 24 muestras pero la inferencia necesita las 48h previas
        para que las features no sean ceros.
        """
        MAX_CHART_POINTS = 120
        win = parse_window(request.args, dataset.end, dataset.start)
        sliced = dataset.slice(win)
        rows = _df_to_rows(sliced)
        now_ts = datetime.now()
        now_str = now_ts.strftime("%Y-%m-%d %H:%M:%S")
        past_rows = [r for r in rows if r["timestamp"] <= now_str]
        latest_row = (past_rows if past_rows else rows)[-1]
        if len(rows) > MAX_CHART_POINTS:
            step = max(1, len(rows) // MAX_CHART_POINTS)
            rows = rows[::step]
        if not rows:
            return (
                jsonify(
                    {
                        "error": "empty_window",
                        "detail": "no readings in selected range",
                        "window": {
                            "start": win.start.isoformat(),
                            "end": win.end.isoformat(),
                            "label": win.label,
                        },
                    }
                ),
                404,
            )

        last_ts = min(sliced["timestamp"].iloc[-1].to_pydatetime(), now_ts)
        rolling = _df_to_rows(
            dataset.trailing_hours(last_ts, hours=MODEL.rolling_prediction_hours)
        )
        health = predict_health(bundle, rolling)
        health["delta_peso_7d"] = round(_compute_delta_peso_7d(dataset, last_ts), 3)
        forecast = forecast_harvest(
            bundle, float(sliced["peso_kg"].iloc[-1]), rolling=rolling
        )
        timeline = _build_timeline(sliced)

        age_seconds = (datetime.now() - last_ts).total_seconds()

        return jsonify(
            {
                "window": {
                    "start": win.start.isoformat(),
                    "end": win.end.isoformat(),
                    "label": win.label,
                },
                "history": rows,
                "latest": latest_row,
                "health": health,
                "forecast": forecast,
                "timeline": timeline,
                "data_freshness": {
                    "last_reading_at": last_ts.isoformat(),
                    "age_seconds": int(age_seconds),
                    "age_human": _human_age(age_seconds),
                    "stale": age_seconds > 3 * 3600,  # >3h sin lectura = sospechoso
                },
                "setpoint_c": BIOLOGY.nido_setpoint_c,
                "goal_kg": BIOLOGY.harvest_goal_kg,
            }
        )

    @app.post("/api/reading")
    def ingest_reading():
        """Ingesta de una lectura cruda: HX711 counts + BME280 (T/H/P).

        Flujo:
            1. `parse_frame` valida y normaliza (counts → kg, rango checks).
            2. Se inserta en `HiveDataset` (in-memory; reiniciar Flask pierde
               la lectura — la persistencia a SQLite es el siguiente paso).
            3. Se recalcula `rolling` con la nueva fila incluida.
            4. Se devuelve diagnóstico actualizado.
        """
        payload = request.get_json(silent=True)
        ip = request.remote_addr
        app.logger.warning(
            "[POST /api/reading] desde %s | peso=%s | temp=%s",
            ip,
            payload.get("peso") if payload else "?",
            payload.get("temperatura") if payload else "?",
        )
        if payload is None:
            raise SensorError("body is not valid JSON")
        frame = parse_frame(payload)
        dataset.append(frame)
        rolling = _df_to_rows(
            dataset.trailing_hours(
                frame.timestamp, hours=MODEL.rolling_prediction_hours
            )
        )
        health = predict_health(bundle, rolling)
        health["delta_peso_7d"] = round(
            _compute_delta_peso_7d(dataset, frame.timestamp), 3
        )
        forecast = forecast_harvest(bundle, frame.peso_kg, rolling=rolling)
        return jsonify(
            {
                "accepted": frame.as_row(),
                "health": health,
                "forecast": forecast,
            }
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=SERVER.host, port=SERVER.port, debug=False)
