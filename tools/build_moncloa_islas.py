"""Genera app/src/main/assets/moncloa_islas.json con las salidas de TODAS las líneas
interurbanas de las islas 1, 2 y 3 del Intercambiador de Moncloa, y la dársena de cada línea.

Dársenas: plano oficial del Consorcio, https://www.crtm.es/media/hyhdlchd/moncloa.pdf (tablas «Andén» de las islas
1, 2 y 3). Horarios: GTFS de autobuses interurbanos del CRTM, expediciones que EMPIEZAN en la parada de la isla
(igual que «Próximas salidas»): Isla 2 e Isla 3 = par_8_17480 (Avda. del Arco de la Victoria), Isla 1 = par_8_06002
(Calle de la Princesa 89).

DOS FEEDS (18/11/2026): el GTFS nuevo del Consorcio (27/10/2026, «datos parciales») NO trae todos los días de algunas
líneas (p. ej. la sierra de Avanza solo tiene calendario de martes, sábado y domingo). Para cada línea se usa el feed
NUEVO solo si no le falta ningún día de la semana que el feed ANTIGUO (23/07/2026) sí trae; si no, se usa el antiguo y
el JSON deja constancia de la versión de cada línea (la app lo avisa en pantalla). Nunca se inventan horarios.

Uso: python tools/build_moncloa_islas.py
  GTFS_DIR=<carpeta del GTFS nuevo> (por defecto tools/gtfs/m89_20261027)
  GTFS_DIR_ANTIGUO=<carpeta del GTFS antiguo> (por defecto tools/gtfs/x)
Comprueba que cada línea del plano tiene salidas en su parada y que ninguna línea de los GTFS en esas paradas se
queda sin dársena; si algo no cuadra, lo imprime y termina con código 1.
"""
import csv
import datetime
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
G_NUEVO = os.environ.get("GTFS_DIR") or os.path.join(HERE, "gtfs", "m89_20261027")
G_ANTIGUO = os.environ.get("GTFS_DIR_ANTIGUO") or os.path.join(HERE, "gtfs", "x")
OUT = os.environ.get("OUT_JSON") or os.path.join(HERE, "..", "app", "src", "main", "assets", "moncloa_islas.json")

STOP_ISLA = {"1": "par_8_06002", "2": "par_8_17480", "3": "par_8_17480"}

# isla -> dársena -> [(línea, empresa)]   (plano oficial del CRTM)
AUT, JC, IRI = "Autoperiferia", "J. de Castro", "Iribus"
AVZ, BEL, LAR = "Avanza Movilidad", "A. Beltrán", "Fco. Larrea"
PLAN = {
    "1": {
        "1": [("632", JC)],
        "2": [("631", JC)],
        "3": [("621", AUT), ("624", AUT), ("629", AUT)],
        "4": [("622", AUT), ("624A", AUT), ("628", AUT)],
        "5": [("625", AUT)],
        "6": [("623", AUT), ("627", AUT)],
        "10": [("645", IRI), ("661A", IRI), ("662", IRI)],
        "11": [("661", IRI), ("664", IRI)],
    },
    "2": {
        "20": [("682", AVZ), ("684", AVZ), ("688", AVZ)],
        "21": [("681", AVZ), ("691", AVZ)],
        "22": [("683", AVZ), ("687", AVZ)],
        "25": [("672", LAR), ("672A", LAR), ("673", LAR)],
        "26": [("671", LAR)],
        "27": [("641", BEL), ("642", BEL), ("643", BEL)],
        "28": [("613", AVZ), ("686", AVZ), ("686A", AVZ)],
        "29": [("611", AVZ), ("611A", AVZ), ("612", AVZ)],
    },
    "3": {
        "31": [("656A", AVZ), ("657A", AVZ), ("658A", AVZ)],
        "35": [("656", AVZ)],
        "36": [("658", AVZ), ("659", AVZ)],
        "37": [("657", AVZ)],
        "38": [("653", AVZ), ("654", AVZ), ("655", AVZ)],
        "39": [("651", AVZ), ("652", AVZ)],
    },
}
# Dársenas sin horario en el GTFS del Consorcio (no son interurbanas del CRTM): isla -> dársena -> texto
SIN_HORARIO = {
    "1": {
        "1": "Línea 635 (Torrelodones - La Navata - Galapagar, J. de Castro): está en el plano, pero el Consorcio no publica expediciones de ella",
        "7": "Alsa: Asturias, León, Valladolid y Palencia (largo recorrido; sin horario del Consorcio)",
        "8": "Avanza Movilidad: Segovia y Salamanca (largo recorrido; sin horario del Consorcio)",
        "9": "Avanza Movilidad: Segovia y Salamanca (largo recorrido; sin horario del Consorcio)",
        "14": "Avanza Movilidad: Salamanca y Segovia (largo recorrido; sin horario del Consorcio)",
    },
    "3": {
        "30": "EMT línea 46 (Puerta del Sol - Sevilla - Moncloa; urbana, sin horario del Consorcio interurbano)",
        "32": "EMT línea G (Moncloa - Ciudad Universitaria; urbana, sin horario del Consorcio interurbano)",
    },
}

# Fiestas laborales de la Comunidad de Madrid (decretos oficiales; NO incluye las 2 fiestas locales de cada municipio):
# Decreto 75/2025, de 24 de septiembre (BOCM 25/09/2025) para 2026 y Decreto 82/2026, de 30 de septiembre (BOCM 01/10/2026) para 2027.
# Solo las que caen dentro del periodo del feed nuevo (27/10/2026 - 28/11/2027).
FESTIVOS_MADRID = {
    "20261102": "traslado de Todos los Santos",
    "20261207": "traslado del Día de la Constitución",
    "20261208": "Inmaculada Concepción",
    "20261225": "Natividad del Señor",
    "20270101": "Año Nuevo",
    "20270106": "Epifanía del Señor",
    "20270319": "San José",
    "20270325": "Jueves Santo",
    "20270326": "Viernes Santo",
    "20270501": "Fiesta del Trabajo",
    "20270816": "traslado de la Asunción",
    "20271012": "Fiesta Nacional de España",
    "20271101": "Todos los Santos",
}

DIAS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# Ventana de fechas para comprobar que un feed trae TODOS los días de una línea. DINÁMICA: desde la fecha de referencia
# (FECHA_REF=AAAA-MM-DD, por defecto HOY) y durante VENTANA_DIAS días (por defecto 28: cuatro semanas completas).
def ventana(hoy=None, dias=None):
    if hoy is None:
        ref = os.environ.get("FECHA_REF")
        hoy = datetime.date.fromisoformat(ref) if ref else datetime.date.today()
    if dias is None:
        dias = int(os.environ.get("VENTANA_DIAS") or 28)
    return [hoy + datetime.timedelta(days=i) for i in range(dias)]


SEMANA_REF = ventana()


def rd(g, name):
    return csv.DictReader(open(os.path.join(g, name), encoding="utf-8-sig"))


class Feed:
    """Un GTFS del CRTM: expediciones que EMPIEZAN en las paradas de las islas, de las líneas del plano."""

    def __init__(self, g):
        self.g = g
        self.version = next(rd(g, "feed_info.txt"), {}).get("feed_version", "")
        paradas = set(STOP_ISLA.values())
        lineas_plano = {ln for ds in PLAN.values() for l in ds.values() for ln, _ in l}
        routes = {r["route_id"]: r["route_short_name"] for r in rd(g, "routes.txt")}
        trips = {t["trip_id"]: t for t in rd(g, "trips.txt")}
        first, hit = {}, {}
        for st in rd(g, "stop_times.txt"):
            tid, seq = st["trip_id"], int(st["stop_sequence"])
            if tid not in first or seq < first[tid][0]:
                first[tid] = (seq, st["stop_id"], st["departure_time"][:5])
            if st["stop_id"] in paradas:
                hit[tid] = st["stop_id"]
        self.deps = []  # (parada, línea, destino, hora, service_id)
        for tid, sid in hit.items():
            if first[tid][1] != sid:
                continue
            ln = routes[trips[tid]["route_id"]]
            if ln not in lineas_plano:
                continue
            t = trips[tid]
            dest = re.sub(r"^\s*\S+\s+", "", t.get("trip_short_name", "")).strip() or t.get("trip_headsign", "").strip()
            self.deps.append((sid, ln, dest, first[tid][2], t["service_id"]))
        self.cal = {c["service_id"]: c for c in rd(g, "calendar.txt")}
        self.exc = [(c["service_id"], c["date"], c["exception_type"]) for c in rd(g, "calendar_dates.txt")]
        self.excepciones_hasta = max((d for _, d, _ in self.exc), default="")
        self._activos = {}

    def activos(self, dia):
        """service_id que circulan el día de servicio [dia] (datetime.date), con excepciones."""
        if dia in self._activos:
            return self._activos[dia]
        ymd = dia.strftime("%Y%m%d")
        wd = DIAS[dia.weekday()]
        ok = {sid for sid, c in self.cal.items() if c["start_date"] <= ymd <= c["end_date"] and c[wd] == "1"}
        for sid, d, ty in self.exc:
            if d == ymd:
                (ok.add if ty == "1" else ok.discard)(sid)
        self._activos[dia] = ok
        return ok

    def tiene(self, linea, dia):
        act = self.activos(dia)
        return any(d[1] == linea and d[4] in act for d in self.deps)


def main():
    nuevo = Feed(G_NUEVO)
    # Sin feed antiguo (modo estricto en la nube): no hay refuerzo posible; una línea sin servicio se detecta igual y el
    # actualizador rechaza el paquete en vez de publicarlo con datos antiguos
    antiguo = Feed(G_ANTIGUO) if os.path.isdir(G_ANTIGUO) else nuevo
    por_version = {nuevo.version: nuevo, antiguo.version: antiguo}
    lineas = sorted({ln for ds in PLAN.values() for l in ds.values() for ln, _ in l})

    # --- 1) qué líneas necesitan el feed ANTIGUO de refuerzo: las que al nuevo le faltan días que el antiguo sí trae.
    # Para esas líneas se guardan los dos y la app decide POR FECHA: si el nuevo trae servicio de esa línea ese día, usa
    # solo el nuevo; si no, usa el antiguo y lo marca en pantalla. Nunca se inventa nada. ---
    con_antiguo, motivos = [], {}
    for ln in lineas:
        faltan = [d for d in SEMANA_REF if antiguo.tiene(ln, d) and not nuevo.tiene(ln, d)]
        if not any(d[1] == ln for d in nuevo.deps):
            con_antiguo.append(ln)
            motivos[ln] = "el feed nuevo no trae ninguna expedición"
        elif faltan:
            con_antiguo.append(ln)
            motivos[ln] = "el feed nuevo no trae estos días: " + ", ".join(d.strftime("%a %d/%m") for d in faltan)

    # --- 2) comprobación cruzada plano <-> GTFS (en el feed elegido para cada línea) ---
    problems = []
    isla_de = {ln: i for i, ds in PLAN.items() for l in ds.values() for ln, _ in l}
    for isla, darsenas in PLAN.items():
        stop = STOP_ISLA[isla]
        for d, lines in darsenas.items():
            for ln, _ in lines:
                if not any(x[0] == stop and x[1] == ln for f in (nuevo, antiguo) for x in f.deps):
                    problems.append(f"Isla {isla} dársena {d}: la línea {ln} NO tiene salidas en {stop} en ningún GTFS")
    paradas = set(STOP_ISLA.values())
    planned = {s: {ln for i, ds in PLAN.items() if STOP_ISLA[i] == s for l in ds.values() for ln, _ in l} for s in paradas}
    for feed in (nuevo, antiguo):
        for s in paradas:
            for ln in sorted({x[1] for x in feed.deps if x[0] == s} - planned[s]):
                problems.append(f"El GTFS {feed.version} tiene la línea {ln} saliendo de {s} pero el plano no le da dársena")

    # --- 3) JSON: servicios y salidas de cada línea, del feed elegido ---
    servicios, sidx, deps = [], {}, []
    for ln in lineas:
        stop = STOP_ISLA[isla_de[ln]]
        # el feed nuevo siempre; el antiguo solo para las líneas a las que el nuevo no trae todos los días
        for feed in ([nuevo, antiguo] if ln in con_antiguo else [nuevo]):
            for (st, l, dest, hora, sid) in feed.deps:
                if l != ln or st != stop:
                    continue
                key = (feed.version, sid)
                if key not in sidx:
                    c = feed.cal.get(sid)
                    servicios.append({
                        "v": feed.version,
                        "days": "".join(c[d] for d in DIAS) if c else "0000000",
                        "from": c["start_date"] if c else "20000101", "to": c["end_date"] if c else "20991231",
                        "add": [d for s_, d, ty in feed.exc if s_ == sid and ty == "1"],
                        "del": [d for s_, d, ty in feed.exc if s_ == sid and ty != "1"],
                    })
                    sidx[key] = len(servicios) - 1
                deps.append([st, ln, dest, hora, sidx[key]])
    deps.sort(key=lambda d: (d[0], d[1], d[3], d[4]))

    stops = {}
    for s in rd(G_NUEVO, "stops.txt"):
        if s["stop_id"] in paradas:
            stops[s["stop_id"]] = [s["stop_name"].strip(), round(float(s["stop_lat"]), 6), round(float(s["stop_lon"]), 6)]
    islas = {i: {d: [{"linea": ln, "empresa": e} for ln, e in lines] for d, lines in ds.items()} for i, ds in PLAN.items()}
    vig = {}
    for feed in (nuevo, antiguo):
        usadas = [servicios[i] for (v, _), i in sidx.items() if v == feed.version]
        vig[feed.version] = max((s["to"] for s in usadas), default="")
    out = {
        "version": nuevo.version,
        "version_antigua": antiguo.version,
        # líneas a las que el feed nuevo no trae todos los días: se guardan también las salidas del antiguo, que la app usa
        # SOLO en las fechas en que el nuevo no trae servicio de esa línea (y lo marca en pantalla)
        "lineas_con_antiguo": con_antiguo,
        "lineas_motivo": motivos,
        # hasta qué día trae cada feed excepciones de calendario (festivos); desde el siguiente NO se sabe si es festivo
        "excepciones_hasta": nuevo.excepciones_hasta,
        "excepciones_hasta_por_version": {nuevo.version: nuevo.excepciones_hasta, antiguo.version: antiguo.excepciones_hasta},
        "vigencia_hasta": max(vig.values()),
        "vigencia_hasta_por_version": vig,
        "festivos": [{"fecha": d, "nombre": n} for d, n in sorted(FESTIVOS_MADRID.items())],
        "festivos_fuente": "Decretos 75/2025 y 82/2026 de la Comunidad de Madrid (BOCM 25/09/2025 y 01/10/2026); sin festivos locales",
        "fuente": "Plano oficial CRTM (crtm.es/media/hyhdlchd/moncloa.pdf) y GTFS interurbano del CRTM",
        "stop_isla": STOP_ISLA,
        "stops": stops,
        "services": servicios,
        "deps": deps,
        "islas": islas,
        "sin_horario": SIN_HORARIO,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))

    print("GTFS nuevo", nuevo.version, "· GTFS antiguo", antiguo.version, "· salidas:", len(deps), "· servicios:", len(servicios),
          "· tamaño:", os.path.getsize(OUT) // 1024, "KB")
    for isla in sorted(PLAN):
        for d in sorted(PLAN[isla], key=int):
            n = {ln: sum(1 for x in deps if x[1] == ln) for ln, _ in PLAN[isla][d]}
            print(f"  Isla {isla} dársena {d:>2}: " + ", ".join(f"{ln}({c}){'*' if ln in con_antiguo else ''}" for ln, c in n.items()))
    print("(*) = línea con refuerzo del feed ANTIGUO en los días que el nuevo no trae:")
    for ln in con_antiguo:
        print(f"   {ln}: {motivos[ln]}")
    for p in problems:
        print("PROBLEMA:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
