"""Genera app/src/main/assets/horarios.json: la hora de paso por cada parada de cada expedición de las
líneas de la app (GTFS de autobuses interurbanos del CRTM), para el aviso de adelanto/retraso.

Para ocupar poco se guardan "patrones": misma línea, mismas paradas y mismos minutos desde la salida;
cada patrón lleva la lista de horas de salida que lo usan.

Uso: python tools/build_horarios.py   (GTFS descomprimido en tools/gtfs/x, ver build_salidas.py)
"""
import csv
import glob
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lineas_comun import lineas_app  # noqa: E402
csv.field_size_limit(10**8)
HERE = os.path.dirname(os.path.abspath(__file__))
G = os.environ.get("GTFS_DIR") or os.path.join(HERE, "gtfs", "x")  # carpeta con el GTFS descomprimido
ASSETS = os.path.join(HERE, "..", "app", "src", "main", "assets")
RUTAS = os.environ.get("RUTAS_DIR") or os.path.join(ASSETS, "rutas")  # GPX de las rutas del conductor
OUT = os.environ.get("OUT_JSON") or os.path.join(ASSETS, "horarios.json")  # el actualizador lo cambia


def rd(name):
    return csv.DictReader(open(os.path.join(G, name), encoding="utf-8-sig"))


def mins(t):
    h, m, s = (int(x) for x in t.split(":"))
    return h * 60 + m + s / 60


def main():
    lines = lineas_app(RUTAS)  # tools/lineas_app.txt (o, si no está, los GPX)
    routes = {}
    for r in rd("routes.txt"):
        m = re.fullmatch(r"(\d{3})[A-Z]?", r["route_short_name"])
        if m and m.group(1) in lines:
            routes[r["route_id"]] = m.group(1)
    trips = {t["trip_id"]: routes[t["route_id"]] for t in rd("trips.txt") if t["route_id"] in routes}

    times = {}
    for st in rd("stop_times.txt"):
        tid = st["trip_id"]
        if tid in trips:
            t = st["arrival_time"] or st["departure_time"]
            if t:
                times.setdefault(tid, []).append((int(st["stop_sequence"]), st["stop_id"], mins(t)))

    patterns = {}
    used = set()
    for tid, lst in times.items():
        lst.sort()
        if len(lst) < 2:
            continue
        start = lst[0][2]
        stops = tuple(s for _, s, _ in lst)
        off = tuple(round(m - start) for _, _, m in lst)
        key = (trips[tid], stops, off)
        hhmm = "%02d:%02d" % (int(start) // 60, int(start) % 60)
        patterns.setdefault(key, set()).add(hhmm)
        used.update(stops)

    stops = {}
    for s in rd("stops.txt"):
        if s["stop_id"] in used:
            stops[s["stop_id"]] = [round(float(s["stop_lat"]), 6), round(float(s["stop_lon"]), 6)]

    out = {
        "stops": stops,
        "patterns": [{"line": l, "stops": list(st), "off": list(o), "starts": sorted(v)}
                     for (l, st, o), v in sorted(patterns.items())],
    }
    path = OUT
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(out['patterns'])} patrones, {len(stops)} paradas, {os.path.getsize(path) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
