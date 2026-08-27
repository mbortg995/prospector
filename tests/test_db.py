"""Capa SQLite y la vista v_cola, que es la que decide a quién se llama."""
import json

from prospector import db


def _alta(con, **kw):
    biz = {"osm_type": "node", "osm_id": 1, "name": "Panadería Pepe",
           "category": "bakery", "lat": 39.6, "lon": -0.54,
           "municipality": "Llíria", "address": "Calle Mayor 3",
           "phone": "961234567", "email": None, "website": None,
           "is_chain": 0, "dist_km": 5.0}
    biz.update(kw)
    return db.upsert_business(con, biz)


def _audita(con, bid, track="sin_web", score=70, run_at=None):
    db.save_audit(con, bid, {"track": track, "score": score, "signals": ["prueba"]})
    if run_at:
        con.execute("UPDATE audits SET run_at=? WHERE id=(SELECT MAX(id) FROM audits)",
                    (run_at,))


class TestEsquema:
    def test_crea_tablas_y_vista(self, con):
        nombres = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        assert {"businesses", "audits", "pipeline", "interactions",
                "exclusions", "v_cola"} <= nombres

    def test_init_es_idempotente(self, tmp_path):
        db.init(tmp_path / "x.db").close()
        db.init(tmp_path / "x.db").close()  # no debe reventar


class TestUpsert:
    def test_alta_crea_fila_de_pipeline_en_nuevo(self, con):
        bid = _alta(con)
        p = con.execute("SELECT * FROM pipeline WHERE business_id=?", (bid,)).fetchone()
        assert p["stage"] == "nuevo"

    def test_reejecutar_discover_no_duplica(self, con):
        assert _alta(con) == _alta(con)
        assert con.execute("SELECT COUNT(*) c FROM businesses").fetchone()["c"] == 1

    def test_reejecutar_discover_refresca_los_datos(self, con):
        bid = _alta(con)
        _alta(con, name="Panadería Pepe e Hijos", phone="600111222")
        b = con.execute("SELECT * FROM businesses WHERE id=?", (bid,)).fetchone()
        assert b["name"] == "Panadería Pepe e Hijos"
        assert b["phone"] == "600111222"

    def test_reejecutar_discover_nunca_pisa_el_pipeline(self, con):
        """La garantía del proyecto: censar de nuevo no borra el trabajo comercial."""
        bid = _alta(con)
        db.set_stage(con, bid, "reunion", next_action="Visita con tablet")
        _alta(con, name="Nombre cambiado")
        p = con.execute("SELECT * FROM pipeline WHERE business_id=?", (bid,)).fetchone()
        assert p["stage"] == "reunion"
        assert p["next_action"] == "Visita con tablet"

    def test_first_seen_no_cambia_al_refrescar(self, con):
        bid = _alta(con)
        antes = con.execute("SELECT first_seen FROM businesses WHERE id=?", (bid,)).fetchone()[0]
        _alta(con, name="Otro")
        assert con.execute("SELECT first_seen FROM businesses WHERE id=?",
                           (bid,)).fetchone()[0] == antes


class TestAuditorias:
    def test_guarda_las_senales_como_json(self, con):
        bid = _alta(con)
        db.save_audit(con, bid, {"track": "sin_web", "score": 70,
                                 "signals": ["no consta web", "sector bakery (+6)"]})
        fila = con.execute("SELECT * FROM audits WHERE business_id=?", (bid,)).fetchone()
        assert json.loads(fila["signals_json"]) == ["no consta web", "sector bakery (+6)"]

    def test_conserva_el_historico(self, con):
        bid = _alta(con)
        _audita(con, bid, score=70, run_at="2026-01-01T10:00:00+00:00")
        _audita(con, bid, score=40, run_at="2026-06-01T10:00:00+00:00")
        assert con.execute("SELECT COUNT(*) c FROM audits").fetchone()["c"] == 2

    def test_borrar_el_negocio_arrastra_sus_auditorias(self, con):
        bid = _alta(con)
        _audita(con, bid)
        con.execute("DELETE FROM businesses WHERE id=?", (bid,))
        assert con.execute("SELECT COUNT(*) c FROM audits").fetchone()["c"] == 0


class TestColaDeTrabajo:
    def test_entra_lo_auditado_y_sin_trabajar(self, con):
        bid = _alta(con)
        _audita(con, bid)
        assert [r["id"] for r in con.execute("SELECT * FROM v_cola")] == [bid]

    def test_ordena_por_puntuacion(self, con):
        a = _alta(con, osm_id=1, name="Flojo")
        b = _alta(con, osm_id=2, name="Bueno")
        _audita(con, a, score=30)
        _audita(con, b, score=90)
        assert [r["name"] for r in con.execute("SELECT * FROM v_cola")] == ["Bueno", "Flojo"]

    def test_web_ok_fuera(self, con):
        _audita(con, _alta(con), track="web_ok", score=10)
        assert con.execute("SELECT COUNT(*) c FROM v_cola").fetchone()["c"] == 0

    def test_franquicia_fuera(self, con):
        _audita(con, _alta(con, is_chain=1))
        assert con.execute("SELECT COUNT(*) c FROM v_cola").fetchone()["c"] == 0

    def test_sin_auditar_fuera(self, con):
        _alta(con)
        assert con.execute("SELECT COUNT(*) c FROM v_cola").fetchone()["c"] == 0

    def test_ya_trabajado_fuera(self, con):
        bid = _alta(con)
        _audita(con, bid)
        db.set_stage(con, bid, "maqueta")
        assert con.execute("SELECT COUNT(*) c FROM v_cola").fetchone()["c"] == 0

    def test_excluido_fuera(self, con):
        bid = _alta(con, osm_type="node", osm_id=99)
        _audita(con, bid)
        con.execute("INSERT INTO exclusions VALUES (?,?,?)",
                    ("osm:node/99", "pidió no volver a llamar", db.now()))
        assert con.execute("SELECT COUNT(*) c FROM v_cola").fetchone()["c"] == 0

    def test_manda_la_auditoria_mas_reciente(self, con):
        """Re-auditar a alguien que ya arregló su web lo saca de la cola."""
        bid = _alta(con)
        _audita(con, bid, track="web_obsoleta", score=80, run_at="2026-01-01T10:00:00+00:00")
        _audita(con, bid, track="web_ok", score=10, run_at="2026-06-01T10:00:00+00:00")
        assert con.execute("SELECT COUNT(*) c FROM v_cola").fetchone()["c"] == 0


class TestInteracciones:
    def test_registra_la_llamada(self, con):
        bid = _alta(con)
        db.log_interaction(con, bid, "llamada", "cita", "Quedamos el jueves")
        fila = con.execute("SELECT * FROM interactions WHERE business_id=?", (bid,)).fetchone()
        assert (fila["kind"], fila["outcome"]) == ("llamada", "cita")

    def test_set_stage_actualiza_campos_extra(self, con):
        bid = _alta(con)
        db.set_stage(con, bid, "maqueta", mockup_path="maquetas/pepe/index.html")
        p = con.execute("SELECT * FROM pipeline WHERE business_id=?", (bid,)).fetchone()
        assert p["stage"] == "maqueta"
        assert p["mockup_path"] == "maquetas/pepe/index.html"


class TestBugsDeDatos:
    def test_cola_desempata_por_la_auditoria_mas_nueva(self, con):
        """run_at tiene precisión de segundo: re-auditar dos veces seguidas
        dejaba a suerte cuál de las dos mandaba."""
        bid = _alta(con)
        mismo = "2026-08-27T10:00:00+00:00"
        _audita(con, bid, track="web_obsoleta", score=80, run_at=mismo)
        _audita(con, bid, track="web_ok", score=10, run_at=mismo)
        assert con.execute("SELECT COUNT(*) c FROM v_cola").fetchone()["c"] == 0

    def test_connect_respeta_db_path_en_caliente(self, tmp_path, monkeypatch):
        """El valor por defecto se evaluaba al importar, así que no había
        forma de redirigir la BD sin reimportar el módulo."""
        destino = tmp_path / "redirigida.db"
        monkeypatch.setattr(db, "DB_PATH", destino)
        db.init().close()
        assert destino.exists()
