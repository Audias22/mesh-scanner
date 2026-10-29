import csv
import os
import sys
from datetime import datetime

import serial

PUERTO = "COM6"
VELOCIDAD = 115200
CAMPOS = ["ms", "fecha_utc", "origen", "destino", "id", "saltos", "canal",
          "largo", "rssi", "snr", "repetido", "lat", "lon", "sats"]


def main():
    os.makedirs("datos", exist_ok=True)
    nombre = datetime.now().strftime("datos/escaneo_%Y%m%d_%H%M%S.csv")

    try:
        puerto = serial.Serial(PUERTO, VELOCIDAD, timeout=1)
    except serial.SerialException as e:
        print(f"No se pudo abrir {PUERTO}: {e}")
        print("Revisa que el monitor serial de PlatformIO este cerrado.")
        sys.exit(1)

    print(f"Escuchando {PUERTO}. Guardando en {nombre}. Ctrl+C para salir.")
    total = 0
    unicos = 0

    with open(nombre, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(CAMPOS)

        try:
            while True:
                linea = puerto.readline().decode("utf-8", errors="ignore").strip()
                if not linea:
                    continue

                # Mensajes informativos del escaner
                if linea.startswith("#"):
                    print(linea)
                    continue

                if not linea.startswith("PKT,"):
                    continue

                partes = linea.split(",")[1:]
                if len(partes) != len(CAMPOS):
                    print(f"Linea incompleta: {linea}")
                    continue

                datos = dict(zip(CAMPOS, partes))
                escritor.writerow(partes)
                archivo.flush()

                total += 1
                if datos["repetido"] == "0":
                    unicos += 1

                posicion = f'{datos["lat"]}, {datos["lon"]}' if datos["lat"] else "sin posicion"
                marca = "  (repetido)" if datos["repetido"] == "1" else ""
                print(f'[{total}] {datos["origen"]}  RSSI {datos["rssi"]} dBm  '
                      f'SNR {datos["snr"]} dB  {posicion}{marca}')

        except KeyboardInterrupt:
            print(f"\nFin. Paquetes: {total}, unicos: {unicos}. Archivo: {nombre}")
        finally:
            puerto.close()


if __name__ == "__main__":
    main()