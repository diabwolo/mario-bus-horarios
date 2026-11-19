"""Qué líneas y qué zona usan los generadores de salidas.json, horarios.json y paradas.json.

En el ordenador de Mario salen de las rutas GPX (app/src/main/assets/rutas). En el servidor de la nube no hay GPX (son rutas
personales y no se publican): se usan los dos archivos pequeños que se sacaron de ellas, `lineas_app.txt` y `zona_paradas.json`.
Si existen, mandan ellos (así el resultado es el mismo en cualquier sitio); si no, se leen los GPX como siempre.
"""
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))


def lineas_app(rutas_dir):
    f = os.environ.get("LINEAS_APP") or os.path.join(HERE, "lineas_app.txt")
    if os.path.isfile(f):
        with open(f, encoding="utf-8") as fh:
            return {l.strip() for l in fh if l.strip() and not l.startswith("#")}
    return {m.group(1) for g in glob.glob(os.path.join(rutas_dir, "*.gpx")) for m in [re.match(r"(\d{3}) ", os.path.basename(g))] if m}


def zona_paradas(rutas_dir):
    """(sur, oeste, norte, este) con margen."""
    f = os.environ.get("ZONA_PARADAS") or os.path.join(HERE, "zona_paradas.json")
    if os.path.isfile(f):
        with open(f, encoding="utf-8") as fh:
            z = json.load(fh)
        return z["s"], z["w"], z["n"], z["e"]
    s, w, n, e = 90.0, 180.0, -90.0, -180.0
    for g in glob.glob(os.path.join(rutas_dir, "*.gpx")):
        with open(g, encoding="utf-8") as fh:
            for lat, lon in re.findall(r'lat="([-\d.]+)"\s+lon="([-\d.]+)"', fh.read()):
                lat, lon = float(lat), float(lon)
                s, n, w, e = min(s, lat), max(n, lat), min(w, lon), max(e, lon)
    return s - 0.03, w - 0.04, n + 0.03, e + 0.04
