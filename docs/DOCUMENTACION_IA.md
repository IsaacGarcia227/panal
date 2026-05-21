# Bee-Smart IoT — Documentación del Sistema de IA

**Proyecto:** Panal Inteligente — Monitoreo de colmena con modelos de inteligencia artificial  
**Ubicación:** Ensenada, Baja California  
**Colonia:** *Apis mellifera*

---

## 1. Visión General

Bee-Smart IoT es un sistema de monitoreo inteligente para colmenas. Combina sensores IoT (ESP32 + HX711 + BME280) con modelos de aprendizaje automático para diagnosticar el estado de salud de la colmena en tiempo real y pronosticar cuándo se podrá cosechar la miel.

**Tecnologías principales:**

| Capa | Tecnología |
|------|-----------|
| Hardware | ESP32, HX711 (peso), BME280 (temperatura/humedad/presión) |
| Backend | Python 3, Flask |
| IA | Scikit-learn (Random Forest + Regresión Lineal) |
| Frontend | HTML + CSS + JavaScript + Chart.js |
| Datos | CSV + pandas (en memoria) |

---

## 2. Arquitectura y Flujo de Datos

```
┌─────────────────────┐
│   ESP32 (firmware)  │
│  HX711 + BME280     │
└────────┬────────────┘
         │ HTTPS POST /api/reading
         │ { raw_counts, temp_c, hum_pct, pres_hpa }
         ▼
┌──────────────────────────────────────────────┐
│           Flask (app_flask.py)               │
│                                              │
│  1. Validar rangos de sensores               │
│  2. Calibrar HX711 → peso_kg                 │
│  3. Guardar en HiveDataset (bee_history.csv) │
│  4. Calcular features temporales             │
│  5. Inferir salud con Random Forest          │
│  6. Pronosticar cosecha con Regresión Lineal │
└────────┬─────────────────────────────────────┘
         │ JSON { health, forecast, history }
         ▼
┌──────────────────────────────────────────────┐
│           Dashboard (navegador)              │
│  - Gauge temperatura y peso                  │
│  - Gráfica tendencia 24h/7d/30d              │
│  - Probabilidades de diagnóstico             │
│  - Pronóstico de cosecha                     │
│  - Línea de tiempo de eventos                │
└──────────────────────────────────────────────┘
```

---

## 3. Estructura de Archivos

```
bee-smart-iot/
├── config.py             # Todas las constantes del sistema (dataclasses)
├── data_generator.py     # Genera dataset sintético de 720 horas
├── ia_engine.py          # Entrena Random Forest + Regresión Lineal
├── app_flask.py          # Servidor Flask con 3 endpoints REST
├── templates/
│   └── dashboard.html    # Interfaz de usuario (HTML)
├── static/
│   ├── app.js            # Lógica del frontend (JavaScript)
│   └── style.css         # Tema oscuro + diseño responsivo
├── bee_history.csv       # [generado] 720 lecturas horarias con etiquetas
├── bee_model.joblib      # [generado] Modelos entrenados serializados
└── requirements.txt      # Dependencias: flask, pandas, numpy, scikit-learn, joblib
```

---

## 4. Generación de Datos Sintéticos (`data_generator.py`)

El dataset de entrenamiento se genera con fórmulas físicas deterministas (semilla `seed=42`), produciendo **720 filas** (30 días × 24 horas).

### 4.1 Submodelos físicos

**Temperatura interna de la colmena** (regulada por las abejas):
```
temp(h) = 35.0 + 0.8 · sin(π · (hora − 6) / 12) + N(0, 0.15)
```
Rango típico: 34.2–35.8 °C (estado saludable)

**Humedad ambiental** (patrón costero):
```
hum(h) = 65% − 25% · sin(π · (hora − 6) / 12) + N(0, 3%)
```
Noches húmedas (~90%), tardes secas (~40%)

**Presión atmosférica** (deriva mensual):
```
pres(día) = 1013 + 3 · sin(2π · día / 30) + N(0, 0.4) hPa
```

**Ganancia de peso** (flujo de néctar):
```
Si 9 ≤ hora ≤ 17:  Δpeso = max(0, 0.05 + N(0.04, 0.02)) kg
Si otro horario:    Δpeso = N(−0.005, 0.004) kg
```
Ganancia diaria promedio en colmena sana: +0.6 a +0.8 kg/día

### 4.2 Eventos inyectados

| Evento | Período | Condición de etiqueta |
|--------|---------|----------------------|
| Santa Ana (vientos secos y calientes) | Días 7–10 | Temperatura > 37 °C → `ESTRES_TERMICO` |
| Enjambrazón (colonia se divide) | Día 15, hora 13 | Pérdida súbita de −3.6 kg → `ENJAMBRAZON` |
| Reservas bajas (sequía de néctar) | Días 23–30 | Pérdida sostenida −1.5 kg/día → `RESERVAS_BAJAS` |

**Durante Santa Ana:**
```
temp(h) = 35.5 + 3.5 · max(0, sin(π · (hora−6) / 14)) + N(0, 0.25)
hum(h)  = max(4%, 8% − 4% · factor_calor + N(0, 1.2%))
pres    = pres − 2.5 hPa
```

**Durante Enjambrazón (hora del evento):**
```
peso(t) = peso(t−1) − 3.6 kg          ← pérdida súbita
temp(t) = 37.6 + N(0, 0.2) °C         ← abejas restantes abanicando
```
Ventana de etiquetado: 3 horas antes + evento + 3 horas después = 7 muestras

### 4.3 Distribución resultante de clases

| Clase | Filas | Porcentaje |
|-------|-------|-----------|
| SANA (0) | 565 | 78.5% |
| ENJAMBRAZON (1) | 7 | 1.0% |
| ESTRES_TERMICO (2) | 29 | 4.0% |
| RESERVAS_BAJAS (3) | 119 | 16.5% |

---

## 5. Modelos de Inteligencia Artificial (`ia_engine.py`)

### 5.1 Random Forest Classifier — Diagnóstico de salud

**¿Qué hace?** Clasifica el estado actual de la colmena en una de 4 categorías y devuelve la probabilidad de cada una.

**Hiperparámetros:**
```python
RandomForestClassifier(
    n_estimators = 200,          # 200 árboles de decisión
    max_depth    = 12,           # profundidad máxima por árbol
    class_weight = "balanced",   # compensa el desbalance 78%/1%/4%/16%
    random_state = 7,
    n_jobs       = -1            # paraleliza en todos los núcleos
)
```

**¿Por qué Random Forest?**
- No necesita escalar los datos (kg, hPa y % tienen magnitudes muy distintas)
- Robusto ante eventos extremos (Santa Ana, enjambrazón) que son outliers intencionales
- `predict_proba()` da confianza por clase, no solo la etiqueta ganadora

**Entrenamiento:**
```
División: 80% entrenamiento / 20% prueba, estratificada por clase
→ Garantiza que clases raras (ENJAMBRAZON = 7 filas) aparezcan en ambos conjuntos
```

**7 features de entrada:**

| Feature | Tipo | Descripción |
|---------|------|-------------|
| `temp_c` | Raw | Temperatura interna (°C) |
| `hum_pct` | Raw | Humedad ambiental (%) |
| `pres_hpa` | Raw | Presión atmosférica (hPa) |
| `weight_kg` | Raw | Peso de la colmena (kg) |
| `delta_peso_1h` | Derivado | Cambio de peso en la última hora |
| `delta_peso_24h` | Derivado | Cambio de peso en las últimas 24 horas |
| `variacion_temp` | Derivado | Desviación de temperatura respecto al setpoint (35 °C) |

**Fórmulas de features derivados:**
```
delta_peso_1h  = peso(t) − interpolar_peso(t − 1h)
delta_peso_24h = peso(t) − interpolar_peso(t − 24h)
variacion_temp = |temp(t) − 35.0|
```

La interpolación lineal (en lugar de `.diff()`) garantiza que funcione correctamente aunque las lecturas lleguen a intervalos irregulares.

**Salida del modelo:**
```json
{
  "label":         "ESTRES_TERMICO",
  "label_id":      2,
  "confidence":    0.87,
  "probabilities": {
    "SANA":           0.08,
    "ENJAMBRAZON":    0.00,
    "ESTRES_TERMICO": 0.87,
    "RESERVAS_BAJAS": 0.05
  }
}
```

---

### 5.2 Regresión Lineal — Pronóstico de cosecha

**¿Qué hace?** Estima cuántos días faltan para alcanzar la meta de 40 kg de miel.

**Datos de entrenamiento:** Solo filas con etiqueta `SANA` de los últimos 7 días saludables (mínimo 24 filas).

**Cálculo de pendiente:**
```
slope_hora  = LinearRegression().fit(horas, pesos).coef_[0]
slope_día   = slope_hora × 24
```

**Lógica de pronóstico:**
```
restante = 40 kg − peso_actual

Si peso_actual ≥ 40 kg    → "Meta alcanzada"
Si slope_día ≤ 0          → "Perdiendo X kg/día — meta inalcanzable"
Si slope_día > 0          → "X.X días al ritmo actual"
```

Se usan dos pendientes: la **actual** (últimas 48 horas) y la **histórica** (entrenamiento). Si el período actual es menor a 1 hora, se usa la histórica como respaldo.

---

## 6. API Flask (`app_flask.py`)

### Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Sirve el dashboard HTML |
| `GET` | `/api/meta` | Metadata estática: umbrales biológicos, etiquetas, columnas |
| `GET` | `/api/window` | Datos históricos filtrados + diagnóstico IA + pronóstico |
| `POST` | `/api/reading` | Ingesta una lectura live del ESP32 |

### GET /api/window — Parámetros de filtro

| Parámetro | Ejemplo | Descripción |
|-----------|---------|-------------|
| `preset` | `?preset=24h` | Últimas 24h / 7d / 30d / all |
| `day` | `?day=2026-04-21` | Día completo 00:00–23:59 |
| `from` + `to` | `?from=ISO&to=ISO` | Rango arbitrario |

Prioridad: `from/to` > `day` > `preset`

### POST /api/reading — Payload esperado

```json
{
  "raw_counts": 730000,
  "temp_c":     39.2,
  "hum_pct":    7.4,
  "pres_hpa":   1010.5
}
```

Validaciones automáticas: rango de 24 bits para HX711, temperatura entre −10 y 60 °C, humedad 0–100%, presión 850–1080 hPa, peso calibrado 0–200 kg.

### Calibración HX711

```
peso_kg = (raw_counts − 84230) / 21500
```
- `84230 counts` → tara (colmena vacía)
- `21500 counts/kg` → factor de escala

---

## 7. Dashboard Frontend

### Componentes visuales

1. **Gauge de temperatura** — Barra horizontal 33–39 °C
   - Azul: temp ≤ 33 °C (frío)
   - Dorado: 33–37 °C (normal)
   - Rojo: ≥ 37 °C (estrés térmico)

2. **Gauge de peso** — Barra de progreso hacia meta de 40 kg

3. **Condiciones ambientales** — Humedad (%) + Presión (hPa)
   - Alerta si humedad < 10% (posible Santa Ana)

4. **Pronóstico de cosecha** — Días estimados + KPIs (Δ1h, Δ24h, Δ7d, ΔTemp)

5. **Gráfica de tendencia** — Chart.js doble eje
   - Eje izquierdo: Peso (kg), color dorado
   - Eje derecho: Temperatura (°C), color azul

6. **Diagnóstico probabilístico** — 4 celdas con porcentaje y barra por clase

7. **Línea de tiempo** — Transiciones de estado (ej. SANA → ESTRES_TERMICO)

### Filtros

Los chips de preset (24h / 7d / 30d / Todo) llaman a `/api/window` con el parámetro correspondiente. También hay selector de fecha y rango personalizado.

### Indicador de frescura

Si la última lectura tiene más de 3 horas de antigüedad, el dashboard muestra una advertencia de datos desactualizados con el tiempo transcurrido en formato legible (`"22h 8m"`).

---

## 8. Cómo Ejecutar el Sistema

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Generar dataset sintético de entrenamiento (720 filas)
python data_generator.py
# → crea bee_history.csv

# 3. Entrenar modelos
python ia_engine.py
# → crea bee_model.joblib
# → imprime reporte de clasificación

# 4. Iniciar el servidor Flask
python app_flask.py
# → http://localhost:5000
```

**Variables de entorno opcionales:**
```
BEE_DATA_DIR     → carpeta donde buscar/escribir CSV y modelo
BEE_SERVER_HOST  → dirección de bind (default: 0.0.0.0)
BEE_SERVER_PORT  → puerto (default: 5000)
```

---

## 9. Clases de Diagnóstico

| ID | Nombre | Descripción | Señal clave |
|----|--------|-------------|-------------|
| 0 | `SANA` | Colmena saludable | Peso estable/creciendo, temp ~35 °C |
| 1 | `ENJAMBRAZON` | La reina se va con parte de la colonia | Caída súbita de peso (−3 a −4 kg en 1h) |
| 2 | `ESTRES_TERMICO` | Calor extremo (Santa Ana / verano) | Temperatura interna > 37 °C |
| 3 | `RESERVAS_BAJAS` | Escasez de néctar, colmena consumiendo reservas | Pérdida sostenida > 1 kg/día durante ≥ 2 días |
