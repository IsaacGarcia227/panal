# Generación de datos sintéticos

Este documento describe cómo generar los archivos de datos (`bee_history.csv`) usando el script
`data_generator.py`.  El script ahora acepta varios **patrones** y **ventanas de eventos** sin necesidad de modificar el código fuente.

---

## 1️⃣  Ejecutar el generador (comportamiento por defecto)

```bash
# Usa la configuración predeterminada (una ventana Santa Ana, enjambrazón y sequía)
python data_generator.py
```

Esto crea/actualiza `bee_history.csv` en la carpeta configurada por `SERVER.data_dir` (por defecto el directorio del proyecto).

---

## 2️⃣  Parámetros de línea de comandos

El script incluye un *parser* simple que permite ajustar los eventos.

| Parámetro | Descripción | Valor por defecto |
|-----------|-------------|-------------------|
| `--runs N` | Cuántas simulaciones generar (cada una con una semilla distinta). | `1` |
| `--seed S` | Semilla inicial (se incrementa automáticamente para cada corrida). | `SIMULATION.seed` (42) |
| `--santa-ana-windows "d1,e1" "d2,e2" …` | Lista de ventanas (días) para **Santa Ana**. Cada par representa *start_day*, *end_day* (inclusive). | `((7, 10),)` |
| `--no-santa-ana` | Desactivar completamente el evento de Santa Ana. | — |
| `--no-swarm` | Desactivar el evento de **enjambrazón**. | — |
| `--no-drought` | Desactivar la ventana de **sequía / reservas bajas**. | — |
| `--output PATH` | Ruta donde guardar el CSV (sobrescribe la ruta de `SERVER.csv_path`). | Ruta por defecto del servidor |

### Ejemplo: dos ventanas de Santa Ana y sin enjambrazón
```bash
python data_generator.py \
    --santa-ana-windows "7,10" "15,17" \
    --no-swarm
```
Esto producirá un archivo CSV con dos periodos de calor seco (días 7‑10 y 15‑17) y **sin** la caída de peso asociada a la enjambrazón.

---

## 3️⃣  Uso avanzado – Patrones predefinidos

Para facilitar pruebas repetibles, puedes definir *patrones* en un archivo YAML llamado
`generation_patterns.yaml` (opcional).

```yaml
patterns:
  standard:
    santa_ana_windows: [[7, 10]]
    swarm_day: 15
    drought_start: 23
    drought_end: 30
  double_santa:
    santa_ana_windows: [[7, 10], [15, 17]]
    swarm_day: 22
    drought_start: 25
    drought_end: 30
  stress_test:
    santa_ana_windows: [[5, 8], [12, 15], [20, 23]]
    swarm_day: 10
    drought_start: 1
    drought_end: 30
```

Ejecutar con un patrón:
```bash
python -m generation_runner --pattern double_santa --seed 101
```
*(`generation_runner` es un pequeño wrapper que lee el YAML y llama a `data_generator.py` con los argumentos correctos.)*

---

## 4️⃣  Integración en CI / Makefile

Si utilizas `make`, puedes añadir objetivos rápidos:
```makefile
# Generar CSV estándar
gen:
	python data_generator.py

# Generar CSV con doble ventana Santa Ana
gen-double:
	python data_generator.py --santa-ana-windows "7,10" "15,17"
```
Ejecuta `make gen` o `make gen-double`.

---

## 5️⃣  Notas de compatibilidad

- Los campos **`santa_ana_start_day`** y **`santa_ana_end_day`** siguen presentes en `config.py` para código legado que los importe directamente.  El generador ahora los usa **solo** para la primera ventana del tuple `santa_ana_windows`.
- No es necesario reinstalar dependencias al cambiar los parámetros; todo se controla desde la CLI.

---

## 📚 Resumen rápido de comandos frecuentes
```bash
# 1. CSV básico (una sola ventana Santa Ana)
python data_generator.py

# 2. CSV con 3 simulaciones diferentes (semillas 42,43,44)
python data_generator.py --runs 3

# 3. CSV sin eventos de sequía
python data_generator.py --no-drought

# 4. CSV con dos periodos Santa Ana y sin enjambrazón
python data_generator.py --santa-ana-windows "7,10" "15,17" --no-swarm
```

Con este conjunto de instrucciones deberías poder generar cualquier combinación de eventos que necesites para entrenar, validar o stress‑testear tu modelo AI.

---

*Archivo creado automáticamente por Claude Code.*
