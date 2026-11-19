"""
1.52: paradas del Consorcio (GTFS público del CRTM) de la zona de nuestras rutas -> app/src/main/assets/paradas.json
Sirve para colocar en el mapa los avisos de cortes de Avanza, que dan los CÓDIGOS de las paradas afectadas
("9253 ALFONSO SENRA – PLAZA MAYOR").

    python tools/build_paradas.py

Formato: {"9253": [lat, lon, "ALFONSO SENRA-PZA.MAYOR"], ...}  (código sin ceros a la izquierda)
"""
import csv
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GTFS = os.path.join(os.environ.get('GTFS_DIR') or os.path.join(ROOT, 'tools', 'gtfs', 'x'), 'stops.txt')
OUT = os.environ.get('OUT_JSON') or os.path.join(ROOT, 'app', 'src', 'main', 'assets', 'paradas.json')
RUTAS = os.environ.get('RUTAS_DIR') or os.path.join(ROOT, 'app', 'src', 'main', 'assets', 'rutas')

# Zona de las rutas (con margen de ~3 km): tools/zona_paradas.json (o, si no está, los GPX)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lineas_comun import zona_paradas  # noqa: E402
s, w, n, e = zona_paradas(RUTAS)

out = {}
with open(GTFS, encoding='utf-8-sig') as fh:
    for r in csv.DictReader(fh):
        try:
            lat, lon = float(r['stop_lat']), float(r['stop_lon'])
        except ValueError:
            continue
        if not (s <= lat <= n and w <= lon <= e):
            continue
        code = r['stop_code'].strip().lstrip('0')
        if not code.isdigit():
            continue
        out[code] = [round(lat, 6), round(lon, 6), r['stop_name'].strip()]

with open(OUT, 'w', encoding='utf-8') as fh:
    json.dump(out, fh, ensure_ascii=False, separators=(',', ':'))
print(f'paradas: {len(out)} -> {OUT} ({os.path.getsize(OUT) // 1024} KB)')
