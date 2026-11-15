// IMU Marcha · firmware único para la IMU lumbar del Proyecto 1 (UNAB).
//
// Funciona en:
//   - M5StickS3 y M5StickC Plus2 (M5Unified reconoce cuál es)   -> dejar EQUIPO_M5STICK
//   - ESP32 DevKit + MPU-6050 (montaje del informe del profesor) -> cambiar a EQUIPO_MPU6050
//
// Qué hace:
//   1. Lee aceleración y giro a 100 Hz exactos (tarea propia, no depende del WiFi).
//   2. Los envía por UDP al puerto 5005 con el MISMO formato de 164 bytes de imu_esp32_udp.ino,
//      así que ver_imu.py, la app Android y la app de Windows los reciben sin cambios.
//   3. Se controla a distancia por HTTP (puerto 8080): iniciar, detener, estado y descargar.
//   4. Mientras graba, guarda un respaldo en su memoria interna (LittleFS): los mismos paquetes,
//      uno tras otro. Al terminar, la app de Windows lo descarga y rellena lo perdido por WiFi.
//
// Arduino IDE: paquete "esp32" de Espressif; para los Stick, además la biblioteca M5Unified.
// Esquema de particiones: elegir uno con espacio para archivos (por ejemplo "8M with spiffs").

#define EQUIPO_M5STICK
// #define EQUIPO_MPU6050

#include <WiFi.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <LittleFS.h>
#ifdef EQUIPO_M5STICK
#include <M5Unified.h>
#else
#include <Wire.h>
#endif

// ---------------------------------------------------------------- configuración
const char*    RED_WIFI      = "IMU-Marcha";     // red que crea el equipo
const char*    CLAVE_WIFI    = "marcha2026";
const char*    RED_EXISTENTE = "";               // si se llena, primero intenta unirse a esa red
const char*    CLAVE_EXISTENTE = "";
const uint16_t PUERTO_DATOS  = 5005;
const uint16_t PUERTO_CONTROL = 8080;
const int      POR_PAQUETE   = 10;
const char*    VERSION       = "1.0";

// ---------------------------------------------------------------- formato (igual que el informe)
struct __attribute__((packed)) Muestra { uint32_t t_us; int16_t ax, ay, az, gx, gy, gz; };
struct __attribute__((packed)) Paquete { uint32_t secuencia; Muestra m[POR_PAQUETE]; };

WiFiUDP udp;
WebServer servidor(PUERTO_CONTROL);
QueueHandle_t cola;
IPAddress destino;

volatile bool     grabando = false;
volatile uint32_t secuenciaActual = 0;
volatile int      bateria = -1;
volatile uint32_t descartados = 0;    // paquetes que no cupieron en la cola
String   sesion = "";
File     archivo;
uint32_t bytesGrabados = 0;
uint32_t inicioGrabacion = 0;
String   nombreEquipo = "ESP32";

// ---------------------------------------------------------------- lectura del sensor
#ifdef EQUIPO_MPU6050
const uint8_t MPU = 0x68;
void escribirRegistro(uint8_t reg, uint8_t valor) {
  Wire.beginTransmission(MPU); Wire.write(reg); Wire.write(valor); Wire.endTransmission();
}
int16_t leer16() {
  uint8_t alto = Wire.read();
  uint8_t bajo = Wire.read();
  return (int16_t)((alto << 8) | bajo);
}
#endif

int16_t aCuentas(float valor, float escala) {
  float c = valor * escala;
  if (c > 32767) c = 32767;
  if (c < -32768) c = -32768;
  return (int16_t)lroundf(c);
}

void leerSensor(Muestra &s) {
#ifdef EQUIPO_M5STICK
  float ax, ay, az, gx, gy, gz;
  M5.Imu.update();
  M5.Imu.getAccel(&ax, &ay, &az);           // g
  M5.Imu.getGyro(&gx, &gy, &gz);            // °/s
  s.t_us = micros();
  s.ax = aCuentas(ax, 8192.0); s.ay = aCuentas(ay, 8192.0); s.az = aCuentas(az, 8192.0);
  s.gx = aCuentas(gx, 65.5);   s.gy = aCuentas(gy, 65.5);   s.gz = aCuentas(gz, 65.5);
#else
  Wire.beginTransmission(MPU); Wire.write(0x3B); Wire.endTransmission(false);
  Wire.requestFrom(MPU, (uint8_t)14);
  s.t_us = micros();
  s.ax = leer16(); s.ay = leer16(); s.az = leer16();
  leer16();                                  // temperatura
  s.gx = leer16(); s.gy = leer16(); s.gz = leer16();
#endif
}

// Tarea de muestreo: 100 Hz exactos, independiente del WiFi y de la memoria.
void tareaMuestreo(void*) {
  Paquete p;
  int n = 0;
  uint32_t sec = 0;
  uint32_t ultimaBateria = 0;
  TickType_t siguiente = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&siguiente, pdMS_TO_TICKS(10));
    leerSensor(p.m[n]);
    if (++n == POR_PAQUETE) {
      p.secuencia = sec++;
      secuenciaActual = sec;
      if (xQueueSend(cola, &p, 0) != pdTRUE) descartados++;
      n = 0;
    }
#ifdef EQUIPO_M5STICK
    if (millis() - ultimaBateria > 2000) {   // mismo hilo que la IMU: evita choques en el bus I2C
      ultimaBateria = millis();
      bateria = M5.Power.getBatteryLevel();
    }
#endif
  }
}

// ---------------------------------------------------------------- control por HTTP
String limpiar(String s) {
  String r;
  for (char c : s) if (isalnum(c) || c == '_' || c == '-') r += c;
  if (r.length() > 40) r = r.substring(0, 40);
  return r;
}

String estadoJson() {
  size_t libre = LittleFS.totalBytes() - LittleFS.usedBytes();
  String j = "{";
  j += "\"equipo\":\"" + nombreEquipo + "\",";
  j += "\"version\":\"" + String(VERSION) + "\",";
  j += "\"mac\":\"" + WiFi.macAddress() + "\",";
  j += "\"grabando\":" + String(grabando ? "true" : "false") + ",";
  j += "\"sesion\":\"" + sesion + "\",";
  j += "\"segundos\":" + String(grabando ? (millis() - inicioGrabacion) / 1000.0 : 0.0, 1) + ",";
  j += "\"bytes\":" + String(bytesGrabados) + ",";
  j += "\"secuencia\":" + String(secuenciaActual) + ",";
  j += "\"descartados\":" + String(descartados) + ",";
  j += "\"bateria\":" + String(bateria) + ",";
  j += "\"libre_kb\":" + String(libre / 1024) + ",";
  j += "\"fs_hz\":100}";
  return j;
}

void responder(int codigo, const String& json) {
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.send(codigo, "application/json", json);
}

void iniciarGrabacion(String nombre) {
  if (grabando) return;
  sesion = limpiar(nombre);
  if (sesion.length() == 0) sesion = "sesion_" + String(millis() / 1000);
  archivo = LittleFS.open("/" + sesion + ".bin", FILE_WRITE);
  bytesGrabados = 0;
  inicioGrabacion = millis();
  grabando = (bool)archivo;
}

void detenerGrabacion() {
  if (!grabando) return;
  grabando = false;
  archivo.close();
}

void configurarServidor() {
  servidor.on("/estado", [] { responder(200, estadoJson()); });
  servidor.on("/inicio", [] {
    iniciarGrabacion(servidor.arg("sesion"));
    responder(grabando ? 200 : 500, estadoJson());
  });
  servidor.on("/fin", [] {
    detenerGrabacion();
    responder(200, estadoJson());
  });
  servidor.on("/archivos", [] {
    String j = "[";
    File raiz = LittleFS.open("/");
    File f = raiz.openNextFile();
    bool primero = true;
    while (f) {
      if (!primero) j += ",";
      j += "{\"nombre\":\"" + String(f.name()) + "\",\"bytes\":" + String(f.size()) + "}";
      primero = false;
      f = raiz.openNextFile();
    }
    j += "]";
    responder(200, j);
  });
  servidor.on("/archivo", [] {
    String n = servidor.arg("n");
    if (grabando && n == sesion + ".bin") { responder(409, "{\"error\":\"grabando\"}"); return; }
    File f = LittleFS.open("/" + n, FILE_READ);
    if (!f) { responder(404, "{\"error\":\"no existe\"}"); return; }
    servidor.streamFile(f, "application/octet-stream");
    f.close();
  });
  servidor.on("/borrar", [] {
    String n = servidor.arg("n");
    if (grabando && n == sesion + ".bin") { responder(409, "{\"error\":\"grabando\"}"); return; }
    bool ok = LittleFS.remove("/" + n);
    responder(ok ? 200 : 404, ok ? "{\"ok\":true}" : "{\"error\":\"no existe\"}");
  });
  servidor.onNotFound([] { responder(404, "{\"error\":\"ruta desconocida\"}"); });
  servidor.begin();
}

// ---------------------------------------------------------------- red
void iniciarRed() {
  WiFi.setSleep(false);
  if (strlen(RED_EXISTENTE) > 0) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(RED_EXISTENTE, CLAVE_EXISTENTE);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 10000) delay(200);
    if (WiFi.status() == WL_CONNECTED) {
      IPAddress ip = WiFi.localIP(), m = WiFi.subnetMask();
      destino = IPAddress(ip[0] | ~m[0], ip[1] | ~m[1], ip[2] | ~m[2], ip[3] | ~m[3]);
      return;
    }
  }
  WiFi.mode(WIFI_AP);
  WiFi.softAP(RED_WIFI, CLAVE_WIFI);
  destino = IPAddress(192, 168, 4, 255);
}

IPAddress miIP() { return WiFi.getMode() == WIFI_STA ? WiFi.localIP() : WiFi.softAPIP(); }

// ---------------------------------------------------------------- pantalla
void mostrarPantalla() {
#ifdef EQUIPO_M5STICK
  auto& d = M5.Display;
  d.startWrite();
  d.fillScreen(grabando ? TFT_RED : TFT_BLACK);
  d.setTextColor(TFT_WHITE);
  d.setCursor(4, 4);
  d.setTextSize(2);
  d.println(grabando ? "GRABANDO" : "LISTO");
  d.setTextSize(1);
  d.println();
  if (grabando) {
    d.printf("%s\n%.0f s  %u kB\n", sesion.c_str(), (millis() - inicioGrabacion) / 1000.0, bytesGrabados / 1024);
  }
  d.printf("Red: %s\nIP: %s\n", WiFi.getMode() == WIFI_STA ? RED_EXISTENTE : RED_WIFI, miIP().toString().c_str());
  d.printf("Bateria: %d %%\n", (int)bateria);
  size_t libre = LittleFS.totalBytes() - LittleFS.usedBytes();
  d.printf("Memoria libre: %u kB (~%u min)\n", (unsigned)(libre / 1024), (unsigned)(libre / 1640 / 60));
  d.endWrite();
#endif
}

// ---------------------------------------------------------------- inicio
void setup() {
  Serial.begin(115200);
#ifdef EQUIPO_M5STICK
  auto cfg = M5.config();
  M5.begin(cfg);                     // también mantiene encendido el Plus2 con batería
  M5.Display.setRotation(1);
  nombreEquipo = (M5.getBoard() == m5::board_t::board_M5StickCPlus2) ? "M5StickC Plus2" : "M5StickS3";
  if (!M5.Imu.isEnabled()) {
    M5.Display.println("IMU ERROR");
  }
#else
  nombreEquipo = "ESP32 + MPU-6050";
  Wire.begin(21, 22);
  Wire.setClock(400000);
  Wire.beginTransmission(MPU); Wire.write(0x75); Wire.endTransmission(false);
  Wire.requestFrom(MPU, (uint8_t)1);
  uint8_t id = Wire.read();
  Serial.printf("WHO_AM_I = 0x%02X %s\n", id, id == 0x68 ? "(OK)" : "(revisar conexiones)");
  escribirRegistro(0x6B, 0x00);      // despertar
  escribirRegistro(0x1A, 0x03);      // filtro interno ~44 Hz
  escribirRegistro(0x1B, 0x08);      // ±500 °/s
  escribirRegistro(0x1C, 0x08);      // ±4 g
#endif
  if (!LittleFS.begin(true)) Serial.println("No se pudo montar LittleFS");
  iniciarRed();
  udp.begin(PUERTO_DATOS);
  configurarServidor();
  Serial.printf("%s listo. IP %s\n", nombreEquipo.c_str(), miIP().toString().c_str());

  cola = xQueueCreate(64, sizeof(Paquete));
  xTaskCreatePinnedToCore(tareaMuestreo, "muestreo", 4096, nullptr, 3, nullptr, 1);
}

void loop() {
  Paquete p;
  // enviar y respaldar todo lo que haya en la cola
  while (xQueueReceive(cola, &p, 0) == pdTRUE) {
    udp.beginPacket(destino, PUERTO_DATOS);
    udp.write((uint8_t*)&p, sizeof(p));
    udp.endPacket();
    if (grabando) {
      size_t w = archivo.write((uint8_t*)&p, sizeof(p));
      bytesGrabados += w;
      if (w != sizeof(p)) detenerGrabacion();          // memoria llena
    }
  }
  servidor.handleClient();

#ifdef EQUIPO_M5STICK
  M5.update();
  if (M5.BtnA.wasPressed()) {                            // respaldo manual con el botón
    if (grabando) detenerGrabacion();
    else iniciarGrabacion("boton_" + String(millis() / 1000));
  }
#endif
  static uint32_t ultimaPantalla = 0;
  if (millis() - ultimaPantalla > 1000) { ultimaPantalla = millis(); mostrarPantalla(); }
  delay(1);
}
