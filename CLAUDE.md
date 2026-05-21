# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Panal Inteligente** — an IoT beehive monitoring system. An ESP32 microcontroller reads weight (HX711 load cell), temperature/humidity/pressure (BME280 via I2C), and movement data, then sends it over WiFi to a Python/Flask server running on a laptop. The server writes readings to a CSV file. A Cloudflare tunnel exposes the server publicly so the ESP32 can reach it from any network.

## Structure

```
panal/
├── firmware/         # ESP32 Arduino sketch
│   ├── firmware.ino  # Main sketch
│   └── config.h      # WiFi credentials and tunnel URL (gitignored)
├── server/           # Flask server
│   ├── servidor.py   # HTTP server (port 8081)
│   └── datos.csv     # Sensor readings
└── .claude/
    └── commands/
        └── panal.md  # /panal skill - initializes the full system
```

## Running the Server

```powershell
cd server
python servidor.py
```

Server listens on `0.0.0.0:8081`. Data is appended to `server/datos.csv` (auto-created on first run with headers: `id, timestamp, peso_kg, temperatura_c, movimiento, alerta`).

## Cloudflare Tunnel

`cloudflared` está instalado en `C:\Program Files (x86)\cloudflared\cloudflared.exe` (no está en el PATH). Comando completo:

```powershell
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8081
```

After starting, copy the `https://xxxx.trycloudflare.com` URL and update `SERVER_URL` in `firmware/config.h`. The tunnel URL changes on every restart — re-upload the firmware after each change.

Use `/panal` to automate all of the above.

## Firmware (Arduino / ESP32)

The sketch is in [firmware/firmware.ino](firmware/firmware.ino). Required libraries:
- `WiFi.h`, `WiFiClientSecure.h`, `HTTPClient.h` (ESP32 core)
- `Adafruit_BME280` + `Adafruit_Sensor`
- `HX711` by bogde

Credentials and server URL live in `firmware/config.h` (not committed):

```cpp
#define WIFI_SSID     "your-network"
#define WIFI_PASSWORD "your-password"
#define SERVER_URL    "https://xxxx.trycloudflare.com"
```

## Architecture

```
ESP32 firmware  →  HTTPS POST /data (JSON)  →  Cloudflare tunnel  →  Flask server  →  datos.csv
```

**Send triggers:** Every `INTERVALO_MS` (15 s demo / 600000 ms for 10 min production) OR when weight changes by more than `UMBRAL_CAMBIO` (1.0 kg).

**Alert logic:** `alerta` field is set to `"Cosecha (>9.5kg)"`, `"Inanición (<4.0kg)"`, or `"OK"` before each POST.

**Weight filtering:** Exponential moving average with `ALFA = 0.15`; values below 0.05 kg are zeroed; range clamped to 0–10 kg.

**Movement detection:** Derived from weight range (2.0–8.0 kg → `movimiento = 1`), not a physical sensor.

## Key Configuration Values (firmware)

| Constant | Value | Purpose |
|---|---|---|
| `CALIBRATION_FACTOR` | `-450.0` | HX711 scale calibration — adjust per physical setup |
| `INTERVALO_MS` | `15000` | Send interval in ms (15 s demo / 600000 for 10 min production) |
| `UMBRAL_CAMBIO` | `1.0 kg` | Weight delta that triggers an immediate send |
| `HX711_DT` / `HX711_SCK` | GPIO 35 / 25 | Load cell amp pins |
| `SDA_PIN` / `SCL_PIN` | GPIO 21 / 22 | BME280 I2C pins |
