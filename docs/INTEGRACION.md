# Integración ESP32 ↔ Servidor ↔ Dashboard IA

Este documento describe cómo fluyen los datos desde el ESP32 hasta el servidor Flask simple (`servidor.py`) y analiza la brecha de integración con el dashboard de inteligencia artificial (`bee-smart-iot`).

---

## 1. Flujo Actual del Sistema

```
┌─────────────────────┐
│   ESP32 (firmware)  │
│  Potenciómetro GPIO34│
│  BME280 (I2C)       │
└────────┬────────────┘
         │ HTTPS POST /data  (Cloudflare Tunnel)
         │ puerto 8081
         ▼
┌──────────────────────────────┐
│   servidor.py (Flask)        │
│   puerto 8081                │
│   Recibe JSON → datos.csv    │
└──────────────────────────────┘

                 (sin conexión directa)

┌──────────────────────────────┐
│   bee-smart-iot (Flask)      │
│   puerto 5000                │
│   Usa bee_history.csv        │
│   (dataset sintético)        │
└──────────────────────────────┘
```

Los dos servidores Flask corren de manera **independiente**. Actualmente no están conectados entre sí.

---

## 2. JSON que envía el ESP32

El firmware construye y envía este JSON a `POST /data` en `servidor.py`:

```json
{
  "id":          1,
  "timestamp":   "2026-05-21T14:32:05",
  "peso":        32.456,
  "temperatura": 35.20,
  "humedad":     65.0,
  "presion":     1013.25,
  "movimiento":  1,
  "alerta":      "OK"
}
```

**Campo `peso`:** El firmware lee el potenciómetro en GPIO 34 vía ADC, mapea 0–4095 a 0–60 kg y aplica un filtro exponencial (ALFA = 0.10). El resultado ya es un peso calibrado en kg.

**Campo `alerta`:** Generada por el firmware según umbrales:
- `"Cosecha (>9.5kg)"` si peso > 9.5 kg
- `"Inanición (<4.0kg)"` si peso < 4.0 kg
- `"OK"` en cualquier otro caso

---

## 3. Cómo `servidor.py` recibe y guarda los datos

```python
# servidor.py — ruta de recepción
@app.route('/data', methods=['POST'])
def receive_data():
    data = request.get_json()
    writer.writerow([
        data.get("id"),
        data.get("timestamp"),
        f"{data.get('peso', 0):.3f}",
        f"{data.get('temperatura', 0):.2f}",
        f"{data.get('humedad', 0):.1f}",
        f"{data.get('presion', 0):.2f}",
        data.get("movimiento"),
        data.get("alerta")
    ])
```

### Esquema de `datos.csv`

| Columna | Tipo | Ejemplo | Origen |
|---------|------|---------|--------|
| `id` | entero | `27` | Contador incremental en firmware |
| `timestamp` | ISO 8601 | `2026-05-21T14:32:05` | Hora NTP del ESP32 |
| `peso_kg` | float (3 dec) | `32.456` | Potenciómetro GPIO 34 → ADC → kg |
| `temperatura_c` | float (2 dec) | `35.20` | BME280 |
| `humedad_pct` | float (1 dec) | `65.0` | BME280 |
| `presion_hpa` | float (2 dec) | `1013.25` | BME280 |
| `movimiento` | 0 / 1 | `1` | Derivado del peso (2–8 kg → 1) |
| `alerta` | string | `"OK"` | Lógica del firmware |

Ejemplo de fila real:
```
27,2026-05-19T22:56:11,0.000,26.31,49.6,1013.79,0,Inanición (<4.0kg)
```

---

## 4. Qué espera `bee-smart-iot` (`POST /api/reading`)

El dashboard IA tiene su propio endpoint de ingesta que espera un formato distinto:

```json
{
  "raw_counts": 730000,
  "temp_c":     35.20,
  "hum_pct":    65.0,
  "pres_hpa":   1013.25
}
```

El dashboard realiza su propia calibración interna:
```
peso_kg = (raw_counts − 84230) / 21500
```

---

## 5. Tabla de Incompatibilidades

| Campo firmware | Campo bee-smart-iot | Tipo de diferencia |
|---|---|---|
| `peso` (kg calibrado) | `raw_counts` (conteos ADC crudos) | **Incompatible** — el firmware ya calibra, el dashboard quiere el valor crudo |
| `temperatura` | `temp_c` | Solo nombre diferente |
| `humedad` | `hum_pct` | Solo nombre diferente |
| `presion` | `pres_hpa` | Solo nombre diferente |
| `id`, `movimiento`, `alerta` | — | El dashboard no los usa |

**Nota sobre `raw_counts`:** Con el HX711 original, el firmware podía enviar los conteos crudos. Desde que se sustituyó el HX711 por un potenciómetro en GPIO 34, el ESP32 calcula el peso directamente desde el ADC y nunca genera `raw_counts` de HX711 reales.

---

## 6. Opciones de Integración

### Opción A — Firmware envía directo al dashboard IA

Modificar el firmware para que envíe al puerto 5000 de `bee-smart-iot` con los nombres correctos. Como no hay `raw_counts` real, se puede invertir la fórmula de calibración para generar un valor compatible:

```
raw_counts_simulado = (peso_kg × 21500) + 84230
```

Cambios necesarios en firmware:
- Renombrar `temperatura` → `temp_c`, `humedad` → `hum_pct`, `presion` → `pres_hpa`
- Agregar campo `raw_counts` calculado a partir del peso
- Apuntar `SERVER_URL` al puerto 5000

**Ventaja:** Los datos reales del ESP32 alimentan el modelo de IA.  
**Desventaja:** Se pierde `servidor.py` como logger simple; hay que adaptar el firmware.

---

### Opción B — Modificar bee-smart-iot para aceptar el formato actual

Agregar soporte en `/api/reading` para recibir `peso` (kg) directamente, sin exigir `raw_counts`. El endpoint detecta si recibe `raw_counts` o `peso` y actúa en consecuencia.

Cambios necesarios en `bee-smart-iot/app_flask.py`:
- Aceptar `peso` como alternativa a `raw_counts`
- Si llega `peso`, calcular `raw_counts = (peso × 21500) + 84230` internamente

**Ventaja:** El firmware no cambia, `servidor.py` sigue funcionando en paralelo.  
**Desventaja:** Requiere modificar el código del dashboard IA.

---

### Opción C — Bridge/adaptador (servidor.py reenvía a bee-smart-iot)

`servidor.py` recibe del ESP32, guarda en `datos.csv` como siempre, y además reenvía la lectura a `bee-smart-iot` con el formato correcto.

```python
# En servidor.py, después de guardar en CSV:
import requests
requests.post("http://localhost:5000/api/reading", json={
    "raw_counts": int(peso_kg * 21500 + 84230),
    "temp_c":     temperatura,
    "hum_pct":    humedad,
    "pres_hpa":   presion
})
```

**Ventaja:** Ninguno de los dos proyectos principales cambia. Solución de bajo impacto.  
**Desventaja:** `servidor.py` se vuelve dependiente de que `bee-smart-iot` esté corriendo.

---

## 7. Estado Actual

| Sistema | Puerto | Estado | Dataset |
|---------|--------|--------|---------|
| `servidor.py` | 8081 | Corriendo | `datos.csv` (lecturas reales) |
| `bee-smart-iot` | 5000 | Independiente | `bee_history.csv` (sintético) |
| Conexión entre ambos | — | **No existe** | — |

El ESP32 actualmente envía datos **solo** a `servidor.py`. El dashboard IA trabaja únicamente con su dataset sintético de 720 horas generado por `data_generator.py`.

---

## 8. Recomendación

Para una demo académica, la **Opción C** (bridge en `servidor.py`) es la menos invasiva: no modifica ni el firmware ni el dashboard, y permite que ambos sistemas funcionen en paralelo mostrando datos reales en el dashboard IA.
