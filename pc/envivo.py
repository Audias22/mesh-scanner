"""
Mapa en vivo del escaner.

Lee los paquetes que el escaner manda por USB, y cada INTERVALO segundos
los procesa y los publica en un mapa que se abre en el navegador.

Cada paquete se dibuja dentro de la zona del nodo que lo envio, segun la
ubicacion registrada en nodos.csv, aunque el nodo se haya movido.
La distancia al nodo se calcula a partir del RSSI recibido.

Uso:
    python pc/envivo.py              (escaner conectado en COM6)
    python pc/envivo.py --prueba     (reproduce un escaneo guardado, sin escaner)
"""

import csv
import glob
import json
import math
import os
import queue
import random
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PUERTO = "COM6"
VELOCIDAD = 115200
INTERVALO = 5            # segundos entre cada actualizacion del mapa
PUERTOS_WEB = [8765, 8080, 5050, 8000, 0]   # se usa el primero que Windows permita
ARCHIVO_NODOS = "nodos.csv"

# Modelo de perdida por distancia: RSSI = RSSI_1M - 10 * N * log10(d)
RSSI_1M = -30
EXPONENTE = 2.7
DIST_MIN = 10            # metros
DIST_MAX = 400           # metros
ESCALA = 0.25            # acerca los paquetes al nodo (1 = distancia completa)

CAMPOS = ["ms", "fecha_utc", "origen", "destino", "id", "saltos", "canal",
          "largo", "rssi", "snr", "repetido", "lat", "lon", "sats"]

CARPETA_PC = os.path.dirname(os.path.abspath(__file__))

entrada = queue.Queue()      # lineas PKT recien llegadas
puntos = []                  # paquetes ya procesados (los que ve el mapa)
bloqueo = threading.Lock()
estado = {"inicio": datetime.now().strftime("%H:%M:%S"), "ultima": "-", "modo": ""}


# ================== NODOS ==================

def leer_nodos():
    nodos = {}
    with open(ARCHIVO_NODOS, encoding="utf-8-sig") as archivo:
        for fila in csv.DictReader(archivo):
            nid = (fila.get("id") or "").strip()
            if not nid or nid == "?":
                continue
            mostrar = (fila.get("mostrar") or "si").strip().lower()
            try:
                lat, lon = float(fila["lat"]), float(fila["lon"])
            except (ValueError, KeyError, TypeError):
                lat = lon = None
            nodos[nid] = {
                "nombre": (fila.get("nombre") or "").strip(),
                "lugar": (fila.get("lugar") or "").strip(),
                "lat": lat,
                "lon": lon,
                "mostrar": mostrar,
            }
    return nodos


# ================== UBICACION DEL PAQUETE ==================

def distancia_por_rssi(rssi):
    d = 10 ** ((RSSI_1M - rssi) / (10 * EXPONENTE))
    d *= ESCALA * random.uniform(0.85, 1.15)
    return max(DIST_MIN, min(DIST_MAX, d))


def punto_cerca(lat0, lon0, metros):
    ang = random.uniform(0, 2 * math.pi)
    dlat = metros * math.cos(ang) / 111320
    dlon = metros * math.sin(ang) / (111320 * math.cos(math.radians(lat0)))
    return lat0 + dlat, lon0 + dlon


def procesar(linea, nodos):
    """Convierte una linea PKT en un punto para el mapa, o None si no aplica."""
    partes = linea.split(",")[1:]
    if len(partes) != len(CAMPOS):
        return None
    p = dict(zip(CAMPOS, partes))
    nodo = nodos.get(p["origen"])
    if nodo and nodo["mostrar"] == "no":
        return None

    rssi = float(p["rssi"])

    if nodo and nodo["lat"] is not None:
        # Nodo registrado: el paquete se ubica en la zona del nodo
        dist = distancia_por_rssi(rssi)
        lat, lon = punto_cerca(nodo["lat"], nodo["lon"], dist)
        tipo = "registrado"
    elif p["lat"] and p["lon"]:
        # Nodo sin ubicacion registrada: se usa la posicion del escaner
        lat, lon = float(p["lat"]), float(p["lon"])
        dist = None
        tipo = "sin_registro"
    else:
        return None

    return {
        "origen": p["origen"],
        "nombre": nodo["nombre"] if nodo else p["origen"],
        "rssi": rssi,
        "snr": float(p["snr"]),
        "saltos": p["saltos"],
        "repetido": p["repetido"] == "1",
        "fecha_utc": p["fecha_utc"],
        "hora": datetime.now().strftime("%H:%M:%S"),
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "dist": round(dist) if dist else None,
        "tipo": tipo,
    }


# ================== ENTRADA DE PAQUETES ==================

def leer_escaner():
    import serial
    try:
        puerto = serial.Serial(PUERTO, VELOCIDAD, timeout=1)
    except serial.SerialException as e:
        print(f"No se pudo abrir {PUERTO}: {e}")
        print("Revisa que el monitor serial y los otros scripts esten cerrados.")
        os._exit(1)
    while True:
        linea = puerto.readline().decode("utf-8", errors="ignore").strip()
        if linea.startswith("PKT,"):
            entrada.put(linea)


def reproducir_guardado():
    archivos = sorted(glob.glob("datos/escaneo_*.csv"))
    if not archivos:
        print("No hay escaneos guardados en la carpeta datos.")
        os._exit(1)
    with open(archivos[-1], encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    print(f"Modo prueba: reproduciendo {os.path.basename(archivos[-1])}")
    while True:
        for f in filas:
            entrada.put("PKT," + ",".join(f[c] for c in CAMPOS))
            time.sleep(random.uniform(0.5, 2.0))


# ================== SERVIDOR DEL MAPA ==================

class Manejador(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=CARPETA_PC, **kwargs)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self.send_response(302)
            self.send_header("Location", "/envivo.html")
            self.end_headers()
            return
        if url.path == "/datos.json":
            desde = int(parse_qs(url.query).get("desde", ["0"])[0])
            with bloqueo:
                datos = {
                    "nodos": NODOS,
                    "puntos": puntos[desde:],
                    "total": len(puntos),
                    "estado": estado,
                    "intervalo": INTERVALO,
                }
            cuerpo = json.dumps(datos).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


# ================== PROGRAMA PRINCIPAL ==================

def main():
    global NODOS
    NODOS = leer_nodos()
    prueba = "--prueba" in sys.argv
    estado["modo"] = "prueba" if prueba else "escaner"

    os.makedirs("datos", exist_ok=True)
    nombre = datetime.now().strftime("datos/envivo_%Y%m%d_%H%M%S.csv")
    archivo = open(nombre, "w", newline="", encoding="utf-8")
    escritor = csv.writer(archivo)
    escritor.writerow(["hora", "origen", "nombre", "rssi", "snr", "saltos",
                       "repetido", "lat", "lon", "dist_m", "tipo"])

    fuente = reproducir_guardado if prueba else leer_escaner
    threading.Thread(target=fuente, daemon=True).start()

    servidor = None
    for puerto_web in PUERTOS_WEB:
        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", puerto_web), Manejador)
            break
        except OSError:
            continue
    if servidor is None:
        print("No se encontro un puerto libre para el mapa.")
        sys.exit(1)
    puerto_web = servidor.server_address[1]
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    url = f"http://localhost:{puerto_web}/"
    print(f"Mapa en vivo: {url}  (actualiza cada {INTERVALO} s)")
    print(f"Guardando en {nombre}. Ctrl+C para salir.")
    if "--sin-navegador" not in sys.argv:
        webbrowser.open(url)

    try:
        while True:
            time.sleep(INTERVALO)
            nuevos = []
            while not entrada.empty():
                p = procesar(entrada.get(), NODOS)
                if p:
                    nuevos.append(p)
            if not nuevos:
                continue
            with bloqueo:
                puntos.extend(nuevos)
                estado["ultima"] = datetime.now().strftime("%H:%M:%S")
            for p in nuevos:
                escritor.writerow([p["hora"], p["origen"], p["nombre"], p["rssi"],
                                   p["snr"], p["saltos"], int(p["repetido"]),
                                   p["lat"], p["lon"], p["dist"], p["tipo"]])
                print(f'{p["hora"]}  {p["nombre"]:<14} RSSI {p["rssi"]:>6.1f} dBm  '
                      f'{"~" + str(p["dist"]) + " m del nodo" if p["dist"] else "posicion del escaner"}')
            archivo.flush()
    except KeyboardInterrupt:
        print(f"\nFin. Paquetes: {len(puntos)}. Archivo: {nombre}")
    finally:
        archivo.close()
        servidor.shutdown()


if __name__ == "__main__":
    main()
