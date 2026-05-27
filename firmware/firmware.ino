/*
  PANAL INTELIGENTE - VERSIÓN ACADÉMICA
  Peso simulado con joystick ARD-358
  Envío cada 15 s O ante cambios bruscos de peso (>1.0 kg)
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <time.h>
#include <Adafruit_BME280.h>
#include <Adafruit_Sensor.h>
#include "config.h"

// ── Tipos ─────────────────────────────────────────────────
struct SensorAmbiente {
  float temperatura;
  float humedad;
  float presion;
  bool  disponible;
};

// ═══════════════════════════════════════════════════════════
// PINES
// GPIO 32 → Joystick VRy  (eje Y — sube/baja peso)
// GPIO 25 → Joystick SW   (botón — reset de peso)
// GPIO 21 → BME280 SDA
// GPIO 22 → BME280 SCL
// ═══════════════════════════════════════════════════════════

const char* ssid      = WIFI_SSID;
const char* password  = WIFI_PASSWORD;
const char* serverURL = SERVER_URL;

// ── Joystick ARD-358 ────────────────────────────────────────
const int JOY_VRY    = 32;   // ADC1 — compatible con WiFi

const int   JOY_DEAD_LO  = 1000;  // zona muerta inferior
const int   JOY_DEAD_HI  = 3000;  // zona muerta superior
const float PASO_KG      = 3.0;   // kg por ciclo mientras joystick inclinado
const int   JOY_SAMPLES  = 20;    // muestras para promediar (reduce ruido ADC)

// ── BME280 ───────────────────────────────────────────────────
const int SDA_PIN = 21;
const int SCL_PIN = 22;
Adafruit_BME280 bme;
bool bmeSensor = false;

// ── Estado del peso ──────────────────────────────────────────
float pesoSimulado      = 0.0;
float ultimoPesoEnviado = -1.0;

// ── Envío ────────────────────────────────────────────────────
const unsigned long INTERVALO_MS = 1000;
const float UMBRAL_CAMBIO = 1.0;
unsigned long ultimoEnvio = 0;
int recordID = 1;


// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1000);

  // WiFi
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);
  delay(200);
  Serial.println("[SCAN] Buscando redes...");
  int n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++)
    Serial.printf("  [%d] %s (%d dBm)\n", i+1, WiFi.SSID(i).c_str(), WiFi.RSSI(i));
  WiFi.begin(ssid, password);
  Serial.print("Conectando WiFi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED) {
    if (millis() - t0 > 20000) {
      Serial.println("\n[ERROR] WiFi timeout - reiniciando...");
      ESP.restart();
    }
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi OK | IP: " + WiFi.localIP().toString());

  // NTP
  configTime(-7 * 3600, 0, "pool.ntp.org", "time.nist.gov");

  // I2C + BME280
  Wire.begin(SDA_PIN, SCL_PIN);
  Serial.print("[INIT] BME280...");
  if (!bme.begin(0x76)) {
    Serial.println(" FALLO - usando valores por defecto");
    bmeSensor = false;
  } else {
    Serial.println(" OK");
    bmeSensor = true;
  }

  // Joystick
  Serial.printf("[INIT] Joystick ARD-358 | VRy -> GPIO %d\n", JOY_VRY);
  Serial.printf("[INIT] VRy en reposo: %d (centro esperado ~2048)\n", analogRead(JOY_VRY));
  Serial.println("[INFO] Izq: +3 kg | Der: -3 kg | Boton deshabilitado");
  Serial.println("[SUCCESS] Sistema listo.\n");
}

// ─────────────────────────────────────────────────────────────
SensorAmbiente leerBME280() {
  SensorAmbiente d;
  if (bmeSensor) {
    d.temperatura = bme.readTemperature();
    d.humedad     = bme.readHumidity();
    d.presion     = bme.readPressure() / 100.0;
    d.disponible  = true;
    Serial.printf("[BME280] Temp: %.2f°C | Hum: %.1f%% | Pres: %.2f hPa\n",
                  d.temperatura, d.humedad, d.presion);
  } else {
    d.temperatura = 24.0;
    d.humedad     = 50.0;
    d.presion     = 1013.25;
    d.disponible  = false;
    Serial.println("[BME280] Sin sensor - valores por defecto");
  }
  return d;
}

int leerJoyPromedio() {
  long suma = 0;
  for (int i = 0; i < JOY_SAMPLES; i++) {
    suma += analogRead(JOY_VRY);
    delay(2);
  }
  return suma / JOY_SAMPLES;
}

float leerPesoJoystick() {
  int vry = leerJoyPromedio();

  if (vry < JOY_DEAD_LO) {
    pesoSimulado += PASO_KG;   // joystick arriba → sube peso
  } else if (vry > JOY_DEAD_HI) {
    pesoSimulado -= PASO_KG;   // joystick abajo  → baja peso
  }

  pesoSimulado = constrain(pesoSimulado, 0.0, 60.0);
  Serial.printf("[JOY] VRy=%4d | Peso: %.1f kg\n", vry, pesoSimulado);
  return pesoSimulado;
}

// ─────────────────────────────────────────────────────────────
void loop() {
  Serial.println("\n===== CICLO =====");

  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) {
    Serial.println("[ERROR] Sin hora NTP. Reintentando...");
    delay(1000);
    return;
  }

  float pesoActual = leerPesoJoystick();
  SensorAmbiente amb = leerBME280();
  int mov = (pesoActual > 2.0 && pesoActual < 8.0) ? 1 : 0;

  bool enviar = false;
  String motivo = "";

  if (ultimoEnvio == 0 || millis() - ultimoEnvio >= INTERVALO_MS) {
    enviar = true;
    motivo = "Intervalo";
  } else if (ultimoPesoEnviado >= 0 && abs(pesoActual - ultimoPesoEnviado) >= UMBRAL_CAMBIO) {
    enviar = true;
    motivo = "Cambio brusco";
    Serial.printf("[TRIGGER] %.3f kg -> %.3f kg\n", ultimoPesoEnviado, pesoActual);
  }

  if (enviar) {
    char ts[30];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%S", &timeinfo);

    String alerta = "OK";
    if (pesoActual > 9.5)      alerta = "Cosecha (>9.5kg)";
    else if (pesoActual < 4.0) alerta = "Inanicion (<4.0kg)";

    HTTPClient http;
    WiFiClientSecure client;
    client.setInsecure();
    http.begin(client, String(serverURL) + "/data");
    http.addHeader("Content-Type", "application/json");

    String json = "{\"id\":"           + String(recordID++) +
                  ",\"timestamp\":\""  + String(ts) +
                  "\",\"peso\":"       + String(pesoActual, 3) +
                  ",\"temperatura\":"  + String(amb.temperatura, 2) +
                  ",\"humedad\":"      + String(amb.humedad, 1) +
                  ",\"presion\":"      + String(amb.presion, 2) +
                  ",\"movimiento\":"   + String(mov) +
                  ",\"alerta\":\""     + alerta + "\"}";

    int code = http.POST(json);
    Serial.printf("[SEND] %s | Peso: %.1f kg | HTTP: %d\n", motivo.c_str(), pesoActual, code);
    http.end();

    ultimoEnvio       = millis();
    ultimoPesoEnviado = pesoActual;
    delay(500);
  }

  delay(1000);
}
