"""Validación profunda de un GTFS (carpeta con los .txt) antes de dejarlo entrar en el sistema de actualizaciones.

Comprueba, además de que existan los archivos:
  * calendarios y fechas: formato AAAAMMDD real, inicio <= fin, marcas 0/1, excepciones de tipo 1/2;
  * referencias: trips→routes y trips→servicio, stop_times→trips y stop_times→stops;
  * expediciones: al menos 2 paradas, stop_sequence creciente, horas HH:MM:SS válidas y que no retroceden;
  * paradas: coordenadas numéricas dentro de un rectángulo razonable (centro de España);
  * SERVICIO EFECTIVO por línea y día en una ventana de fechas DINÁMICA (desde hoy, 28 días): cuántas expediciones
    circulan de verdad cada día de cada línea, calculado con calendar + calendar_dates (no solo «hay calendario»);
  * comparación con el feed anterior: líneas que tenían servicio y ya no lo tienen, y días enteros sin ningún servicio.
Devuelve (errores, avisos, estadísticas). Un error impide aceptar el feed; un aviso queda escrito en el manifiesto.

Uso: python tools/validar_gtfs.py CARPETA_GTFS [CARPETA_ANTERIOR] [--hoy AAAA-MM-DD] [--dias N]
"""
import csv
import datetime
import os
import re
import sys

csv.field_size_limit(10**8)
sys.stdout.reconfigure(encoding="utf-8")

REQUERIDOS = {
    "feed_info.txt": ["feed_version"],
    "routes.txt": ["route_id", "route_short_name"],
    "trips.txt": ["route_id", "service_id", "trip_id"],
    "stop_times.txt": ["trip_id", "stop_id", "stop_sequence", "departure_time"],
    "calendar.txt": ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
    "calendar_dates.txt": ["service_id", "date", "exception_type"],
    "stops.txt": ["stop_id", "stop_lat", "stop_lon"],
    "agency.txt": ["agency_name"],
}
DIAS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
RE_FECHA = re.compile(r"^\d{8}$")
RE_HORA = re.compile(r"^(\d{1,2}):([0-5]\d):([0-5]\d)$")
# Rectángulo amplio alrededor de la Comunidad de Madrid y provincias vecinas
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 39.0, 42.0, -6.0, -2.0
MAX_RATIO_HUERFANOS = 0.001        # más del 0,1 % de referencias rotas = feed dañado
MAX_RATIO_VIAJES_MALOS = 0.01      # más del 1 % de expediciones con paradas/horas mal = feed dañado
MAX_RATIO_LINEAS_PERDIDAS = 0.25   # más de un 25 % de las líneas con servicio antes, sin servicio ahora = feed sospechoso
DIAS_SIN_SERVICIO_MAX = 0          # ningún día de la ventana puede quedarse sin NINGUNA expedición en toda la red


def fecha_valida(s):
    if not RE_FECHA.match(s or ""):
        return None
    try:
        return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    except ValueError:
        return None


def ventana(hoy=None, dias=28):
    hoy = hoy or datetime.date.today()
    return [hoy + datetime.timedelta(days=i) for i in range(dias)]


def _leer(g, nombre):
    with open(os.path.join(g, nombre), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _cabecera(g, nombre):
    with open(os.path.join(g, nombre), encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f), [])


def servicios_activos_por_dia(calendario, excepciones, dias):
    """{fecha: set(service_id)} con calendar + calendar_dates aplicados."""
    out = {}
    for d in dias:
        ymd, wd = d.strftime("%Y%m%d"), DIAS[d.weekday()]
        ok = {c["service_id"] for c in calendario if c["start_date"] <= ymd <= c["end_date"] and c.get(wd) == "1"}
        for e in excepciones:
            if e["date"] == ymd:
                (ok.add if e["exception_type"] == "1" else ok.discard)(e["service_id"])
        out[d] = ok
    return out


def servicio_por_linea_y_dia(g, dias):
    """{línea (route_short_name): {fecha: nº de expediciones que circulan ese día}} — SERVICIO EFECTIVO."""
    rutas = {r["route_id"]: r["route_short_name"] for r in _leer(g, "routes.txt")}
    cal, exc = _leer(g, "calendar.txt"), _leer(g, "calendar_dates.txt")
    activos = servicios_activos_por_dia(cal, exc, dias)
    por_servicio = {}
    for t in _leer(g, "trips.txt"):
        ln = rutas.get(t["route_id"])
        if ln is not None:
            por_servicio.setdefault((ln, t["service_id"]), 0)
            por_servicio[(ln, t["service_id"])] += 1
    out = {ln: {d: 0 for d in dias} for ln in set(rutas.values())}
    for (ln, sid), n in por_servicio.items():
        for d in dias:
            if sid in activos[d]:
                out[ln][d] += n
    return out


def validar_gtfs(g, anterior=None, hoy=None, dias=28, lineas_clave=(), paradas_clave=()):
    """Devuelve (errores, avisos, stats)."""
    errores, avisos, stats = [], [], {}
    ventana_dias = ventana(hoy, dias)
    stats["ventana"] = [ventana_dias[0].isoformat(), ventana_dias[-1].isoformat()]

    # --- 1) archivos y columnas ---
    for nombre, cols in REQUERIDOS.items():
        ruta = os.path.join(g, nombre)
        if not os.path.exists(ruta):
            errores.append(f"Falta {nombre}")
            continue
        cab = _cabecera(g, nombre)
        falta = [c for c in cols if c not in cab]
        if falta:
            errores.append(f"{nombre}: faltan las columnas {', '.join(falta)}")
    if errores:
        return errores, avisos, stats

    # --- 2) calendarios y fechas ---
    cal, exc = _leer(g, "calendar.txt"), _leer(g, "calendar_dates.txt")
    if not cal:
        errores.append("calendar.txt no trae ningún calendario")
    malos = 0
    for c in cal:
        a, b = fecha_valida(c["start_date"]), fecha_valida(c["end_date"])
        marcas = all(c[d] in ("0", "1") for d in DIAS)
        if a is None or b is None or a > b or not marcas:
            malos += 1
    if malos:
        errores.append(f"calendar.txt: {malos} calendarios con fechas inválidas, inicio posterior al fin o marcas distintas de 0/1")
    malas = sum(1 for e in exc if fecha_valida(e["date"]) is None or e["exception_type"] not in ("1", "2"))
    if malas:
        errores.append(f"calendar_dates.txt: {malas} excepciones con fecha inválida o tipo distinto de 1/2")
    servicios = {c["service_id"] for c in cal} | {e["service_id"] for e in exc if e["exception_type"] == "1"}
    sin_calendario = {e["service_id"] for e in exc} - servicios
    if sin_calendario:
        avisos.append(f"calendar_dates.txt: {len(sin_calendario)} servicios solo con excepciones de tipo 2 y sin calendario")
    fin = max((c["end_date"] for c in cal), default="")
    stats["calendario_fin"] = fin
    stats["excepciones_hasta"] = max((e["date"] for e in exc), default="")

    # --- 3) rutas, expediciones, paradas ---
    rutas = {r["route_id"]: r["route_short_name"] for r in _leer(g, "routes.txt")}
    stops = _leer(g, "stops.txt")
    ids_paradas = {s["stop_id"] for s in stops}
    malas_coord = 0
    for s in stops:
        try:
            lat, lon = float(s["stop_lat"]), float(s["stop_lon"])
            if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
                malas_coord += 1
        except ValueError:
            malas_coord += 1
    if malas_coord / max(1, len(stops)) > MAX_RATIO_HUERFANOS:
        errores.append(f"stops.txt: {malas_coord} de {len(stops)} paradas con coordenadas fuera de rango o no numéricas")
    elif malas_coord:
        avisos.append(f"stops.txt: {malas_coord} paradas con coordenadas raras")
    trips = _leer(g, "trips.txt")
    stats["expediciones"] = len(trips)
    ids_trip = {t["trip_id"] for t in trips}
    if len(ids_trip) != len(trips):
        errores.append(f"trips.txt: {len(trips) - len(ids_trip)} identificadores de expedición repetidos")
    sin_ruta = sum(1 for t in trips if t["route_id"] not in rutas)
    sin_serv = sum(1 for t in trips if t["service_id"] not in servicios)
    for n, txt in ((sin_ruta, "con una ruta que no existe"), (sin_serv, "con un servicio sin calendario")):
        if n / max(1, len(trips)) > MAX_RATIO_HUERFANOS:
            errores.append(f"trips.txt: {n} expediciones {txt}")
        elif n:
            avisos.append(f"trips.txt: {n} expediciones {txt}")

    # --- 4) stop_times (una sola pasada): paradas, orden, horas ---
    huerf_trip = huerf_stop = 0
    cuenta, ultimo = {}, {}
    mal_orden = mala_hora = 0
    filas = 0
    with open(os.path.join(g, "stop_times.txt"), encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        cab = next(rd)
        i_t, i_s, i_q, i_d = (cab.index(c) for c in ("trip_id", "stop_id", "stop_sequence", "departure_time"))
        i_a = cab.index("arrival_time") if "arrival_time" in cab else None
        for fila in rd:
            filas += 1
            t = fila[i_t]
            if t not in ids_trip:
                huerf_trip += 1
                continue
            if fila[i_s] not in ids_paradas:
                huerf_stop += 1
            hora = fila[i_d] or (fila[i_a] if i_a is not None else "")
            m = RE_HORA.match(hora)
            minutos = None
            if not m:
                mala_hora += 1
            else:
                minutos = int(m.group(1)) * 60 + int(m.group(2))
            try:
                q = int(fila[i_q])
            except ValueError:
                mal_orden += 1
                continue
            prev = ultimo.get(t)
            if prev is not None:
                if q <= prev[0] or (minutos is not None and prev[1] is not None and minutos < prev[1]):
                    mal_orden += 1
            ultimo[t] = (q, minutos if minutos is not None else (prev[1] if prev else None))
            cuenta[t] = cuenta.get(t, 0) + 1
    stats["filas_stop_times"] = filas
    for n, txt in ((huerf_trip, "de expediciones que no existen"), (huerf_stop, "de paradas que no existen")):
        if n / max(1, filas) > MAX_RATIO_HUERFANOS:
            errores.append(f"stop_times.txt: {n} filas {txt}")
        elif n:
            avisos.append(f"stop_times.txt: {n} filas {txt}")
    if mala_hora / max(1, filas) > MAX_RATIO_HUERFANOS:
        errores.append(f"stop_times.txt: {mala_hora} horas con formato inválido")
    elif mala_hora:
        avisos.append(f"stop_times.txt: {mala_hora} horas con formato inválido")
    if mal_orden / max(1, filas) > MAX_RATIO_HUERFANOS:
        errores.append(f"stop_times.txt: {mal_orden} paradas desordenadas (secuencia o hora que retrocede)")
    elif mal_orden:
        avisos.append(f"stop_times.txt: {mal_orden} paradas desordenadas (secuencia o hora que retrocede)")
    cortas = sum(1 for t in ids_trip if cuenta.get(t, 0) < 2)
    if cortas / max(1, len(ids_trip)) > MAX_RATIO_VIAJES_MALOS:
        errores.append(f"{cortas} de {len(ids_trip)} expediciones con menos de 2 paradas")
    elif cortas:
        avisos.append(f"{cortas} expediciones con menos de 2 paradas")

    # --- 5) servicio efectivo por línea y día ---
    efectivo = servicio_por_linea_y_dia(g, ventana_dias)
    stats["lineas_total"] = len(efectivo)
    sin_servicio = sorted(ln for ln, d in efectivo.items() if sum(d.values()) == 0)
    stats["lineas_sin_servicio_en_ventana"] = sin_servicio
    for d in ventana_dias[:14]:
        if sum(v[d] for v in efectivo.values()) <= DIAS_SIN_SERVICIO_MAX:
            errores.append(f"El día {d.isoformat()} no circula ninguna expedición en toda la red")
    if fin and fin < (ventana_dias[0] + datetime.timedelta(days=14)).strftime("%Y%m%d"):
        errores.append(f"Los calendarios acaban el {fin}: menos de 14 días por delante")
    claves_falta = [ln for ln in lineas_clave if ln not in efectivo]
    if claves_falta:
        errores.append(f"Líneas imprescindibles que no existen en el feed: {', '.join(sorted(claves_falta))}")
    claves_sin = [ln for ln in lineas_clave if ln in efectivo and sum(efectivo[ln].values()) == 0]
    if claves_sin:
        avisos.append(f"Líneas imprescindibles sin NINGÚN servicio en la ventana: {', '.join(sorted(claves_sin))}")
    para_falta = [p for p in paradas_clave if p not in ids_paradas]
    if para_falta:
        errores.append(f"Paradas imprescindibles que no existen: {', '.join(para_falta)}")

    # --- 6) frente al feed anterior ---
    if anterior and os.path.isdir(anterior):
        ant = servicio_por_linea_y_dia(anterior, ventana_dias)
        activas_antes = {ln for ln, d in ant.items() if sum(d.values()) > 0}
        perdidas = sorted(ln for ln in activas_antes if sum(efectivo.get(ln, {}).values()) == 0)
        stats["lineas_perdidas_frente_al_anterior"] = perdidas
        if perdidas:
            avisos.append(f"{len(perdidas)} líneas con servicio en el feed anterior no lo tienen en este: {', '.join(perdidas[:30])}")
        if activas_antes and len(perdidas) / len(activas_antes) > MAX_RATIO_LINEAS_PERDIDAS:
            errores.append(f"Se pierden {len(perdidas)} de {len(activas_antes)} líneas con servicio: feed sospechoso")
    return errores, avisos, stats


NOMBRES_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def resumen_servicio(g, hoy=None, dias=28):
    """Huella compacta del servicio de un feed, para compararlo con el siguiente SIN guardar el feed entero:
    {"version", "ventana", "lineas": {línea: [máximo de expediciones en lunes, martes, … domingo]}} (solo líneas con servicio)."""
    v = ventana(hoy, dias)
    lineas = {}
    for ln, por_dia in servicio_por_linea_y_dia(g, v).items():
        w = [0] * 7
        for d, n in por_dia.items():
            w[d.weekday()] = max(w[d.weekday()], n)
        if any(w):
            lineas[ln] = w
    info = _leer(g, "feed_info.txt")
    return {"version": info[0]["feed_version"] if info else "", "ventana": [v[0].isoformat(), v[-1].isoformat()], "lineas": lineas}


def comparar_con_base(nuevo, base, explicadas=None, caida_max=0.5):
    """Qué había en la última versión buena y ya no está en la nueva: líneas que desaparecen, días de la semana sin servicio y
    caídas de expediciones superiores a `caida_max`. `explicadas` = {línea: motivo} aprobadas a propósito (p. ej. línea suprimida)."""
    explicadas = explicadas or {}
    problemas = []
    for ln, wb in sorted(base["lineas"].items()):
        if ln in explicadas:
            continue
        wn = nuevo["lineas"].get(ln)
        if wn is None:
            problemas.append(f"{ln}: la línea desaparece (antes tenía servicio)")
            continue
        sin = [NOMBRES_DIAS[i] for i in range(7) if wb[i] > 0 and wn[i] == 0]
        if sin:
            problemas.append(f"{ln}: sin servicio los {', '.join(sin)} (antes sí)")
            continue
        cae = [NOMBRES_DIAS[i] for i in range(7) if wb[i] > 0 and wn[i] < wb[i] * (1 - caida_max)]
        if cae:
            problemas.append(f"{ln}: caen más de un {int(caida_max * 100)} % las expediciones los {', '.join(cae)}")
    return problemas


def main(argv):
    pos = [a for a in argv if not a.startswith("--")]
    hoy = datetime.date.fromisoformat(argv[argv.index("--hoy") + 1]) if "--hoy" in argv else None
    dias = int(argv[argv.index("--dias") + 1]) if "--dias" in argv else 28
    pos = [a for a in pos if a not in (argv[argv.index("--hoy") + 1] if "--hoy" in argv else "", argv[argv.index("--dias") + 1] if "--dias" in argv else "")]
    if not pos:
        print(__doc__)
        return 2
    e, a, s = validar_gtfs(pos[0], pos[1] if len(pos) > 1 else None, hoy, dias)
    print("ESTADÍSTICAS:", s)
    for x in a:
        print("AVISO:", x)
    for x in e:
        print("ERROR:", x)
    print("RESULTADO:", "RECHAZADO" if e else "VÁLIDO")
    return 1 if e else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
