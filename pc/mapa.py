import csv
import glob
import os
import sys
import webbrowser
from pathlib import Path

import folium

ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services"
ZOOM_MAXIMO = 20        # Hasta donde deja acercar el mapa
ZOOM_IMAGENES = 18      # Ultimo nivel con imagenes reales en la zona
RSSI_SATURADO = -20     # Mas fuerte que esto: transmisor demasiado cerca
ARCHIVO_NODOS = "nodos.csv"

# Colores de borde por nodo (distintos a los del relleno de RSSI)
COLORES_NODO = ["#1e90ff", "#ff00ff", "#00e5ff", "#ffffff",
                "#8e44ad", "#ff6fb5", "#000000", "#a0522d"]


def color_por_rssi(rssi):
    if rssi >= -90:
        return "#2ecc71"   # verde: muy buena
    if rssi >= -105:
        return "#f1c40f"   # amarillo: buena
    if rssi >= -115:
        return "#e67e22"   # naranja: debil
    return "#e74c3c"       # rojo: limite


def archivo_mas_reciente():
    archivos = sorted(glob.glob("datos/escaneo_*.csv"))
    return archivos[-1] if archivos else None


def leer_puntos(ruta):
    puntos = []
    with open(ruta, encoding="utf-8") as archivo:
        for fila in csv.DictReader(archivo):
            if not fila["lat"] or not fila["lon"]:
                continue
            fila["lat"] = float(fila["lat"])
            fila["lon"] = float(fila["lon"])
            fila["rssi"] = float(fila["rssi"])
            fila["snr"] = float(fila["snr"])
            fila["saturado"] = fila["rssi"] > RSSI_SATURADO
            puntos.append(fila)
    return puntos


def leer_nodos():
    """Devuelve (nombres, fijos, ocultos, solo_mapa).
    mostrar = si    -> en el mapa y en el recuadro
    mostrar = mapa  -> solo en el mapa
    mostrar = no    -> en ningun lado"""
    nombres = {}
    fijos = {}
    ocultos = set()
    solo_mapa = set()
    if not os.path.exists(ARCHIVO_NODOS):
        return nombres, fijos, ocultos, solo_mapa

    with open(ARCHIVO_NODOS, encoding="utf-8-sig") as archivo:
        for fila in csv.DictReader(archivo):
            nid = (fila.get("id") or "").strip()
            nombre = (fila.get("nombre") or "").strip()
            lugar = (fila.get("lugar") or "").strip()
            mostrar = (fila.get("mostrar") or "si").strip().lower()
            if not nid or nid == "?":
                nid = nombre
            if not nid:
                continue

            if mostrar == "no":
                ocultos.add(nid)
                continue
            if mostrar == "mapa":
                solo_mapa.add(nid)

            nombres[nid] = nombre

            try:
                lat = float(fila["lat"])
                lon = float(fila["lon"])
            except (ValueError, KeyError, TypeError):
                continue   # Nodo sin coordenadas: solo se usa su nombre

            fijos[nid] = {"nombre": nombre, "lugar": lugar, "lat": lat, "lon": lon}

    return nombres, fijos, ocultos, solo_mapa


def asignar_colores(puntos, fijos):
    medidos = sorted(set(p["origen"] for p in puntos))
    otros = sorted(set(fijos.keys()) - set(medidos))
    ids = medidos + otros
    return {nid: COLORES_NODO[i % len(COLORES_NODO)] for i, nid in enumerate(ids)}


def nombre_nodo(nid, nombres):
    nombre = nombres.get(nid)
    if nombre and nombre != nid:
        return f"{nombre} ({nid})"
    return nid


def crear_mapa(puntos, fijos):
    lats = [p["lat"] for p in puntos] + [n["lat"] for n in fijos.values()]
    lons = [p["lon"] for p in puntos] + [n["lon"] for n in fijos.values()]

    mapa = folium.Map(location=[sum(lats) / len(lats), sum(lons) / len(lons)],
                      zoom_start=17, max_zoom=ZOOM_MAXIMO, tiles=None)

    folium.TileLayer(
        tiles=ESRI + "/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri, Maxar, Earthstar Geographics",
        name="Satelite",
        max_zoom=ZOOM_MAXIMO,
        max_native_zoom=ZOOM_IMAGENES,
        show=True,
    ).add_to(mapa)

    folium.TileLayer(
        tiles=ESRI + "/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Calles",
        max_zoom=ZOOM_MAXIMO,
        max_native_zoom=ZOOM_IMAGENES,
        show=False,
    ).add_to(mapa)

    folium.TileLayer(
        tiles=ESRI + "/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Nombres de lugares",
        overlay=True,
        show=True,
        max_zoom=ZOOM_MAXIMO,
        max_native_zoom=ZOOM_IMAGENES,
    ).add_to(mapa)

    if max(lats) - min(lats) > 0.0001 or max(lons) - min(lons) > 0.0001:
        mapa.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])

    return mapa


def agregar_mediciones(mapa, puntos, nombres, colores):
    for nid in sorted(set(p["origen"] for p in puntos)):
        propios = [p for p in puntos if p["origen"] == nid]
        capa = folium.FeatureGroup(
            name=f'Mediciones de {nombre_nodo(nid, nombres)} ({len(propios)})',
            show=True)

        for p in propios:
            texto = (f'<b>Nodo:</b> {nombre_nodo(nid, nombres)}<br>'
                     f'<b>RSSI:</b> {p["rssi"]:.1f} dBm<br>'
                     f'<b>SNR:</b> {p["snr"]:.1f} dB<br>'
                     f'<b>Saltos restantes:</b> {p["saltos"]}<br>'
                     f'<b>Hora UTC:</b> {p["fecha_utc"]}<br>'
                     f'<b>Satelites:</b> {p["sats"]}<br>'
                     f'<b>Repetido:</b> {"si" if p["repetido"] == "1" else "no"}')
            if p["saturado"]:
                texto = "<b>SATURADO</b> (transmisor muy cerca)<br>" + texto

            folium.CircleMarker(
                location=[p["lat"], p["lon"]],
                radius=7,
                color=colores[nid],
                weight=3,
                fill=True,
                fill_color=color_por_rssi(p["rssi"]),
                fill_opacity=0.85,
                popup=folium.Popup(texto, max_width=260),
            ).add_to(capa)

        capa.add_to(mapa)


def agregar_nodos_fijos(mapa, fijos):
    if not fijos:
        return
    capa = folium.FeatureGroup(name="Nodos instalados", show=True)
    for nid, n in fijos.items():
        texto = (f'<b>{n["nombre"]}</b><br>'
                 f'<b>ID:</b> {nid}<br>'
                 f'<b>Lugar:</b> {n["lugar"]}<br>'
                 f'<b>Coordenadas:</b> {n["lat"]:.6f}, {n["lon"]:.6f}')
        folium.Marker(
            location=[n["lat"], n["lon"]],
            icon=folium.Icon(color="red", icon="tower-broadcast", prefix="fa"),
            tooltip=folium.Tooltip(n["nombre"], permanent=True),
            popup=folium.Popup(texto, max_width=260),
        ).add_to(capa)
    capa.add_to(mapa)


def agregar_lineas(mapa, puntos, fijos, colores):
    capa = folium.FeatureGroup(name="Lineas hacia el nodo", show=False)
    hay_lineas = False
    for p in puntos:
        n = fijos.get(p["origen"])
        if n is None or p["repetido"] == "1":
            continue
        folium.PolyLine(
            locations=[[p["lat"], p["lon"]], [n["lat"], n["lon"]]],
            color=colores[p["origen"]],
            weight=1.5,
            opacity=0.7,
        ).add_to(capa)
        hay_lineas = True
    if hay_lineas:
        capa.add_to(mapa)


def fila_panel(nid, puntos, nombres, colores):
    propios = [p for p in puntos if p["origen"] == nid]
    validos = [p["rssi"] for p in propios if not p["saturado"]]

    if not propios:
        rango = '<span style="color:#888;">sin paquetes en este escaneo</span>'
    elif validos:
        rango = f"{max(validos):.0f} a {min(validos):.0f} dBm"
    else:
        rango = "solo saturados"

    return (f'<tr><td><span style="color:{colores[nid]}; '
            f'text-shadow: 0 0 1px #000;">&#9632;</span> '
            f'{nombre_nodo(nid, nombres)}</td>'
            f'<td style="text-align:right; padding-left:10px;">{len(propios)}</td>'
            f'<td style="padding-left:10px;">{rango}</td></tr>')


def agregar_paneles(mapa, puntos, nombres, fijos, solo_mapa, colores, nombre_archivo):
    # El conteo general solo considera lo que aparece en el recuadro
    visibles = [p for p in puntos if p["origen"] not in solo_mapa]
    total = len(visibles)
    unicos = sum(1 for p in visibles if p["repetido"] == "0")
    saturados = sum(1 for p in visibles if p["saturado"])

    encabezado = ('<tr><th style="text-align:left;">{titulo}</th>'
                  '<th style="text-align:right; padding-left:10px;">Paq.</th>'
                  '<th style="text-align:left; padding-left:10px;">Mejor a peor RSSI</th></tr>')

    # Nodos instalados: siempre aparecen, aunque no se hayan escuchado en este escaneo
    filas = encabezado.format(titulo="Nodos instalados")
    for nid in sorted(fijos, key=lambda k: fijos[k]["nombre"]):
        if nid in solo_mapa:
            continue
        filas += fila_panel(nid, puntos, nombres, colores)

    # Otros nodos que el escaner escucho
    otros = sorted(set(p["origen"] for p in puntos) - set(fijos) - solo_mapa)
    if otros:
        filas += '<tr><td colspan="3" style="height:6px;"></td></tr>'
        filas += encabezado.format(titulo="Otros nodos detectados")
        for nid in otros:
            filas += fila_panel(nid, puntos, nombres, colores)

    resumen = f"""
    <div style="position: fixed; top: 15px; left: 60px; z-index: 9999;
                background: white; padding: 10px 14px; border: 1px solid #999;
                font-size: 13px; line-height: 1.5; max-width: 560px;">
      <b>Escaneo:</b> {nombre_archivo}<br>
      <b>Paquetes con GPS:</b> {total} ({unicos} unicos, {saturados} saturados)<br>
      <table style="margin-top:6px; font-size:13px;">
        {filas}
      </table>
    </div>
    """

    leyenda = f"""
    <div style="position: fixed; bottom: 30px; left: 15px; z-index: 9999;
                background: white; padding: 10px; border: 1px solid #999;
                font-size: 13px; line-height: 1.6;">
      <b>Relleno = RSSI (dBm)</b><br>
      <span style="color:#2ecc71;">&#9679;</span> mayor a -90<br>
      <span style="color:#f1c40f;">&#9679;</span> -90 a -105<br>
      <span style="color:#e67e22;">&#9679;</span> -105 a -115<br>
      <span style="color:#e74c3c;">&#9679;</span> menor a -115<br>
      <b>Borde</b> = color del nodo<br>
      Mayor a {RSSI_SATURADO} dBm = saturado
    </div>
    """

    mapa.get_root().html.add_child(folium.Element(resumen))
    mapa.get_root().html.add_child(folium.Element(leyenda))


def main():
    ruta = sys.argv[1] if len(sys.argv) > 1 else archivo_mas_reciente()
    if not ruta or not os.path.exists(ruta):
        print("No se encontro ningun archivo de escaneo en la carpeta datos.")
        sys.exit(1)

    nombres, fijos, ocultos, solo_mapa = leer_nodos()

    puntos = [p for p in leer_puntos(ruta) if p["origen"] not in ocultos]
    if not puntos and not fijos:
        print(f"{ruta} no tiene paquetes con posicion GPS para mostrar.")
        sys.exit(1)

    colores = asignar_colores(puntos, fijos)

    mapa = crear_mapa(puntos, fijos)
    agregar_mediciones(mapa, puntos, nombres, colores)
    agregar_nodos_fijos(mapa, fijos)
    agregar_lineas(mapa, puntos, fijos, colores)
    agregar_paneles(mapa, puntos, nombres, fijos, solo_mapa, colores,
                    os.path.basename(ruta))
    folium.LayerControl(collapsed=False).add_to(mapa)

    salida = ruta.replace(".csv", "_mapa.html")
    mapa.save(salida)
    print(f"Mapa generado: {salida} ({len(puntos)} puntos, "
          f"{len(fijos)} nodos instalados)")
    webbrowser.open(Path(salida).resolve().as_uri())


if __name__ == "__main__":
    main()