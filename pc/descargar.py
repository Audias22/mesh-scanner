import csv
import os
import sys
import time
from datetime import datetime

import serial

PUERTO = "COM6"
VELOCIDAD = 115200
CAMPOS = ["ms", "fecha_utc", "origen", "destino", "id", "saltos", "canal",
          "largo", "rssi", "snr", "repetido", "lat", "lon", "sats"]


def abrir_puerto():
    try:
        puerto = serial.Serial(PUERTO, VELOCIDAD, timeout=3)
    except serial.SerialException as e:
        print(f"No se pudo abrir {PUERTO}: {e}")
        print("Revisa que el monitor serial de PlatformIO y escaner.py esten cerrados.")
        sys.exit(1)

    # Por si el escaner se reinicia al abrir el puerto
    time.sleep(2)
    puerto.reset_input_buffer()
    return puerto


def esperar_linea(puerto, texto, segundos=10):
    limite = time.time() + segundos
    while time.time() < limite:
        linea = puerto.readline().decode("utf-8", errors="ignore").strip()
        if linea == texto:
            return True
    return False


def descargar(puerto):
    puerto.write(b"DUMP\n")

    if not esperar_linea(puerto, "# INICIO_DESCARGA"):
        print("El escaner no respondio. Revisa que tenga el firmware con memoria.")
        return None

    filas = []
    descartadas = 0
    while True:
        linea = puerto.readline().decode("utf-8", errors="ignore").strip()
        if linea == "# FIN_DESCARGA":
            break
        if not linea:
            print("Se corto la descarga antes de terminar.")
            break
        if linea.startswith("#") or linea.startswith("PKT,"):
            continue

        partes = linea.split(",")
        if len(partes) == len(CAMPOS):
            filas.append(partes)
            if len(filas) % 200 == 0:
                print(f"  {len(filas)} mediciones...")
        else:
            descartadas += 1

    if descartadas:
        print(f"Se descartaron {descartadas} lineas incompletas.")
    return filas


def guardar_csv(filas):
    os.makedirs("datos", exist_ok=True)
    nombre = datetime.now().strftime("datos/escaneo_%Y%m%d_%H%M%S.csv")
    with open(nombre, "w", newline="", encoding="utf-8") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(CAMPOS)
        escritor.writerows(filas)
    return nombre


def borrar(puerto):
    puerto.reset_input_buffer()
    puerto.write(b"BORRAR\n")
    if esperar_linea(puerto, "# MEMORIA_BORRADA"):
        print("Memoria del escaner borrada.")
    else:
        print("No se pudo confirmar el borrado. Intenta de nuevo.")


def main():
    puerto = abrir_puerto()
    print(f"Conectado a {PUERTO}. Descargando mediciones...")

    filas = descargar(puerto)
    if filas is None:
        puerto.close()
        sys.exit(1)

    if not filas:
        print("El escaner no tiene mediciones guardadas.")
        puerto.close()
        return

    nombre = guardar_csv(filas)
    con_gps = sum(1 for f in filas if f[11] and f[12])
    unicos = sum(1 for f in filas if f[10] == "0")
    nodos = len(set(f[2] for f in filas))

    print(f"\nDescarga completa: {nombre}")
    print(f"  Mediciones: {len(filas)} ({unicos} unicas)")
    print(f"  Con posicion GPS: {con_gps}")
    print(f"  Nodos distintos: {nodos}")

    respuesta = input("\nBorrar la memoria del escaner? (s/n): ").strip().lower()
    if respuesta == "s":
        borrar(puerto)
    else:
        print("La memoria del escaner se conserva.")

    puerto.close()
    print("\nPara ver el mapa: python pc/mapa.py")


if __name__ == "__main__":
    main()