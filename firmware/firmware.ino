/*
  PANAL INTELIGENTE - VERSIÓN ACADÉMICA
  Envío cada 10 min O ante cambios bruscos de peso (>0.4 kg)
  Datos: Peso, Temperatura, Movimiento → Guarda en laptop vía WiFi
*/
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <time.h>
#include <Adafruit_BME280.h>
#include <Adafruit_Sensor.h>
#include "HX711.h"
#include "config.h"

// ═══════════════════════════════════════════════════════════
// 📍 CONFIGURACIÓN DE PINES Y SENSORES
// ═══════════════════════════════════════════════════════════
// PIN 25 (GPIO25) → HX711 SCK - Sensor de PESO (Serial Clock)
// PIN 35 (GPIO35) → HX711 DT  - Sensor de PESO (Data)
// PIN 21 (GPIO21) → BME280 SDA - Sensor TEMPERATURA/HUMEDAD/PRESIÓN (I2C Data)
// PIN 22 (GPIO22) → BME280 SCL - Sensor TEMPERATURA/HUMEDAD/PRESIÓN (I2C Clock)
// ═══════════════════════════════════════════════════════════

// ================= CONFIGURACIÓN WIFI =================
const char* ssid = WIFI_SSID;
const char* password = WIFI_PASSWORD;
const char* serverURL = SERVER_URL;

// POTENCIÓMETRO — Simulación de sensor de peso
const int POT_PIN = 34;  // GPIO34: lectura analógica del potenciómetro
const float ALFA = 0.10;

// ───────────────────────────────────────────────────────────
// 🔧 SENSOR DE PESO HX711
// Lee el peso usando celdas de carga amplificadas
// ───────────────────────────────────────────────────────────
const int HX711_DT = 35;   // Pin de datos (GPIO35)
const int HX711_SCK = 25;  // Pin de reloj (GPIO25)
const float CALIBRATION_FACTOR = -450.0; // Ajusta este valor calibrando tu escala

// ───────────────────────────────────────────────────────────
// 🌡️ SENSOR AMBIENTAL BME280 (I2C)
// Lee: Temperatura (°C), Humedad (%), Presión (hPa)
// Protocolo: I2C (comunicación de dos líneas)
// Dirección I2C: 0x76
// ───────────────────────────────────────────────────────────
const int SDA_PIN = 21;    // I2C Data/SDA (GPIO21)
const int SCL_PIN = 22;    // I2C Clock/SCL (GPIO22)

// ️ LÓGICA DE ENVÍO
// CAMBIO PARA PRESENTACIÓN:
const unsigned long INTERVALO_MS = 15000; // 15 segundos
const float UMBRAL_CAMBIO = 1.0;                   // kg para disparo inmediato

float pesoFiltrado = 0.0;
float ultimoPesoEnviado = -1.0;
unsigned long ultimoEnvio = 0;
int recordID = 1;

// BME280 Sensor
Adafruit_BME280 bme;
bool bmeSensor = false;

// HX711 Sensor de Peso
HX711 scale;
bool weightSensor = false;

// ═══════════════════════════════════════════════════════════════════════════
// 📊 ESTRUCTURAS DE DATOS PARA SENSORES
// ═══════════════════════════════════════════════════════════════════════════

struct SensorPeso {
  float pesoRaw;
  float pesoFiltrado;
  float pesoMinimo;
  float pesoMaximo;
  bool disponible;
  uint32_t ultimaLectura;
};

struct SensorAmbiente {
  float temperatura;
  float humedad;
  float presion;
  bool disponible;
  uint32_t ultimaLectura;
};

struct DatosColmena {
  uint32_t id;
  char timestamp[30];
  SensorPeso peso;
  SensorAmbiente ambiente;
  int movimiento;
  char alerta[30];
  int httpCode;
  char motivo[20];
};

struct BME280Data {
  float temperatura;
  float humedad;
  float presion;
};

void setup() {
  Serial.begin(115200);
  delay(1000);

  // WiFi
  WiFi.begin(ssid, password);
  Serial.print("Conectando");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nWiFi OK | IP ESP32: " + WiFi.localIP().toString());

  // Hora real 
  configTime(-6 * 3600, 0, "pool.ntp.org", "time.nist.gov");
  
  // Configurar pines I2C para BME280
  Wire.begin(SDA_PIN, SCL_PIN);
  
  // ═══════════════════════════════════════════════════════════
  //  INICIALIZAR SENSOR BME280 (TEMPERATURA/HUMEDAD/PRESIÓN)
  // Pin: 21 (SDA) y 22 (SCL) - Protocolo: I2C
  // ═══════════════════════════════════════════════════════════
  // BME280 Inicialización
  Serial.print("[INIT] Intentando conectar con BME280 en dirección 0x76 (I2C: SDA=21, SCL=22)...");
  if (!bme.begin(0x76)) {
    Serial.println(" FALLO");
    Serial.println("[ERROR] BME280 NO encontrado. Continuando sin sensor ambiental.");
    bmeSensor = false;
  } else {
    Serial.println(" ✓ OK");
    Serial.println("[INFO] BME280 inicializado correctamente!");
    bmeSensor = true;
  }
  
  // ═══════════════════════════════════════════════════════════
  // 🔧 SENSOR DE PESO — Potenciómetro en GPIO 34 (simulación)
  // Rango: 0.0 – 60.0 kg
  // ═══════════════════════════════════════════════════════════
  Serial.print("[INIT] Configurando potenciómetro en GPIO 34 (simulación de peso)...");
  pinMode(POT_PIN, INPUT);
  weightSensor = true;
  Serial.println(" ✓ OK");
  Serial.println("[INFO] Rango configurado: 0.0 - 60.0 kg");
  
  // ADC
  analogSetAttenuation(ADC_11db);
  Serial.println("[SUCCESS] Sistema listo. Esperando datos...\n");
}

// Lectura del sensor BME280
SensorAmbiente leerBME280() {
  SensorAmbiente datos;
  datos.ultimaLectura = millis();
  
  if (bmeSensor) {
    datos.temperatura = bme.readTemperature();
    datos.humedad = bme.readHumidity();
    datos.presion = bme.readPressure() / 100.0; // Convertir a hPa
    datos.disponible = true;
    Serial.printf("[BME280] Temp: %.2f°C | Humedad: %.1f%% | Presión: %.2f hPa\n",
                  datos.temperatura, datos.humedad, datos.presion);
  } else {
    // Valores por defecto si sensor no disponible
    datos.temperatura = 24.0;
    datos.humedad = 50.0;
    datos.presion = 1013.25;
    datos.disponible = false;
    Serial.println("[BME280] ⚠️  Sensor no disponible. Usando valores por defecto.");
  }
  return datos;
}

// Lectura de peso via potenciómetro en GPIO 34
SensorPeso leerHX711() {
  SensorPeso datos;
  datos.ultimaLectura = millis();

  // Promediar 10 lecturas ADC para reducir ruido
  long suma = 0;
  for (int i = 0; i < 25; i++) {
    suma += analogRead(POT_PIN);
    delay(2);
  }
  float rawADC = suma / 10.0;

  // Mapear ADC (0-4095) a rango de peso (0-60 kg)
  datos.pesoRaw = (rawADC / 4095.0) * 60.0;
  datos.disponible = true;

  // Aplicar filtro exponencial
  pesoFiltrado = (pesoFiltrado * (1.0 - ALFA)) + (datos.pesoRaw * ALFA);

  // Descartar valores muy pequeños (ruido)
  if (pesoFiltrado < 0.3) pesoFiltrado = 0.0;

  // Limitar rango (0-60 kg)
  datos.pesoFiltrado = constrain(pesoFiltrado, 0.0, 60.0);

  // Registrar mínimo y máximo
  if (datos.pesoFiltrado > 0) {
    datos.pesoMinimo = (datos.pesoMinimo == 0) ? datos.pesoFiltrado : min(datos.pesoMinimo, datos.pesoFiltrado);
    datos.pesoMaximo = max(datos.pesoMaximo, datos.pesoFiltrado);
  }

  Serial.printf("[PESO] ADC: %.0f | Raw: %.3f kg | Filtrado: %.3f kg | Min: %.3f kg | Max: %.3f kg\n",
                rawADC, datos.pesoRaw, datos.pesoFiltrado, datos.pesoMinimo, datos.pesoMaximo);
  return datos;
}

void loop() {
  Serial.println("\n========== CICLO NUEVO ==========");
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) { 
    Serial.println("[ERROR] No se pudo obtener la hora del NTP. Reintentando...");
    delay(1000); 
    return; 
  }

  // 📊 LEER TODOS LOS SENSORES Y GUARDAR EN ESTRUCTURAS
  SensorPeso pesoData = leerHX711();
  float pesoActual = pesoData.pesoFiltrado;
  
  // Lectura del sensor BME280
  SensorAmbiente ambienteData = leerBME280();
  float temp = ambienteData.temperatura;
  float humidity = ambienteData.humedad;
  float pressure = ambienteData.presion;
  
  int mov = (pesoActual > 2.0 && pesoActual < 8.0) ? 1 : 0;
  Serial.printf("[MOVIMIENTO] %s\n", mov ? "Actividad detectada" : "Sin movimiento");

  bool enviar = false;
  String motivo = "";

  //  Condición 1: Cada 10 minutos
  if (ultimoEnvio == 0 || millis() - ultimoEnvio >= INTERVALO_MS) {
    enviar = true;
    motivo = "Intervalo 10 min";
    Serial.printf("[TRIGGER] Envío por intervalo (Pasaron %lu ms)\n", millis() - ultimoEnvio);
  }
  // Condición 2: Cambio brusco de peso
  else if (ultimoPesoEnviado >= 0 && abs(pesoActual - ultimoPesoEnviado) >= UMBRAL_CAMBIO) {
    enviar = true;
    motivo = "Cambio brusco";
    Serial.printf("[TRIGGER] Cambio brusco detectado (%.3f kg → %.3f kg, diferencia: %.3f kg)\n", 
                  ultimoPesoEnviado, pesoActual, abs(pesoActual - ultimoPesoEnviado));
  } else {
    Serial.println("[INFO] Sin condiciones para envío. Esperando...");
  }

  if (enviar) {
    Serial.println("[SEND] ==================== ENVIANDO DATOS ====================");
    char ts[30];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%S", &timeinfo);
    Serial.printf("[SEND] Timestamp: %s\n", ts);

    String alerta = "OK";
    if (pesoActual > 9.5) {
      alerta = "Cosecha (>9.5kg)";
      Serial.println("[ALERTA] ⚠️  COSECHA: Peso superior a 9.5kg");
    }
    else if (pesoActual < 4.0) {
      alerta = "Inanición (<4.0kg)";
      Serial.println("[ALERTA] ⚠️  INANICIÓN: Peso inferior a 4.0kg");
    }

    HTTPClient http;
    WiFiClientSecure client;
    client.setInsecure(); // Cloudflare tunnel - sin verificacion de certificado
    String url = String(serverURL) + "/data";
    Serial.printf("[SEND] URL: %s\n", url.c_str());
    http.begin(client, url);
    http.addHeader("Content-Type", "application/json");

    String json = "{\"id\":" + String(recordID++) +
                  ",\"timestamp\":\"" + String(ts) +
                  "\",\"peso\":" + String(pesoActual, 3) +
                  ",\"temperatura\":" + String(temp, 2) +
                  ",\"humedad\":" + String(humidity, 1) +
                  ",\"presion\":" + String(pressure, 2) +
                  ",\"movimiento\":" + String(mov) +
                  ",\"alerta\":\"" + alerta + "\"}";  
    Serial.println("[SEND] Enviando petición HTTP POST...");
    Serial.printf("[SEND] JSON: %s\n", json.c_str());
    
    int code = http.POST(json);
    
    if (code == 200) {
      Serial.printf("[SUCCESS] HTTP %d - Datos enviados correctamente!\n", code);
    } else {
      Serial.printf("[ERROR] HTTP %d - Error en la petición\n", code);
    }
    
    // LÍNEA COMPLETA CON TODOS LOS DATOS:
    Serial.printf("\n[RESUMEN] [%s] %s | Peso: %.3f kg | Temp: %.2f °C | Humedad: %.1f %% | Presión: %.2f hPa | Mov: %d | Alerta: %s | HTTP: %d\n",
          ts, motivo.c_str(), pesoActual, temp, humidity, pressure, mov, alerta.c_str(), code);
    http.end();

    ultimoEnvio = millis();
    ultimoPesoEnviado = pesoActual;
    Serial.println("[SEND] Pausa técnica de 2 segundos post-envío...");
    Serial.println("========================================\n");
    delay(2000); // Pausa técnica post-envío
  }

  Serial.println("[LOOP] Próximo ciclo en 1 segundo...\n");
  delay(1000); // Ciclo principal
}
