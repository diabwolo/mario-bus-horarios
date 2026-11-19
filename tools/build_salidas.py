"""Genera app/src/main/assets/salidas.json con las salidas (primera parada de cada expedición)
de las líneas de las rutas del conductor, a partir del GTFS de autobuses interurbanos del CRTM.

Uso: python tools/build_salidas.py   (GTFS descomprimido en tools/gtfs/x)
Fuente: https://datos.crtm.es/datasets/885399f83408473c8d815e40c5e702b7
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
HERE = os.path.dirname(os.path.abspath(__file__))
G = os.environ.get("GTFS_DIR") or os.path.join(HERE, "gtfs", "x")  # carpeta con el GTFS descomprimido
ASSETS = os.path.join(HERE, "..", "app", "src", "main", "assets")
RUTAS = os.environ.get("RUTAS_DIR") or os.path.join(ASSETS, "rutas")  # GPX de las rutas del conductor
OUT = os.environ.get("OUT_JSON") or os.path.join(ASSETS, "salidas.json")  # el actualizador lo cambia


def rd(name):
    return csv.DictReader(open(os.path.join(G, name), encoding="utf-8-sig"))


def main():
    # Líneas de las rutas del conductor ("684 101.gpx" → 684)
    lines = lineas_app(RUTAS)  # tools/lineas_app.txt (o, si no está, los GPX)
    routes = {}
    for r in rd("routes.txt"):
        m = re.fullmatch(r"(\d{3})[A-Z]?", r["route_short_name"])
        if m and m.group(1) in lines:
            routes[r["route_id"]] = r["route_short_name"]
    trips = {t["trip_id"]: t for t in rd("trips.txt") if t["route_id"] in routes}

    # Primera parada de cada expedición = salida desde la cabecera
    first = {}
    for st in rd("stop_times.txt"):
        tid = st["trip_id"]
        if tid not in trips:
            continue
        seq = int(st["stop_sequence"])
        if tid not in first or seq < first[tid][0]:
            first[tid] = (seq, st["stop_id"], st["departure_time"][:5])

    used_stops = {v[1] for v in first.values()}
    stops = {}
    for s in rd("stops.txt"):
        if s["stop_id"] in used_stops:
            stops[s["stop_id"]] = [s["stop_name"].strip(), round(float(s["stop_lat"]), 6), round(float(s["stop_lon"]), 6)]

    used_services = {trips[t]["service_id"] for t in first}
    services = {}
    for c in rd("calendar.txt"):
        if c["service_id"] in used_services:
            mask = "".join(c[d] for d in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])
            services[c["service_id"]] = {"days": mask, "from": c["start_date"], "to": c["end_date"], "add": [], "del": []}
    for c in rd("calendar_dates.txt"):
        sid = c["service_id"]
        if sid not in used_services:
            continue
        s = services.setdefault(sid, {"days": "0000000", "from": "20000101", "to": "20991231", "add": [], "del": []})
        (s["add"] if c["exception_type"] == "1" else s["del"]).append(c["date"])

    sids = sorted(services)
    sidx = {s: i for i, s in enumerate(sids)}
    deps = []
    for tid, (_, stop, time) in first.items():
        t = trips[tid]
        if t["service_id"] not in sidx:
            continue
        # "684 CERCEDILLA" → "CERCEDILLA" (destino); si no hay, la última parada
        dest = re.sub(r"^\s*\S+\s+", "", t.get("trip_short_name", "")).strip() or t.get("trip_headsign", "").strip()
        deps.append([stop, routes[t["route_id"]], dest, time, sidx[t["service_id"]]])
    deps.sort(key=lambda d: (d[0], d[3]))

    info = next(rd("feed_info.txt"), {})
    out = {"version": info.get("feed_version", ""), "stops": stops,
           "services": [services[s] for s in sids], "deps": deps}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print("líneas:", sorted(set(routes.values())), "\nexpediciones:", len(deps), "· cabeceras:", len(stops),
          "· calendarios:", len(sids), "· tamaño:", os.path.getsize(OUT) // 1024, "KB")


if __name__ == "__main__":
    main()
