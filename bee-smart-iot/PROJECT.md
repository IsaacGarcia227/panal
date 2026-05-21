# Bee-Smart IoT — Documentación técnica completa

> Sistema de monitoreo y diagnóstico AI-driven para colmena Apis mellifera en
> Ensenada, Baja California. Este archivo es el manual de referencia: una IA
> u operador humano que lo lea de principio a fin debe poder reproducir,
> entender y extender el proyecto sin necesidad de leer el código.

---

## 1. Visión general

El sistema ingiere lecturas de sensores físicos de una colmena (peso vía
celda de carga HX711; temperatura/humedad/presión vía BME280) y, a partir
de ellas, diagnostica el estado de salud de la colonia en uno de cuatro
estados biológicamente significativos:

| ID | Estado | Firma física |
|----|--------|--------------|
| 0 | **SANA** | T ∈ [34, 36] °C estable, peso creciente o estable |
| 1 | **ENJAMBRAZON** | Caída de peso > 3 kg en < 1 h + cluster a 37-38 °C |
| 2 | **ESTRES_TERMICO** | T interna > 37 °C sostenida (vientos de Santa Ana) |
| 3 | **RESERVAS_BAJAS** | Pérdida de peso acumulada por ≥ 7 días |

Además calcula el pronóstico de cosecha (días estimados para alcanzar 40 kg
al ritmo actual de la colmena) y mantiene un timeline de transiciones de
estado para que el operador vea de un vistazo cuándo cambió algo.

### Por qué Ensenada

El clima costero de Baja California presenta dos amenazas estacionales muy
distintas:

1. **Vientos de Santa Ana**: aire muy seco (humedad < 10 %) y caliente
   (> 38 °C) descendiendo de la sierra; estrés térmico severo para la
   colmena.
2. **Sequía / dearth nectar**: ausencia de flores en agosto-septiembre,
   la colonia consume sus reservas y se debilita.

El generador sintético inyecta ambos eventos en el dataset de
entrenamiento para que el modelo aprenda sus firmas.

---

## 2. Quick Start

### Requisitos

- Python 3.10+ (probado con 3.13)
- ~100 MB de espacio en disco

### Instalación y arranque (4 comandos)

```bash
cd bee-smart-iot

# 1. Crear venv e instalar dependencias
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. Generar dataset sintético (720 filas horarias, 30 días)
.venv/bin/python data_generator.py
# → bee_history.csv

# 3. Entrenar Random Forest + Linear Regression
.venv/bin/python ia_engine.py
# → bee_model.joblib

# 4. Levantar el servidor + dashboard
.venv/bin/python app_flask.py
# → http://127.0.0.1:5000
```

### Variables de entorno (opcionales)

```bash
BEE_DATA_DIR=/path/to/data   # dónde residen CSV y joblib (default: dir del proyecto)
BEE_SERVER_HOST=0.0.0.0      # default 0.0.0.0
BEE_SERVER_PORT=5000         # default 5000
```

---

## 3. Estructura del proyecto

```
bee-smart-iot/
├── config.py              # ÚNICA fuente de constantes (hardware, biología,
│                          #   simulación, modelo, servidor)
├── data_generator.py      # Genera bee_history.csv con eventos inyectados
├── ia_engine.py           # Entrena RF + LinearRegression → bee_model.joblib
├── app_flask.py           # Servidor: dashboard + API + inferencia en vivo
├── templates/
│   └── dashboard.html     # Estructura del dashboard
├── static/
│   ├── style.css          # Estilos (dark, responsive)
│   └── app.js             # Lógica del frontend (filtros, polling on-demand,
│                          #   pintado de gauges, chart, probabilidades, timeline)
├── requirements.txt
├── bee_history.csv        # (generado) 720 filas horarias, schema en §4.1
├── bee_model.joblib       # (generado) bundle dict con classifier+regressor
└── PROJECT.md             # este archivo
```

**Regla de oro**: ningún número mágico debe vivir fuera de `config.py`. Si
encuentras una constante hardcodeada en otro archivo, es un bug.

---

## 4. Capa de datos

### 4.1. Schema del CSV (`bee_history.csv`)

| Columna | Tipo | Unidad | Descripción |
|---------|------|--------|-------------|
| `timestamp` | `str` (ISO) | — | Marca temporal de la lectura, paso uniforme de 1 h |
| `raw_counts` | `int` | counts | Valor crudo de 24-bit del ADC HX711 |
| `weight_kg` | `float` | kg | Peso derivado tras aplicar la calibración |
| `temp_c` | `float` | °C | Temperatura *interna* del nido (al lado del cluster) |
| `hum_pct` | `float` | % | Humedad ambiente exterior |
| `pres_hpa` | `float` | hPa | Presión atmosférica al nivel de la colmena |
| `label` | `int` | — | Ground truth: 0/1/2/3 según `LABEL_MAP` |

Total: 720 filas (30 días × 24 horas), generadas determinísticamente con
`seed=42`.

### 4.2. Calibración HX711

La ecuación de la celda de carga (lineal, dos parámetros de calibración):

```
peso_kg = (raw_counts - offset) / scale_factor
```

donde:
- `offset` = counts a tara (colmena vacía sobre la balanza), default 84 230
- `scale_factor` = counts por kg, default 21 500

Inversa (usada por el generador para fabricar `raw_counts` consistentes
con el peso simulado):

```
raw_counts = round(peso_kg · scale_factor + offset)
```

**Verificación**: el round-trip `raw_to_kg(kg_to_raw(w))` debe diferir
de `w` en a lo más `1/scale_factor ≈ 47 µg`. El error que se observa en
el CSV (~0.5 mg) es por redondear `weight_kg` a 3 decimales al escribir,
NO error de calibración.

---

## 5. Generador sintético (`data_generator.py`)

### 5.1. Submodelos físicos

**Temperatura interna del nido** (regulación apícola + oscilación diurna):

```
temp_int(h) = nido_setpoint_c
            + amp · sin(π · (hora - 6) / 12)
            + N(0, σ)
```

donde `amp = 0.8 °C` (oscilación normal) y `σ = 0.15 °C` (ruido). Las
abejas mantienen el nido a 35 °C muy estrictamente; la oscilación diurna
es pequeña excepto durante eventos.

**Humedad ambiente costera** (noches húmedas, tardes secas):

```
hum(h) = base - amp · sin(π · (hora - 6) / 12) + N(0, σ)
```

con `base = 65 %`, `amp = 25 %`, `σ = 3 %`.

**Presión atmosférica** (deriva mensual + ruido):

```
pres(día) = 1013 + 3 · sin(2π · día / 30) + N(0, 0.4)
```

**Mass-balance de forrajeo** (flujo de néctar diurno + consumo nocturno):

```
peso(h+1) = peso(h) + nectar_flow(hora)

nectar_flow(hora) = max(0, 0.05 + N(0.04, 0.02))   si 9 ≤ hora ≤ 17  (día)
                  = N(-0.005, 0.004)                en otro caso       (noche)
```

Promedio neto diario ≈ +0.6 a +0.8 kg/día durante operación sana.

### 5.2. Eventos inyectados

#### Santa Ana (días 7-10)

72 h de aire seco caliente offshore:

```
heat_factor(h) = max(0, sin(π · (hora - 6) / 14))

temp_int = 35.5 + 3.5 · heat_factor + N(0, 0.25)    → llega a ~39 °C en tarde
hum      = max(4, 8 - 4 · heat_factor + N(0, 1.2)) → mínimo 4 %
pres    -= 2.5                                       → ridge offshore

label = ESTRES_TERMICO   ⇔   temp_int > 37.0
```

**Notar**: el etiquetado es estrictamente por umbral. Solo las horas donde
la temperatura realmente cruza 37 °C reciben la etiqueta — típicamente las
9-18 h de cada uno de los 3 días.

#### Enjambrazón (día 15, hora 13:00)

Evento puntual: la reina parte con ~½ de la colonia.

```
peso(t)     = peso(t-1) - 3.6 kg                ← caída brusca
temp_int(t) = 37.6 °C + N(0, 0.2)               ← cluster restante fan-cooling

# Ventana de etiquetado: 3 h antes + evento + 3 h después = 7 h
label[t-3 ... t-1] = ENJAMBRAZON  con temp ≈ 36.4 °C (inquietud pre-swarm)
label[t]           = ENJAMBRAZON
label[t+1 ... t+3] = ENJAMBRAZON  con temp ≈ 37.1 °C (cluster fanning)
```

#### Sequía / RESERVAS_BAJAS (días 23-30)

7 días continuos de pérdida sostenida:

```
peso(h+1) = peso(h) - (0.06 + U(0, 0.03))   ≈ -1.5 a -2.2 kg/día

label = RESERVAS_BAJAS  para horas > 48 h después del inicio
        (los primeros 2 días son "grace" para diferenciarse de ruido normal)
```

### 5.3. Distribución resultante

Sobre 720 filas con `seed=42`:

| Etiqueta | Filas | % |
|----------|-------|---|
| SANA | 565 | 78.5 % |
| ENJAMBRAZON | 7 | 1.0 % |
| ESTRES_TERMICO | 29 | 4.0 % |
| RESERVAS_BAJAS | 119 | 16.5 % |

El desbalance (SANA domina) justifica `class_weight="balanced"` en el RF.

---

## 6. Ingeniería de features (`ia_engine.engineer_features`)

El clasificador recibe **7 features** por observación. 4 son crudas y 3
son derivadas temporales:

### Features crudas
- `temp_c`, `hum_pct`, `pres_hpa`, `weight_kg`

### Features derivadas

| Feature | Fórmula | Para qué |
|---------|---------|----------|
| `delta_peso_1h` | `peso(t) - peso(t - 1h)` | Detecta enjambrazón (spike negativo grande) |
| `delta_peso_24h` | `peso(t) - peso(t - 24h)` | Tendencia diaria, detecta sequía sostenida |
| `variacion_temp` | `\|temp(t) - 35.0\|` | Magnitud de la desviación térmica |

### Cómo se calculan las deltas (importante)

La implementación usa **lag temporal real**, no `pd.DataFrame.diff(periods=N)`:

```python
ts_sec = (timestamps - t0).total_seconds() / 3600     # horas relativas
target = ts_sec[i] - lag_hours                         # objetivo a N horas atrás
weight_at_lag = np.interp(target, ts_sec, weights, left=NaN)
delta = weights[i] - weight_at_lag
```

**Razón**: en el dataset de entrenamiento las filas están a 1 h exacta, así
que `.diff()` posicional y `np.interp` temporal dan el mismo número. Pero
en producción, si llega un POST después de un hueco de 21 h, `.diff()`
produce basura (la delta de "1 hora" sería en realidad la de 21 horas).
`np.interp` traza una recta entre las dos observaciones y devuelve el
valor correcto en `t - 1h`.

**Garantía de idempotencia**: probado que `max |new_features - old_features| = 0`
sobre el CSV uniforme.

### Métrica de display: `delta_peso_7d`

Calculada por separado en `_compute_delta_peso_7d` (no es input del modelo),
expuesta en `health.delta_peso_7d`. Sirve como early-warning de
RESERVAS_BAJAS: una caída de varios kg en 7 días aparece como `Δ7d`
fuertemente negativo antes de que el clasificador acumule las 48 h
necesarias.

---

## 7. Random Forest classifier

### Hiperparámetros (de `config.MODEL`)

```python
RandomForestClassifier(
    n_estimators=200,         # 200 árboles
    max_depth=12,             # profundidad máxima por árbol
    class_weight="balanced",  # compensa el 78%/1%/4%/16% del dataset
    random_state=7,           # reproducibilidad
    n_jobs=-1,                # paraleliza
)
```

### Por qué Random Forest (y no SVM, GBM, NN)

1. **Sin scaling**: tolera features con magnitudes muy distintas (peso en kg,
   presión en hPa, humedad en %). No se necesita StandardScaler.
2. **Robusto a outliers**: justo los eventos extremos (Santa Ana, swarm) son
   outliers físicos que queremos detectar, no descartar.
3. **Probabilidades por voting**: `predict_proba` devuelve la fracción de
   árboles que votaron por cada clase — interpretable como confianza.
4. **Sin tuning fino**: con 720 filas y 7 features, el default de 200
   árboles + max_depth=12 es óptimo. Más es overfit; menos no captura.

### Entrenamiento

```python
X_tr, X_te, y_tr, y_te = train_test_split(
    X, y,
    test_size=0.2,
    stratify=y,         # ← clave: garantiza ≥1 muestra de cada clase en cada fold
    random_state=7,
)
```

**Stratify es no-negociable** porque ENJAMBRAZON solo tiene 7 muestras
en 720; sin stratify, podría quedar ausente del test set o del train set
y el modelo fallaría silenciosamente.

### Métricas esperadas (sobre datos sintéticos)

```
                precision  recall  f1-score   support
          SANA       1.00    1.00      1.00       113
   ENJAMBRAZON       1.00    1.00      1.00         1
ESTRES_TERMICO       1.00    1.00      1.00         6
RESERVAS_BAJAS       1.00    1.00      1.00        24
      accuracy                           1.00       144
```

**100 % es lo esperado y correcto** sobre datos sintéticos donde las
etiquetas se derivan de umbrales explícitos. En datos reales esperarías
75-90 % accuracy típico de un problema biológico ruidoso.

### Inferencia: pipeline en producción

```
rolling window (48 h, ≥24 h para que delta_24h sea válida)
        │
        ▼
engineer_features(...)
        │
        ▼
last_row[FEATURE_COLUMNS].to_numpy(dtype=float64)
        │  ← cast explícito a float64 blinda contra strings llegados por JSON
        ▼
predict_proba(X)[0]
        │
        ▼
{label_id, label, confidence, probabilities, deltas}
```

**Por qué 48 h y no 24 h**: para que `delta_peso_24h` no caiga en NaN
cuando la rolling window solo tiene 24 filas. 48 h da margen para la
interpolación temporal.

---

## 8. Pronóstico de cosecha (Linear Regression + slope actual)

### Dos pendientes coexistiendo

#### 8.1. Slope histórico (en el bundle)

Entrenado con `sklearn.LinearRegression` sobre los últimos 7 días SANA
del CSV:

```python
healthy_recent = df[df.label == 0].tail(7 días)
slope_per_hour = LinearRegression().fit(hours, weights).coef_[0]
slope_per_day = slope_per_hour * 24
```

Valor típico: ~+0.21 kg/día. **Es un valor histórico fijo**, no refleja
el estado actual.

#### 8.2. Slope actual (calculado por request)

Sobre el rolling window de 48 h del momento de la consulta:

```python
slope_kg_per_día = np.polyfit(hours_relative, weights, 1)[0] * 24
```

Refleja el ritmo real **ahora**. Si la colmena está perdiendo peso, sale
negativo.

### Cuál se usa para `days_to_goal`

El **actual** siempre que tenga ≥ 2 puntos separados por > 1 h. Si no
(p.ej. una sola lectura), cae al histórico.

### Casos de salida

```python
def forecast_harvest(bundle, current_weight, rolling):
    historical = bundle.harvest_regressor.coef_[0] * 24
    current    = _current_slope_kg_per_day(rolling)  # None si insuficiente
    slope      = current if current is not None else historical

    if remaining (= goal - peso) ≤ 0:
        → days_to_goal = 0, "Meta alcanzada"
    elif slope ≤ 0:
        → days_to_goal = None, "Perdiendo X kg/día"
    else:
        → days_to_goal = remaining / slope, "Al ritmo actual: X días"
```

Tanto `slope_kg_per_day` (actual usado) como `historical_slope_kg_per_day`
se devuelven en el JSON para que el dashboard pueda mostrar el contraste.

---

## 9. Servidor Flask (`app_flask.py`)

### 9.1. Endpoints

#### `GET /` — Dashboard
Renderiza `templates/dashboard.html`. No requiere parámetros.

#### `GET /api/meta`
Metadata estática para el frontend (rango del dataset, umbrales biológicos,
labels, presets).

```json
{
  "dataset": {"start": "ISO", "end": "ISO", "hours": 719},
  "presets": ["24h", "7d", "30d", "all"],
  "biology": {
    "setpoint_c": 35.0,
    "stress_temp_c": 37.0,
    "harvest_goal_kg": 40.0,
    "gauge_temp_min_c": 33.0,
    "gauge_temp_max_c": 39.0
  },
  "labels": {"0": "SANA", "1": "ENJAMBRAZON", ...},
  "feature_columns": ["temp_c", "hum_pct", ...]
}
```

#### `GET /api/window` — Slice filtrado + diagnóstico
Acepta uno de estos query strings (prioridad: from/to > day > preset):

| Query | Significado |
|-------|-------------|
| `?preset=24h\|7d\|30d\|all` | Últimas N horas o todo el dataset |
| `?day=YYYY-MM-DD` | Las 24 h de ese día |
| `?from=ISO&to=ISO` | Rango arbitrario |

Respuesta:

```json
{
  "window": {"start": "ISO", "end": "ISO", "label": "24h"},
  "history": [ {timestamp, raw_counts, weight_kg, ...}, ... ],
  "latest": { …última fila },
  "health": {
    "label": "RESERVAS_BAJAS",
    "label_id": 3,
    "confidence": 0.995,
    "probabilities": { "SANA": 0.005, "ENJAMBRAZON": 0.0, ... },
    "delta_peso_1h": -0.08,
    "delta_peso_24h": -1.752,
    "delta_peso_7d": -12.598,
    "variacion_temp": 0.7
  },
  "forecast": {
    "slope_kg_per_day": -1.791,
    "historical_slope_kg_per_day": 0.21,
    "days_to_goal": null,
    "source": "current",
    "message": "Perdiendo 1.79 kg/día — meta inalcanzable...",
    "goal_kg": 40.0
  },
  "timeline": [
    {"timestamp": "2026-04-21 09:00:00", "from": "SANA", "to": "ESTRES_TERMICO"},
    ...
  ],
  "data_freshness": {
    "last_reading_at": "ISO",
    "age_seconds": 79712,
    "age_human": "22h 8m",
    "stale": true
  },
  "setpoint_c": 35.0,
  "goal_kg": 40.0
}
```

#### `POST /api/reading` — Ingesta en vivo

```json
{
  "raw_counts": 730000,        // (req) int 24-bit
  "temp_c": 39.2,              // (req) [-10, 60]
  "hum_pct": 7.4,              // (req) [0, 100]
  "pres_hpa": 1010.5,          // (req) [850, 1080]
  "timestamp": "ISO",          // (opt) si falta usa datetime.now()
  "offset": 84230,             // (opt) override calibración HX711
  "scale_factor": 21500        // (opt) override calibración HX711
}
```

Acepta números como strings (se coercionan a int/float internamente).
Respuesta similar a `/api/window` pero con `accepted` en vez de `history`.

### 9.2. Manejo de errores

| Excepción | Status | Body |
|-----------|--------|------|
| `SensorError` (validación de payload) | 400 | `{"error":"sensor_error","detail":"..."}` |
| `FilterError` (query param malo) | 400 | `{"error":"filter_error","detail":"..."}` |
| `Exception` (no manejada) | 500 | `{"error":"internal","detail":"..."}` |
| Ventana vacía | 404 | `{"error":"empty_window","detail":"..."}` |

### 9.3. Repositorio en memoria: `HiveDataset`

Reemplaza una base de datos para esta primera versión. Carga el CSV
completo al boot y mantiene un DataFrame ordenado por timestamp con
dtypes forzados (`int64` / `float64`). Operaciones thread-safe vía
`threading.Lock`. Tres métodos:

- `slice(window)` — filas dentro del rango temporal.
- `trailing_hours(end, hours)` — últimas N horas hasta `end`.
- `append(frame)` — agrega un POST a la cola, preservando dtypes.

**Limitación conocida**: reiniciar Flask pierde los POSTs. Migrar a SQLite
es el siguiente paso natural (ver §13).

---

## 10. Frontend dashboard (`static/app.js`)

### 10.1. Comportamiento

- **Sin polling automático**: el dashboard hace UNA llamada a `/api/window`
  cuando carga la página y otra cada vez que el operador toca un filtro.
  No hay setInterval.
- **Barra de filtros**: chips de preset (24h/7d/30d/Todo), selector de día,
  rango personalizado (datetime-local), botones Aplicar/Limpiar.
- **Chart.js**: línea dual con eje izquierdo (peso, dorado) y derecho
  (temperatura, azul). Ejes auto-ajustados con padding y snap a pasos
  enteros para evitar jitter. Altura fija via wrapper de 220 px.
- **Probabilidades**: 4 cuadritos con porcentaje a 1 decimal (suman 100.0 %
  exacto, no como `Math.round` que podía dar 101 %).
- **Timeline**: lista cronológica (más reciente primero) con borde
  coloreado según el estado destino.

### 10.2. Tarjetas

1. **Temperatura interna** — gauge horizontal de 33 a 39 °C, color por zona.
2. **Peso actual** — gauge mostrando avance hacia los 40 kg.
3. **Condiciones ambientales** — humedad y presión actuales + hint.
4. **Pronóstico de cosecha** — días a meta + KPIs (Δ1h, Δ24h, Δ7d, ΔT).
5. **Tendencia** — Chart.js con la ventana filtrada.
6. **Diagnóstico Random Forest** — 4 cuadritos de probabilidad.
7. **Eventos detectados** — timeline con transiciones de estado.

### 10.3. Sincronización con el backend

El frontend NO hardcodea umbrales. Lee de `/api/meta`:
- Rangos del gauge térmico (`gauge_temp_min_c`, `gauge_temp_max_c`)
- Umbral de estrés (`stress_temp_c`)
- Meta de cosecha (`harvest_goal_kg`)
- Set-point (`setpoint_c`)

Cambias el valor en `config.py` → el dashboard lo refleja en el próximo
refresh, sin tocar JS.

---

## 11. Validación end-to-end

### 11.1. Script de auditoría del CSV

```bash
.venv/bin/python -c "
import pandas as pd
from config import BIOLOGY, HARDWARE
df = pd.read_csv('bee_history.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])
assert len(df) == 720
assert (df['timestamp'].diff().dropna() == pd.Timedelta(hours=1)).all()
print('OK: 720 filas, paso uniforme 1h')
"
```

### 11.2. Escenarios POST esperados (en aislamiento, después de reiniciar)

| Payload | Diagnóstico | Confianza | Δ1h | Δ24h |
|---------|-------------|-----------|-----|------|
| `temp=35.1, hum=60, pres=1013, raw=638000` | SANA | 1.00 | -0.006 | -0.29 |
| `temp=39.2, hum=7.4, pres=1010.5, raw=730000` | ESTRES_TERMICO | 0.63 | +0.19 | +3.99 |
| `temp=37.5, hum=15, pres=1012, raw=640000` | ESTRES_TERMICO | 0.49 | -0.002 | -0.20 |
| `temp=34.8, hum=75, pres=1014, raw=580000` | RESERVAS_BAJAS | 0.61 | -0.13 | -2.99 |

### 11.3. Filtros que deben funcionar

```bash
curl /api/window?preset=24h        # → RESERVAS_BAJAS (fin de simulación)
curl /api/window?day=2026-04-21    # → SANA (segunda semana sin eventos)
curl /api/window?day=2026-04-23    # → mezcla SANA/ESTRES, depende de la hora final
curl /api/window?day=2026-04-29    # → ENJAMBRAZON (día del swarm)
curl /api/window?from=2026-04-23T13:00&to=2026-04-23T14:00  # → ESTRES_TERMICO pico
```

### 11.4. Validaciones de error que deben devolver 400

```bash
POST /api/reading {"raw_counts": "abc"}    # not integer
POST /api/reading {"temp_c": 80}           # out of range
POST /api/reading {"scale_factor": 0}      # division by zero
GET  /api/window?preset=bogus              # unknown preset
GET  /api/window?from=2026-05-08&to=2026-05-05  # to < from
```

---

## 12. Convenciones de código

### Estilo
- Python 3.10+, type hints obligatorios en funciones públicas.
- Docstrings en español (audiencia inicial es nuestro equipo en BC).
- Imports ordenados: stdlib → third-party → local.
- `frozen=True` en todas las dataclasses de `config.py`.
- Constantes en MAYÚSCULAS, instancias singleton (HARDWARE, BIOLOGY, …).

### Error hierarchy
- `SensorError(ValueError)` para problemas con un payload de lectura.
- `FilterError(ValueError)` para problemas con un query param.
- Ambas se mapean a 400 con detalle legible. Cualquier otra excepción es 500.

### Tipos
- Coerción explícita en bordes: `int()`, `float()`, `pd.to_numeric(..., errors="coerce")`.
- `to_numpy(dtype=np.float64)` antes de `predict_proba` — sklearn no
  tolera arrays con dtype `object`.
- `_SCHEMA` en `HiveDataset` fuerza dtypes al cargar el CSV y al `append`.

### Sin números mágicos
Cualquier valor literal numérico fuera de `config.py` y de cálculos
puramente matemáticos (e.g. `360` segundos = 1 hora) es un bug. Mover
a una dataclass.

---

## 13. Limitaciones conocidas y direcciones de mejora

### Persistencia

**Estado actual**: `HiveDataset` vive en RAM. Reiniciar Flask pierde
todos los POSTs (el CSV se reload pero los datos nuevos no).

**Siguiente paso natural**: SQLite con dos tablas:

```sql
readings(id, timestamp, raw_counts, weight_kg, temp_c, hum_pct, pres_hpa,
         label_id, confidence, delta_*, source, ingested_at)

events(id, timestamp, from_label, to_label, reading_id, confidence,
       acknowledged, notes)
```

- Cada POST: clasifica → INSERT en `readings`. Si `label != prev_label`:
  INSERT en `events`.
- Endpoints nuevos: `GET /api/events?acknowledged=0`, `POST /api/events/<id>/ack`.
- Migración del CSV inicial: `data_generator.py --bootstrap-db`.

### Modelo

- **Re-entrenamiento online**: hoy el bundle se entrena una sola vez. En
  producción habría que reentrenar mensualmente con datos persistidos.
- **Una sola colmena**: no hay `hive_id`. Múltiples colmenas requerirían
  particionar el modelo o entrenar uno global con feature `hive_id`.
- **Sin etiquetado de POSTs**: nuevos POSTs reciben `label=0` placeholder
  en `HiveDataset.append`; idealmente la inferencia sobrescribiría con
  la predicción y se usaría para alimentar el timeline.

### Frontend

- Sin auth — exponer a internet requiere agregar al menos HTTP Basic.
- Sin gráficos exportables (PDF/PNG) para reportes.
- Sin notificaciones push cuando se detecta ENJAMBRAZON.

### Datos

- 720 filas son suficientes para entrenar el clasificador pero limitan
  el regresor lineal: la pendiente "histórica" está sesgada por el período
  de recuperación post-swarm + inicio de sequía. Más datos sanos
  contiguos producirían un mejor estimador.
- El generador no modela:
  - Variación de la humedad *interna* del nido (las abejas la regulan).
  - Sonido (los micrófonos detectan enjambrazón antes que el peso).
  - Actividad de pecoreadoras (contadores ópticos en la piquera).

---

## 14. Mapa de constantes (cheat sheet de `config.py`)

| Dataclass | Constantes clave |
|-----------|------------------|
| `HardwareConfig` | `hx711_scale_factor=21500`, `hx711_offset=84230`, rangos de validación BME280 |
| `BiologyConfig` | `nido_setpoint_c=35.0`, `harvest_goal_kg=40.0`, `stress_temp_c=37.0`, `swarm_min_loss_kg=3.0`, `low_reserves_days=7` |
| `SimulationConfig` | `days=30`, `seed=42`, `start_weight_kg=25.0`, anclas de evento, ruido por evento |
| `ModelConfig` | `rf_n_estimators=200`, `rf_max_depth=12`, `rolling_prediction_hours=48`, `delta_short_hours=1`, `delta_long_hours=24` |
| `ServerConfig` | `host=0.0.0.0`, `port=5000`, `preset_hours`, paths derivados de `data_dir` |

Globales:
- `LABEL_MAP = {0: "SANA", 1: "ENJAMBRAZON", 2: "ESTRES_TERMICO", 3: "RESERVAS_BAJAS"}`
- `FEATURE_COLUMNS = ("temp_c", "hum_pct", "pres_hpa", "weight_kg",
                      "delta_peso_1h", "delta_peso_24h", "variacion_temp")`

---

## 15. Cómo extender el sistema

### Agregar una nueva feature al clasificador

1. Editar `config.FEATURE_COLUMNS` añadiendo el nombre.
2. En `ia_engine.engineer_features`, calcular la columna nueva.
3. Re-ejecutar `data_generator.py` (no es necesario si la feature se
   deriva de columnas existentes) y `ia_engine.py` para reentrenar.
4. Verificar que el nuevo bundle se serializa con la columna en su lista.

### Agregar un nuevo estado de salud

1. Definir el LABEL_ID nuevo en `config.py` y agregarlo a `LABEL_MAP`.
2. En `data_generator.py`, agregar la lógica que produce esa etiqueta.
3. Regenerar dataset + reentrenar.
4. Frontend: agregar entrada en `LABEL_TO_STATE` de `app.js` (texto + color).

### Agregar un endpoint

1. En `create_app()`, registrar la nueva ruta con `@app.get(...)` o `@app.post(...)`.
2. Usar `parse_window()` si el endpoint acepta filtros temporales.
3. Reusar `_df_to_rows()` para serializar slices de DataFrame.
4. Lanzar `SensorError` / `FilterError` para validación; el handler global
   los convierte en 400.

### Cambiar la calibración del HX711

1. En `config.HardwareConfig`, ajustar `hx711_scale_factor` y `hx711_offset`.
2. Regenerar `bee_history.csv` (los `raw_counts` se recalculan).
3. Reentrenar el modelo.
4. El cliente ESP32 puede también enviar `offset` y `scale_factor` en cada
   POST si la calibración varía por instalación.

---

## 16. FAQ para IAs / colaboradores

**¿Por qué Python en lugar de TypeScript/Go?**
Pandas y scikit-learn son insustituibles para este tipo de pipeline.

**¿Por qué Flask y no FastAPI?**
Para un dashboard interno con < 10 endpoints, Flask es más simple.
Migrar a FastAPI cuando queramos OpenAPI auto-generado o async I/O.

**¿Por qué un dict serializado y no una dataclass para el bundle?**
Hay un gotcha sutil de pickling: si entrenas con `python ia_engine.py`,
las clases del módulo se pickling como `__main__.HiveModelBundle`. Al
cargar desde `app_flask.py` (otro `__main__`), pickle no las encuentra.
Un dict puro evita el problema y es trivialmente compatible entre módulos.

**¿Por qué el modelo da 100 % en hold-out?**
Porque la ground truth se deriva de umbrales explícitos (e.g. `label =
ESTRES_TERMICO ⇔ temp > 37 °C`) y las features incluyen `temp_c` directamente.
El modelo aprende los umbrales perfectamente. En datos reales con etiquetas
manuales del apicultor (ruidosas), esperaríamos 75-90 %.

**¿Por qué los gauges no usan d3?**
Chart.js es suficiente para una línea + dos gauges. Sumar d3 triplicaría
el bundle del frontend sin valor proporcional.

**¿Por qué no usar timezone-aware datetimes?**
Toda la simulación es en local-time arbitrario; al usuario final solo le
importan rangos relativos. Cuando se conecte un ESP32 real con NTP, se
añadirá `tzinfo=UTC` en el ingest.

---

## 17. Glosario apícola

| Término | Significado |
|---------|-------------|
| **Nido de cría** | Zona central de la colmena donde la reina pone huevos y las obreras crían larvas. Temperatura ferozmente regulada a 35 °C. |
| **Cluster** | Apiñamiento térmico de obreras alrededor de la cría. |
| **Enjambrazón / swarm** | Reproducción de la colonia: la reina parte con ~½ de las abejas a fundar una nueva colmena. |
| **Pecoreadoras** | Obreras forrajeras (las que salen a recolectar néctar/polen). |
| **Néctar / dearth** | Periodos sin flujo de néctar (sequía o invierno). |
| **Cosecha** | Extracción de miel cuando la colmena alcanza una masa objetivo (40 kg típico para un Langstroth productivo). |
| **Apis mellifera** | Especie de la abeja melífera europea (la dominante en Norteamérica). |

---

*Fin del documento. Cualquier divergencia entre este documento y el código
es un bug del documento: el código manda. Actualizar este archivo cuando
agregues una feature, cambies un default en `config.py`, o modifiques la
forma de un endpoint.*
