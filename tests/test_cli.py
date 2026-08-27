"""CLI de punta a punta: subproceso real contra una BD temporal, sin red.

`discover` y `audit` salen a internet, así que aquí se siembra la BD a mano
y se ejercita el resto del ciclo comercial, que es el que se usa a diario.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from prospector import cli as climod
from prospector import db
from prospector.cli import slug
from prospector.discover import LA_POBLA


class TestSlug:
    @pytest.mark.parametrize("nombre,esperado", [
        ("Panadería Pepe", "panaderia-pepe"),
        ("Clínica Dental Llíria", "clinica-dental-lliria"),
        ("Bar  &  Restaurante  'El Rincón'", "bar-restaurante-el-rincon"),
        ("---Ejemplo---", "ejemplo"),
    ])
    def test_normaliza(self, nombre, esperado):
        assert slug(nombre) == esperado

    def test_trunca_a_50(self):
        assert len(slug("Muy Largo " * 20)) <= 50

    def test_no_deja_guiones_en_los_bordes(self):
        s = slug("¡¡¡Ejemplo!!!")
        assert not s.startswith("-") and not s.endswith("-")


@pytest.fixture
def cli(tmp_path):
    """Ejecuta el CLI con la BD y las maquetas aisladas en tmp."""
    entorno = {
        **os.environ,
        "PROSPECTOR_DB": str(tmp_path / "cli.db"),
        "PROSPECTOR_MAQUETAS": str(tmp_path / "maquetas"),
    }

    def correr(*args, esperar_exito=True):
        r = subprocess.run(
            [sys.executable, "-m", "prospector.cli", *args],
            capture_output=True, text=True, env=entorno,
        )
        if esperar_exito:
            assert r.returncode == 0, f"falló `{' '.join(args)}`:\n{r.stderr}"
        return r

    correr.db = tmp_path / "cli.db"
    correr.maquetas = tmp_path / "maquetas"
    return correr


def _sembrar(ruta, track="sin_web", score=88):
    con = sqlite3.connect(ruta)
    con.execute(
        """INSERT INTO businesses (osm_type, osm_id, name, category, lat, lon,
           municipality, address, phone, email, website, is_chain, dist_km,
           first_seen, last_seen)
           VALUES ('node',1,'Panadería Pepe','bakery',39.6,-0.54,'Llíria',
                   'Calle Mayor 3','961234567',NULL,NULL,0,5.0,
                   '2026-08-01T10:00:00+00:00','2026-08-01T10:00:00+00:00')""")
    con.execute("INSERT INTO pipeline (business_id, stage, updated_at) "
                "VALUES (1,'nuevo','2026-08-01T10:00:00+00:00')")
    con.execute(
        """INSERT INTO audits (business_id, run_at, track, score, signals_json)
           VALUES (1,'2026-08-01T10:00:00+00:00',?,?,?)""",
        (track, score, json.dumps(["no consta web", "sector bakery (+6)"])))
    con.commit()
    con.close()


class TestCicloCompleto:
    def test_init_crea_la_bd_donde_dice_la_variable(self, cli):
        r = cli("init")
        assert cli.db.exists()
        assert str(cli.db) in r.stdout

    def test_cola_vacia_no_revienta(self, cli):
        cli("init")
        assert "Cola vacía" in cli("cola").stdout

    def test_cola_muestra_el_negocio(self, cli):
        cli("init"); _sembrar(cli.db)
        salida = cli("cola", "-n", "5").stdout
        assert "Panadería Pepe" in salida and "88" in salida

    def test_cola_filtra_por_carril(self, cli):
        cli("init"); _sembrar(cli.db, track="sin_web")
        assert "Panadería Pepe" in cli("cola", "--track", "sin_web").stdout
        assert "Cola vacía" in cli("cola", "--track", "web_caida").stdout

    def test_ficha_muestra_las_senales(self, cli):
        cli("init"); _sembrar(cli.db)
        salida = cli("ficha", "1").stdout
        assert "Panadería Pepe" in salida
        assert "no consta web" in salida
        assert "PUNTUACIÓN 88" in salida

    def test_brief_saca_json_valido(self, cli):
        cli("init"); _sembrar(cli.db)
        datos = json.loads(cli("brief", "-n", "1").stdout)
        assert datos[0]["slug"] == "panaderia-pepe"
        assert datos[0]["problemas"] == ["no consta web", "sector bakery (+6)"]

    def test_maqueta_avanza_la_etapa_y_saca_de_la_cola(self, cli):
        cli("init"); _sembrar(cli.db)
        cli("maqueta", "1")
        assert "maqueta" in cli("ficha", "1").stdout
        assert "Cola vacía" in cli("cola").stdout

    def test_log_registra_y_mueve_a_reunion(self, cli):
        cli("init"); _sembrar(cli.db)
        cli("log", "1", "llamada", "cita", "--next", "Visita con tablet",
            "--fecha", "2026-09-03")
        salida = cli("ficha", "1").stdout
        assert "reunion" in salida
        assert "Visita con tablet" in salida

    def test_excluir_lo_saca_para_siempre(self, cli):
        cli("init"); _sembrar(cli.db)
        cli("excluir", "1", "--reason", "pidió no volver a llamar")
        assert "Cola vacía" in cli("cola").stdout

    def test_embudo_cuenta_las_etapas(self, cli):
        cli("init"); _sembrar(cli.db)
        assert "nuevo" in cli("embudo").stdout

    def test_export_saca_csv_con_cabecera(self, cli):
        cli("init"); _sembrar(cli.db)
        lineas = cli("export").stdout.strip().splitlines()
        assert lineas[0].startswith("id,name,category")
        assert "Panadería Pepe" in lineas[1]


class TestInvocacion:
    def test_python_m_prospector(self, cli):
        r = subprocess.run([sys.executable, "-m", "prospector", "--help"],
                           capture_output=True, text=True)
        assert r.returncode == 0 and "discover" in r.stdout

    def test_comando_desconocido_falla(self, cli):
        assert cli("inventado", esperar_exito=False).returncode != 0


class TestBugsDelPipeline:
    """Regresiones concretas. Cada test aquí falló antes de su arreglo."""

    def test_log_sin_next_no_borra_la_accion_pendiente(self, cli):
        """Registrar una llamada no debe hacerte perder la visita ya agendada."""
        cli("init"); _sembrar(cli.db)
        cli("log", "1", "llamada", "cita", "--next", "Visita con tablet",
            "--fecha", "2026-09-03")
        cli("log", "1", "llamada", "no_contesta")
        salida = cli("ficha", "1").stdout
        assert "Visita con tablet" in salida
        assert "2026-09-03" in salida

    def test_log_no_retrocede_de_etapa(self, cli):
        """Un 'no contesta' después de una cita no te devuelve a 'contactado'."""
        cli("init"); _sembrar(cli.db)
        cli("log", "1", "llamada", "cita")
        cli("log", "1", "llamada", "no_contesta")
        assert "etapa: reunion" in cli("ficha", "1").stdout

    def test_log_si_avanza_de_etapa(self, cli):
        cli("init"); _sembrar(cli.db)
        cli("log", "1", "llamada", "no_contesta")
        assert "etapa: contactado" in cli("ficha", "1").stdout
        cli("log", "1", "llamada", "cita")
        assert "etapa: reunion" in cli("ficha", "1").stdout

    def test_no_interesado_manda_aunque_sea_hacia_atras(self, cli):
        """Los carriles terminales siempre ganan: si dice que no, es que no."""
        cli("init"); _sembrar(cli.db)
        cli("log", "1", "llamada", "cita")
        cli("log", "1", "visita", "no_interesado", "--notes", "Se lo lleva un familiar")
        assert "etapa: perdido" in cli("ficha", "1").stdout

    @pytest.mark.parametrize("args", [
        ("ficha", "999"),
        ("maqueta", "999"),
        ("excluir", "999"),
        ("log", "999", "llamada", "cita"),
    ])
    def test_id_inexistente_falla_limpio(self, cli, args):
        """Sin traceback y con código de salida distinto de cero."""
        cli("init"); _sembrar(cli.db)
        r = cli(*args, esperar_exito=False)
        assert r.returncode != 0
        assert "Traceback" not in r.stderr
        assert "999" in (r.stdout + r.stderr)

    def test_export_vacio_saca_solo_la_cabecera(self, cli):
        """Un CSV sin filas debe seguir siendo un CSV, no una línea en blanco."""
        cli("init")
        lineas = [ln for ln in cli("export").stdout.splitlines() if ln.strip()]
        assert len(lineas) == 1
        assert lineas[0].startswith("id,name,category")


class TestNormalizar:
    def test_deja_en_forma_canonica_los_ya_guardados(self, bd):
        _negocio(bd, oid=1, nombre="Con espacios", tlf="962 76 13 46")
        _negocio(bd, oid=2, nombre="Sin prefijo", tlf="961234567")
        _negocio(bd, oid=3, nombre="Ya canónico", tlf="+34600111222")
        climod.cmd_normalizar(argparse.Namespace(simular=False))
        assert [r[0] for r in bd.execute(
            "SELECT phone FROM businesses ORDER BY osm_id")] == [
            "+34962761346", "+34961234567", "+34600111222"]

    def test_simular_no_toca_nada(self, bd):
        bid = _negocio(bd, tlf="962 76 13 46")
        climod.cmd_normalizar(argparse.Namespace(simular=True))
        assert bd.execute("SELECT phone FROM businesses WHERE id=?",
                          (bid,)).fetchone()[0] == "962 76 13 46"

    def test_no_toca_los_que_ya_estan_bien(self, bd, capsys):
        _negocio(bd, tlf="+34961234567")
        climod.cmd_normalizar(argparse.Namespace(simular=False))
        assert "ya están en forma canónica" in capsys.readouterr().out


class TestDiscoverSinRed:
    """--desde-json y --simular existen para poder afinar el parser sin
    gastar consultas contra un servicio comunitario gratuito."""

    CRUDO = {"elements": [
        {"type": "node", "id": 1, "lat": 39.5900, "lon": -0.5420,
         "tags": {"name": "Panadería Pepe", "shop": "bakery",
                  "phone": "961234567", "addr:city": "Llíria"}},
        {"type": "node", "id": 2, "lat": 39.5890, "lon": -0.5410,
         "tags": {"name": "Mercadona", "shop": "supermarket", "brand": "Mercadona"}},
        {"type": "node", "id": 3, "lat": 39.9000, "lon": -0.9000,
         "tags": {"name": "Muy Lejos", "shop": "bakery", "phone": "961000000"}},
    ]}

    @pytest.fixture
    def crudo(self, tmp_path):
        ruta = tmp_path / "crudo.json"
        ruta.write_text(json.dumps(self.CRUDO), encoding="utf-8")
        return str(ruta)

    def test_desde_json_no_sale_a_la_red(self, cli, crudo):
        cli("init")
        r = cli("discover", "--desde-json", crudo, "--radius", "5000")
        assert "sin red" in r.stderr
        con = sqlite3.connect(cli.db)
        nombres = {n for (n,) in con.execute("SELECT name FROM businesses")}
        assert nombres == {"Panadería Pepe", "Mercadona"}  # "Muy Lejos" queda fuera

    def test_simular_no_toca_la_bd(self, cli, crudo):
        cli("init")
        r = cli("discover", "--desde-json", crudo, "--radius", "5000", "--simular")
        assert "simulacro" in r.stdout
        con = sqlite3.connect(cli.db)
        assert con.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 0

    def test_el_resumen_separa_lo_auditable(self, cli, crudo):
        cli("init")
        salida = cli("discover", "--desde-json", crudo, "--radius", "5000",
                     "--simular").stdout
        assert "2 negocios · 1 cadenas" in salida
        assert "1 con vía de contacto" in salida  # la cadena no cuenta
        assert "Llíria" in salida
        assert "bakery" in salida

    def test_reejecutar_desde_el_mismo_volcado_no_duplica(self, cli, crudo):
        cli("init")
        cli("discover", "--desde-json", crudo, "--radius", "5000")
        r = cli("discover", "--desde-json", crudo, "--radius", "5000")
        assert "0 nuevos" in r.stdout

    def test_guardar_json_vuelca_lo_que_luego_se_puede_reparsear(self, tmp_path, monkeypatch):
        """El doble va sobre cli.descargar, no sobre discover.descargar:
        cli.py lo importa a su propio espacio de nombres."""
        destino = tmp_path / "volcado.json"
        monkeypatch.setattr(climod, "descargar", lambda *a, **k: self.CRUDO)
        climod.cmd_discover(argparse.Namespace(
            lat=LA_POBLA[0], lon=LA_POBLA[1], radius=5000, espera=0,
            comarca=None, municipios=None, refrescar_municipios=False,
            desde_json=None, guardar_json=str(destino), simular=True))
        assert json.loads(destino.read_text(encoding="utf-8")) == self.CRUDO


@pytest.fixture
def bd(tmp_path, monkeypatch):
    """BD temporal alcanzable por los comandos, que abren su propia conexión."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "enriquecer.db")
    con = db.init()
    yield con
    con.close()


def _negocio(con, oid=1, nombre="Panadería Pepe", tlf=None, web=None, muni="Llíria"):
    bid = db.upsert_business(con, {
        "osm_type": "node", "osm_id": oid, "name": nombre, "category": "bakery",
        "lat": 39.5878, "lon": -0.5397, "municipality": muni, "address": "Calle Mayor 3",
        "phone": tlf, "email": None, "website": web, "is_chain": 0, "dist_km": 2.0})
    con.commit()
    return bid


def _args(**kw):
    base = dict(limite=25, municipio=None, espera=0, simular=False)
    return argparse.Namespace(**{**base, **kw})


class TestEnriquecer:
    """Cada consulta se paga: lo que se prueba aquí es no gastar de más
    y no escribir un teléfono equivocado."""

    def _places(self, monkeypatch, resultado):
        llamadas = []
        monkeypatch.setattr(climod.places, "clave", lambda: "clave-de-prueba")

        def buscar(texto, lat, lon, **kw):
            llamadas.append(texto)
            if isinstance(resultado, Exception):
                raise resultado
            return resultado

        monkeypatch.setattr(climod.places, "buscar", buscar)
        return llamadas

    def test_simular_no_gasta_ni_una_consulta(self, bd, monkeypatch, capsys):
        _negocio(bd)
        llamadas = self._places(monkeypatch, [])
        climod.cmd_enriquecer(_args(simular=True))
        assert llamadas == []
        assert "0 consultas hechas" in capsys.readouterr().out

    def test_rellena_el_telefono_que_falta(self, bd, monkeypatch):
        bid = _negocio(bd)
        self._places(monkeypatch, [{
            "id": "places/abc", "displayName": {"text": "Panadería Pepe"},
            "location": {"latitude": 39.5878, "longitude": -0.5397},
            "nationalPhoneNumber": "961234567", "websiteUri": "https://pepe.es"}])
        climod.cmd_enriquecer(_args())
        b = bd.execute("SELECT phone, website FROM businesses WHERE id=?", (bid,)).fetchone()
        assert b["phone"] == "+34961234567"  # canónico, como los de OSM
        assert b["website"] == "https://pepe.es"

    def test_nunca_pisa_un_dato_que_ya_venia_de_osm(self, bd, monkeypatch):
        bid = _negocio(bd, tlf="600111222")
        self._places(monkeypatch, [{
            "id": "places/abc", "displayName": {"text": "Panadería Pepe"},
            "location": {"latitude": 39.5878, "longitude": -0.5397},
            "nationalPhoneNumber": "999999999", "websiteUri": "https://pepe.es"}])
        climod.cmd_enriquecer(_args())
        b = bd.execute("SELECT phone, website FROM businesses WHERE id=?", (bid,)).fetchone()
        assert b["phone"] == "600111222"   # el de OSM manda
        assert b["website"] == "https://pepe.es"  # este sí faltaba

    def test_un_candidato_dudoso_no_escribe_nada(self, bd, monkeypatch):
        bid = _negocio(bd)
        self._places(monkeypatch, [{
            "id": "places/xyz", "displayName": {"text": "Clínica Dental Sonrisa"},
            "location": {"latitude": 39.5878, "longitude": -0.5397},
            "nationalPhoneNumber": "999999999"}])
        climod.cmd_enriquecer(_args())
        b = bd.execute("SELECT phone FROM businesses WHERE id=?", (bid,)).fetchone()
        assert b["phone"] is None
        fila = bd.execute("SELECT * FROM place_lookups WHERE business_id=?",
                          (bid,)).fetchone()
        assert fila["matched"] == 0
        # La consulta ya se pagó: queda por qué se descartó, para poder
        # revisar el criterio más adelante sin volver a pagarla.
        assert "Clínica Dental Sonrisa" in fila["motivo"]
        assert fila["similitud"] is not None
        assert fila["distancia_km"] is not None

    def test_no_se_pregunta_dos_veces_por_el_mismo(self, bd, monkeypatch):
        """Un fallo también se paga: queda anotado igual."""
        _negocio(bd)
        llamadas = self._places(monkeypatch, [])
        climod.cmd_enriquecer(_args())
        climod.cmd_enriquecer(_args())
        assert len(llamadas) == 1

    def test_el_limite_topa_el_gasto(self, bd, monkeypatch):
        for i in range(5):
            _negocio(bd, oid=i, nombre=f"Negocio {i}")
        llamadas = self._places(monkeypatch, [])
        climod.cmd_enriquecer(_args(limite=2))
        assert len(llamadas) == 2

    def test_primero_los_que_no_tienen_ninguna_via_de_contacto(self, bd, monkeypatch):
        _negocio(bd, oid=1, nombre="Con teléfono", tlf="961234567")
        _negocio(bd, oid=2, nombre="Sin nada")
        llamadas = self._places(monkeypatch, [])
        climod.cmd_enriquecer(_args(limite=1))
        assert "Sin nada" in llamadas[0]

    def test_las_cadenas_no_se_consultan(self, bd, monkeypatch):
        bd.execute("UPDATE businesses SET is_chain=1 WHERE id=?", (_negocio(bd),))
        bd.commit()
        llamadas = self._places(monkeypatch, [])
        climod.cmd_enriquecer(_args())
        assert llamadas == []

    def test_los_excluidos_no_se_consultan(self, bd, monkeypatch):
        _negocio(bd, oid=7)
        bd.execute("INSERT INTO exclusions VALUES (?,?,?)",
                   ("osm:node/7", "pidió no volver a llamar", db.now()))
        bd.commit()
        llamadas = self._places(monkeypatch, [])
        climod.cmd_enriquecer(_args())
        assert llamadas == []

    def test_se_puede_acotar_a_un_municipio(self, bd, monkeypatch):
        _negocio(bd, oid=1, nombre="De Llíria", muni="Llíria")
        _negocio(bd, oid=2, nombre="De Bétera", muni="Bétera")
        llamadas = self._places(monkeypatch, [])
        climod.cmd_enriquecer(_args(municipio="Bétera"))
        assert len(llamadas) == 1 and "De Bétera" in llamadas[0]

    def test_un_fallo_de_la_api_para_pero_guarda_lo_hecho(self, bd, monkeypatch, capsys):
        _negocio(bd, oid=1, nombre="Primero")
        _negocio(bd, oid=2, nombre="Segundo")
        llamadas = []
        monkeypatch.setattr(climod.places, "clave", lambda: "clave-de-prueba")

        def buscar(texto, lat, lon, **kw):
            llamadas.append(texto)
            if len(llamadas) == 2:
                raise climod.places.PlacesError("Google rechaza la clave")
            return []

        monkeypatch.setattr(climod.places, "buscar", buscar)
        climod.cmd_enriquecer(_args())
        assert bd.execute("SELECT COUNT(*) c FROM place_lookups").fetchone()["c"] == 1
        assert "Parado en 1/2" in capsys.readouterr().err

    def test_sin_clave_para_antes_de_empezar(self, bd, monkeypatch, capsys):
        """Sin clave no hay nada que hacer: nada de 'parado en 0/N'."""
        _negocio(bd)
        monkeypatch.setattr(climod.places, "clave", lambda: (_ for _ in ()).throw(
            climod.places.SinClave("No hay clave de Google Places.")))
        monkeypatch.setattr(climod.places, "buscar",
                            lambda *a, **k: pytest.fail("no debe consultar"))
        with pytest.raises(SystemExit) as e:
            climod.cmd_enriquecer(_args())
        assert e.value.code == 1
        assert "No hay clave" in capsys.readouterr().err
        assert bd.execute("SELECT COUNT(*) c FROM place_lookups").fetchone()["c"] == 0

    def test_el_simulacro_no_necesita_clave(self, bd, monkeypatch):
        """Poder ver cuánto costaría antes de tener la clave."""
        _negocio(bd)
        monkeypatch.setattr(climod.places, "clave", lambda: (_ for _ in ()).throw(
            climod.places.SinClave("sin clave")))
        climod.cmd_enriquecer(_args(simular=True))  # no revienta

    def test_la_busqueda_lleva_nombre_direccion_y_municipio(self, bd, monkeypatch):
        _negocio(bd)
        llamadas = self._places(monkeypatch, [])
        climod.cmd_enriquecer(_args())
        assert llamadas[0] == "Panadería Pepe Calle Mayor 3 Llíria"
