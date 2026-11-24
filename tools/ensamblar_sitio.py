#!/usr/bin/env python3
"""Ensambla el sitio de GitHub Pages (_site) con PRODUCCIÓN y PRUEBAS en rutas SEPARADAS.

GitHub Pages sustituye el sitio entero en cada despliegue, así que quien despliega (horarios.yml o calendario.yml) tiene que volver a
montar TODO a partir del estado guardado en el repositorio. Este script es el único que decide qué va a cada ruta:

  estado/publicable/                  (horarios)   estado COMPLETO            -> /                 (PRODUCCIÓN: lo que descarga Google Play)
                                                   estado PRUEBA_CONTROLADA   -> /prueba/          (solo la variante pruebaR8)
  estado/calendario/publicable/       (calendario) estado COMPLETO+COMPROBADO -> /calendario/      (PRODUCCIÓN)
  estado/calendario_prueba/publicable (calendario) estado PRUEBA_CONTROLADA   -> /prueba/calendario/

Garantías (si alguna falla, NO se ensambla nada y el despliegue se para):
  * la raíz y /calendario/ NUNCA llevan un paquete de prueba; /prueba/ NUNCA lleva uno definitivo;
  * ningún estado desconocido ni parcial llega a producción;
  * ninguna clave privada (.pem, .jks, .key, .p12) acaba dentro del sitio.

Uso:  python tools/ensamblar_sitio.py [--repo .] [--destino _site]
"""
import argparse
import json
import os
import shutil
import sys

PROHIBIDOS = (".pem", ".jks", ".key", ".p12", ".keystore")
ESTADO_PRODUCCION = "COMPLETO"
ESTADO_PRUEBA = "PRUEBA_CONTROLADA"


class ErrorEnsamblado(Exception):
    pass


def _manifiesto(carpeta):
    ruta = os.path.join(carpeta, "manifest.json")
    if not os.path.isfile(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ErrorEnsamblado(f"{ruta}: manifiesto ilegible ({e})")


def _copiar(origen, destino):
    shutil.copytree(origen, destino, dirs_exist_ok=True)


def ensamblar(repo, destino):
    """Devuelve la lista de lo que se publicó (para el registro). Lanza ErrorEnsamblado si algo no cuadra."""
    publicado = []
    os.makedirs(destino, exist_ok=True)

    # 1) Horarios: según su estado, producción (raíz) o prueba (/prueba/)
    pub = os.path.join(repo, "estado", "publicable")
    m = _manifiesto(pub)
    if m is not None:
        estado = m.get("estado")
        if estado == ESTADO_PRODUCCION:
            _copiar(pub, destino)
            publicado.append("horarios PRODUCCIÓN -> /")
        elif estado == ESTADO_PRUEBA:
            _copiar(pub, os.path.join(destino, "prueba"))
            publicado.append("horarios PRUEBA -> /prueba/")
        else:
            raise ErrorEnsamblado(f"estado/publicable tiene el estado {estado!r}: solo se publica COMPLETO (producción) o PRUEBA_CONTROLADA (pruebas)")

    # 2) Calendario de producción: solo definitivo y comprobado
    cal = os.path.join(repo, "estado", "calendario", "publicable")
    m = _manifiesto(cal)
    if m is not None:
        if m.get("estado") != ESTADO_PRODUCCION or (m.get("verificacion") or {}).get("estado") != "COMPROBADO":
            raise ErrorEnsamblado("el calendario de producción no está COMPLETO y COMPROBADO: no se publica")
        _copiar(cal, os.path.join(destino, "calendario"))
        publicado.append("calendario PRODUCCIÓN -> /calendario/")

    # 3) Calendario de pruebas
    calp = os.path.join(repo, "estado", "calendario_prueba", "publicable")
    m = _manifiesto(calp)
    if m is not None:
        if m.get("estado") != ESTADO_PRUEBA:
            raise ErrorEnsamblado("el calendario de pruebas debe estar marcado PRUEBA_CONTROLADA")
        _copiar(calp, os.path.join(destino, "prueba", "calendario"))
        publicado.append("calendario PRUEBA -> /prueba/calendario/")

    comprobar(destino)
    return publicado


def comprobar(destino):
    """Revisión final del sitio ya montado: separación de rutas y ninguna clave privada."""
    for raiz, _dirs, archivos in os.walk(destino):
        for a in archivos:
            if a.lower().endswith(PROHIBIDOS):
                raise ErrorEnsamblado(f"clave privada dentro del sitio: {os.path.join(raiz, a)}")
    for ruta_rel, esperado in (("", ESTADO_PRODUCCION), ("calendario", ESTADO_PRODUCCION), ("prueba", ESTADO_PRUEBA), (os.path.join("prueba", "calendario"), ESTADO_PRUEBA)):
        m = _manifiesto(os.path.join(destino, ruta_rel))
        if m is not None and m.get("estado") != esperado:
            raise ErrorEnsamblado(f"/{ruta_rel.replace(os.sep, '/')} lleva un paquete {m.get('estado')!r} y solo puede llevar {esperado!r}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--destino", default="_site")
    a = ap.parse_args(argv)
    if os.path.exists(a.destino):
        shutil.rmtree(a.destino)
    try:
        lista = ensamblar(a.repo, a.destino)
    except ErrorEnsamblado as e:
        print(f"ERROR: {e}", file=sys.stderr)
        shutil.rmtree(a.destino, ignore_errors=True)
        return 2
    print("Sitio ensamblado:" if lista else "Sitio vacío (nada publicable todavía):")
    for x in lista:
        print("  -", x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
