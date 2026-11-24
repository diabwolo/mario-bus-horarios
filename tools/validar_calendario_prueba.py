#!/usr/bin/env python3
"""Puerta del calendario de PRUEBA (/prueba/calendario/). Más suave que la de producción en una sola cosa (tolera fiestas locales pendientes,
porque es para ensayar la descarga con datos reales) y más estricta en otra: exige que el paquete esté marcado PRUEBA_CONTROLADA y firmado con
la clave de PRUEBA. Un paquete firmado con la clave de producción NO pasa por aquí, ni uno de prueba pasa por la puerta de producción.

Uso:  python tools/validar_calendario_prueba.py --paquete DIR --publica BASE64_DE_LA_CLAVE_DE_PRUEBA
Salida: 0 = publicable como prueba; 2 = no.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def validar(paquete, publica):
    motivos = []
    ruta_m = os.path.join(paquete, "manifest.json")
    ruta_s = os.path.join(paquete, "manifest.sig")
    ruta_c = os.path.join(paquete, "calendario.json")
    for r in (ruta_m, ruta_s, ruta_c):
        if not os.path.isfile(r):
            return [f"falta {os.path.basename(r)}"]
    with open(ruta_m, encoding="utf-8") as f:
        m = json.load(f)
    with open(ruta_c, "rb") as f:
        cal_bytes = f.read()
    if m.get("esquema") != 1:
        motivos.append("esquema no soportado")
    if m.get("estado") != "PRUEBA_CONTROLADA":
        motivos.append(f"un calendario de prueba debe estar marcado PRUEBA_CONTROLADA (estado {m.get('estado')!r})")
    try:
        import firma_manifiesto as F
        if not F.verificar_archivo(ruta_m, ruta_s, publica):
            motivos.append("la firma no es válida con la clave de PRUEBA")
    except Exception as e:  # noqa: BLE001
        motivos.append(f"no se pudo verificar la firma: {e}")
    archivos = m.get("archivos") or []
    if len(archivos) != 1 or archivos[0].get("nombre") != "calendario.json":
        motivos.append("el manifiesto debe describir exactamente calendario.json")
    elif archivos[0].get("bytes") != len(cal_bytes) or archivos[0].get("sha256") != hashlib.sha256(cal_bytes).hexdigest():
        motivos.append("calendario.json no coincide con el manifiesto (tamaño o SHA-256)")
    return motivos


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--paquete", required=True)
    ap.add_argument("--publica", required=True)
    a = ap.parse_args(argv)
    motivos = validar(a.paquete, a.publica)
    if motivos:
        print("CALENDARIO DE PRUEBA NO PUBLICABLE:")
        for x in motivos:
            print("  -", x)
        return 2
    print("Calendario de prueba publicable: marcado PRUEBA_CONTROLADA y firmado con la clave de prueba.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
