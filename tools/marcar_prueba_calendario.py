#!/usr/bin/env python3
"""Marca un manifiesto de calendario como PRUEBA (estado PRUEBA_CONTROLADA). Se ejecuta ANTES de firmar, porque la firma cubre el manifiesto.
Solo lo usa el modo «publicar_prueba» de calendario.yml. Un calendario marcado así NUNCA lo acepta Google Play: solo la variante pruebaR8.

Uso:  python tools/marcar_prueba_calendario.py RUTA/manifest.json
"""
import json
import sys


def marcar(ruta):
    with open(ruta, encoding="utf-8") as f:
        m = json.load(f)
    m["estado"] = "PRUEBA_CONTROLADA"
    m["prueba_controlada"] = True
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, sort_keys=True, indent=1)
    return m


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    marcar(sys.argv[1])
    print("Manifiesto marcado PRUEBA_CONTROLADA:", sys.argv[1])
