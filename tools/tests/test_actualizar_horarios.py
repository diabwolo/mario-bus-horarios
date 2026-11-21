"""Pruebas del actualizador de horarios v3 (tools/actualizar_horarios.py, firma_manifiesto.py, validar_gtfs.py). Ejecutar:
    python tools/tests/test_actualizar_horarios.py
Usan ZIPs GTFS sintéticos, claves de prueba y un generador falso (no tocan la app, ni Internet, ni tools/publicar_horarios real).
La última clase usa los feeds REALES de tools/gtfs (se omite si faltan)."""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(AQUI)
sys.path.insert(0, TOOLS)
import actualizar_horarios as A  # noqa: E402
import firma_manifiesto as F  # noqa: E402
import validar_gtfs as V  # noqa: E402

HOY = datetime.date(2026, 11, 18)
ASSET = os.path.join(TOOLS, "..", "app", "src", "main", "assets", "moncloa_islas.json")


def leer_bytes(ruta):
    with open(ruta, "rb") as f:
        return f.read()


def feed_csv(version="20261027", fin="20271128", exc="20261127", trips=10, huerfanos=False, hora_mala=False,
             rutas=("611",), fecha_mala=False, retrocede=False):
    """Archivos de un GTFS sintético pero COHERENTE (rutas, paradas, expediciones con 2 paradas y horas válidas)."""
    r = [f"R{i},{n}" for i, n in enumerate(rutas)]
    t, st = [], []
    for i in range(trips):
        t.append(f"R{i % len(rutas)},S1,T{i}")
        st.append(f"T{i},A,1,10:{i % 50:02d}:00")
        if hora_mala and i == 0:
            st.append(f"T{i},B,2,25:99:99")
        elif retrocede and i == 0:
            st.append(f"T{i},B,2,09:{i % 50:02d}:00")
        else:
            st.append(f"T{i},B,2,11:{i % 50:02d}:00")
    if huerfanos:
        st.append("TX,A,1,10:00:00")
        st.append("T0,ZZ,3,12:00:00")
    inicio = "20261100" if fecha_mala else "20260722"
    return {
        "feed_info": f"feed_publisher_name,feed_version\nCRTM,{version}\n",
        "routes": "route_id,route_short_name\n" + "\n".join(r) + "\n",
        "trips": "route_id,service_id,trip_id\n" + "\n".join(t) + "\n",
        "stop_times": "trip_id,stop_id,stop_sequence,departure_time\n" + "\n".join(st) + "\n",
        "calendar": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                    f"S1,1,1,1,1,1,1,1,{inicio},{fin}\n",
        "calendar_dates": f"service_id,date,exception_type\nS1,{exc},1\n",
        "stops": "stop_id,stop_lat,stop_lon\nA,40.4,-3.7\nB,40.5,-3.8\n",
        "agency": "agency_id,agency_name\n1,Test\n",
    }


def escribir_feed(carpeta, **k):
    os.makedirs(carpeta, exist_ok=True)
    for nombre, txt in feed_csv(**k).items():
        with open(os.path.join(carpeta, nombre + ".txt"), "w", encoding="utf-8") as f:
            f.write(txt)
    return carpeta


def zip_gtfs(ruta, omitir=(), truncar=False, **k):
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, txt in feed_csv(**k).items():
            if nombre not in omitir:
                z.writestr(nombre + ".txt", txt)
    if truncar:
        datos = leer_bytes(ruta)
        with open(ruta, "wb") as f:
            f.write(datos[: len(datos) // 2])
    return ruta


def generador(parcial=False, falla=False, vacio=False, antiguas=None, lineas=("611", "612", "613", "614")):
    def gen(carpeta_feed, carpeta_antigua, carpeta_salida, hoy, dias):
        if falla:
            raise A.Rechazo("el generador falla")
        ant = antiguas if antiguas is not None else (["611", "612"] if parcial else [])
        for nombre in A.DATASETS:
            datos = {"marca": nombre, "x": [1]}
            if nombre == "moncloa_islas.json":
                datos.update({"deps": {} if vacio else {"a": 1}, "islas": {"1": {"1": [{"linea": l} for l in lineas]}}})
            with open(os.path.join(carpeta_salida, nombre), "w", encoding="utf-8") as f:
                json.dump(datos, f)
        ds = {n: {"completo": True, "lineas_con_datos_antiguos": []} for n in A.DATASETS}
        ds["salidas.json"] = {"completo": not ant, "lineas_con_datos_antiguos": ant}
        return {"datasets": ds, "version_antigua": "20260723"}
    return gen


class Apagon(BaseException):
    """Simula que el ordenador se apaga: no lo captura ningún `except Exception` ni se ejecuta limpieza alguna."""


def apaga_en(hito):
    def gancho(nombre):
        if nombre == hito:
            raise Apagon()
    return gancho


def ejecutar_main(salida, claves, args, evento=None, origen=None):
    """Ejecuta A.main() como lo haría el workflow: con el EVENTO real de GitHub y el ORIGEN en variables de entorno.
    Devuelve (código de salida, texto impreso)."""
    import contextlib
    import io
    entorno, argv = dict(os.environ), sys.argv
    try:
        for k in ("MARIOBUS_EVENTO", "MARIOBUS_ORIGEN"):
            os.environ.pop(k, None)
        if evento:
            os.environ["MARIOBUS_EVENTO"] = evento
        if origen:
            os.environ["MARIOBUS_ORIGEN"] = origen
        sys.argv = ["actualizar_horarios.py", "--salida", salida, "--claves", claves] + list(args)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            codigo = A.main()
        return codigo, out.getvalue()
    finally:
        sys.argv = argv
        os.environ.clear()
        os.environ.update(entorno)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir_claves = tempfile.mkdtemp(prefix="claves_")
        _, cls.publica = F.generar_claves(cls.dir_claves)
        cls.otras = tempfile.mkdtemp(prefix="otras_claves_")
        _, cls.publica_otra = F.generar_claves(cls.otras)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir_claves, ignore_errors=True)
        shutil.rmtree(cls.otras, ignore_errors=True)

    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="tah_")
        self.salida = os.path.join(self.t, "salida")
        self.antiguo = escribir_feed(os.path.join(self.t, "antiguo"), version="20260723")
        A.Hitos.gancho = None

    def tearDown(self):
        A.Hitos.gancho = None
        shutil.rmtree(self.t, ignore_errors=True)

    def zip(self, nombre, **k):
        return zip_gtfs(os.path.join(self.t, nombre), **k)

    def proc(self, z, gen=None):
        return A.procesar_zip(z, self.salida, self.antiguo, gen or generador(), HOY, self.dir_claves)

    def manifest(self, sub):
        with open(os.path.join(self.salida, sub, "manifest.json"), encoding="utf-8") as f:
            return json.load(f)

    def estado(self):
        with open(os.path.join(self.salida, "estado.json"), encoding="utf-8") as f:
            return json.load(f)

    def huella_publicable(self):
        d = os.path.join(self.salida, "publicable")
        if not os.path.isdir(d):
            return None
        return {n: leer_bytes(os.path.join(d, n)) for n in sorted(os.listdir(d))}


class TestParcialNuncaCompleto(Base):
    def test_completo_va_a_publicable_firmado(self):
        r = self.proc(self.zip("n.zip"))
        self.assertEqual(r["estado"], "COMPLETO", r)
        m = self.manifest("publicable")
        self.assertEqual(m["estado"], "COMPLETO")
        self.assertFalse(m["parcial"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.salida, "publicable"))),
                         sorted(list(A.DATASETS) + ["manifest.json", "manifest.sig"]))
        A.verificar_paquete(os.path.join(self.salida, "publicable"), self.publica)

    def test_parcial_va_a_pendiente_y_no_a_publicable(self):
        r = self.proc(self.zip("n.zip"), generador(parcial=True))
        self.assertEqual(r["estado"], "PARCIAL")
        self.assertEqual(r["datasets_incompletos"], ["salidas.json"])
        self.assertFalse(os.path.isdir(os.path.join(self.salida, "publicable")))
        m = self.manifest(os.path.join("pendiente_revision", "20261027"))
        self.assertEqual(m["estado"], "PARCIAL_REQUIERE_REVISION")
        self.assertTrue(m["parcial"] and m["revision_manual_necesaria"])
        self.assertFalse(m["publicable_automaticamente"])
        self.assertNotIn("ultima_version_publicable", self.estado())

    def test_parcial_no_toca_publicable_existente(self):
        self.proc(self.zip("n1.zip", version="20261027"))
        antes = self.huella_publicable()
        r = self.proc(self.zip("n2.zip", version="20261201"), generador(parcial=True))
        self.assertEqual(r["estado"], "PARCIAL")
        self.assertEqual(self.huella_publicable(), antes)
        self.assertEqual(self.estado()["ultima_version_publicable"], "20261027")

    def test_aprobar_parcial_exige_bandera_resigna_y_sigue_parcial(self):
        self.proc(self.zip("n.zip"), generador(parcial=True))
        base = ["x", "--aprobar-parcial", "20261027", "--salida", self.salida, "--claves", self.dir_claves]
        sys.argv = list(base)
        self.assertEqual(A.main(), 1)  # sin --si-estoy-seguro no hace nada
        self.assertFalse(os.path.isdir(os.path.join(self.salida, "publicable")))
        sys.argv = base + ["--si-estoy-seguro"]
        self.assertEqual(A.main(), 0)
        m = self.manifest("publicable")
        self.assertEqual(m["estado"], "PARCIAL_APROBADO_MANUALMENTE")
        self.assertTrue(m["parcial"])
        self.assertFalse(m["publicable_automaticamente"])
        A.verificar_paquete(os.path.join(self.salida, "publicable"), self.publica)  # la firma nueva es válida
        with self.assertRaises(A.Rechazo):  # con otra clave no
            A.verificar_paquete(os.path.join(self.salida, "publicable"), self.publica_otra)

    def test_aprobar_version_inexistente(self):
        with self.assertRaises(A.Rechazo):
            A.aprobar_parcial("99999999", self.salida, self.dir_claves)

    def test_umbral_de_lineas_parciales(self):
        claves4 = ("611", "612", "613", "614")
        r = A.procesar_zip(self.zip("n.zip", rutas=claves4), self.salida, self.antiguo, generador(antiguas=["611", "612", "613"]), HOY, self.dir_claves, lineas_clave=claves4)
        self.assertEqual(r["estado"], "PARCIAL")  # 3 de 4 = 75 %: se admite
        shutil.rmtree(self.salida)
        r = A.procesar_zip(self.zip("n2.zip", rutas=claves4), self.salida, self.antiguo, generador(antiguas=list(claves4)), HOY, self.dir_claves, lineas_clave=claves4)
        self.assertEqual(r["estado"], "RECHAZADO")


class TestUltimaVersionValida(Base):
    def _corrupto(self):
        ruta = os.path.join(self.t, "co.zip")
        with open(ruta, "wb") as f:
            f.write(b"esto no es un zip, es basura " * 50)
        return ruta

    def test_rechazos_dejan_publicable_y_estado_intactos(self):
        self.proc(self.zip("ok.zip", version="20261027"))
        antes, est = self.huella_publicable(), self.estado()
        casos = {
            "corrupto": self._corrupto,
            "truncado": lambda: self.zip("tr.zip", version="20261101", truncar=True),
            "falta_archivo": lambda: self.zip("fa.zip", version="20261102", omitir=("stop_times",)),
            "mas_vieja": lambda: self.zip("mo.zip", version="20260101"),
            "calendario_corto": lambda: self.zip("cc.zip", version="20261103", fin="20261120"),
            "caida_expediciones": lambda: self.zip("ce.zip", version="20261104", trips=1),
        }
        for nombre, hacer in casos.items():
            with self.subTest(nombre):
                r = self.proc(hacer())
                self.assertEqual(r["estado"], "RECHAZADO", r)
                self.assertEqual(self.huella_publicable(), antes)
                self.assertEqual(self.estado(), est)
        self.assertEqual([n for n in os.listdir(self.salida) if n.startswith(("paquete_", "aprobado_"))], [])

    def test_fallo_del_generador_no_cambia_nada(self):
        self.proc(self.zip("ok.zip"))
        antes = self.huella_publicable()
        r = self.proc(self.zip("n.zip", version="20261201"), generador(falla=True))
        self.assertEqual(r["estado"], "RECHAZADO")
        self.assertEqual(self.huella_publicable(), antes)
        self.assertEqual([n for n in os.listdir(self.salida) if n.startswith("paquete_")], [])

    def test_paquete_sin_salidas_se_rechaza(self):
        self.proc(self.zip("ok.zip"))
        antes = self.huella_publicable()
        r = self.proc(self.zip("n.zip", version="20261201"), generador(vacio=True))
        self.assertEqual(r["estado"], "RECHAZADO")
        self.assertEqual(self.huella_publicable(), antes)

    def test_sin_clave_privada_no_se_publica_nada(self):
        r = A.procesar_zip(self.zip("n.zip"), self.salida, self.antiguo, generador(), HOY, os.path.join(self.t, "sin_claves"))
        self.assertEqual(r["estado"], "RECHAZADO")
        self.assertFalse(os.path.isdir(os.path.join(self.salida, "publicable")))

    def test_historial_conserva_la_anterior(self):
        self.proc(self.zip("a1.zip", version="20261027"))
        self.proc(self.zip("a2.zip", version="20261201"))
        self.assertEqual(self.manifest("publicable")["version_gtfs"], "20261201")
        self.assertEqual(self.manifest(os.path.join("historial", "20261027"))["version_gtfs"], "20261027")

    def test_mismo_zip_dos_veces(self):
        z = self.zip("n.zip")
        self.assertEqual(self.proc(z)["estado"], "COMPLETO")
        self.assertEqual(self.proc(z)["estado"], "YA_PROCESADO")

    def test_secuencia_del_manifiesto_no_baja(self):
        self.proc(self.zip("a1.zip", version="20261027"))
        s1 = self.manifest("publicable")["secuencia"]
        self.proc(self.zip("a2.zip", version="20261201"))
        self.assertGreaterEqual(self.manifest("publicable")["secuencia"], s1)

    def test_huellas_y_atribucion(self):
        self.proc(self.zip("n.zip"))
        m = self.manifest("publicable")
        self.assertEqual({a["nombre"] for a in m["archivos"]}, set(A.DATASETS))
        for a in m["archivos"]:
            self.assertEqual(A.sha256_archivo(os.path.join(self.salida, "publicable", a["nombre"])), a["sha256"])
        self.assertIn("Powered by CRTM", m["atribucion"])
        self.assertEqual(m["esquema"], 2)
        self.assertEqual(m["ventana_comprobada"]["dias"], 28)
        self.assertEqual(m["ventana_comprobada"]["desde"], "2026-11-18")


class TestFirma(Base):
    def test_firma_valida_y_manipulada(self):
        self.proc(self.zip("n.zip"))
        pub = os.path.join(self.salida, "publicable")
        A.verificar_paquete(pub, self.publica)
        ruta = os.path.join(pub, "manifest.json")
        original = leer_bytes(ruta)
        with open(ruta, "wb") as f:
            f.write(original.replace(b'"COMPLETO"', b'"COMPLETO "', 1))
        with self.assertRaises(A.Rechazo):
            A.verificar_paquete(pub, self.publica)
        with open(ruta, "wb") as f:
            f.write(original)
        A.verificar_paquete(pub, self.publica)

    def test_archivo_de_datos_alterado_se_detecta_aunque_la_firma_sea_buena(self):
        self.proc(self.zip("n.zip"))
        pub = os.path.join(self.salida, "publicable")
        with open(os.path.join(pub, "salidas.json"), "a") as f:
            f.write(" ")
        with self.assertRaises(A.Rechazo):
            A.verificar_paquete(pub, self.publica)

    def test_firma_borrada_o_basura(self):
        self.proc(self.zip("n.zip"))
        pub = os.path.join(self.salida, "publicable")
        with open(os.path.join(pub, "manifest.sig"), "w") as f:
            f.write("AAAA\n")
        with self.assertRaises(A.Rechazo):
            A.verificar_paquete(pub, self.publica)
        os.remove(os.path.join(pub, "manifest.sig"))
        with self.assertRaises(A.Rechazo):
            A.verificar_paquete(pub, self.publica)

    def test_clave_no_se_sobrescribe_y_bytes_firmados(self):
        with self.assertRaises(F.ErrorFirma):
            F.generar_claves(self.dir_claves)
        priv = F.cargar_privada(self.dir_claves)
        sig = F.firmar_bytes(b"hola", priv)
        self.assertTrue(F.verificar_bytes(b"hola", sig, self.publica))
        self.assertFalse(F.verificar_bytes(b"hola!", sig, self.publica))
        self.assertFalse(F.verificar_bytes(b"hola", sig, self.publica_otra))
        self.assertFalse(F.verificar_bytes(b"hola", "no es base64", self.publica))
        self.assertFalse(F.verificar_bytes(b"hola", sig, "clave basura"))


class TestApagones(Base):
    """Si el ordenador se apaga en cualquier punto del cambio, publicable/ no se pierde: recuperar() la deja válida."""

    def _con_publicable(self):
        self.assertEqual(self.proc(self.zip("a1.zip", version="20261027"))["estado"], "COMPLETO")
        return A.Carpetas(self.salida)

    def _paquete_nuevo(self):
        """Un paquete nuevo válido y firmado, como el que deja procesar_zip antes de instalarlo."""
        tmp = tempfile.mkdtemp(prefix="paquete_", dir=self.salida)
        generador()("", "", tmp, HOY, 28)
        nuevo = {"version": "20261201", "excepciones_hasta": "", "expediciones": 1}
        ds = {n: {"completo": True, "lineas_con_datos_antiguos": []} for n in A.DATASETS}
        A.escribir_manifiesto(tmp, nuevo, "zipsha", ds, "", [], {}, HOY, 28, self.dir_claves)
        return tmp

    def test_apagon_en_cada_hito(self):
        for hito in ("tras_diario", "tras_mover_anterior", "tras_instalar_nuevo"):
            with self.subTest(hito):
                shutil.rmtree(self.salida, ignore_errors=True)
                c = self._con_publicable()
                tmp = self._paquete_nuevo()
                A.Hitos.gancho = apaga_en(hito)
                with self.assertRaises(Apagon):
                    A.instalar_publicable(c, tmp, self.publica)
                A.Hitos.gancho = None
                # ---- arranque siguiente ----
                A.recuperar(A.Carpetas(self.salida), self.publica)
                pub = os.path.join(self.salida, "publicable")
                self.assertTrue(os.path.isdir(pub), f"tras el apagón en {hito} FALTA publicable/")
                m = A.verificar_paquete(pub, self.publica)
                self.assertIn(m["version_gtfs"], ("20261027", "20261201"))
                self.assertFalse(os.path.exists(c.diario))
                self.assertEqual([n for n in os.listdir(self.salida) if n.startswith("paquete_")], [])
                self.assertEqual(self.proc(self.zip(f"s_{hito}.zip", version="20270101"))["estado"], "COMPLETO")  # se puede seguir

    def test_apagon_tras_mover_anterior_termina_la_instalacion(self):
        c = self._con_publicable()
        tmp = self._paquete_nuevo()
        A.Hitos.gancho = apaga_en("tras_mover_anterior")
        with self.assertRaises(Apagon):
            A.instalar_publicable(c, tmp, self.publica)
        A.Hitos.gancho = None
        self.assertFalse(os.path.isdir(c.publicable))  # justo el hueco peligroso
        self.assertIn("terminó la instalación", A.recuperar(c, self.publica))
        self.assertEqual(self.manifest("publicable")["version_gtfs"], "20261201")

    def test_apagon_con_paquete_nuevo_dañado_vuelve_a_la_anterior(self):
        c = self._con_publicable()
        tmp = self._paquete_nuevo()
        A.Hitos.gancho = apaga_en("tras_mover_anterior")
        with self.assertRaises(Apagon):
            A.instalar_publicable(c, tmp, self.publica)
        A.Hitos.gancho = None
        with open(os.path.join(tmp, "salidas.json"), "a") as f:
            f.write("basura")  # el paquete nuevo quedó a medias
        self.assertIn("restauró la anterior", A.recuperar(c, self.publica))
        self.assertEqual(self.manifest("publicable")["version_gtfs"], "20261027")

    def test_diario_ilegible_no_rompe_nada(self):
        c = self._con_publicable()
        antes = self.huella_publicable()
        with open(c.diario, "w") as f:
            f.write("{esto no es json")
        A.recuperar(c, self.publica)
        self.assertFalse(os.path.exists(c.diario))
        self.assertEqual(self.huella_publicable(), antes)

    def test_procesar_recupera_solo_al_empezar(self):
        c = self._con_publicable()
        tmp = self._paquete_nuevo()
        A.Hitos.gancho = apaga_en("tras_mover_anterior")
        with self.assertRaises(Apagon):
            A.instalar_publicable(c, tmp, self.publica)
        A.Hitos.gancho = None
        r = self.proc(self.zip("b.zip", version="20270101"))  # sin llamar a recuperar() a mano
        self.assertEqual(r["estado"], "COMPLETO", r)


class TestValidacionGtfs(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix="vg_")

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def feed(self, nombre="nuevo", **k):
        return escribir_feed(os.path.join(self.t, nombre), **k)

    def val(self, g, **k):
        return V.validar_gtfs(g, hoy=HOY, **k)

    def test_feed_sano(self):
        e, a, s = self.val(self.feed())
        self.assertEqual(e, [], e)
        self.assertEqual(s["lineas_total"], 1)
        self.assertEqual(s["ventana"], ["2026-11-18", "2026-12-15"])

    def test_referencias_rotas(self):
        e, a, s = self.val(self.feed(huerfanos=True))
        self.assertTrue(any("stop_times" in x for x in e), e)

    def test_hora_invalida(self):
        e, a, s = self.val(self.feed(hora_mala=True, trips=1))
        self.assertTrue(any("horas" in x for x in e), e)

    def test_hora_que_retrocede(self):
        e, a, s = self.val(self.feed(retrocede=True, trips=1))
        self.assertTrue(any("desordenadas" in x for x in e), e)

    def test_fecha_de_calendario_invalida(self):
        e, a, s = self.val(self.feed(fecha_mala=True))
        self.assertTrue(any("calendar.txt" in x for x in e), e)

    def test_calendario_que_acaba_pronto(self):
        e, a, s = self.val(self.feed(fin="20261125"))
        self.assertTrue(any("menos de 14 días" in x for x in e), e)

    def test_dia_sin_ningun_servicio_en_la_red(self):
        g = self.feed()
        with open(os.path.join(g, "calendar.txt"), "w") as f:  # solo circula los lunes
            f.write("service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nS1,1,0,0,0,0,0,0,20260722,20271128\n")
        e, a, s = self.val(g)
        self.assertTrue(any("no circula ninguna expedición" in x for x in e), e)

    def test_excepcion_tipo_2_quita_el_servicio_ese_dia(self):
        g = self.feed()
        dia = (HOY + datetime.timedelta(days=3)).strftime("%Y%m%d")
        with open(os.path.join(g, "calendar_dates.txt"), "w") as f:
            f.write(f"service_id,date,exception_type\nS1,{dia},2\n")
        e, a, s = self.val(g)
        self.assertTrue(any("no circula ninguna expedición" in x for x in e), e)

    def test_lineas_clave_y_paradas_clave(self):
        e, a, s = self.val(self.feed(), lineas_clave=("611", "999"), paradas_clave=("A", "NOEXISTE"))
        self.assertTrue(any("999" in x for x in e), e)
        self.assertTrue(any("NOEXISTE" in x for x in e), e)

    def test_lineas_que_se_pierden_frente_al_anterior(self):
        ant = self.feed("ant", rutas=("611", "612", "613", "614"), trips=8)
        nuevo = self.feed("nue", rutas=("611", "612", "613", "614"), trips=2)  # trips 0,1 → solo 611 y 612 circulan
        e, a, s = self.val(nuevo, anterior=ant)
        self.assertEqual(s["lineas_perdidas_frente_al_anterior"], ["613", "614"])
        self.assertTrue(any("613" in x for x in a), a)
        self.assertTrue(any("sospechoso" in x for x in e), e)  # 2 de 4 = 50 % > 25 %

    def test_servicio_efectivo_cuenta_expediciones_por_dia(self):
        efectivo = V.servicio_por_linea_y_dia(self.feed(trips=10), V.ventana(HOY, 3))
        self.assertEqual(list(efectivo["611"].values()), [10, 10, 10])

    def test_ventana_dinamica(self):
        self.assertEqual(V.ventana(datetime.date(2027, 3, 1), 2), [datetime.date(2027, 3, 1), datetime.date(2027, 3, 2)])
        self.assertEqual(V.ventana(None, 1)[0], datetime.date.today())


class TestEstricto(Base):
    """Modo del servidor: nunca se publica un feed al que le falten líneas, días de servicio o expediciones que había antes."""

    def setUp(self):
        super().setUp()
        self.base_feed = escribir_feed(os.path.join(self.t, "base"), version="20260723", rutas=("611", "612"), trips=10)
        A.crear_base(self.base_feed, self.salida, HOY)

    def estricto(self, nombre, gen=None, **k):
        z = self.zip(nombre, **k)
        return A.procesar_zip(z, self.salida, os.path.join(self.t, "no_existe"), gen or generador(), HOY, self.dir_claves, estricto=True)

    def test_feed_igual_de_completo_se_publica_y_pasa_a_ser_la_referencia(self):
        r = self.estricto("n.zip", rutas=("611", "612"), trips=10)
        self.assertEqual(r["estado"], "COMPLETO", r)
        self.assertEqual(A.leer_json(A.ruta_base(self.salida))["version"], "20261027")

    def test_desaparece_una_linea_se_rechaza_y_no_se_publica_nada(self):
        r = self.estricto("n.zip", rutas=("611",), trips=10)
        self.assertEqual(r["estado"], "RECHAZADO", r)
        self.assertIn("612: la línea desaparece", r["mensaje"])
        self.assertIsNone(self.huella_publicable())
        self.assertEqual(os.listdir(os.path.join(self.salida, "pendiente_revision")), [])  # tampoco queda «pendiente»

    def test_un_dia_de_servicio_que_faltaba_se_rechaza(self):
        z = os.path.join(self.t, "d.zip")
        with zipfile.ZipFile(z, "w") as zz:
            for n, txt in feed_csv(rutas=("611", "612"), trips=10).items():
                if n == "calendar":  # sin domingos
                    txt = txt.replace("S1,1,1,1,1,1,1,1,", "S1,1,1,1,1,1,1,0,")
                zz.writestr(n + ".txt", txt)
        r = A.procesar_zip(z, self.salida, os.path.join(self.t, "no_existe"), generador(), HOY, self.dir_claves, estricto=True)
        self.assertEqual(r["estado"], "RECHAZADO", r)
        self.assertTrue("sin servicio los domingo" in r["mensaje"] or "no circula ninguna expedición" in r["mensaje"], r["mensaje"])

    def test_caida_fuerte_de_expediciones_se_rechaza(self):
        r = self.estricto("n.zip", rutas=("611", "612"), trips=3)  # 10 → 3 expediciones
        self.assertEqual(r["estado"], "RECHAZADO", r)

    def test_una_linea_explicada_puede_desaparecer(self):
        ruta = os.path.join(self.t, "explicadas.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"lineas": {"612": "línea suprimida por el Consorcio (comunicado de ejemplo)"}}, f)
        antes = A.EXPLICADAS
        A.EXPLICADAS = ruta
        try:
            r = self.estricto("n.zip", rutas=("611",), trips=10)
        finally:
            A.EXPLICADAS = antes
        self.assertEqual(r["estado"], "COMPLETO", r)

    def test_sin_base_no_se_publica(self):
        os.remove(A.ruta_base(self.salida))
        r = self.estricto("n.zip", rutas=("611", "612"))
        self.assertEqual(r["estado"], "RECHAZADO")
        self.assertIn("crea", r["mensaje"].lower())

    def test_si_el_generador_deja_lineas_con_datos_antiguos_se_rechaza_no_es_parcial(self):
        r = self.estricto("n.zip", gen=generador(parcial=True), rutas=("611", "612"))
        self.assertEqual(r["estado"], "RECHAZADO", r)
        self.assertEqual(os.listdir(os.path.join(self.salida, "pendiente_revision")), [])

    def test_nunca_se_aprueban_parciales(self):
        os.environ["MARIOBUS_SIN_PARCIALES"] = "1"
        try:
            with self.assertRaises(A.Rechazo):
                A.aprobar_parcial("20261027", self.salida, self.dir_claves)
        finally:
            del os.environ["MARIOBUS_SIN_PARCIALES"]

    def test_rechazo_conserva_la_ultima_version_publicada(self):
        self.assertEqual(self.estricto("a.zip", version="20261027", rutas=("611", "612"))["estado"], "COMPLETO")
        antes = self.huella_publicable()
        self.assertEqual(self.estricto("b.zip", version="20261201", rutas=("611",))["estado"], "RECHAZADO")
        self.assertEqual(self.huella_publicable(), antes)

    def test_comparar_con_base_detecta_cada_tipo(self):
        base = {"lineas": {"1": [5, 5, 5, 5, 5, 0, 0], "2": [4] * 7, "3": [10] * 7}}
        nuevo = {"lineas": {"1": [5, 5, 5, 5, 5, 0, 0], "2": [4, 4, 4, 4, 4, 4, 0], "3": [10, 2, 10, 10, 10, 10, 10]}}
        p = V.comparar_con_base(nuevo, base)
        self.assertEqual(len(p), 2)
        self.assertTrue(any(x.startswith("2: sin servicio los domingo") for x in p))
        self.assertTrue(any(x.startswith("3: caen más de un 50 %") for x in p))
        self.assertEqual(V.comparar_con_base(base, base), [])


class TestPruebaControlada(Base):
    """Actualización de prueba para demostrar servidor → móvil: marcada como prueba, firmada y con datos REALES ya incluidos."""

    def datos(self):
        d = os.path.join(self.t, "datos")
        os.makedirs(d)
        for n in A.DATASETS:
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                json.dump({"version": "20261027", "marca": n, "deps": [1], "islas": {"1": {}}}, f)
        return d

    def test_se_publica_marcada_y_firmada(self):
        m = A.publicar_prueba_controlada(self.salida, self.dir_claves, self.datos(), "prueba")
        self.assertEqual(m["estado"], "PRUEBA_CONTROLADA")
        self.assertTrue(m["prueba_controlada"])
        self.assertIn("No es un feed nuevo del Consorcio", m["aviso"])
        A.verificar_paquete(os.path.join(self.salida, "publicable"), self.publica)
        self.assertTrue(self.estado()["publicable_es_prueba"])

    def test_un_paquete_real_posterior_quita_la_marca(self):
        A.publicar_prueba_controlada(self.salida, self.dir_claves, self.datos(), "prueba")
        r = self.proc(self.zip("n.zip", version="20261201"))  # un feed REAL más nuevo que el de la prueba
        self.assertEqual(r["estado"], "COMPLETO", r)
        self.assertNotIn("publicable_es_prueba", self.estado())

    def test_la_prueba_guarda_la_prueba_anterior_en_historial(self):
        # (una prueba solo puede ir sobre otra prueba o sobre nada: nunca sobre una publicación oficial)
        datos = self.datos()
        A.publicar_prueba_controlada(self.salida, self.dir_claves, datos, "prueba 1")
        A.publicar_prueba_controlada(self.salida, self.dir_claves, datos, "prueba 2")
        self.assertEqual(len(os.listdir(os.path.join(self.salida, "historial"))), 1)

    def test_el_manifiesto_dice_que_no_es_publicable_automaticamente(self):
        m = A.publicar_prueba_controlada(self.salida, self.dir_claves, self.datos())
        self.assertFalse(m["publicable_automaticamente"])


class TestRestaurar(Base):
    def dos_versiones(self):
        self.assertEqual(self.proc(self.zip("a.zip", version="20261027"))["estado"], "COMPLETO")
        self.assertEqual(self.proc(self.zip("b.zip", version="20261201"))["estado"], "COMPLETO")

    def test_restaurar_la_anterior_con_secuencia_nueva_y_firma_valida(self):
        self.dos_versiones()
        seq_antes = self.manifest("publicable")["secuencia"]
        m = A.restaurar_version("20261027", self.salida, self.dir_claves)
        self.assertEqual(m["version_gtfs"], "20261027")
        self.assertGreater(m["secuencia"], seq_antes)  # la app solo acepta secuencias mayores
        self.assertEqual(m["restaurada_de"], "20261027")
        A.verificar_paquete(os.path.join(self.salida, "publicable"), self.publica)
        self.assertEqual(self.estado()["ultima_version_publicable"], "20261027")
        self.assertTrue(os.path.isdir(os.path.join(self.salida, "historial", "20261201")))  # la que se retira queda guardada

    def test_restaurar_version_inexistente(self):
        self.dos_versiones()
        antes = self.huella_publicable()
        with self.assertRaises(A.Rechazo):
            A.restaurar_version("19990101", self.salida, self.dir_claves)
        self.assertEqual(self.huella_publicable(), antes)

    def test_no_restaura_una_version_dañada(self):
        self.dos_versiones()
        with open(os.path.join(self.salida, "historial", "20261027", "salidas.json"), "a") as f:
            f.write("basura")
        antes = self.huella_publicable()
        with self.assertRaises(A.Rechazo):
            A.restaurar_version("20261027", self.salida, self.dir_claves)
        self.assertEqual(self.huella_publicable(), antes)


class TestVersiones(Base):
    """Versión del feed (GTFS) ≠ secuencia de publicación ≠ tipo de paquete (OFICIAL / PRUEBA_CONTROLADA)."""

    def setUp(self):
        super().setUp()
        self.base_feed = escribir_feed(os.path.join(self.t, "base"), version="20260723", rutas=("611", "612"), trips=10)
        A.crear_base(self.base_feed, self.salida, HOY)

    def est(self, nombre, gen=None, **k):
        k.setdefault("rutas", ("611", "612"))
        z = self.zip(nombre, **k)
        return A.procesar_zip(z, self.salida, os.path.join(self.t, "no_existe"), gen or generador(), HOY, self.dir_claves, estricto=True)

    def prueba(self):
        d = os.path.join(self.t, "datos")
        os.makedirs(d, exist_ok=True)
        for n in A.DATASETS:
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                json.dump({"version": "20261027", "marca": n, "deps": [1], "islas": {"1": {}}}, f)
        return A.publicar_prueba_controlada(self.salida, self.dir_claves, d, "prueba")

    def test_una_prueba_controlada_no_bloquea_el_feed_oficial_con_la_misma_version(self):
        self.prueba()  # publicada con versión 20261027
        r = self.est("oficial.zip", version="20261027", trips=10)  # el feed oficial trae ese mismo número
        self.assertEqual(r["estado"], "COMPLETO", r)
        self.assertEqual(self.manifest("publicable")["estado"], "COMPLETO")
        self.assertEqual(self.estado()["tipo_publicable"], "OFICIAL")

    def test_feed_corregido_con_el_mismo_numero_de_version_se_publica(self):
        self.assertEqual(self.est("a.zip", version="20261027", trips=10)["estado"], "COMPLETO")
        r = self.est("b.zip", version="20261027", trips=11)  # archivo DISTINTO, mismo número, completo
        self.assertEqual(r["estado"], "COMPLETO", r)
        self.assertTrue(any("feed corregido" in a for a in r["avisos"]))

    def test_el_mismo_archivo_no_se_vuelve_a_publicar(self):
        z = self.zip("a.zip", version="20261027", rutas=("611", "612"))
        args = (self.salida, os.path.join(self.t, "no_existe"), generador(), HOY, self.dir_claves)
        self.assertEqual(A.procesar_zip(z, *args, estricto=True)["estado"], "COMPLETO")
        self.assertEqual(A.procesar_zip(z, *args, estricto=True)["estado"], "YA_PROCESADO")

    def test_una_version_anterior_a_la_oficial_se_rechaza(self):
        self.assertEqual(self.est("a.zip", version="20261201")["estado"], "COMPLETO")
        antes = self.huella_publicable()
        r = self.est("b.zip", version="20261027", trips=11)
        self.assertEqual(r["estado"], "RECHAZADO", r)
        self.assertIn("no se vuelve atrás", r["mensaje"])
        self.assertEqual(self.huella_publicable(), antes)

    def test_cambiar_el_numero_de_version_no_hace_valido_un_feed_incompleto(self):
        self.prueba()
        antes = self.huella_publicable()
        for v in ("20261027", "20261028", "20270101"):  # incompleto (falta la 612) con cualquier número de versión
            r = self.est(f"i{v}.zip", version=v, rutas=("611",))
            self.assertEqual(r["estado"], "RECHAZADO", (v, r))
            self.assertIn("612", r["mensaje"])
        self.assertEqual(self.huella_publicable(), antes)  # la publicación válida (aquí la prueba) no se tocó
        self.assertEqual(self.manifest("publicable")["estado"], "PRUEBA_CONTROLADA")

    def test_la_secuencia_siempre_crece_aunque_dos_publicaciones_ocurran_en_el_mismo_segundo(self):
        s = [self.prueba()["secuencia"], self.prueba()["secuencia"],
             self.est("a.zip", version="20261027")["estado"] and self.manifest("publicable")["secuencia"]]
        self.assertTrue(s[0] < s[1] < s[2], s)

    def test_no_se_instala_un_paquete_con_secuencia_menor_o_igual(self):
        self.prueba()
        actual = self.manifest("publicable")["secuencia"]
        tmp = os.path.join(self.salida, "paquete_viejo")
        shutil.copytree(os.path.join(self.salida, "publicable"), tmp)
        m = A.leer_json(os.path.join(tmp, "manifest.json"))
        m["secuencia"] = actual - 5
        A.escribir_json_atomico(os.path.join(tmp, "manifest.json"), m)
        carpetas = A.Carpetas(self.salida)
        antes = self.huella_publicable()
        with self.assertRaises(A.Rechazo):
            A.instalar_publicable(carpetas, tmp)
        self.assertEqual(self.huella_publicable(), antes)

    def test_los_paquetes_de_prueba_se_identifican(self):
        self.prueba()
        self.assertEqual(self.estado()["tipo_publicable"], "PRUEBA_CONTROLADA")
        self.assertEqual(self.manifest("publicable")["estado"], "PRUEBA_CONTROLADA")

    def test_se_pueden_restaurar_las_oficiales(self):
        self.assertEqual(self.est("a.zip", version="20261027")["estado"], "COMPLETO")
        self.assertEqual(self.est("b.zip", version="20261201")["estado"], "COMPLETO")
        m = A.restaurar_version("20261027", self.salida, self.dir_claves)  # vuelve a una oficial válida (con secuencia nueva)
        self.assertEqual(m["estado"], "COMPLETO")
        self.assertEqual(self.estado()["tipo_publicable"], "OFICIAL")
        A.verificar_paquete(os.path.join(self.salida, "publicable"), self.publica)


class TestProteccionDelRespaldo(Base):
    """El respaldo externo (cron-job.org) solo puede iniciar el ciclo NORMAL. Quien llama a la API escribe el «origen» (puede mentir):
    manda el EVENTO real de GitHub, y solo un `push` (que exige poder escribir en el repositorio) o el uso local pueden lo extraordinario."""

    EXTRA = [["--forzar"], ["--prueba-controlada", "x"], ["--restaurar", "20261027"],
             ["--aprobar-parcial", "20261027", "--si-estoy-seguro"], ["--crear-base", "x"]]

    def test_el_ciclo_normal_esta_permitido_desde_cualquier_origen(self):
        for ev in (None, "schedule", "workflow_dispatch", "push"):
            for og in (None, "respaldo-externo", "manual", "push"):
                self.assertIsNone(A.acciones_no_permitidas(["--salida", "estado"], ev, og), (ev, og))

    def test_el_respaldo_externo_no_puede_nada_extraordinario_con_ningun_evento(self):
        for extra in self.EXTRA:
            for ev in (None, "schedule", "workflow_dispatch", "push"):
                self.assertIsNotNone(A.acciones_no_permitidas(extra, ev, "respaldo-externo"), (extra, ev))

    def test_api_y_cron_no_pueden_aunque_mientan_en_el_origen(self):
        for extra in self.EXTRA:
            for ev in ("schedule", "workflow_dispatch"):
                for og in (None, "manual", "push", "respaldo-externo"):
                    self.assertIsNotNone(A.acciones_no_permitidas(extra, ev, og), (extra, ev, og))

    def test_solo_un_push_o_el_uso_local_pueden_lo_extraordinario(self):
        for extra in self.EXTRA:
            self.assertIsNone(A.acciones_no_permitidas(extra, "push", "push"))
            self.assertIsNone(A.acciones_no_permitidas(extra, None, None))

    def test_de_extremo_a_extremo_el_respaldo_no_puede_restaurar_ni_publicar_pruebas_ni_forzar(self):
        self.assertEqual(self.proc(self.zip("a.zip", version="20261027"))["estado"], "COMPLETO")
        self.assertEqual(self.proc(self.zip("b.zip", version="20261201"))["estado"], "COMPLETO")
        antes, hist = self.huella_publicable(), sorted(os.listdir(os.path.join(self.salida, "historial")))
        estado_antes = self.estado()
        pruebas = [["--restaurar", "20261027"], ["--prueba-controlada", os.path.join(self.t, "datos")], ["--forzar"]]
        for args in pruebas:
            for ev, og in (("workflow_dispatch", "respaldo-externo"), ("workflow_dispatch", "manual"), ("schedule", None)):
                codigo, texto = ejecutar_main(self.salida, self.dir_claves, args, ev, og)
                self.assertEqual(codigo, 1, (args, ev, og, texto))
                self.assertIn("acción no permitida", texto)
        self.assertEqual(self.huella_publicable(), antes)  # lo publicado, intacto
        self.assertEqual(sorted(os.listdir(os.path.join(self.salida, "historial"))), hist)
        self.assertEqual(self.estado(), estado_antes)  # ni siquiera se tocó el estado

    def test_control_positivo_un_push_si_puede_restaurar(self):
        self.assertEqual(self.proc(self.zip("a.zip", version="20261027"))["estado"], "COMPLETO")
        self.assertEqual(self.proc(self.zip("b.zip", version="20261201"))["estado"], "COMPLETO")
        codigo, texto = ejecutar_main(self.salida, self.dir_claves, ["--restaurar", "20261027"], "push", "push")
        self.assertEqual(codigo, 0, texto)
        self.assertEqual(self.manifest("publicable")["version_gtfs"], "20261027")


class TestPruebaNoPisaOficial(Base):
    """Una publicación de prueba NUNCA sustituye a una publicación oficial (protección en el código Python)."""

    def datos(self):
        d = os.path.join(self.t, "datos")
        os.makedirs(d, exist_ok=True)
        for n in A.DATASETS:
            with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                json.dump({"version": "20261027", "marca": n, "deps": [1], "islas": {"1": {}}}, f)
        return d

    def test_no_sustituye_una_publicacion_oficial(self):
        self.assertEqual(self.proc(self.zip("a.zip"))["estado"], "COMPLETO")
        antes = self.huella_publicable()
        with self.assertRaises(A.Rechazo) as cm:
            A.publicar_prueba_controlada(self.salida, self.dir_claves, self.datos(), "intento")
        self.assertIn("OFICIAL", str(cm.exception))
        self.assertEqual(self.huella_publicable(), antes)
        self.assertEqual(self.estado()["tipo_publicable"], "OFICIAL")
        self.assertEqual(os.listdir(os.path.join(self.salida, "historial")), [])  # tampoco se movió nada al historial

    def test_tampoco_por_la_linea_de_ordenes_ni_siquiera_desde_un_push(self):
        self.assertEqual(self.proc(self.zip("a.zip"))["estado"], "COMPLETO")
        antes = self.huella_publicable()
        codigo, texto = ejecutar_main(self.salida, self.dir_claves, ["--prueba-controlada", self.datos()], "push", "push")
        self.assertEqual(codigo, 1, texto)
        self.assertIn("OFICIAL", texto)
        self.assertEqual(self.huella_publicable(), antes)

    def test_un_manifiesto_ilegible_tampoco_se_pisa(self):
        self.assertEqual(self.proc(self.zip("a.zip"))["estado"], "COMPLETO")
        with open(os.path.join(self.salida, "publicable", "manifest.json"), "w") as f:
            f.write("basura")
        antes = self.huella_publicable()
        with self.assertRaises(A.Rechazo):
            A.publicar_prueba_controlada(self.salida, self.dir_claves, self.datos(), "intento")
        self.assertEqual(self.huella_publicable(), antes)

    def test_si_se_puede_sobre_nada_y_sobre_otra_prueba(self):
        self.assertEqual(A.publicar_prueba_controlada(self.salida, self.dir_claves, self.datos(), "1")["estado"], "PRUEBA_CONTROLADA")
        self.assertEqual(A.publicar_prueba_controlada(self.salida, self.dir_claves, self.datos(), "2")["estado"], "PRUEBA_CONTROLADA")

    def test_un_feed_oficial_si_sustituye_a_una_prueba(self):
        A.publicar_prueba_controlada(self.salida, self.dir_claves, self.datos(), "1")
        self.assertEqual(self.proc(self.zip("a.zip", version="20261201"))["estado"], "COMPLETO")
        self.assertEqual(self.manifest("publicable")["estado"], "COMPLETO")


class TestClasificacionDeErrores(Base):
    """procesar_zip distingue: rechazo por CONTENIDO (comprobación correcta) frente a error inesperado o de firma (fallo nuestro)."""

    def test_un_feed_malo_es_rechazo_de_contenido_no_inesperado(self):
        r = self.proc(self.zip("n.zip", version="20260101"))
        self.assertEqual(r["estado"], "RECHAZADO")
        self.assertFalse(r.get("inesperado"))

    def test_un_error_inesperado_se_marca_como_tal(self):
        def gen(*a, **k):
            raise ValueError("fallo raro")
        r = A.procesar_zip(self.zip("n.zip"), self.salida, self.antiguo, gen, HOY, self.dir_claves)
        self.assertEqual(r["estado"], "RECHAZADO")
        self.assertTrue(r.get("inesperado"), r)
        self.assertIsNone(self.huella_publicable())

    def test_la_falta_de_clave_es_un_error_nuestro_no_del_feed(self):
        vacia = tempfile.mkdtemp(prefix="sin_clave_")
        self.addCleanup(shutil.rmtree, vacia, True)
        r = A.procesar_zip(self.zip("n.zip"), self.salida, self.antiguo, generador(), HOY, vacia)
        self.assertEqual(r["estado"], "RECHAZADO")
        self.assertTrue(r.get("inesperado"), r)


class TestWorkflowDelRespaldo(unittest.TestCase):
    """El workflow (que no se puede ejecutar aquí) se revisa por su texto: la API no admite acciones y Python recibe el evento real."""

    def setUp(self):
        ruta = os.path.join(TOOLS, "..", ".github", "workflows", "horarios.yml")
        if not os.path.exists(ruta):
            self.skipTest("no hay workflow")
        with open(ruta, encoding="utf-8") as f:
            self.txt = f.read()

    def test_la_llamada_por_api_no_admite_accion_ni_version(self):
        self.assertNotIn("inputs.accion", self.txt)
        self.assertNotIn("inputs.version", self.txt)
        self.assertNotIn("options: [ciclo", self.txt)

    def test_python_recibe_el_evento_real_de_github(self):
        self.assertIn('MARIOBUS_EVENTO="$EVENTO"', self.txt)
        self.assertIn("github.event_name", self.txt)

    def test_las_acciones_extraordinarias_solo_se_leen_en_un_push(self):
        self.assertIn('[ "$EVENTO" = "push" ] && [ -f disparar/accion.txt ]', self.txt)
        self.assertIn("ACCION=ciclo", self.txt)


class TestCicloDiario(Base):
    """El servidor comprueba como mucho una vez al día y NO trabaja si la fuente no cambió."""

    META1 = {"modified": 1793891639000, "size": 76437176}
    META2 = {"modified": 1794000000000, "size": 76500000}
    DIA = 24 * 3600

    def montar(self, rechazo_contenido=False, falla_descarga=False, falla_meta=False, inesperado=False, corte=False):
        self.llamadas = {"meta": 0, "descarga": 0, "proceso": 0}

        def abrir_meta():
            self.llamadas["meta"] += 1
            if falla_meta:
                raise OSError("sin red")
            return self.meta_actual

        def descargar():
            self.llamadas["descarga"] += 1
            if corte:
                raise Apagon()  # el trabajo se corta a mitad de la descarga (tiempo agotado, caída…)
            if falla_descarga:
                raise A.Rechazo("Descarga interrumpida")
            return self.zip(f"d{self.llamadas['descarga']}.zip", version=f"2026{10 + self.llamadas['descarga']:02d}27")

        def procesar(z):
            self.llamadas["proceso"] += 1
            if inesperado:
                return {"estado": "RECHAZADO", "mensaje": "Error inesperado (ValueError): x", "inesperado": True}
            if rechazo_contenido:
                return {"estado": "RECHAZADO", "mensaje": "datos incompletos"}
            return self.proc(z)
        return abrir_meta, descargar, procesar

    def vuelta(self, ahora, **k):
        abrir_meta, descargar, procesar = self.montar_actual
        return A.ciclo_diario(self.salida, procesar, ahora, abrir_meta=abrir_meta, descargar=descargar, **k)

    def setUp(self):
        super().setUp()
        self.meta_actual = self.META1
        self.montar_actual = self.montar()

    def test_primera_vez_descarga_y_procesa(self):
        r = self.vuelta(1000)
        self.assertEqual(r["estado"], "COMPLETO", r)
        self.assertEqual(self.llamadas, {"meta": 1, "descarga": 1, "proceso": 1})
        est = self.estado()
        self.assertEqual(est["fuente_meta"], self.META1)
        self.assertEqual(est["ultima_comprobacion"], 1000)

    def test_sin_cambios_no_descarga_ni_convierte_ni_publica(self):
        self.vuelta(1000)
        antes = self.huella_publicable()
        r = self.vuelta(1000 + self.DIA)
        self.assertEqual(r["estado"], "SIN_CAMBIOS")
        self.assertFalse(r["descargado"])
        self.assertEqual(self.llamadas, {"meta": 2, "descarga": 1, "proceso": 1})  # solo se miró el resumen
        self.assertEqual(self.huella_publicable(), antes)
        self.assertEqual(self.estado()["ultima_comprobacion"], 1000 + self.DIA)

    def test_como_mucho_una_comprobacion_al_dia_ni_siquiera_mira_la_fuente(self):
        self.vuelta(1000)
        for horas in (1, 6, 12, 19):
            r = self.vuelta(1000 + horas * 3600)
            self.assertEqual(r["estado"], "OMITIDO", r)
        self.assertEqual(self.llamadas["meta"], 1)  # ninguna petición más
        self.assertEqual(self.vuelta(1000 + 21 * 3600)["estado"], "SIN_CAMBIOS")

    def test_fuente_nueva_descarga_valida_y_publica(self):
        self.vuelta(1000)
        self.meta_actual = self.META2
        r = self.vuelta(1000 + self.DIA)
        self.assertEqual(r["estado"], "COMPLETO", r)
        self.assertEqual(self.llamadas["descarga"], 2)
        self.assertEqual(self.estado()["fuente_meta"], self.META2)

    def test_sin_red_no_anota_nada_y_reintenta_mañana(self):
        self.montar_actual = self.montar(falla_meta=True)
        r = self.vuelta(1000)
        self.assertEqual(r["estado"], "SIN_RED")
        self.assertEqual(self.llamadas["descarga"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.salida, "estado.json")) and "fuente_meta" in self.estado())
        self.montar_actual = self.montar()
        self.assertEqual(self.vuelta(1001)["estado"], "COMPLETO")  # no quedó anotada ninguna comprobación

    def test_descarga_cortada_no_marca_la_fuente_como_procesada(self):
        self.montar_actual = self.montar(falla_descarga=True)
        r = self.vuelta(1000)
        self.assertTrue(r.get("transitorio"))
        self.assertNotIn("fuente_meta", self.estado())
        self.montar_actual = self.montar()  # al día siguiente la descarga funciona
        self.assertEqual(self.vuelta(1000 + self.DIA)["estado"], "COMPLETO")

    def test_rechazo_por_contenido_no_se_repite_cada_dia_mientras_la_fuente_siga_igual(self):
        self.montar_actual = self.montar(rechazo_contenido=True)
        self.assertEqual(self.vuelta(1000)["estado"], "RECHAZADO")
        self.assertEqual(self.vuelta(1000 + self.DIA)["estado"], "SIN_CAMBIOS")
        self.assertEqual(self.llamadas["descarga"], 1)  # no se vuelve a descargar 76 MB para rechazar lo mismo
        self.meta_actual = self.META2  # el Consorcio publica otra cosa → se vuelve a mirar
        self.assertEqual(self.vuelta(1000 + 2 * self.DIA)["estado"], "RECHAZADO")
        self.assertEqual(self.llamadas["descarga"], 2)

    def test_forzar_ignora_el_tope_y_los_cambios(self):
        self.vuelta(1000)
        self.vuelta(1001, forzar=True)
        self.assertEqual(self.llamadas["descarga"], 2)

    def test_decidir_es_puro_y_no_toca_la_red(self):
        self.assertEqual(A.decidir({}, 5)[0], "DESCARGAR")
        self.assertEqual(A.decidir({"ultima_comprobacion": 0}, 3600)[0], "OMITIR_RECIENTE")
        est = {"ultima_comprobacion": 0, "fuente_meta": self.META1}
        self.assertEqual(A.decidir(est, self.DIA, self.META1)[0], "OMITIR_SIN_CAMBIOS")
        self.assertEqual(A.decidir(est, self.DIA, self.META2)[0], "DESCARGAR")

    # ---- recuperación tras errores: un fallo NO es una comprobación correcta y no obliga a esperar otras 20 h ----
    H = 3600

    def test_una_descarga_fallida_no_cuenta_como_comprobacion_correcta(self):
        self.montar_actual = self.montar(falla_descarga=True)
        r = self.vuelta(1000)
        self.assertEqual(r["tipo"], "ERROR_DE_DESCARGA")
        est = self.estado()
        self.assertNotIn("ultima_comprobacion", est)  # no se anota como hecha
        self.assertNotIn("fuente_meta", est)  # ni se da la fuente por procesada
        self.assertEqual(est["fallos_seguidos"], 1)
        self.assertEqual(est["ultimo_intento"], 1000)

    def test_se_reintenta_tras_la_espera_corta_sin_esperar_20_horas(self):
        self.montar_actual = self.montar(falla_descarga=True)
        self.vuelta(1000)
        self.montar_actual = self.montar()  # la red ya funciona
        self.assertEqual(self.vuelta(1000 + 1 * self.H)["estado"], "OMITIDO")  # demasiado pronto (menos de 3 h)
        self.assertEqual(self.llamadas, {"meta": 0, "descarga": 0, "proceso": 0})  # ni siquiera se mira la fuente
        r = self.vuelta(1000 + 3 * self.H)  # 3 h después se reintenta, mucho antes de las 20 h
        self.assertEqual(r["estado"], "COMPLETO", r)
        est = self.estado()
        self.assertEqual(est["fallos_seguidos"], 0)
        self.assertEqual(est["ultima_comprobacion"], 1000 + 3 * self.H)
        self.assertEqual(est["fuente_meta"], self.META1)

    def test_tras_el_tope_de_fallos_seguidos_vuelve_la_espera_de_20_horas(self):
        self.montar_actual = self.montar(falla_descarga=True)
        for h in (0, 3, 6):  # tres intentos fallidos seguidos
            self.assertEqual(self.vuelta(1000 + h * self.H)["tipo"], "ERROR_DE_DESCARGA")
        self.assertEqual(self.llamadas["descarga"], 3)
        for h in (9, 12, 20, 25):  # ya no se insiste: 20 h desde el último intento (a las 6 h)
            self.assertEqual(self.vuelta(1000 + h * self.H)["estado"], "OMITIDO", h)
        self.assertEqual(self.llamadas["descarga"], 3)
        self.assertEqual(self.vuelta(1000 + 26 * self.H)["tipo"], "ERROR_DE_DESCARGA")  # a las 20 h exactas, un intento más
        self.assertEqual(self.llamadas["descarga"], 4)

    def test_un_error_inesperado_no_marca_la_fuente_ni_cuenta_como_comprobacion(self):
        self.montar_actual = self.montar(inesperado=True)
        r = self.vuelta(1000)
        self.assertEqual(r["tipo"], "ERROR_INESPERADO")
        est = self.estado()
        self.assertNotIn("fuente_meta", est)
        self.assertNotIn("ultima_comprobacion", est)
        self.assertEqual(est["fallos_seguidos"], 1)
        self.montar_actual = self.montar()  # el fallo ya está corregido
        self.assertEqual(self.vuelta(1000 + 3 * self.H)["estado"], "COMPLETO")

    def test_el_rechazo_por_contenido_es_una_comprobacion_correcta_y_no_se_reintenta(self):
        self.montar_actual = self.montar(rechazo_contenido=True)
        r = self.vuelta(1000)
        self.assertEqual(r["tipo"], "VALIDACION_DEL_CONTENIDO")
        est = self.estado()
        self.assertEqual(est["ultima_comprobacion"], 1000)
        self.assertEqual(est["fallos_seguidos"], 0)
        self.assertEqual(est["fuente_meta"], self.META1)
        self.assertEqual(self.vuelta(1000 + 3 * self.H)["estado"], "OMITIDO")  # dentro de las 20 h
        self.assertEqual(self.llamadas["descarga"], 1)

    def test_sin_novedades_es_una_comprobacion_correcta(self):
        self.vuelta(1000)
        r = self.vuelta(1000 + self.DIA)
        self.assertEqual(r["tipo"], "SIN_NOVEDADES")
        est = self.estado()
        self.assertEqual(est["ultima_comprobacion"], 1000 + self.DIA)
        self.assertEqual(est["fallos_seguidos"], 0)

    def test_sin_red_en_el_resumen_no_cuenta_como_fallo_de_descarga(self):
        self.montar_actual = self.montar(falla_meta=True)
        r = self.vuelta(1000)
        self.assertEqual(r["tipo"], "ERROR_DE_CONEXION")
        ruta = os.path.join(self.salida, "estado.json")
        self.assertFalse(os.path.exists(ruta) and "fallos_seguidos" in self.estado())
        self.assertFalse(os.path.exists(ruta) and "ultima_comprobacion" in self.estado())

    def test_si_el_trabajo_se_corta_a_mitad_el_intento_queda_contado(self):
        self.montar_actual = self.montar(corte=True)
        with self.assertRaises(Apagon):
            self.vuelta(1000)
        est = self.estado()
        self.assertEqual(est["fallos_seguidos"], 1)
        self.assertNotIn("ultima_comprobacion", est)
        self.montar_actual = self.montar()
        self.assertEqual(self.vuelta(1000 + 1 * self.H)["estado"], "OMITIDO")  # no se reintenta sin esperar
        self.assertEqual(self.vuelta(1000 + 3 * self.H)["estado"], "COMPLETO")

    def test_el_forzado_ignora_las_esperas_tras_fallo(self):
        self.montar_actual = self.montar(falla_descarga=True)
        self.vuelta(1000)
        self.montar_actual = self.montar()
        self.assertEqual(self.vuelta(1060, forzar=True)["estado"], "COMPLETO")

    def test_la_regla_de_20_horas_entre_comprobaciones_correctas_no_cambia(self):
        self.assertEqual(A.MIN_HORAS_ENTRE_COMPROBACIONES, 20)
        self.vuelta(1000)
        for h in (1, 3, 6, 12, 19):
            self.assertEqual(self.vuelta(1000 + h * self.H)["estado"], "OMITIDO", h)
        self.assertEqual(self.llamadas["meta"], 1)  # ni una petición más a la fuente
        self.assertEqual(self.vuelta(1000 + 20 * self.H)["estado"], "SIN_CAMBIOS")

    def test_decidir_con_fallos(self):
        est = {"ultima_comprobacion": 0, "fallos_seguidos": 1, "ultimo_intento": 20 * self.H}
        self.assertEqual(A.decidir(est, 21 * self.H)[0], "OMITIR_TRAS_FALLO")
        self.assertEqual(A.decidir(est, 23 * self.H)[0], "DESCARGAR")
        est["fallos_seguidos"] = A.MAX_INTENTOS_FALLIDOS
        self.assertEqual(A.decidir(est, 39 * self.H)[0], "OMITIR_TRAS_FALLO")
        self.assertEqual(A.decidir(est, 40 * self.H)[0], "DESCARGAR")

    def test_el_manifiesto_es_pequeno(self):
        self.proc(self.zip("n.zip"))
        self.assertLess(os.path.getsize(os.path.join(self.salida, "publicable", "manifest.json")), 16 * 1024)
        self.assertLess(os.path.getsize(os.path.join(self.salida, "publicable", "manifest.sig")), 1024)


FEED_NUEVO = os.path.join(TOOLS, "gtfs", "m89_20261027")
FEED_ANTIGUO = os.path.join(TOOLS, "gtfs", "x")


@unittest.skipUnless(os.path.isdir(FEED_NUEVO) and os.path.isdir(FEED_ANTIGUO) and os.path.exists(ASSET),
                     "faltan los feeds reales en tools/gtfs")
class TestLineasRealesFechasDinamicas(Base):
    def test_ventana_de_la_candidata_da_las_24_lineas(self):
        con, completas, det = A.verificar_lineas(FEED_NUEVO, FEED_ANTIGUO, datetime.date(2026, 11, 16), 7)
        self.assertEqual(len(con), 24, con)
        with open(ASSET, encoding="utf-8") as f:
            self.assertEqual(sorted(con), sorted(json.load(f)["lineas_con_antiguo"]))

    def test_el_resultado_depende_de_la_fecha_no_de_una_semana_fija(self):
        con, _, _ = A.verificar_lineas(FEED_NUEVO, FEED_ANTIGUO, datetime.date(2027, 9, 1), 28)
        self.assertEqual(con, [])  # el feed antiguo caduca el 26/08/2027: ya no hay con qué reforzar
        con, _, det = A.verificar_lineas(FEED_NUEVO, FEED_ANTIGUO, HOY, 28)
        self.assertGreater(len(con), 20)
        self.assertTrue(any("18/11" in d for d in det.values()))
        self.assertFalse(any("16/11" in d for d in det.values()))  # el lunes 16/11 ya no se mira

    def test_la_comprobacion_independiente_coincide_con_el_generador_con_la_misma_ventana(self):
        con, _, _ = A.verificar_lineas(FEED_NUEVO, FEED_ANTIGUO, HOY, 28)
        out = os.path.join(self.t, "islas.json")
        env = dict(os.environ, GTFS_DIR=FEED_NUEVO, GTFS_DIR_ANTIGUO=FEED_ANTIGUO, OUT_JSON=out, FECHA_REF=HOY.isoformat(), VENTANA_DIAS="28")
        subprocess.run([sys.executable, os.path.join(TOOLS, "build_moncloa_islas.py")], env=env, check=True, capture_output=True)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(sorted(con), sorted(json.load(f)["lineas_con_antiguo"]))

    def test_generador_con_la_ventana_de_la_candidata_es_identico_a_su_json(self):
        out = os.path.join(self.t, "islas.json")
        env = dict(os.environ, GTFS_DIR=FEED_NUEVO, GTFS_DIR_ANTIGUO=FEED_ANTIGUO, OUT_JSON=out, FECHA_REF="2026-11-16", VENTANA_DIAS="7")
        subprocess.run([sys.executable, os.path.join(TOOLS, "build_moncloa_islas.py")], env=env, check=True, capture_output=True)
        self.assertEqual(leer_bytes(out), leer_bytes(ASSET))

    def test_los_feeds_reales_pasan_la_validacion_profunda(self):
        e, a, s = V.validar_gtfs(FEED_NUEVO, FEED_ANTIGUO, HOY)
        self.assertEqual(e, [], e)
        self.assertEqual(s["lineas_perdidas_frente_al_anterior"], [])
        e, a, s = V.validar_gtfs(FEED_ANTIGUO, None, HOY)
        self.assertEqual(e, [], e)

    def test_procesar_el_feed_real_da_parcial_firmado_y_con_los_4_archivos(self):
        z = os.path.join(self.t, "real.zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zz:
            for n in os.listdir(FEED_NUEVO):
                zz.write(os.path.join(FEED_NUEVO, n), n)
        from build_moncloa_islas import PLAN, STOP_ISLA
        lineas = sorted({ln for ds in PLAN.values() for l in ds.values() for ln, _ in l})
        r = A.procesar_zip(z, self.salida, FEED_ANTIGUO, A.generar_paquete, HOY, self.dir_claves, 28, lineas, sorted(set(STOP_ISLA.values())))
        self.assertEqual(r["estado"], "PARCIAL", r)
        self.assertEqual(r["datasets_incompletos"], ["moncloa_islas.json", "salidas.json"])
        self.assertFalse(os.path.isdir(os.path.join(self.salida, "publicable")))
        m = A.verificar_paquete(os.path.join(self.salida, "pendiente_revision", "20261027"), self.publica)
        self.assertEqual(m["estado"], "PARCIAL_REQUIERE_REVISION")
        self.assertEqual({a["nombre"] for a in m["archivos"]}, set(A.DATASETS))
        self.assertEqual(len(m["datasets"]["moncloa_islas.json"]["lineas_con_datos_antiguos"]), 24)


if __name__ == "__main__":
    unittest.main(verbosity=2)
