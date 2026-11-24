"""Pruebas de la separación producción/pruebas del sitio (tools/ensamblar_sitio.py) y de la puerta del calendario
(tools/validar_calendario_publicable.py). Ejecutar:   python tools/tests/test_calendario_servidor.py
No usan Internet ni las claves reales: generan claves de prueba en una carpeta temporal."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(AQUI)
sys.path.insert(0, TOOLS)
import ensamblar_sitio as E  # noqa: E402
import firma_manifiesto as F  # noqa: E402
import validar_calendario_publicable as V  # noqa: E402


def escribir_json(ruta, obj):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(obj, f)


class Sitio(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.dest = os.path.join(self.repo, "_site")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def manif(self, carpeta, estado, **extra):
        escribir_json(os.path.join(self.repo, carpeta, "manifest.json"), dict(estado=estado, **extra))
        open(os.path.join(self.repo, carpeta, "manifest.sig"), "w").write("firma")

    def existe(self, ruta):
        return os.path.isfile(os.path.join(self.dest, ruta))

    def test_horarios_definitivos_van_a_la_raiz(self):
        self.manif("estado/publicable", "COMPLETO")
        E.ensamblar(self.repo, self.dest)
        self.assertTrue(self.existe("manifest.json"))
        self.assertFalse(os.path.isdir(os.path.join(self.dest, "prueba")))

    def test_horarios_de_prueba_van_a_prueba_y_la_raiz_queda_vacia(self):
        self.manif("estado/publicable", "PRUEBA_CONTROLADA")
        E.ensamblar(self.repo, self.dest)
        self.assertTrue(self.existe("prueba/manifest.json"))
        self.assertFalse(self.existe("manifest.json"), "un paquete de prueba NUNCA en la raíz (producción)")

    def test_estado_parcial_o_desconocido_no_se_publica(self):
        for estado in ("PARCIAL_APROBADO_MANUALMENTE", "PARCIAL_REQUIERE_REVISION", "OTRO", None):
            self.manif("estado/publicable", estado)
            with self.assertRaises(E.ErrorEnsamblado):
                E.ensamblar(self.repo, self.dest)
            shutil.rmtree(self.dest, ignore_errors=True)

    def test_calendario_de_produccion_exige_completo_y_comprobado(self):
        self.manif("estado/calendario/publicable", "COMPLETO", verificacion={"estado": "PENDIENTE"})
        with self.assertRaises(E.ErrorEnsamblado):
            E.ensamblar(self.repo, self.dest)
        shutil.rmtree(self.dest, ignore_errors=True)
        self.manif("estado/calendario/publicable", "REVISION", verificacion={"estado": "COMPROBADO"})
        with self.assertRaises(E.ErrorEnsamblado):
            E.ensamblar(self.repo, self.dest)
        shutil.rmtree(self.dest, ignore_errors=True)
        self.manif("estado/calendario/publicable", "COMPLETO", verificacion={"estado": "COMPROBADO"})
        E.ensamblar(self.repo, self.dest)
        self.assertTrue(self.existe("calendario/manifest.json"))

    def test_calendario_de_prueba_solo_en_prueba_y_marcado_prueba(self):
        self.manif("estado/calendario_prueba/publicable", "COMPLETO")
        with self.assertRaises(E.ErrorEnsamblado):
            E.ensamblar(self.repo, self.dest)
        shutil.rmtree(self.dest, ignore_errors=True)
        self.manif("estado/calendario_prueba/publicable", "PRUEBA_CONTROLADA")
        E.ensamblar(self.repo, self.dest)
        self.assertTrue(self.existe("prueba/calendario/manifest.json"))
        self.assertFalse(self.existe("calendario/manifest.json"))

    def test_todo_a_la_vez_sin_mezclar(self):
        self.manif("estado/publicable", "COMPLETO")
        self.manif("estado/calendario/publicable", "COMPLETO", verificacion={"estado": "COMPROBADO"})
        self.manif("estado/calendario_prueba/publicable", "PRUEBA_CONTROLADA")
        lista = E.ensamblar(self.repo, self.dest)
        self.assertEqual(3, len(lista))
        for ruta, esperado in (("manifest.json", "COMPLETO"), ("calendario/manifest.json", "COMPLETO"), ("prueba/calendario/manifest.json", "PRUEBA_CONTROLADA")):
            self.assertEqual(esperado, json.load(open(os.path.join(self.dest, ruta)))["estado"])

    def test_una_clave_privada_dentro_del_sitio_detiene_el_despliegue(self):
        self.manif("estado/publicable", "COMPLETO")
        open(os.path.join(self.repo, "estado", "publicable", "horarios-firma-privada.pem"), "w").write("secreto")
        with self.assertRaises(E.ErrorEnsamblado):
            E.ensamblar(self.repo, self.dest)

    def test_sin_nada_publicable_el_sitio_queda_vacio(self):
        self.assertEqual([], E.ensamblar(self.repo, self.dest))


class PuertaDelCalendario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.claves = os.path.join(self.tmp, "claves")
        F.generar_claves(self.claves)
        self.publica = F.publica_de(self.claves)
        self.paq = os.path.join(self.tmp, "paquete")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hacer(self, anios=(2026, 2027), locales="PUBLICADO", secuencia=10, estado="COMPLETO", comprobado="COMPROBADO", claves=None):
        os.makedirs(self.paq, exist_ok=True)
        cal = {"verificacion": {"estado": comprobado}, "anios": {str(a): {"locales": {"estado": locales}} for a in anios}}
        datos = json.dumps(cal).encode()
        open(os.path.join(self.paq, "calendario.json"), "wb").write(datos)
        m = {"esquema": 1, "secuencia": secuencia, "estado": estado, "verificacion": {"estado": comprobado},
             "archivos": [{"nombre": "calendario.json", "bytes": len(datos), "sha256": hashlib.sha256(datos).hexdigest()}]}
        escribir_json(os.path.join(self.paq, "manifest.json"), m)
        F.firmar_archivo(os.path.join(self.paq, "manifest.json"), claves or self.claves)

    def motivos(self, minimos=(2026, 2027), actual=0):
        return V.validar(self.paq, self.publica, minimos, actual)

    def test_paquete_completo_firmado_es_publicable(self):
        self.hacer()
        self.assertEqual([], self.motivos())

    def test_locales_pendientes_es_incompleto(self):
        self.hacer(locales="PENDIENTE")
        self.assertTrue(any("locales" in m and "incompleto" in m for m in self.motivos()))

    def test_falta_un_anio_que_ya_lleva_la_app(self):
        self.hacer(anios=(2026,))
        self.assertTrue(any("2027" in m and "incompleto" in m for m in self.motivos()))

    def test_estado_no_completo_o_sin_comprobar(self):
        self.hacer(estado="REVISION")
        self.assertTrue(any("COMPLETO" in m for m in self.motivos()))
        shutil.rmtree(self.paq)
        self.hacer(comprobado="PENDIENTE")
        self.assertTrue(any("COMPROBADO" in m for m in self.motivos()))

    def test_firma_de_otra_clave_se_rechaza(self):
        otra = os.path.join(self.tmp, "otra")
        F.generar_claves(otra)
        self.hacer(claves=otra)
        self.assertTrue(any("firma" in m for m in self.motivos()))

    def test_calendario_manipulado_despues_de_firmar(self):
        self.hacer()
        with open(os.path.join(self.paq, "calendario.json"), "ab") as f:
            f.write(b" ")
        m = self.motivos()
        self.assertTrue(any("mide" in x or "SHA-256" in x for x in m))

    def test_secuencia_no_mayor_que_la_publicada(self):
        self.hacer(secuencia=10)
        self.assertTrue(any("secuencia" in m for m in self.motivos(actual=10)))
        self.assertEqual([], self.motivos(actual=9))


import marcar_prueba_calendario as M  # noqa: E402
import validar_calendario_prueba as VP  # noqa: E402


class PruebaYProduccionNoSeIntercambian(unittest.TestCase):
    """Un calendario de PRUEBA (estado PRUEBA_CONTROLADA, clave de prueba) y uno de PRODUCCIÓN (COMPLETO, clave de producción) no se confunden."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.prod = os.path.join(self.tmp, "prod")
        self.prueba = os.path.join(self.tmp, "prueba")
        F.generar_claves(self.prod)
        F.generar_claves(self.prueba)
        self.pub_prod = F.publica_de(self.prod)
        self.pub_prueba = F.publica_de(self.prueba)
        self.paq = os.path.join(self.tmp, "paquete")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hacer(self, firmar_con, marcar):
        os.makedirs(self.paq, exist_ok=True)
        cal = {"verificacion": {"estado": "COMPROBADO"}, "anios": {"2026": {"locales": {"estado": "PUBLICADO"}}, "2027": {"locales": {"estado": "PUBLICADO"}}}}
        datos = json.dumps(cal).encode()
        open(os.path.join(self.paq, "calendario.json"), "wb").write(datos)
        m = {"esquema": 1, "secuencia": 10, "estado": "COMPLETO", "verificacion": {"estado": "COMPROBADO"},
             "archivos": [{"nombre": "calendario.json", "bytes": len(datos), "sha256": hashlib.sha256(datos).hexdigest()}]}
        ruta = os.path.join(self.paq, "manifest.json")
        escribir_json(ruta, m)
        if marcar:
            M.marcar(ruta)
        F.firmar_archivo(ruta, firmar_con)  # la firma va DESPUÉS de marcar, como en el workflow

    def test_prueba_marcada_y_firmada_con_la_clave_de_prueba_pasa_su_puerta(self):
        self.hacer(self.prueba, marcar=True)
        self.assertEqual([], VP.validar(self.paq, self.pub_prueba))

    def test_prueba_firmada_con_la_clave_de_produccion_no_pasa_la_puerta_de_prueba(self):
        self.hacer(self.prod, marcar=True)
        self.assertTrue(any("firma" in m for m in VP.validar(self.paq, self.pub_prueba)))

    def test_un_paquete_de_prueba_jamas_pasa_la_puerta_de_produccion(self):
        self.hacer(self.prueba, marcar=True)
        motivos = V.validar(self.paq, self.pub_prod, (2026, 2027))
        self.assertTrue(any("COMPLETO" in m for m in motivos))
        self.assertTrue(any("firma" in m for m in motivos))

    def test_un_paquete_de_produccion_no_pasa_como_prueba(self):
        self.hacer(self.prod, marcar=False)
        self.assertTrue(any("PRUEBA_CONTROLADA" in m for m in VP.validar(self.paq, self.pub_prod)))

    def test_marcar_conserva_el_resto_del_manifiesto(self):
        self.hacer(self.prueba, marcar=True)
        m = json.load(open(os.path.join(self.paq, "manifest.json"), encoding="utf-8"))
        self.assertEqual("PRUEBA_CONTROLADA", m["estado"])
        self.assertEqual(10, m["secuencia"])
        self.assertEqual("calendario.json", m["archivos"][0]["nombre"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
