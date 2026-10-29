#include <Arduino.h>
#include <RadioLib.h>
#include <U8g2lib.h>
#include <TinyGPSPlus.h>
#include <LittleFS.h>

// =====================================================
//  CONFIGURACION DE LA RED (debe coincidir con los nodos)
// =====================================================
#define NOMBRE_PRESET   "LongModerate"
#define FRECUENCIA      902.6875   // MHz (sobrescrita en los nodos)
#define ANCHO_BANDA     125.0      // kHz
#define SPREADING       11         // SF
#define CODING_RATE     8          // 4/8
#define SYNC_WORD       0x2B       // Meshtastic
#define PREAMBULO       16
// =====================================================

// ---- Pines del radio SX1262 (Heltec V3) ----
#define LORA_NSS   8
#define LORA_SCK   9
#define LORA_MOSI 10
#define LORA_MISO 11
#define LORA_RST  12
#define LORA_BUSY 13
#define LORA_DIO1 14

// ---- Pines de la pantalla OLED (Heltec V3) ----
#define OLED_SDA  17
#define OLED_SCL  18
#define OLED_RST  21
#define VEXT      36   // En LOW enciende la alimentacion de la OLED

// ---- Pines del GPS ATGM336H ----
#define GPS_RX    47   // Aqui llega el TX del GPS
#define GPS_TX    48   // Va al RX del GPS

// ---- Memoria interna ----
#define ARCHIVO_LOG     "/mediciones.csv"
#define MARGEN_LIBRE    8192   // Bytes que se dejan libres para no llenar del todo

SX1262 radio = new Module(LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY);
U8G2_SSD1306_128X64_NONAME_F_HW_I2C pantalla(U8G2_R0, OLED_RST, OLED_SCL, OLED_SDA);
HardwareSerial gpsSerial(1);
TinyGPSPlus gps;

volatile bool llegoPaquete = false;

// Datos del ultimo paquete
bool hayDatos = false;
uint32_t ultimoOrigen = 0;
float ultimoRssi = 0;
float ultimoSnr = 0;
uint32_t ultimoMillis = 0;

// Contadores
uint32_t totalPaquetes = 0;
uint32_t paquetesUnicos = 0;

// Memoria de los ultimos IDs para detectar repetidos
uint32_t idsVistos[32] = {0};
uint8_t posId = 0;

// Nodos distintos escuchados
uint32_t nodosVistos[16] = {0};
uint8_t cantidadNodos = 0;

// Estado de la memoria interna
bool memoriaOk = false;
bool memoriaLlena = false;
uint32_t guardados = 0;

// Control de refresco de pantalla
uint32_t ultimoRefresco = 0;

// Comando recibido por USB
String comando = "";

void IRAM_ATTR alRecibir() {
  llegoPaquete = true;
}

uint32_t leer32(uint8_t *p) {
  return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24);
}

bool idYaVisto(uint32_t id) {
  for (int i = 0; i < 32; i++) {
    if (idsVistos[i] == id) return true;
  }
  return false;
}

void guardarId(uint32_t id) {
  idsVistos[posId] = id;
  posId = (posId + 1) % 32;
}

void registrarNodo(uint32_t nodo) {
  for (int i = 0; i < cantidadNodos; i++) {
    if (nodosVistos[i] == nodo) return;
  }
  if (cantidadNodos < 16) {
    nodosVistos[cantidadNodos] = nodo;
    cantidadNodos++;
  }
}

// Posicion valida y reciente (menos de 3 segundos)
bool gpsValido() {
  return gps.location.isValid() && gps.location.age() < 3000;
}

// Mismos rangos y nombres que el mapa
const char *calidadSenal(float rssi) {
  if (rssi >= -90)  return "Excelente";
  if (rssi >= -105) return "Buena";
  if (rssi >= -115) return "Debil";
  return "Limite";
}

// ================== MEMORIA INTERNA ==================

void iniciarMemoria() {
  if (!LittleFS.begin(true)) {   // true = formatear si es la primera vez
    Serial.println("# Error al montar la memoria interna");
    return;
  }
  memoriaOk = true;

  // Contar las mediciones que ya habia guardadas
  File f = LittleFS.open(ARCHIVO_LOG, "r");
  if (f) {
    uint8_t bloque[512];
    size_t n;
    while ((n = f.read(bloque, sizeof(bloque))) > 0) {
      for (size_t i = 0; i < n; i++) {
        if (bloque[i] == '\n') guardados++;
      }
    }
    f.close();
  }

  if (LittleFS.totalBytes() - LittleFS.usedBytes() < MARGEN_LIBRE) {
    memoriaLlena = true;
  }
}

void guardarLinea(const char *linea) {
  if (!memoriaOk || memoriaLlena) return;

  if (LittleFS.totalBytes() - LittleFS.usedBytes() < MARGEN_LIBRE) {
    memoriaLlena = true;
    Serial.println("# Memoria llena, ya no se guardan mediciones");
    return;
  }

  File f = LittleFS.open(ARCHIVO_LOG, "a");
  if (!f) {
    Serial.println("# Error al abrir el archivo de mediciones");
    return;
  }
  f.print(linea);
  f.close();
  guardados++;
}

void descargarMemoria() {
  Serial.println("# INICIO_DESCARGA");
  File f = LittleFS.open(ARCHIVO_LOG, "r");
  if (f) {
    uint8_t bloque[256];
    size_t n;
    while ((n = f.read(bloque, sizeof(bloque))) > 0) {
      Serial.write(bloque, n);
    }
    f.close();
  }
  Serial.println("# FIN_DESCARGA");
}

void borrarMemoria() {
  LittleFS.remove(ARCHIVO_LOG);
  guardados = 0;
  memoriaLlena = false;
  Serial.println("# MEMORIA_BORRADA");
}

void infoMemoria() {
  Serial.printf("# Guardados: %u | Usado: %u de %u bytes\n",
                (unsigned)guardados,
                (unsigned)LittleFS.usedBytes(),
                (unsigned)LittleFS.totalBytes());
}

void atenderComandos() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      comando.trim();
      comando.toUpperCase();
      if (comando == "DUMP")        descargarMemoria();
      else if (comando == "BORRAR") borrarMemoria();
      else if (comando == "INFO")   infoMemoria();
      else if (comando.length() > 0) Serial.println("# Comando desconocido");
      comando = "";
    } else if (comando.length() < 32) {
      comando += c;
    }
  }
}

// ================== PANTALLA ==================

void dibujarPantalla() {
  char linea[40];

  pantalla.clearBuffer();

  // ---- Encabezado: estado del GPS y tiempo desde el ultimo paquete ----
  pantalla.setFont(u8g2_font_6x10_tf);
  if (gpsValido()) {
    snprintf(linea, sizeof(linea), "GPS %usat", (unsigned)gps.satellites.value());
  } else if (gps.satellites.isValid() && gps.satellites.value() > 0) {
    snprintf(linea, sizeof(linea), "GPS buscando");
  } else {
    snprintf(linea, sizeof(linea), "Sin GPS");
  }
  pantalla.drawStr(0, 10, linea);

  if (hayDatos) {
    uint32_t seg = (millis() - ultimoMillis) / 1000;
    if (seg < 100) {
      snprintf(linea, sizeof(linea), "hace %us", (unsigned)seg);
    } else if (seg < 6000) {
      snprintf(linea, sizeof(linea), "hace %um", (unsigned)(seg / 60));
    } else {
      snprintf(linea, sizeof(linea), "hace +1h");
    }
    pantalla.drawStr(128 - pantalla.getStrWidth(linea), 10, linea);
  }
  pantalla.drawHLine(0, 12, 128);

  if (!hayDatos) {
    // ---- Todavia no llega nada ----
    pantalla.setFont(u8g2_font_helvB10_tf);
    pantalla.drawStr(0, 30, "Escuchando...");
    pantalla.setFont(u8g2_font_6x10_tf);
    snprintf(linea, sizeof(linea), "%s %.4f", NOMBRE_PRESET, FRECUENCIA);
    pantalla.drawStr(0, 48, linea);
  } else {
    // ---- Nodo y calidad en palabras ----
    pantalla.setFont(u8g2_font_helvB10_tf);
    snprintf(linea, sizeof(linea), "%04x  %s",
             (unsigned)(ultimoOrigen & 0xFFFF), calidadSenal(ultimoRssi));
    pantalla.drawStr(0, 27, linea);

    // ---- Barra de senal (de -125 a -30 dBm) ----
    int rssi = constrain((int)ultimoRssi, -125, -30);
    int ancho = map(rssi, -125, -30, 0, 124);
    pantalla.drawFrame(0, 31, 128, 8);
    pantalla.drawBox(2, 33, ancho, 4);

    // ---- Valores exactos ----
    pantalla.setFont(u8g2_font_6x10_tf);
    snprintf(linea, sizeof(linea), "RSSI %.0f  SNR %.1f", ultimoRssi, ultimoSnr);
    pantalla.drawStr(0, 50, linea);
  }

  // ---- Estado de la memoria y nodos ----
  if (!memoriaOk) {
    snprintf(linea, sizeof(linea), "SIN MEMORIA");
  } else if (memoriaLlena) {
    snprintf(linea, sizeof(linea), "MEMORIA LLENA");
  } else {
    snprintf(linea, sizeof(linea), "Guardados:%u Nodos:%u",
             (unsigned)guardados, (unsigned)cantidadNodos);
  }
  pantalla.drawStr(0, 63, linea);

  pantalla.sendBuffer();
  ultimoRefresco = millis();
}

// ================== RADIO ==================

void procesarPaquete() {
  size_t largo = radio.getPacketLength();
  uint8_t buf[256];
  int estado = radio.readData(buf, largo);

  if (estado == RADIOLIB_ERR_NONE && largo >= 16) {
    uint32_t destino = leer32(&buf[0]);
    uint32_t origen  = leer32(&buf[4]);
    uint32_t id      = leer32(&buf[8]);
    uint8_t  flags   = buf[12];
    float rssi = radio.getRSSI();
    float snr  = radio.getSNR();

    totalPaquetes++;
    bool repetido = idYaVisto(id);
    if (!repetido) {
      paquetesUnicos++;
      guardarId(id);
    }
    registrarNodo(origen);

    hayDatos = true;
    ultimoOrigen = origen;
    ultimoRssi = rssi;
    ultimoSnr = snr;
    ultimoMillis = millis();

    // Fecha y hora UTC segun el GPS
    char fecha[24] = "";
    if (gps.date.isValid() && gps.time.isValid()) {
      snprintf(fecha, sizeof(fecha), "%04u-%02u-%02uT%02u:%02u:%02uZ",
               gps.date.year(), gps.date.month(), gps.date.day(),
               gps.time.hour(), gps.time.minute(), gps.time.second());
    }

    // Posicion (vacia si no hay fix)
    char lat[16] = "";
    char lon[16] = "";
    unsigned sats = 0;
    if (gpsValido()) {
      snprintf(lat, sizeof(lat), "%.6f", gps.location.lat());
      snprintf(lon, sizeof(lon), "%.6f", gps.location.lng());
      sats = gps.satellites.value();
    }

    // Una linea por paquete:
    // ms,fecha_utc,origen,destino,id,saltos,canal,largo,rssi,snr,repetido,lat,lon,sats
    char registro[160];
    snprintf(registro, sizeof(registro),
             "%lu,%s,!%08x,!%08x,%08x,%u,%02x,%u,%.1f,%.1f,%d,%s,%s,%u\n",
             (unsigned long)millis(), fecha, origen, destino, id,
             flags & 0x07, buf[13], (unsigned)largo, rssi, snr,
             repetido ? 1 : 0, lat, lon, sats);

    Serial.print("PKT,");
    Serial.print(registro);
    guardarLinea(registro);

    dibujarPantalla();
  } else if (estado == RADIOLIB_ERR_CRC_MISMATCH) {
    Serial.println("# Paquete con error de CRC");
  } else if (estado != RADIOLIB_ERR_NONE) {
    Serial.printf("# Error al leer: %d\n", estado);
  }

  radio.startReceive();
}

// ================== PROGRAMA PRINCIPAL ==================

void setup() {
  Serial.begin(115200);
  delay(1000);

  // Encender y arrancar la OLED
  pinMode(VEXT, OUTPUT);
  digitalWrite(VEXT, LOW);
  delay(100);
  pantalla.begin();

  pantalla.clearBuffer();
  pantalla.setFont(u8g2_font_6x10_tf);
  pantalla.drawStr(0, 32, "Iniciando...");
  pantalla.sendBuffer();

  // Memoria interna
  iniciarMemoria();

  // Arrancar el GPS
  gpsSerial.setRxBufferSize(1024);
  gpsSerial.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);

  // Arrancar el radio
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);

  // Potencia 10 dBm no se usa (el escaner nunca transmite). TCXO 1.8V.
  int estado = radio.begin(FRECUENCIA, ANCHO_BANDA, SPREADING, CODING_RATE,
                           SYNC_WORD, 10, PREAMBULO, 1.8);
  if (estado != RADIOLIB_ERR_NONE) {
    Serial.printf("# Error al iniciar radio: %d\n", estado);
    pantalla.clearBuffer();
    pantalla.drawStr(0, 32, "Error en el radio");
    pantalla.sendBuffer();
    while (true) delay(1000);
  }

  radio.setDio2AsRfSwitch(true);
  radio.setRxBoostedGainMode(true);
  radio.setPacketReceivedAction(alRecibir);
  radio.startReceive();

  Serial.printf("# Escaner listo, escuchando %.4f MHz (%s, SF%d, BW %.0f kHz, CR 4/%d)\n",
                FRECUENCIA, NOMBRE_PRESET, SPREADING, ANCHO_BANDA, CODING_RATE);
  Serial.println("# Formato: PKT,ms,fecha_utc,origen,destino,id,saltos,canal,largo,rssi,snr,repetido,lat,lon,sats");
  if (memoriaOk) infoMemoria();
  dibujarPantalla();
}

void loop() {
  // Leer el GPS siempre
  while (gpsSerial.available()) {
    gps.encode(gpsSerial.read());
  }

  // Ordenes desde la PC
  atenderComandos();

  // Procesar paquete si llego uno
  if (llegoPaquete) {
    llegoPaquete = false;
    procesarPaquete();
  }

  // Refrescar pantalla cada segundo
  if (millis() - ultimoRefresco > 1000) {
    dibujarPantalla();
  }
}