#!/usr/bin/env python3
"""Puerta de salida del calendario: NADA se publica en /calendario/ si no pasa TODAS estas comprobaciones, hechas de forma independiente
del generador (tools/actualizar_calendario.py) y con la misma lógica que aplica la app de Google Play al descargar:

  1. manifest.json: esquema 1, estado COMPLETO, verificación COMPROBADO, secuencia mayor que la ya publicada;
  2. manifest.sig: firma ECDSA válida con la clave PÚBLICA DE PRODUCCIÓN (la misma que lleva la app);
  3. calendario.json mide lo que dice el manifiesto y su SHA-256 coincide;
  4. cubre TODOS los años mínimos (los que ya lleva la app): la app usa un paquete entero o el suyo, nunca los mezcla;
  5. TODOS los años tienen sus fiestas locales PUBLICADAS (un paquete con locales «PENDIENTES» es incompleto y no se publica).

Uso:  python tools/validar_calendario_publicable.py --paquete DIR --publica BASE64 --anios-minimos 2026 2027 [--secuencia-actual N]
Salida: 0 = publicable; 2 = no publicable (cada motivo en una línea).
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def validar(paquete, publica, anios_minimos, secuencia_actual=0):
    """Lista de motivos por los que el paquete NO es publicable (vacía = publicable)."""
    motivos = []
    ruta_m = os.path.join(paquete, "manifest.json")
    ruta_s = os.path.join(paquete, "manifest.sig")
    ruta_c = os.path.join(paquete, "calendario.json")
    for r in (ruta_m, ruta_s, ruta_c):
        if not os.path.isfile(r):
            return [f"falta {os.path.basename(r)}"]
    try:
        with open(ruta_m, encoding="utf-8") as f:
            m = json.load(f)
        with open(ruta_c, "rb") as f:
            cal_bytes = f.read()
        cal = json.loads(cal_bytes.decode("utf-8"))
    except (OSError, ValueError) as e:
        return [f"archivo ilegible: {e}"]

    if m.get("esquema") != 1:
        motivos.append("esquema no soportado")
    if m.get("estado") != "COMPLETO":
        motivos.append(f"el manifiesto no está COMPLETO (estado {m.get('estado')!r})")
    if (m.get("verificacion") or {}).get("estado") != "COMPROBADO":
        motivos.append("el manifiesto no está COMPROBADO")
    if int(m.get("secuencia") or 0) <= int(secuencia_actual):
        motivos.append(f"la secuencia {m.get('secuencia')} no es mayor que la ya publicada ({secuencia_actual})")

    try:
        import firma_manifiesto as F
        if not F.verificar_archivo(ruta_m, ruta_s, publica):
            motivos.append("la firma no es válida con la clave de producción")
    except Exception as e:  # noqa: BLE001 - cualquier fallo de verificación es un motivo de rechazo
        motivos.append(f"no se pudo verificar la firma: {e}")

    archivos = m.get("archivos") or []
    if len(archivos) != 1 or archivos[0].get("nombre") != "calendario.json":
        motivos.append("el manifiesto debe describir exactamente calendario.json")
    else:
        if archivos[0].get("bytes") != len(cal_bytes):
            motivos.append("calendario.json no mide lo que dice el manifiesto")
        if archivos[0].get("sha256") != hashlib.sha256(cal_bytes).hexdigest():
            motivos.append("el SHA-256 de calendario.json no coincide con el del manifiesto")

    if (cal.get("verificacion") or {}).get("estado") != "COMPROBADO":
        motivos.append("calendario.json no está COMPROBADO")
    anios = {int(a) for a in (cal.get("anios") or {})}
    faltan = sorted(set(anios_minimos) - anios)
    if faltan:
        motivos.append(f"no cubre los años {faltan}, que ya lleva la app: paquete incompleto")
    for a, d in sorted((cal.get("anios") or {}).items()):
        if (d.get("locales") or {}).get("estado") != "PUBLICADO":
            motivos.append(f"las fiestas locales de {a} no están publicadas: paquete incompleto")
    return motivos


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--paquete", required=True)
    ap.add_argument("--publica", required=True, help="clave pública de PRODUCCIÓN (Base64 X.509), la misma de la app")
    ap.add_argument("--anios-minimos", nargs="+", type=int, required=True)
    ap.add_argument("--secuencia-actual", type=int, default=0)
    a = ap.parse_args(argv)
    motivos = validar(a.paquete, a.publica, a.anios_minimos, a.secuencia_actual)
    if motivos:
        print("CALENDARIO NO PUBLICABLE:")
        for x in motivos:
            print("  -", x)
        return 2
    print("Calendario publicable: pasa todas las comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
