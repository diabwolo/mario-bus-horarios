"""Actualizador de datos del CRTM para Mario-Bus (independiente de la app: NO toca la app ni publica nada por sí solo).

Genera, a partir del GTFS oficial del CRTM, un PAQUETE FIRMADO con todos los datos de la app que salen del GTFS:
  salidas.json · horarios.json · moncloa_islas.json · paradas.json  (+ manifest.json + manifest.sig)
(ver tools/publicar_horarios/INVENTARIO_DATOS.md: qué pantalla usa cada archivo y por qué no se actualizan los demás).

Garantías (cada una tiene su prueba en tools/tests/):
  * Un paquete PARCIAL nunca se publica como completo: va a pendiente_revision/ y solo pasa a publicable/ con una orden
    manual (--aprobar-parcial VERSION --si-estoy-seguro); entonces el manifiesto sigue diciendo PARCIAL.
  * Se conserva SIEMPRE la última versión válida. publicable/ solo cambia al entrar un paquete COMPLETO, verificado y
    FIRMADO; el anterior pasa a historial/. Un rechazo, un error, un corte de red o un APAGÓN deja publicable/ válida:
    la instalación usa un diario (instalacion.json) y `recuperar()` la termina o la deshace al volver a arrancar.
  * Una descarga interrumpida o un archivo corrupto no sustituye nada (.part, tamaño, ZIP, validación del GTFS).
  * Manifiesto firmado (ECDSA P-256): la app solo acepta lo que firma la clave privada de Mario (tools/firma_manifiesto.py).
  * Validación profunda del GTFS (tools/validar_gtfs.py): calendarios, fechas, referencias, expediciones, paradas y
    servicio efectivo por línea y día en una ventana de fechas DINÁMICA (desde hoy, 28 días).

Carpetas (todas bajo tools/publicar_horarios/):
  publicable/            ÚLTIMA versión válida y COMPLETA (manifest.json, manifest.sig y los datos). Es lo único que se sube.
  pendiente_revision/V/  paquetes PARCIALES (líneas con datos antiguos): NO se suben sin revisión de Mario.
  historial/V/           versiones publicables anteriores.
  estado.json · instalacion.json (diario; solo existe durante un cambio)

Uso:
  python tools/actualizar_horarios.py                       # ciclo diario: mira el resumen de la fuente y SOLO si cambió descarga y procesa
  python tools/actualizar_horarios.py --forzar              # lo mismo sin respetar «una vez al día» ni «sin cambios»
  python tools/actualizar_horarios.py --solo-comprobar      # solo cabeceras de la fuente
  python tools/actualizar_horarios.py --zip RUTA.zip        # procesa un ZIP ya descargado
  python tools/actualizar_horarios.py --verificar-lineas [--zip RUTA.zip] [--hoy AAAA-MM-DD] [--dias N]
  python tools/actualizar_horarios.py --aprobar-parcial VERSION --si-estoy-seguro
  python tools/actualizar_horarios.py --verificar-publicable    # comprueba huellas y firma de publicable/
  python tools/actualizar_horarios.py --recuperar | --estado | --autotest
  opciones: --salida CARPETA  --claves CARPETA  --hoy AAAA-MM-DD  --dias N
Fuente: https://datos.crtm.es/datasets/885399f83408473c8d815e40c5e702b7  (licencia: https://crtm.es/licencia-de-uso).
"""
import csv
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import firma_manifiesto as FIRMA  # noqa: E402
import validar_gtfs as VAL  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
csv.field_size_limit(10**8)
HERE = os.path.dirname(os.path.abspath(__file__))
FUENTE = "https://crtm.maps.arcgis.com/sharing/rest/content/items/885399f83408473c8d815e40c5e702b7/data"
FUENTE_META = "https://crtm.maps.arcgis.com/sharing/rest/content/items/885399f83408473c8d815e40c5e702b7?f=json"  # resumen de 1 KB
PAGINA = "https://datos.crtm.es/datasets/885399f83408473c8d815e40c5e702b7"
LICENCIA = "https://crtm.es/licencia-de-uso"
ATRIBUCION = "Powered by CRTM · www.crtm.es (datos elaborados)"
TRABAJO = os.path.join(HERE, "gtfs", "actualizaciones")
SALIDA = os.path.join(HERE, "publicar_horarios")
BASE = os.path.join(HERE, "gtfs", "x")  # feed anterior de refuerzo (23/07/2026)
RUTAS = os.path.join(HERE, "..", "app", "src", "main", "assets", "rutas")
ESQUEMA = 2
CLAVES = {"feed_info": "feed_version", "routes": "route_id", "trips": "trip_id", "stop_times": "stop_id",
          "calendar": "service_id", "calendar_dates": "service_id", "stops": "stop_id", "agency": "agency_name"}
CAIDA_MAX_EXPEDICIONES = 0.30
MAX_LINEAS_PARCIALES = 0.75  # si a más del 75 % de las líneas del plano les faltan días, el feed está demasiado roto
DIAS_VENTANA = 28
# Datos que la app puede recibir por este canal (la app tiene la MISMA lista: DatosActualizables.PERMITIDOS)
DATASETS = {
    "moncloa_islas.json": "build_moncloa_islas.py",
    "salidas.json": "build_salidas.py",
    "horarios.json": "build_horarios.py",
    "paradas.json": "build_paradas.py",
}
ESTADOS_PUBLICABLES = ("COMPLETO", "PARCIAL_APROBADO_MANUALMENTE", "PRUEBA_CONTROLADA")
EXPLICADAS = os.path.join(HERE, "regresiones_explicadas.json")  # {"lineas": {"721": "motivo"}}: líneas que pueden desaparecer a propósito


class Rechazo(Exception):
    """El feed, la descarga o el paquete no es válido: no se cambia nada."""


# ---------------------------------------------------------------- utilidades
def sha256_archivo(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def escribir_json_atomico(ruta, datos):
    """Escribe en un .tmp, fuerza a disco (fsync) y renombra: o queda el archivo viejo o el nuevo entero."""
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ruta)


def leer_json(ruta, defecto=None):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


class Carpetas:
    def __init__(self, salida):
        self.salida = salida
        self.publicable = os.path.join(salida, "publicable")
        self.pendiente = os.path.join(salida, "pendiente_revision")
        self.historial = os.path.join(salida, "historial")
        self.estado = os.path.join(salida, "estado.json")
        self.diario = os.path.join(salida, "instalacion.json")
        for d in (salida, self.pendiente, self.historial):
            os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------- ciclo diario (poco trabajo, poca red)
MIN_HORAS_ENTRE_COMPROBACIONES = 20  # como máximo una comprobación al día aunque algo dispare el trabajo varias veces


def metadatos_fuente(abrir=urllib.request.urlopen):
    """Resumen de la fuente oficial (≈1 KB, sin descargar el ZIP de 76 MB): fecha de modificación y tamaño."""
    req = urllib.request.Request(FUENTE_META, headers={"User-Agent": "Mario-Bus-actualizador (app no oficial)"})
    with abrir(req, timeout=60) as r:
        datos = json.loads(r.read().decode("utf-8"))
    if "modified" not in datos or "size" not in datos:
        raise Rechazo("La fuente oficial no devuelve fecha ni tamaño: formato inesperado")
    return {"modified": int(datos["modified"]), "size": int(datos["size"])}


# Recuperación tras un FALLO (descarga cortada, error inesperado…): un fallo NO es una comprobación correcta, así que no obliga a esperar
# otras 20 h. Se reintenta pronto, pero con tope para no machacar la fuente oficial: espera mínima entre intentos fallidos y, tras
# MAX_INTENTOS_FALLIDOS seguidos, se vuelve a la espera normal de 20 h. La regla de «una comprobación CORRECTA cada 20 h» no cambia.
MIN_HORAS_REINTENTO_TRAS_FALLO = 3
MAX_INTENTOS_FALLIDOS = 3


def decidir(estado, ahora, meta=None, min_horas=MIN_HORAS_ENTRE_COMPROBACIONES):
    """('OMITIR_RECIENTE' | 'OMITIR_TRAS_FALLO' | 'OMITIR_SIN_CAMBIOS' | 'DESCARGAR', motivo). No toca la red.
    `ultima_comprobacion` es solo de comprobaciones CORRECTAS (sin novedades, publicada o rechazada por su contenido)."""
    ultima = estado.get("ultima_comprobacion")
    if ultima is not None and ahora - ultima < min_horas * 3600:
        return "OMITIR_RECIENTE", f"ya se comprobó hace menos de {min_horas} h"
    fallos = int(estado.get("fallos_seguidos") or 0)
    intento = estado.get("ultimo_intento")
    if fallos > 0 and intento is not None:
        espera = min_horas if fallos >= MAX_INTENTOS_FALLIDOS else MIN_HORAS_REINTENTO_TRAS_FALLO
        if ahora - intento < espera * 3600:
            return "OMITIR_TRAS_FALLO", f"el último intento falló hace menos de {espera} h ({fallos} fallo(s) seguido(s)): se reintentará más tarde"
    if meta is not None and estado.get("fuente_meta") == meta:
        return "OMITIR_SIN_CAMBIOS", "la fuente oficial no ha cambiado (misma fecha y tamaño): no se descarga ni se convierte nada"
    return "DESCARGAR", "la fuente es nueva o cambió"


def ciclo_diario(salida, procesar, ahora=None, forzar=False, abrir_meta=metadatos_fuente, descargar=None):
    """Una vuelta del trabajo diario del servidor. `procesar(ruta_zip)` devuelve el resultado de procesar_zip;
    `descargar()` baja el ZIP y devuelve su ruta. Reglas: (1) como mucho una comprobación al día; (2) si el resumen de la fuente no
    cambió, NO se descarga, convierte ni publica nada; (3) un fallo de red no se anota (se reintenta mañana); un rechazo por el
    CONTENIDO sí (no se repite cada día el mismo trabajo inútil mientras la fuente siga igual)."""
    ahora = time.time() if ahora is None else ahora
    carpetas = Carpetas(salida)
    estado = leer_json(carpetas.estado, {})
    accion, motivo = decidir(estado, ahora) if not forzar else ("DESCARGAR", "forzado")
    if accion in ("OMITIR_RECIENTE", "OMITIR_TRAS_FALLO"):
        return {"estado": "OMITIDO", "mensaje": motivo, "descargado": False, "tipo": "OMITIDO"}
    try:
        meta = abrir_meta()
    except Exception as e:  # sin red o fuente caída: no se anota nada, se vuelve a mirar en el siguiente disparo
        return {"estado": "SIN_RED", "mensaje": f"No se pudo consultar la fuente oficial: {e}", "descargado": False, "tipo": "ERROR_DE_CONEXION"}
    if not forzar:
        accion, motivo = decidir(estado, ahora, meta)
    estado = leer_json(carpetas.estado, {})
    if accion == "OMITIR_SIN_CAMBIOS":  # comprobación CORRECTA sin novedades
        estado["ultima_comprobacion"] = ahora
        estado["fallos_seguidos"] = 0
        escribir_json_atomico(carpetas.estado, estado)
        return {"estado": "SIN_CAMBIOS", "mensaje": motivo, "descargado": False, "tipo": "SIN_NOVEDADES"}
    # Se va a descargar: se anota el INTENTO antes de empezar y de forma pesimista (cuenta como fallo). Así, si el trabajo se
    # corta a mitad (tiempo agotado, apagón del servidor…), también queda contado y no se reintenta sin espera. Solo una comprobación
    # CORRECTA (más abajo) pone `ultima_comprobacion` y vuelve el contador de fallos a cero.
    estado["ultimo_intento"] = ahora
    estado["fallos_seguidos"] = int(estado.get("fallos_seguidos") or 0) + 1
    escribir_json_atomico(carpetas.estado, estado)
    try:
        ruta = descargar()
    except Rechazo as e:  # descarga cortada o ZIP roto: FALLO (no es comprobación correcta); se reintentará pronto
        return {"estado": "RECHAZADO", "mensaje": str(e), "descargado": True, "transitorio": True, "tipo": "ERROR_DE_DESCARGA"}
    res = procesar(ruta)
    res["descargado"] = True
    if res.get("transitorio") or res.get("inesperado"):  # fallo real: no se marca la fuente ni la comprobación como hechas
        res["tipo"] = "ERROR_INESPERADO" if res.get("inesperado") else "ERROR_DE_DESCARGA"
        return res
    estado = leer_json(carpetas.estado, {})
    estado["ultima_comprobacion"] = ahora
    estado["fallos_seguidos"] = 0
    estado["fuente_meta"] = meta  # procesado (o rechazado por su CONTENIDO): no se vuelve a trabajar hasta que la fuente cambie
    escribir_json_atomico(carpetas.estado, estado)
    res["tipo"] = "VALIDACION_DEL_CONTENIDO" if res.get("estado") == "RECHAZADO" else "PUBLICADO_O_PROCESADO"
    return res


# ---------------------------------------------------------------- descarga segura
def cabeceras_fuente(abrir=urllib.request.urlopen):
    req = urllib.request.Request(FUENTE, method="HEAD", headers={"User-Agent": "Mario-Bus-actualizador (app no oficial)"})
    with abrir(req, timeout=60) as r:
        return {k.lower(): v for k, v in r.headers.items()}


def descargar_seguro(destino, abrir=urllib.request.urlopen):
    """Descarga a destino+'.part'; solo si llega COMPLETO (tamaño igual al anunciado) y es un ZIP sano lo renombra a destino.
    Ante cualquier fallo borra el .part y lanza Rechazo: nunca deja un archivo a medias."""
    parcial = destino + ".part"
    try:
        req = urllib.request.Request(FUENTE, headers={"User-Agent": "Mario-Bus-actualizador (app no oficial)"})
        with abrir(req, timeout=600) as r, open(parcial, "wb") as f:
            esperado = r.headers.get("Content-Length")
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        if esperado is None or int(esperado) != os.path.getsize(parcial):
            raise Rechazo(f"Descarga incompleta: {os.path.getsize(parcial)} bytes de {esperado}")
        comprobar_zip(parcial)
        os.replace(parcial, destino)
        return destino
    except Rechazo:
        raise
    except Exception as e:  # corte de red, tiempo agotado, disco lleno…
        raise Rechazo(f"Descarga interrumpida: {e}") from e
    finally:
        if os.path.exists(parcial):
            os.remove(parcial)


def comprobar_zip(ruta):
    try:
        with zipfile.ZipFile(ruta) as z:
            malo = z.testzip()
    except zipfile.BadZipFile as e:
        raise Rechazo(f"ZIP corrupto: {e}") from e
    if malo is not None:
        raise Rechazo(f"ZIP corrupto (entrada dañada: {malo})")


def extraer_seguro(zip_ruta, destino):
    comprobar_zip(zip_ruta)
    os.makedirs(destino, exist_ok=True)
    with zipfile.ZipFile(zip_ruta) as z:
        nombres = {os.path.basename(n): n for n in z.namelist()}
        for base, columna in CLAVES.items():
            n = nombres.get(base + ".txt")
            if n is None:
                raise Rechazo(f"Falta {base}.txt en el ZIP")
            datos = z.read(n)
            if not datos.strip():
                raise Rechazo(f"{base}.txt está vacío")
            cabecera = datos.split(b"\n", 1)[0].decode("utf-8-sig", "ignore")
            if columna not in cabecera:
                raise Rechazo(f"{base}.txt no tiene la columna {columna}: formato inesperado")
            with open(os.path.join(destino, base + ".txt"), "wb") as f:
                f.write(datos)


# ---------------------------------------------------------------- lectura y validación rápida
def leer(g, nombre):
    with open(os.path.join(g, nombre), encoding="utf-8-sig", newline="") as f:
        return iter(list(csv.DictReader(f)))


def resumen_feed(g):
    info = next(leer(g, "feed_info.txt"), {})
    cal = list(leer(g, "calendar.txt"))
    exc = list(leer(g, "calendar_dates.txt"))
    return {
        "version": info.get("feed_version", ""),
        "expediciones": sum(1 for _ in leer(g, "trips.txt")),
        "calendario_fin": max((c["end_date"] for c in cal), default=""),
        "calendario_inicio": min((c["start_date"] for c in cal), default=""),
        "excepciones_hasta": max((e["date"] for e in exc), default=""),
    }


def ultima_oficial(carpetas, salida):
    """Referencia de VERSIÓN: la última publicación OFICIAL (feed del Consorcio que pasó todas las validaciones). Una
    PRUEBA_CONTROLADA nunca cuenta: no puede bloquear una actualización oficial. {'version', 'zip_sha256'} o None."""
    est = leer_json(carpetas.estado, {}) or {}
    if est.get("ultima_oficial", {}).get("version"):
        return est["ultima_oficial"]
    man = leer_json(os.path.join(carpetas.publicable, "manifest.json"), {}) or {}
    if man.get("estado") in ("COMPLETO", "PARCIAL_APROBADO_MANUALMENTE") and man.get("version_gtfs"):
        return {"version": man["version_gtfs"], "zip_sha256": man.get("gtfs_zip_sha256")}
    base = leer_json(ruta_base(salida), {}) or {}
    if base.get("version"):  # la «última versión buena» inicial (el feed completo de julio)
        return {"version": base["version"], "zip_sha256": None}
    return None


def validar(nuevo, anterior, ultima_valida, hoy=None, zip_sha=None):
    """(errores, avisos) rápidos: versión, calendario por delante, caída de expediciones. La validación profunda
    (referencias, horas, servicio por línea y día…) está en validar_gtfs.py y se ejecuta aparte.
    VERSIÓN: ya no se compara con «lo último publicado» sino con la última publicación OFICIAL (`ultima_valida`):
      · versión menor  → rechazo (no se vuelve atrás);
      · versión igual  → solo si el ZIP es DISTINTO (feed corregido con el mismo número); el mismo ZIP ya está procesado;
      · versión mayor  → sigue a la validación de contenido.
    Cambiar el número de versión NO hace válido un feed: el contenido se compara siempre con la última versión buena."""
    hoy = hoy or datetime.date.today()
    errores, avisos = [], []
    if not nuevo["version"]:
        errores.append("El feed no trae feed_version")
    else:
        if anterior and anterior.get("version") and nuevo["version"] <= anterior["version"]:
            errores.append(f"La versión {nuevo['version']} no es más nueva que la del feed de refuerzo ({anterior['version']})")
        ref = ultima_valida
        if ref and ref.get("version"):
            if nuevo["version"] < ref["version"]:
                errores.append(f"La versión {nuevo['version']} es anterior a la última oficial publicada ({ref['version']}): no se vuelve atrás")
            elif nuevo["version"] == ref["version"]:
                sha_ref = ref.get("zip_sha256")
                if zip_sha is None or sha_ref is None or sha_ref == zip_sha:
                    errores.append(f"La versión {nuevo['version']} no es más nueva que la última oficial publicada ({ref['version']}) "
                                   "y no es un archivo corregido")
                else:
                    avisos.append(f"Misma versión {nuevo['version']} pero archivo distinto: se trata como feed corregido y se valida igual")
    limite = (hoy + datetime.timedelta(days=14)).strftime("%Y%m%d")
    if nuevo["calendario_fin"] < limite:
        errores.append(f"Los calendarios acaban el {nuevo['calendario_fin']}: menos de 14 días por delante")
    if nuevo["expediciones"] <= 0:
        errores.append("El feed no trae expediciones")
    elif anterior and nuevo["expediciones"] < anterior["expediciones"] * (1 - CAIDA_MAX_EXPEDICIONES):
        errores.append(f"El feed trae {nuevo['expediciones']} expediciones frente a {anterior['expediciones']}: caída sospechosa")
    if nuevo["excepciones_hasta"] and nuevo["excepciones_hasta"] < limite:
        avisos.append(f"Las excepciones de calendario (festivos) solo llegan hasta el {nuevo['excepciones_hasta']}")
    return errores, avisos


# ---------------------------------------------------------------- generación del paquete (los 4 conjuntos de datos)
def lineas_de_la_app(rutas=RUTAS):
    """Líneas de las rutas del conductor (nombres «684 101.gpx» → 684): las que usan salidas.json y horarios.json."""
    import glob
    import re
    return sorted({m.group(1) for f in glob.glob(os.path.join(rutas, "*.gpx"))
                   for m in [re.match(r"(\d{3}) ", os.path.basename(f))] if m})


def lineas_incompletas(nuevo, antiguo, lineas_base, dias):
    """{línea: [fechas que faltan]} de las líneas (de 3 cifras en lineas_base) a las que el feed nuevo le faltan días que el
    antiguo sí trae."""
    import re
    n, a = VAL.servicio_por_linea_y_dia(nuevo, dias), VAL.servicio_por_linea_y_dia(antiguo, dias)
    res = {}
    for ln, por_dia in a.items():
        m = re.fullmatch(r"(\d{3})[A-Z]?", ln)
        if not m or m.group(1) not in lineas_base:
            continue
        faltan = [d for d in dias if por_dia[d] > 0 and n.get(ln, {}).get(d, 0) == 0]
        if faltan:
            res[ln] = faltan
    return res


def generar_paquete(carpeta_feed, carpeta_antigua, carpeta_salida, hoy, dias=DIAS_VENTANA):
    """Ejecuta los 4 generadores con el feed ya validado. Devuelve
    {"datasets": {nombre: {"completo": bool, "lineas_con_datos_antiguos": [...]}}, "version_antigua": str}."""
    env_base = dict(os.environ, GTFS_DIR=carpeta_feed, GTFS_DIR_ANTIGUO=carpeta_antigua, FECHA_REF=hoy.isoformat(),
                    VENTANA_DIAS=str(dias), RUTAS_DIR=RUTAS)
    for nombre, script in DATASETS.items():
        env = dict(env_base, OUT_JSON=os.path.join(carpeta_salida, nombre))
        r = subprocess.run([sys.executable, os.path.join(HERE, script)], env=env, capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0 or not os.path.exists(env["OUT_JSON"]):
            raise Rechazo(f"El generador {script} falla con este feed:\n" + (r.stdout + r.stderr)[-1500:])
    islas = leer_json(os.path.join(carpeta_salida, "moncloa_islas.json")) or {}
    ventana = VAL.ventana(hoy, dias)
    base = set(lineas_de_la_app())
    inc = lineas_incompletas(carpeta_feed, carpeta_antigua, base, ventana) if os.path.isdir(carpeta_antigua) else {}
    efectivo = VAL.servicio_por_linea_y_dia(carpeta_feed, ventana)
    sin_exp = sorted(ln for ln in inc if not any(efectivo.get(ln, {}).values()))
    ant_islas = sorted(islas.get("lineas_con_antiguo", []))
    datasets = {
        "moncloa_islas.json": {"completo": not ant_islas, "lineas_con_datos_antiguos": ant_islas},
        "salidas.json": {"completo": not inc, "lineas_con_datos_antiguos": sorted(inc)},
        "horarios.json": {"completo": not sin_exp, "lineas_con_datos_antiguos": sin_exp},
        "paradas.json": {"completo": True, "lineas_con_datos_antiguos": []},
    }
    return {"datasets": datasets, "version_antigua": islas.get("version_antigua", "")}


# ---------------------------------------------------------------- manifiesto y verificación del paquete
def siguiente_secuencia(salida):
    """Secuencia de publicación: SIEMPRE mayor que la del paquete que hay publicado (aunque dos pasen en el mismo segundo)."""
    actual = (leer_json(os.path.join(salida, "publicable", "manifest.json"), {}) or {}).get("secuencia", 0)
    return max(int(time.time()), int(actual) + 1)


def escribir_manifiesto(tmp_pkg, nuevo, zip_sha, datasets, version_antigua, avisos, stats, hoy, dias, claves):
    parcial = any(not d["completo"] for d in datasets.values())
    antiguas = sorted({ln for d in datasets.values() for ln in d["lineas_con_datos_antiguos"]})
    archivos = [{"nombre": n, "bytes": os.path.getsize(os.path.join(tmp_pkg, n)), "sha256": sha256_archivo(os.path.join(tmp_pkg, n))}
                for n in sorted(DATASETS)]
    v = VAL.ventana(hoy, dias)
    manifest = {
        "esquema": ESQUEMA,
        "secuencia": siguiente_secuencia(os.path.dirname(tmp_pkg)),  # siempre creciente: la app rechaza una secuencia menor
        "generado": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "version_gtfs": nuevo["version"],
        "gtfs_zip_sha256": zip_sha,
        "fuente": PAGINA,
        "licencia": LICENCIA,
        "atribucion": ATRIBUCION,
        "estado": "PARCIAL_REQUIERE_REVISION" if parcial else "COMPLETO",
        "parcial": parcial,
        "publicable_automaticamente": not parcial,
        "revision_manual_necesaria": parcial,
        "datasets": datasets,
        "lineas_con_datos_antiguos": antiguas,
        "version_gtfs_de_refuerzo": version_antigua,
        "excepciones_hasta": nuevo["excepciones_hasta"],
        "ventana_comprobada": {"desde": v[0].isoformat(), "hasta": v[-1].isoformat(), "dias": dias},
        "avisos": avisos,
        "validacion": stats,
        "archivos": archivos,
    }
    escribir_json_atomico(os.path.join(tmp_pkg, "manifest.json"), manifest)
    FIRMA.firmar_archivo(os.path.join(tmp_pkg, "manifest.json"), claves)
    return manifest


def verificar_paquete(carpeta, publica=None):
    """Huellas = manifiesto, JSON legible y con contenido y, si se da la clave pública, FIRMA válida."""
    ruta_m = os.path.join(carpeta, "manifest.json")
    man = leer_json(ruta_m)
    if not man or not man.get("archivos"):
        raise Rechazo("El manifiesto del paquete no es legible")
    if publica is not None:
        ruta_s = os.path.join(carpeta, "manifest.sig")
        if not os.path.exists(ruta_s) or not FIRMA.verificar_archivo(ruta_m, ruta_s, publica):
            raise Rechazo("La firma del manifiesto no es válida")
    nombres = {a["nombre"] for a in man["archivos"]}
    if not nombres <= set(DATASETS):
        raise Rechazo(f"El manifiesto lista archivos no permitidos: {sorted(nombres - set(DATASETS))}")
    for a in man["archivos"]:
        ruta = os.path.join(carpeta, a["nombre"])
        if not os.path.exists(ruta) or os.path.getsize(ruta) != a["bytes"] or sha256_archivo(ruta) != a["sha256"]:
            raise Rechazo(f"El archivo {a['nombre']} no coincide con su huella")
        if leer_json(ruta) in (None, {}, []):
            raise Rechazo(f"{a['nombre']} no es un JSON legible o está vacío")
    if "moncloa_islas.json" in nombres:
        islas = leer_json(os.path.join(carpeta, "moncloa_islas.json"))
        if not islas.get("deps") or not islas.get("islas"):
            raise Rechazo("moncloa_islas.json no trae salidas")
    return man


# ---------------------------------------------------------------- instalación a prueba de apagones
class Hitos:
    """Puntos de la instalación donde las pruebas simulan un apagón (en producción no hace nada)."""
    gancho = None

    @classmethod
    def pasa(cls, nombre):
        if cls.gancho:
            cls.gancho(nombre)


def _destino_historial(carpetas, version):
    destino = os.path.join(carpetas.historial, version or "sin_version")
    n = 1
    while os.path.exists(destino):
        n += 1
        destino = os.path.join(carpetas.historial, f"{version}_{n}")
    return destino


def instalar_publicable(carpetas, paquete_tmp, publica=None):
    """Pone paquete_tmp como publicable/ con un DIARIO: si el ordenador se apaga en cualquier punto, recuperar() termina o
    deshace el cambio y publicable/ vuelve a existir y a ser válida. El anterior pasa a historial/."""
    nuevo_man = leer_json(os.path.join(paquete_tmp, "manifest.json"), {})
    anterior_dest = None
    if os.path.isdir(carpetas.publicable):
        man = leer_json(os.path.join(carpetas.publicable, "manifest.json"), {})
        # nunca se vuelve a una publicación anterior: la secuencia debe ser MAYOR que la publicada (la app también lo exige)
        if int(nuevo_man.get("secuencia", 0)) <= int(man.get("secuencia", 0)):
            raise Rechazo(f"La secuencia {nuevo_man.get('secuencia')} no es mayor que la publicada ({man.get('secuencia')}): "
                          "no se vuelve a una publicación anterior")
        anterior_dest = _destino_historial(carpetas, man.get("version_gtfs", ""))
    escribir_json_atomico(carpetas.diario, {
        "fase": "preparado", "paquete_tmp": paquete_tmp, "anterior_historial": anterior_dest,
        "nueva_version": nuevo_man.get("version_gtfs"), "nueva_secuencia": nuevo_man.get("secuencia")})
    Hitos.pasa("tras_diario")
    if anterior_dest:
        os.replace(carpetas.publicable, anterior_dest)
    Hitos.pasa("tras_mover_anterior")
    os.replace(paquete_tmp, carpetas.publicable)
    Hitos.pasa("tras_instalar_nuevo")
    os.remove(carpetas.diario)


def recuperar(carpetas, publica=None):
    """Llamar SIEMPRE al empezar. Si quedó una instalación a medias (apagón), la termina o la deshace. Devuelve un texto."""
    j = leer_json(carpetas.diario)
    if j is None:
        if os.path.exists(carpetas.diario):  # diario ilegible (corte justo al escribirlo): nada se había movido todavía
            os.remove(carpetas.diario)
        return "sin instalación pendiente"
    tmp, hist = j.get("paquete_tmp"), j.get("anterior_historial")

    def valido(ruta):
        try:
            return bool(ruta) and os.path.isdir(ruta) and verificar_paquete(ruta, publica) is not None
        except Rechazo:
            return False

    if os.path.isdir(carpetas.publicable):
        man = leer_json(os.path.join(carpetas.publicable, "manifest.json"), {})
        es_nueva = man.get("version_gtfs") == j.get("nueva_version") and man.get("secuencia") == j.get("nueva_secuencia")
        if es_nueva and valido(carpetas.publicable):
            msg = "instalación terminada antes del apagón: la nueva versión ya estaba puesta"
        elif valido(carpetas.publicable):
            msg = "el apagón llegó antes de mover nada: se queda la versión anterior"
        elif valido(hist):  # publicable existe pero está dañada y hay copia anterior
            os.replace(carpetas.publicable, _destino_historial(carpetas, "dañada"))
            os.replace(hist, carpetas.publicable)
            msg = "publicable dañada: se restauró la versión anterior"
        else:
            msg = "publicable no verificable y sin copia anterior: se deja como está (revisar a mano)"
    elif valido(tmp):
        os.replace(tmp, carpetas.publicable)
        msg = "faltaba publicable: se terminó la instalación de la nueva versión"
    elif valido(hist):
        os.replace(hist, carpetas.publicable)
        msg = "faltaba publicable y la nueva no era válida: se restauró la anterior desde historial"
    else:
        msg = "faltaba publicable y no hay ninguna copia válida"
    if tmp and os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.remove(carpetas.diario)
    for n in os.listdir(carpetas.salida):  # temporales abandonados por un apagón
        if n.startswith(("paquete_", "aprobado_")):
            shutil.rmtree(os.path.join(carpetas.salida, n), ignore_errors=True)
    return msg


def instalar_pendiente(carpetas, paquete_tmp, version):
    destino = os.path.join(carpetas.pendiente, version)
    if os.path.exists(destino):
        shutil.rmtree(destino)
    os.replace(paquete_tmp, destino)
    return destino


# ---------------------------------------------------------------- proceso principal
def ruta_base(salida):
    return os.path.join(salida, "base_resumen.json")


def crear_base(feed_dir, salida, hoy=None, dias=DIAS_VENTANA):
    """Guarda la huella de servicio de un feed COMPLETO y bueno como «última versión buena» con la que se compararán los siguientes."""
    Carpetas(salida)
    escribir_json_atomico(ruta_base(salida), VAL.resumen_servicio(feed_dir, hoy or datetime.date.today(), dias))


def explicadas():
    return (leer_json(EXPLICADAS, {}) or {}).get("lineas", {})


def procesar_zip(zip_ruta, salida=SALIDA, carpeta_antigua=BASE, generar=generar_paquete, hoy=None, claves=None,
                 dias=DIAS_VENTANA, lineas_clave=(), paradas_clave=(), estricto=False):
    """Procesa un ZIP ya descargado. Devuelve un dict con 'estado' (COMPLETO | PARCIAL | RECHAZADO | YA_PROCESADO).
    estricto=True (servidor de la nube): sin feed antiguo; se compara con la huella de la última versión buena y CUALQUIER
    carencia (línea, día de servicio, caída de expediciones, dato antiguo de refuerzo) es un RECHAZO: nunca hay paquetes parciales."""
    hoy = hoy or datetime.date.today()
    claves = claves or FIRMA.CARPETA_CLAVES
    carpetas = Carpetas(salida)
    os.makedirs(TRABAJO, exist_ok=True)
    temporales = []
    try:
        publica = FIRMA.publica_de(claves)  # sin clave privada no se publica nada (falla antes de tocar nada)
        recuperar(carpetas, publica)
        comprobar_zip(zip_ruta)
        zip_sha = sha256_archivo(zip_ruta)
        est = leer_json(carpetas.estado, {})
        if est.get("ultima_version_procesada") and est.get("zip_sha256") == zip_sha:
            return {"estado": "YA_PROCESADO", "mensaje": "Este mismo archivo ya se procesó", "version": est["ultima_version_procesada"]}
        tmp_feed = tempfile.mkdtemp(prefix="feed_", dir=TRABAJO)
        temporales.append(tmp_feed)
        extraer_seguro(zip_ruta, tmp_feed)
        nuevo = resumen_feed(tmp_feed)
        if estricto:
            carpeta_antigua = os.path.join(TRABAJO, "_sin_feed_antiguo")  # no existe: ni refuerzo ni comparación con julio
        anterior = resumen_feed(carpeta_antigua) if os.path.isdir(carpeta_antigua) else None
        ultima = ultima_oficial(carpetas, salida)  # una PRUEBA_CONTROLADA publicada no cuenta como referencia de versión
        errores, avisos = validar(nuevo, anterior, ultima, hoy, zip_sha)
        if errores:
            raise Rechazo("; ".join(errores))
        ev, av, stats = VAL.validar_gtfs(tmp_feed, carpeta_antigua if anterior else None, hoy, dias, lineas_clave, paradas_clave)
        if ev:
            raise Rechazo("Validación del GTFS: " + "; ".join(ev))
        avisos += av
        if estricto:
            base = leer_json(ruta_base(salida))
            if not base:
                raise Rechazo("No hay «última versión buena» con la que comparar (falta base_resumen.json): créala con --crear-base")
            problemas = VAL.comparar_con_base(VAL.resumen_servicio(tmp_feed, hoy, dias), base, explicadas())
            if problemas:
                raise Rechazo(f"Servicio que había y ya no está ({len(problemas)}): " + "; ".join(problemas[:12]) + ("…" if len(problemas) > 12 else ""))
        tmp_pkg = tempfile.mkdtemp(prefix="paquete_", dir=salida)
        temporales.append(tmp_pkg)
        res_gen = generar(tmp_feed, carpeta_antigua, tmp_pkg, hoy, dias)
        datasets = res_gen["datasets"]
        incompletas = {ln for d in datasets.values() for ln in d["lineas_con_datos_antiguos"]}
        if lineas_clave and len(incompletas) / len(lineas_clave) > MAX_LINEAS_PARCIALES:
            raise Rechazo(f"A {len(incompletas)} de {len(lineas_clave)} líneas les faltan días en este feed: demasiado incompleto")
        if estricto and incompletas:
            raise Rechazo(f"Líneas con datos antiguos o sin servicio en el paquete ({len(incompletas)}): {sorted(incompletas)[:12]}")
        manifest = escribir_manifiesto(tmp_pkg, nuevo, zip_sha, datasets, res_gen.get("version_antigua", ""), avisos, stats, hoy, dias, claves)
        verificar_paquete(tmp_pkg, publica)
        if manifest["parcial"]:
            instalar_pendiente(carpetas, tmp_pkg, nuevo["version"])
            temporales.remove(tmp_pkg)
            res = {"estado": "PARCIAL", "version": nuevo["version"], "lineas_antiguas": manifest["lineas_con_datos_antiguos"],
                   "datasets_incompletos": sorted(n for n, d in datasets.items() if not d["completo"]),
                   "mensaje": f"PARCIAL ({len(manifest['lineas_con_datos_antiguos'])} líneas con datos antiguos): guardado en pendiente_revision/, NO publicable"}
        else:
            instalar_publicable(carpetas, tmp_pkg, publica)
            temporales.remove(tmp_pkg)
            if estricto:
                escribir_json_atomico(ruta_base(salida), VAL.resumen_servicio(tmp_feed, hoy, dias))  # la nueva versión buena es la referencia
            res = {"estado": "COMPLETO", "version": nuevo["version"], "mensaje": "COMPLETO: ya es la nueva versión publicable/"}
        est = leer_json(carpetas.estado, {})
        est.update({"ultima_version_procesada": nuevo["version"], "zip_sha256": zip_sha, "resultado": res["estado"],
                    "actualizado": datetime.datetime.now().astimezone().isoformat(timespec="seconds")})
        if res["estado"] == "COMPLETO":
            est["ultima_version_publicable"] = nuevo["version"]
            est["ultima_oficial"] = {"version": nuevo["version"], "zip_sha256": zip_sha, "secuencia": manifest["secuencia"],
                                     "publicada": est["actualizado"]}
            est["tipo_publicable"] = "OFICIAL"
            est.pop("publicable_es_prueba", None)
        escribir_json_atomico(carpetas.estado, est)
        res["avisos"] = avisos
        return res
    except Rechazo as e:  # el CONTENIDO del feed no es válido: es una comprobación correcta cuyo resultado es «no se publica»
        return {"estado": "RECHAZADO", "mensaje": str(e)}
    except FIRMA.ErrorFirma as e:  # falta/está mal la clave de firma: problema NUESTRO, no del feed → se reintentará (no se anota la fuente)
        return {"estado": "RECHAZADO", "mensaje": f"Error de firma: {e}", "inesperado": True}
    except Exception as e:  # cualquier fallo inesperado: no se cambia nada y NO se da por comprobada la fuente
        return {"estado": "RECHAZADO", "mensaje": f"Error inesperado ({type(e).__name__}): {e}", "inesperado": True}
    finally:
        for t in temporales:
            shutil.rmtree(t, ignore_errors=True)


def aprobar_parcial(version, salida=SALIDA, claves=None):
    """Pasa un paquete PARCIAL a publicable/ SOLO por orden manual. El manifiesto sigue diciendo PARCIAL (y se vuelve a firmar)."""
    if os.environ.get("MARIOBUS_SIN_PARCIALES"):
        raise Rechazo("En este sistema NO se aprueban paquetes parciales (regla de Mario)")
    claves = claves or FIRMA.CARPETA_CLAVES
    carpetas = Carpetas(salida)
    publica = FIRMA.publica_de(claves)
    recuperar(carpetas, publica)
    origen = os.path.join(carpetas.pendiente, version)
    if not os.path.isdir(origen):
        raise Rechazo(f"No hay ningún paquete pendiente de revisión con la versión {version}")
    man = verificar_paquete(origen, publica)
    man.update({"estado": "PARCIAL_APROBADO_MANUALMENTE", "parcial": True, "publicable_automaticamente": False,
                "revision_manual_necesaria": False, "secuencia": max(siguiente_secuencia(carpetas.salida), man.get("secuencia", 0) + 1),
                "aprobado_manualmente": datetime.datetime.now().astimezone().isoformat(timespec="seconds")})
    tmp = tempfile.mkdtemp(prefix="aprobado_", dir=carpetas.salida)
    try:
        for nombre in os.listdir(origen):
            if nombre not in ("manifest.json", "manifest.sig"):
                shutil.copy2(os.path.join(origen, nombre), os.path.join(tmp, nombre))
        escribir_json_atomico(os.path.join(tmp, "manifest.json"), man)
        FIRMA.firmar_archivo(os.path.join(tmp, "manifest.json"), claves)
        verificar_paquete(tmp, publica)
        instalar_publicable(carpetas, tmp, publica)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    est = leer_json(carpetas.estado, {})
    est["ultima_version_publicable"] = version
    est["publicable_es_parcial"] = True
    escribir_json_atomico(carpetas.estado, est)
    return man


# ---------------------------------------------------------------- restaurar una versión anterior
def restaurar_version(version, salida=SALIDA, claves=None):
    """Vuelve a publicar una versión de historial/. Se REFIRMA con una secuencia NUEVA (mayor que la actual): la app rechaza
    cualquier manifiesto con secuencia menor (protección contra copias viejas), así que una restauración tiene que parecer, para la
    app, una actualización más. Los datos y huellas son exactamente los de esa versión."""
    claves = claves or FIRMA.CARPETA_CLAVES
    carpetas = Carpetas(salida)
    publica = FIRMA.publica_de(claves)
    recuperar(carpetas, publica)
    origen = os.path.join(carpetas.historial, version)
    if not os.path.isdir(origen):
        raise Rechazo(f"No hay ninguna versión «{version}» en historial/ (hay: {sorted(os.listdir(carpetas.historial))})")
    man = verificar_paquete(origen, publica)  # la firma original también debe ser válida: no se restaura nada dañado
    actual = (leer_json(os.path.join(carpetas.publicable, "manifest.json"), {}) or {}).get("secuencia", 0)
    man.update({"secuencia": max(int(time.time()), actual + 1, man.get("secuencia", 0) + 1), "restaurada_de": version,
                "restaurada_el": datetime.datetime.now().astimezone().isoformat(timespec="seconds")})
    tmp = tempfile.mkdtemp(prefix="paquete_", dir=carpetas.salida)
    try:
        for nombre in os.listdir(origen):
            if nombre not in ("manifest.json", "manifest.sig"):
                shutil.copy2(os.path.join(origen, nombre), os.path.join(tmp, nombre))
        escribir_json_atomico(os.path.join(tmp, "manifest.json"), man)
        FIRMA.firmar_archivo(os.path.join(tmp, "manifest.json"), claves)
        verificar_paquete(tmp, publica)
        instalar_publicable(carpetas, tmp, publica)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    est = leer_json(carpetas.estado, {})
    est["ultima_version_publicable"] = man.get("version_gtfs")
    est["restaurada_de"] = version
    if man.get("estado") == "COMPLETO":  # restaurar una OFICIAL la vuelve a ser la referencia de versión; una prueba no
        est["ultima_oficial"] = {"version": man.get("version_gtfs"), "zip_sha256": man.get("gtfs_zip_sha256"), "secuencia": man["secuencia"],
                                 "publicada": man["restaurada_el"]}
        est["tipo_publicable"] = "OFICIAL"
        est.pop("publicable_es_prueba", None)
    else:
        est["tipo_publicable"] = "PRUEBA_CONTROLADA"
        est["publicable_es_prueba"] = True
    escribir_json_atomico(carpetas.estado, est)
    return man


# ---------------------------------------------------------------- actualización de PRUEBA CONTROLADA
def publicar_prueba_controlada(salida, claves, carpeta_datos, motivo="prueba del recorrido completo"):
    """Publica, claramente MARCADA, una actualización de prueba hecha con los 4 JSON reales que ya lleva la app (carpeta_datos):
    no es un feed nuevo del Consorcio ni se hace pasar por uno. Sirve para demostrar servidor → móvil cuando el Consorcio no ha
    publicado nada. La app solo la acepta en la variante de prueba; la de Google Play la rechaza."""
    carpetas = Carpetas(salida)
    publica = FIRMA.publica_de(claves)
    recuperar(carpetas, publica)
    # Una prueba NUNCA sustituye a una publicación OFICIAL (ni a algo ilegible que podría serlo): solo puede ir sobre otra prueba o
    # sobre nada. (Protección en el código, no solo en el workflow.)
    if os.path.isdir(carpetas.publicable):
        man_pub = leer_json(os.path.join(carpetas.publicable, "manifest.json"), {}) or {}
        if man_pub.get("estado") != "PRUEBA_CONTROLADA":
            raise Rechazo("Hay una publicación OFICIAL (estado «%s», versión %s) o no se puede leer: una prueba controlada no puede sustituirla"
                          % (man_pub.get("estado", "ilegible"), man_pub.get("version_gtfs", "?")))
    tmp = tempfile.mkdtemp(prefix="paquete_", dir=salida)
    try:
        for n in DATASETS:
            shutil.copy2(os.path.join(carpeta_datos, n), os.path.join(tmp, n))
        islas = leer_json(os.path.join(tmp, "moncloa_islas.json")) or {}
        ahora = datetime.datetime.now().astimezone()
        archivos = [{"nombre": n, "bytes": os.path.getsize(os.path.join(tmp, n)), "sha256": sha256_archivo(os.path.join(tmp, n))} for n in sorted(DATASETS)]
        man = {
            "esquema": ESQUEMA, "secuencia": siguiente_secuencia(salida), "generado": ahora.isoformat(timespec="seconds"),
            "version_gtfs": islas.get("version", ""), "estado": "PRUEBA_CONTROLADA", "parcial": False,
            "prueba_controlada": True, "publicable_automaticamente": False, "revision_manual_necesaria": False,
            "aviso": "ACTUALIZACIÓN DE PRUEBA: son los mismos datos reales que ya lleva la app, republicados para probar la descarga. "
                     "No es un feed nuevo del Consorcio. Motivo: " + motivo,
            "fuente": PAGINA, "licencia": LICENCIA, "atribucion": ATRIBUCION, "archivos": archivos,
        }
        escribir_json_atomico(os.path.join(tmp, "manifest.json"), man)
        FIRMA.firmar_archivo(os.path.join(tmp, "manifest.json"), claves)
        verificar_paquete(tmp, publica)
        instalar_publicable(carpetas, tmp, publica)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    est = leer_json(carpetas.estado, {})
    est["publicable_es_prueba"] = True
    est["tipo_publicable"] = "PRUEBA_CONTROLADA"
    est["ultima_prueba_controlada"] = man["generado"]
    escribir_json_atomico(carpetas.estado, est)
    return man


# ---------------------------------------------------------------- verificación de las líneas con horarios antiguos
def dias_por_linea(g, dias):
    """{línea: [fechas de `dias` con servicio]} de un GTFS, contando solo expediciones que EMPIEZAN en las paradas de las islas."""
    sys.path.insert(0, HERE)
    from build_moncloa_islas import PLAN, STOP_ISLA  # noqa: E402
    lineas = {ln for ds in PLAN.values() for l in ds.values() for ln, _ in l}
    paradas = set(STOP_ISLA.values())
    routes = {r["route_id"]: r["route_short_name"] for r in leer(g, "routes.txt")}
    trips = {t["trip_id"]: t for t in leer(g, "trips.txt")}
    primero, en = {}, {}
    for st in leer(g, "stop_times.txt"):
        tid, seq = st["trip_id"], int(st["stop_sequence"])
        if tid not in primero or seq < primero[tid][0]:
            primero[tid] = (seq, st["stop_id"])
        if st["stop_id"] in paradas:
            en[tid] = st["stop_id"]
    servicios = {}
    for tid, sid in en.items():
        ln = routes[trips[tid]["route_id"]]
        if primero[tid][1] == sid and ln in lineas:
            servicios.setdefault(ln, set()).add(trips[tid]["service_id"])
    activos = VAL.servicios_activos_por_dia(list(leer(g, "calendar.txt")), list(leer(g, "calendar_dates.txt")), dias)
    return {ln: [d for d in dias if servicios.get(ln, set()) & activos[d]] for ln in sorted(lineas)}, lineas


def verificar_lineas(carpeta_nueva, carpeta_antigua=BASE, hoy=None, dias=DIAS_VENTANA):
    """Para cada línea: ¿le faltan días al feed nuevo que el antiguo sí trae? FECHAS DINÁMICAS: desde `hoy` (por defecto la
    fecha actual) durante `dias` días. Devuelve (con_antiguo, completas, detalle)."""
    ventana = VAL.ventana(hoy, dias)
    nuevo, lineas = dias_por_linea(carpeta_nueva, ventana)
    antiguo, _ = dias_por_linea(carpeta_antigua, ventana)
    con_antiguo, completas, detalle = [], [], {}
    for ln in sorted(lineas):
        faltan = [d for d in antiguo[ln] if d not in nuevo[ln]]
        if not nuevo[ln] and not antiguo[ln]:
            completas.append(ln)  # ni uno ni otro traen servicio en la ventana: no hay nada que reforzar
        elif not nuevo[ln] or faltan:
            con_antiguo.append(ln)
            detalle[ln] = ("el feed nuevo no trae ninguna expedición" if not nuevo[ln]
                           else "faltan en el nuevo: " + ", ".join(d.strftime("%a %d/%m") for d in faltan))
        else:
            completas.append(ln)
    return con_antiguo, completas, detalle


# ---------------------------------------------------------------- autoprueba y CLI
def autotest():
    base = {"version": "20260723", "expediciones": 100000}
    hoy = datetime.date(2026, 11, 18)

    def f(**k):
        d = {"version": "20261027", "expediciones": 100000, "calendario_fin": "20271128", "calendario_inicio": "20260722", "excepciones_hasta": "20261127"}
        d.update(k)
        return d

    ok = True

    def c(nombre, cond):
        nonlocal ok
        print(("OK   " if cond else "FALLA"), nombre)
        ok &= cond

    e, a = validar(f(), base, None, hoy); c("feed bueno se acepta", not e)
    e, a = validar(f(version="20260723"), base, None, hoy); c("misma versión se rechaza", bool(e))
    e, a = validar(f(), base, {"version": "20270101"}, hoy); c("más viejo que la última publicable se rechaza", bool(e))
    e, a = validar(f(calendario_fin="20261125"), base, None, hoy); c("calendario corto se rechaza", bool(e))
    e, a = validar(f(expediciones=60000), base, None, hoy); c("caída del 40 % se rechaza", bool(e))
    e, a = validar(f(expediciones=0), base, None, hoy); c("sin expediciones se rechaza", bool(e))
    e, a = validar(f(excepciones_hasta="20261130"), base, None, hoy); c("excepciones cortas → solo aviso", not e and bool(a))
    print("RESULTADO:", "TODAS LAS REGLAS BIEN" if ok else "HAY FALLOS")
    return 0 if ok else 1


def imprimir(res):
    tipo = f" [{res['tipo']}]" if res.get("tipo") else ""
    print("RESULTADO:", res["estado"] + tipo, "·", res.get("mensaje", ""))
    for a in res.get("avisos", []):
        print("AVISO:", a)


# ---------------------------------------------------------------- quién puede pedir qué (protección del respaldo externo)
# Acciones EXTRAORDINARIAS: cambian lo publicado de forma no rutinaria (restaurar, publicar una prueba, forzar una descarga, aprobar un
# parcial, rehacer la base de referencia). Solo se permiten desde un `git push` (evento «push» de GitHub: exige poder escribir en el
# repositorio, lo que el token del respaldo externo NO tiene) o a mano en local (sin variable MARIOBUS_EVENTO). Desde un cron
# («schedule») o una llamada a la API («workflow_dispatch», que es lo que usa cron-job.org) solo se permite el CICLO NORMAL. El evento
# lo pone GitHub y quien llama a la API no puede falsearlo; el «origen» que escribe el llamante solo se usa para endurecer más.
ACCIONES_EXTRAORDINARIAS = {
    "--forzar": "ciclo forzado", "--prueba-controlada": "publicación de prueba", "--restaurar": "restauración",
    "--aprobar-parcial": "aprobación de parciales", "--crear-base": "rehacer la base de referencia",
}
EVENTOS_CON_PERMISO = ("push",)
ORIGENES_SIN_PERMISO = ("respaldo-externo",)


def acciones_no_permitidas(args, evento=None, origen=None):
    """None si todo está permitido; si no, el motivo (texto). `evento` = github.event_name (variable MARIOBUS_EVENTO) o None (uso local);
    `origen` = quien dice disparar (variable MARIOBUS_ORIGEN, solo informativo/endurecedor)."""
    pedidas = [nombre for flag, nombre in ACCIONES_EXTRAORDINARIAS.items() if flag in args]
    if not pedidas:
        return None  # ciclo normal: lo puede pedir cualquiera
    if origen in ORIGENES_SIN_PERMISO:
        return f"el origen «{origen}» solo puede ejecutar el ciclo normal (pidió: {', '.join(pedidas)})"
    if evento is not None and evento not in EVENTOS_CON_PERMISO:
        return f"desde el evento «{evento}» solo se puede ejecutar el ciclo normal (pidió: {', '.join(pedidas)})"
    return None


def _opt(args, nombre, defecto=None):
    return args[args.index(nombre) + 1] if nombre in args else defecto


def main():
    args = sys.argv[1:]
    salida = _opt(args, "--salida", SALIDA)
    claves = _opt(args, "--claves", FIRMA.CARPETA_CLAVES)
    zip_arg = _opt(args, "--zip")
    hoy = datetime.date.fromisoformat(_opt(args, "--hoy")) if "--hoy" in args else datetime.date.today()
    dias = int(_opt(args, "--dias", DIAS_VENTANA))
    prohibido = acciones_no_permitidas(args, os.environ.get("MARIOBUS_EVENTO"), os.environ.get("MARIOBUS_ORIGEN"))
    if prohibido:  # antes de tocar nada
        print("RESULTADO: RECHAZADO · acción no permitida:", prohibido)
        return 1
    if "--autotest" in args:
        return autotest()
    if "--estado" in args:
        c = Carpetas(salida)
        print(json.dumps(leer_json(c.estado, {}), ensure_ascii=False, indent=2))
        print("publicable:", os.listdir(c.publicable) if os.path.isdir(c.publicable) else "(vacío: aún no hay ninguna versión COMPLETA)")
        print("pendiente_revision:", sorted(os.listdir(c.pendiente)))
        print("historial:", sorted(os.listdir(c.historial)))
        print("instalación a medias:", "SÍ (ejecuta --recuperar)" if os.path.exists(c.diario) else "no")
        return 0
    if "--restaurar" in args:
        try:
            man = restaurar_version(_opt(args, "--restaurar"), salida, claves)
        except (Rechazo, FIRMA.ErrorFirma) as e:
            print("RECHAZADO:", e)
            return 1
        print("Restaurada la versión", man["restaurada_de"], "con la secuencia nueva", man["secuencia"])
        return 0
    if "--crear-base" in args:
        crear_base(_opt(args, "--crear-base"), salida, hoy, dias)
        print("Base creada:", ruta_base(salida))
        return 0
    if "--prueba-controlada" in args:
        try:
            man = publicar_prueba_controlada(salida, claves, _opt(args, "--prueba-controlada"), _opt(args, "--motivo", "prueba del recorrido completo"))
        except (Rechazo, FIRMA.ErrorFirma, OSError) as e:
            print("RECHAZADO:", e)
            return 1
        print("Publicada la PRUEBA CONTROLADA (secuencia", man["secuencia"], ") en publicable/. Marcada como prueba; no es un feed del Consorcio.")
        return 0
    if "--recuperar" in args:
        print(recuperar(Carpetas(salida), FIRMA.publica_de(claves)))
        return 0
    if "--verificar-publicable" in args:
        try:
            m = verificar_paquete(Carpetas(salida).publicable, FIRMA.publica_de(claves))
        except Rechazo as e:
            print("NO VÁLIDO:", e)
            return 1
        print("VÁLIDO · versión", m["version_gtfs"], "·", m["estado"], "· firma y huellas correctas")
        return 0
    if "--aprobar-parcial" in args:
        if "--si-estoy-seguro" not in args:
            print("Falta --si-estoy-seguro: un paquete PARCIAL lleva horarios antiguos en varias líneas. No se hace nada.")
            return 1
        try:
            man = aprobar_parcial(_opt(args, "--aprobar-parcial"), salida, claves)
        except (Rechazo, FIRMA.ErrorFirma) as e:
            print("RECHAZADO:", e)
            return 1
        print("Aprobado manualmente; sigue marcado como PARCIAL:", man["estado"])
        return 0
    if "--verificar-lineas" in args:
        if not zip_arg:
            nueva = os.path.join(HERE, "gtfs", "m89_20261027")
        else:
            os.makedirs(TRABAJO, exist_ok=True)
            nueva = tempfile.mkdtemp(prefix="feed_", dir=TRABAJO)
            extraer_seguro(zip_arg, nueva)
        con, completas, det = verificar_lineas(nueva, BASE, hoy, dias)
        v = VAL.ventana(hoy, dias)
        print(f"Ventana comprobada: {v[0].isoformat()} → {v[-1].isoformat()} ({dias} días)")
        print(f"Líneas con datos antiguos: {len(con)} · líneas completas en el feed nuevo: {len(completas)}")
        for ln in con:
            print(f"  {ln}: {det[ln]}")
        print("Completas:", ", ".join(completas))
        return 0
    sys.path.insert(0, HERE)
    from build_moncloa_islas import PLAN, STOP_ISLA  # noqa: E402
    lineas_clave = sorted({ln for ds in PLAN.values() for l in ds.values() for ln, _ in l})
    paradas_clave = sorted(set(STOP_ISLA.values()))
    if zip_arg:
        res = procesar_zip(zip_arg, salida, BASE, generar_paquete, hoy, claves, dias, lineas_clave, paradas_clave,
                           "--estricto" in args or bool(os.environ.get("MARIOBUS_ESTRICTO")))
        imprimir(res)
        return 0 if res["estado"] != "RECHAZADO" else 1
    if "--solo-comprobar" in args:
        try:
            m = metadatos_fuente()
        except Exception as e:
            print("No se pudo consultar la fuente:", e)
            return 1
        est = leer_json(Carpetas(salida).estado, {})
        print("Fuente oficial:", PAGINA, "· modificada", datetime.datetime.fromtimestamp(m["modified"] / 1000).isoformat(timespec="minutes"),
              "· tamaño", m["size"], "bytes ·", "SIN CAMBIOS desde la última vez" if est.get("fuente_meta") == m else "NUEVA o distinta a la última procesada")
        return 0

    def descargar():
        os.makedirs(TRABAJO, exist_ok=True)
        return descargar_seguro(os.path.join(TRABAJO, "descarga_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"))

    estricto = "--estricto" in args or bool(os.environ.get("MARIOBUS_ESTRICTO"))
    res = ciclo_diario(salida, lambda z: procesar_zip(z, salida, BASE, generar_paquete, hoy, claves, dias, lineas_clave, paradas_clave, estricto),
                       forzar="--forzar" in args, descargar=descargar)
    imprimir(res)
    return 0 if res["estado"] not in ("RECHAZADO",) else 1


if __name__ == "__main__":
    sys.exit(main())
