#!/usr/bin/env python3
"""Calendario de festivos de Mario-Bus: lo lee de las fuentes OFICIALES, lo comprueba y genera el paquete firmable.

Fuentes (solo BOE y BOCM; ver tools/calendario/fuentes.json):
  * BOE: Resolución de la Dirección General de Trabajo con la relación de fiestas laborales del año (nacionales).
  * BOCM: Decreto del Consejo de Gobierno con las fiestas laborales del año en la Comunidad de Madrid.
  * BOCM: Resolución de la Dirección General de Trabajo con las fiestas locales (Collado Villalba). Se publica hacia diciembre
    del año anterior: mientras no exista, el año queda con los locales «PENDIENTES» (no se inventan).

Qué comprueba (cada comprobación queda escrita en el paquete, con su resultado):
  1. La huella SHA-256 de cada documento coincide con la de fuentes.json.
  2. Cada documento es del año que dice ser.
  3. El decreto de Madrid trae EXACTAMENTE 12 fiestas, sin repetir y todas dentro del año; si indica el día de la semana, coincide.
  4. TODAS las fiestas nacionales del BOE están en el decreto de Madrid (en su fecha, o trasladadas al lunes si cayeron en domingo).
  5. Lo que el decreto añade a las nacionales son solo fiestas propias conocidas (Jueves Santo, San José, 2 de mayo).
  6. Los locales de Collado Villalba son exactamente 2 fechas distintas del año (o «no publicados todavía»).
Si algo no cuadra, el paquete NO se publica (va a pendiente_revision/) y el script termina con código 2.

Qué NO hace: no sube nada a ningún servidor, no toca la app, no mezcla festivos con descansos ni con condiciones laborales.

Uso (desde la raíz del proyecto):
  python tools/actualizar_calendario.py --anios 2026 2027 --salida tools/publicar_calendario [--asset app/src/main/assets/calendario.json]
                                        [--firmar [--claves CARPETA]] [--descargar] [--ahora 2026-11-23T12:00:00+01:00]
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ESQUEMA = 1
MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
         "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
FIESTAS_PROPIAS_CONOCIDAS = ("jueves santo", "san josé", "fiesta de la comunidad de madrid")
# Palabras que identifican, en la columna izquierda del anexo del BOE, las fiestas de ámbito NACIONAL
NACIONALES_BOE = ("año nuevo", "epifanía del señor", "viernes santo", "fiesta del trabajo", "asunción",
                  "fiesta nacional de españa", "todos los santos", "constitución", "inmaculada concepción", "natividad del señor")


class ErrorCalendario(Exception):
    pass


# ---------------------------------------------------------------------------------------------------- lectura de PDF

def texto_de_pdf(ruta):
    """Texto del PDF con `pdftotext` (poppler) o, si no está, con pypdf. Sin ninguno de los dos: error claro."""
    try:
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", "-layout", ruta, "-"], capture_output=True, check=True)
        return r.stdout.decode("utf-8", "replace")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    try:
        import pypdf  # type: ignore
        return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(ruta).pages)
    except ImportError:
        raise ErrorCalendario("Falta leer PDF: instala poppler (pdftotext) o `pip install pypdf`")


def sha256_archivo(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _plano(s):
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------------------------------- analizadores

def parsear_decreto_madrid(texto, anio):
    """[(date, nombre, dia_semana_indicado|None)] del anexo del Decreto de fiestas laborales de la Comunidad de Madrid."""
    if not re.search(r"fiestas\s+laborales\s+para\s+el\s+año\s+%d" % anio, texto):
        raise ErrorCalendario("El decreto no es de fiestas laborales de %d" % anio)
    i = texto.find("ANEXO")
    if i < 0:
        raise ErrorCalendario("El decreto no tiene ANEXO")
    fiestas = []
    for linea in texto[i:].splitlines():
        m = re.match(r"^\s*[—–-]\s*(\d{1,2})\s+de\s+([a-záéíóú]+)(?:\s*\(([a-záéíóú]+)\))?\s*,\s*(.+?)\.?\s*$", linea)
        if not m:
            continue
        mes = MESES.get(m.group(2))
        if not mes:
            raise ErrorCalendario("Mes desconocido: %s" % m.group(2))
        fiestas.append((datetime.date(anio, mes, int(m.group(1))), _plano(m.group(4)), m.group(3)))
    return fiestas


def parsear_locales(texto, anio, municipio):
    """[date, ...] de las fiestas locales del municipio, o None si el BOCM dice que no se comunicaron."""
    plano = _plano(texto)
    if not re.search(r"ámbito local.*?para el año %d" % anio, plano):
        raise ErrorCalendario("La resolución no es de fiestas locales de %d" % anio)
    m = re.search(re.escape(municipio) + r":\s*([^—]+?)\.\s*(?:—|B\.O\.C\.M|$)", plano)
    if not m:
        raise ErrorCalendario("%s no aparece en la resolución de locales de %d" % (municipio, anio))
    cuerpo = m.group(1)
    if "no comunicado" in cuerpo.lower():
        return None
    return [datetime.date(anio, MESES[mes], int(d)) for d, mes in re.findall(r"(\d{1,2})\s+de\s+([a-záéíóú]+)", cuerpo)]


def parsear_boe(texto, anio):
    """{date: nombre} de las fiestas de ámbito NACIONAL de la columna izquierda del anexo de la resolución del BOE."""
    if not re.search(r"fiestas\s+laborales\s+para\s+el\s+año\s+%d" % anio, _plano(texto)):
        raise ErrorCalendario("La resolución del BOE no es de fiestas laborales de %d" % anio)
    i = texto.find("ANEXO")
    if i < 0:
        raise ErrorCalendario("La resolución del BOE no tiene ANEXO")
    # En algunos años «Enero» va en la misma línea que la palabra ANEXO: se empieza en «Fecha de las fiestas» (cabecera de la tabla)
    j = texto.find("Fecha de las fiestas")
    inicio = min(i, j) if j >= 0 else i
    mes_actual = None
    nacionales = {}
    for linea in texto[inicio:].splitlines():
        mm = re.match(r"^\s{4,}(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Octubre|Noviembre|Diciembre)\b", linea)
        if mm:
            mes_actual = MESES[mm.group(1).lower()]
            continue
        # El nombre acaba en punto, en 2 espacios (empieza la columna de comunidades) o al final de la línea
        m = re.match(r"^\s{0,12}(\d{1,2})\s+([A-ZÁÉÍÓÚ].*?)(?:\.|\s{2,}|$)", linea)
        if not m or mes_actual is None:
            continue
        nombre = _plano(m.group(2))
        bajo = nombre.lower()
        if any(k in bajo for k in NACIONALES_BOE):
            try:
                nacionales[datetime.date(anio, mes_actual, int(m.group(1)))] = nombre
            except ValueError:
                pass
    return nacionales


# ---------------------------------------------------------------------------------------------------- comprobaciones

def _comp(id_, ok, detalle):
    return {"id": id_, "ok": bool(ok), "detalle": detalle}


def construir_anio(anio, fuentes_anio, base, municipio, leer_texto=texto_de_pdf):
    """Devuelve (festivos, locales, comprobaciones, fuentes_usadas). No lanza por datos malos: los deja como comprobación fallida."""
    comps = []
    usadas = {}
    textos = {}
    for clave in ("boe", "bocm_decreto", "bocm_locales"):
        f = fuentes_anio.get(clave)
        if f is None:
            continue
        ruta = os.path.join(base, f["archivo"])
        if not os.path.isfile(ruta):
            comps.append(_comp("fuente_%s" % clave, False, "Falta el documento %s" % f["archivo"]))
            continue
        huella = sha256_archivo(ruta)
        ok = huella == f.get("sha256")
        comps.append(_comp("huella_%s" % clave, ok, "sha256 %s %s" % (huella[:16] + "…", "coincide con fuentes.json" if ok else "NO coincide con fuentes.json")))
        usadas[clave] = {"referencia": f["referencia"], "url": f["url"], "sha256": huella}
        if ok:
            try:
                textos[clave] = leer_texto(ruta)
            except ErrorCalendario as e:
                comps.append(_comp("lectura_%s" % clave, False, str(e)))

    madrid, nacionales = [], {}
    if "bocm_decreto" in textos:
        try:
            madrid = parsear_decreto_madrid(textos["bocm_decreto"], anio)
            comps.append(_comp("decreto_del_anio", True, "El decreto es de fiestas laborales de %d" % anio))
        except ErrorCalendario as e:
            comps.append(_comp("decreto_del_anio", False, str(e)))
    if "boe" in textos:
        try:
            nacionales = parsear_boe(textos["boe"], anio)
            comps.append(_comp("boe_del_anio", True, "La resolución del BOE es de %d y trae %d fiestas nacionales" % (anio, len(nacionales))))
        except ErrorCalendario as e:
            comps.append(_comp("boe_del_anio", False, str(e)))

    # 3. Decreto: 12 fiestas, sin repetir, dentro del año, día de la semana coherente
    fechas_madrid = [d for d, _, _ in madrid]
    comps.append(_comp("madrid_12_fiestas", len(madrid) == 12, "El decreto de Madrid trae %d fiestas (debe haber 12)" % len(madrid)))
    comps.append(_comp("madrid_sin_repetidas", len(set(fechas_madrid)) == len(fechas_madrid), "Fechas distintas: %d de %d" % (len(set(fechas_madrid)), len(fechas_madrid))))
    malas_semana = [str(d) for d, _, s in madrid if s and DIAS_SEMANA[d.weekday()] != s.lower()]
    comps.append(_comp("madrid_dia_semana", not malas_semana, "Día de la semana del decreto coherente con el calendario" if not malas_semana else "No cuadra: " + ", ".join(malas_semana)))

    # 4. El BOE trae las 10 fiestas nacionales (aunque alguna se haya trasladado al lunes) y todas están en el decreto de Madrid
    comps.append(_comp("boe_10_nacionales", len(nacionales) == 10, "El BOE trae %d fiestas nacionales (deben ser 10)" % len(nacionales)))
    faltan = [str(d) for d in nacionales if d not in set(fechas_madrid)]
    comps.append(_comp("nacionales_en_madrid", bool(nacionales) and not faltan,
                       "Las %d fiestas nacionales del BOE están todas en el decreto de Madrid" % len(nacionales) if not faltan and nacionales else
                       "Nacionales del BOE que no están en Madrid: %s" % (", ".join(faltan) or "(no se pudieron leer)")))

    # 5. Lo que añade Madrid son fiestas propias conocidas
    propias = [(d, n) for d, n, _ in madrid if d not in nacionales]
    raras = [n for d, n in propias if not any(k in n.lower() for k in FIESTAS_PROPIAS_CONOCIDAS)]
    comps.append(_comp("madrid_solo_propias_conocidas", not raras,
                       "Fiestas propias de Madrid: %s" % ", ".join(n for _, n in propias) if not raras else "Fiesta propia desconocida, revisar a mano: %s" % ", ".join(raras)))

    # 6. Locales
    locales = {"estado": "PENDIENTE", "motivo": "Aún no publicados en el BOCM"}
    fl = []
    if "bocm_locales" in textos:
        try:
            locales_fechas = parsear_locales(textos["bocm_locales"], anio, municipio["nombre"])
            if locales_fechas is None:
                comps.append(_comp("locales_publicados", True, "El BOCM indica que %s no comunicó fiestas locales" % municipio["nombre"]))
                locales = {"estado": "PENDIENTE", "motivo": "El Ayuntamiento no las comunicó"}
            else:
                ok = len(locales_fechas) == 2 and len(set(locales_fechas)) == 2
                comps.append(_comp("locales_dos_fechas", ok, "%s: %s" % (municipio["nombre"], ", ".join(str(d) for d in locales_fechas))))
                solapadas = [str(d) for d in locales_fechas if d in set(fechas_madrid)]
                comps.append(_comp("locales_no_coinciden_con_regionales", not solapadas, "Sin coincidencias con festivos regionales" if not solapadas else "Coinciden: " + ", ".join(solapadas)))
                if ok:
                    locales = {"estado": "PUBLICADO", "fuente": "bocm_locales"}
                    fl = list(locales_fechas)
        except ErrorCalendario as e:
            comps.append(_comp("locales_publicados", False, str(e)))
    else:
        comps.append(_comp("locales_pendientes", True, "No hay resolución de locales de %d todavía: quedan PENDIENTES (no se inventan)" % anio))

    festivos = []
    ref_dec = (fuentes_anio.get("bocm_decreto") or {}).get("referencia", "")
    for d, nombre, _ in sorted(madrid):
        bajo = nombre.lower()
        if d in nacionales:
            traslado = "traslado" in bajo or "lunes siguiente" in nacionales[d].lower() or "día siguiente" in nacionales[d].lower()
            desc = "Fiesta laboral NACIONAL (BOE: «%s»)" % nacionales[d]
            if traslado:
                desc += ". Se disfruta en lunes porque la fiesta cayó en domingo"
            festivos.append({"fecha": str(d), "ambito": "nacional", "nombre": nombre[0].upper() + nombre[1:], "descripcion": desc + ".",
                             "fuente": "bocm_decreto", "trasladada": traslado})
        else:
            extra = " (Ley 8/1984)" if "comunidad de madrid" in bajo else ""
            festivos.append({"fecha": str(d), "ambito": "autonomico", "nombre": nombre[0].upper() + nombre[1:],
                             "descripcion": "Fiesta laboral de la Comunidad de Madrid%s (%s)." % (extra, ref_dec.split(",")[0]),
                             "fuente": "bocm_decreto", "trasladada": False})
    ref_loc = (fuentes_anio.get("bocm_locales") or {}).get("referencia", "")
    for d in sorted(fl):
        festivos.append({"fecha": str(d), "ambito": "local", "nombre": "Fiesta local de %s" % municipio["nombre"],
                         "descripcion": "Fiesta local de %s (%s)." % (municipio["nombre"], ref_loc.split(",")[0] or "BOCM"),
                         "fuente": "bocm_locales", "trasladada": False})
    festivos.sort(key=lambda f: f["fecha"])
    return festivos, locales, comps, usadas


def construir_calendario(anios, fuentes, base, ahora, leer_texto=texto_de_pdf):
    municipio = fuentes["municipio"]
    datos_anios, todas, usadas_total = {}, {}, {}
    for anio in anios:
        fa = fuentes["anios"].get(str(anio))
        if fa is None:
            raise ErrorCalendario("No hay fuentes para el año %d en fuentes.json" % anio)
        festivos, locales, comps, usadas = construir_anio(anio, fa, base, municipio, leer_texto)
        datos_anios[str(anio)] = {"festivos": festivos, "locales": locales}
        todas[str(anio)] = comps
        usadas_total[str(anio)] = usadas
    fallos = [(a, c) for a, cs in todas.items() for c in cs if not c["ok"]]
    pendientes = ["Fiestas locales de %s %s: aún no publicadas en el BOCM" % (municipio["nombre"], a)
                  for a, d in datos_anios.items() if d["locales"]["estado"] != "PUBLICADO"]
    estado = "COMPROBADO" if not fallos else "PENDIENTE"
    generado = ahora.isoformat(timespec="seconds")
    cal = {
        "esquema": ESQUEMA,
        "version": ahora.strftime("%Y%m%d"),
        "generado": generado,
        "municipio": municipio,
        "anios": datos_anios,
        "fuentes": usadas_total,
        "verificacion": {
            "estado": estado,
            "fecha": generado,
            "por": "tools/actualizar_calendario.py",
            "comprobaciones": todas,
            "pendientes": pendientes,
        },
    }
    return cal, fallos


def json_estable(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def escribir_paquete(cal, carpeta, ahora, firmar=False, claves=None):
    """calendario.json + manifest.json (+ manifest.sig). El manifiesto describe EXACTAMENTE los bytes de calendario.json."""
    os.makedirs(carpeta, exist_ok=True)
    datos = json_estable(cal)
    with open(os.path.join(carpeta, "calendario.json"), "wb") as f:
        f.write(datos)
    manifiesto = {
        "esquema": ESQUEMA,
        "secuencia": int(ahora.timestamp()),
        "generado": cal["generado"],
        "estado": "COMPLETO" if cal["verificacion"]["estado"] == "COMPROBADO" else "REVISION",
        "anios": sorted(int(a) for a in cal["anios"]),
        "verificacion": {"estado": cal["verificacion"]["estado"], "fecha": cal["verificacion"]["fecha"],
                         "pendientes": cal["verificacion"]["pendientes"]},
        "archivos": [{"nombre": "calendario.json", "bytes": len(datos), "sha256": hashlib.sha256(datos).hexdigest()}],
    }
    ruta_m = os.path.join(carpeta, "manifest.json")
    with open(ruta_m, "wb") as f:
        f.write(json_estable(manifiesto))
    if firmar:
        import firma_manifiesto as FIRMA
        FIRMA.firmar_archivo(ruta_m, claves or FIRMA.CARPETA_CLAVES)
    return manifiesto


# ---------------------------------------------------------------------------------------------------- descarga (opcional)

def descargar_fuentes(fuentes, base, anios, abrir=urllib.request.urlopen):
    """Baja los documentos que falten (HTTPS, solo URLs de fuentes.json). NO cambia las huellas: si el documento oficial cambió, la
    comprobación de huella falla y hay que revisarlo a mano antes de actualizar fuentes.json."""
    for anio in anios:
        for clave, f in (fuentes["anios"].get(str(anio)) or {}).items():
            if not f:
                continue
            ruta = os.path.join(base, f["archivo"])
            if os.path.isfile(ruta):
                continue
            if not f["url"].startswith("https://"):
                raise ErrorCalendario("Solo HTTPS: %s" % f["url"])
            os.makedirs(os.path.dirname(ruta), exist_ok=True)
            req = urllib.request.Request(f["url"], headers={"User-Agent": "Mozilla/5.0"})
            with abrir(req, timeout=60) as r, open(ruta, "wb") as out:
                out.write(r.read())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anios", nargs="+", type=int, required=True)
    ap.add_argument("--fuentes", default=os.path.join(HERE, "calendario", "fuentes.json"))
    ap.add_argument("--salida", default=os.path.join(HERE, "publicar_calendario"))
    ap.add_argument("--asset", help="Escribe también calendario.json (sin firma) en esta ruta, para incluirlo en la app")
    ap.add_argument("--firmar", action="store_true")
    ap.add_argument("--claves")
    ap.add_argument("--descargar", action="store_true")
    ap.add_argument("--ahora", help="Fecha y hora ISO (solo para pruebas)")
    a = ap.parse_args(argv)
    with open(a.fuentes, encoding="utf-8") as f:
        fuentes = json.load(f)
    base = os.path.dirname(os.path.abspath(a.fuentes))
    ahora = datetime.datetime.fromisoformat(a.ahora) if a.ahora else datetime.datetime.now().astimezone()
    try:
        if a.descargar:
            descargar_fuentes(fuentes, base, a.anios)
        cal, fallos = construir_calendario(a.anios, fuentes, base, ahora)
    except ErrorCalendario as e:
        print("ERROR:", e)
        return 2
    for anio, comps in cal["verificacion"]["comprobaciones"].items():
        print("== %s" % anio)
        for c in comps:
            print("  [%s] %s: %s" % ("OK " if c["ok"] else "MAL", c["id"], c["detalle"]))
    for p in cal["verificacion"]["pendientes"]:
        print("  PENDIENTE:", p)
    destino = os.path.join(a.salida, "publicable" if not fallos else "pendiente_revision")
    escribir_paquete(cal, destino, ahora, firmar=a.firmar and not fallos, claves=a.claves)
    if a.asset and not fallos:
        os.makedirs(os.path.dirname(os.path.abspath(a.asset)), exist_ok=True)
        with open(a.asset, "wb") as f:
            f.write(json_estable(cal))
    print("Paquete en %s — estado: %s" % (destino, cal["verificacion"]["estado"]))
    return 0 if not fallos else 2


if __name__ == "__main__":
    sys.exit(main())
