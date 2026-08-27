"""Panel local. Se levanta un servidor de verdad en un puerto libre y se le
habla por HTTP: lo que importa aquí es la capa web, no las consultas."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from prospector import db, panel


@pytest.fixture
def servidor(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "panel.db")
    con = db.init()
    bid = db.upsert_business(con, {
        "osm_type": "node", "osm_id": 1, "name": "Panadería Pepe",
        "category": "bakery", "lat": 39.5878, "lon": -0.5397,
        "municipality": "Llíria", "address": "Calle Mayor 3",
        "phone": "961234567", "email": None, "website": None,
        "is_chain": 0, "dist_km": 2.0})
    db.save_audit(con, bid, {"track": "sin_web", "score": 88,
                             "signals": ["no consta web"]})
    con.commit()

    s = ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    s.base = f"http://127.0.0.1:{s.server_address[1]}"
    s.con = con
    s.bid = bid
    yield s
    s.shutdown(); s.server_close(); con.close()


def get(s, ruta):
    with urllib.request.urlopen(s.base + ruta, timeout=10) as r:
        return json.loads(r.read())


def post(s, ruta, datos):
    req = urllib.request.Request(
        s.base + ruta, data=json.dumps(datos).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestLectura:
    def test_la_portada_es_html(self, servidor):
        with urllib.request.urlopen(servidor.base + "/", timeout=10) as r:
            cuerpo = r.read().decode()
        assert r.headers["Content-Type"].startswith("text/html")
        assert "<title>Prospector</title>" in cuerpo

    def test_la_portada_no_pide_nada_a_internet(self, servidor):
        """Igual que las maquetas: tiene que funcionar sin cobertura."""
        cuerpo = panel.PLANTILLA.read_text(encoding="utf-8")
        for fuera in ("http://", "https://", "//cdn", "src=\"//"):
            assert fuera not in cuerpo.replace("http://127.0.0.1", "")

    def test_cola(self, servidor):
        (f,) = get(servidor, "/api/cola")
        assert f["name"] == "Panadería Pepe"
        assert f["score"] == 88

    def test_cola_filtra_por_carril(self, servidor):
        assert len(get(servidor, "/api/cola?track=sin_web")) == 1
        assert get(servidor, "/api/cola?track=web_caida") == []

    def test_cola_filtra_por_municipio_con_acentos(self, servidor):
        """Los municipios de la comarca llevan acentos y apóstrofos."""
        q = urllib.parse.urlencode({"municipio": "Llíria"})
        assert len(get(servidor, "/api/cola?" + q)) == 1
        assert get(servidor, "/api/cola?" + urllib.parse.urlencode(
            {"municipio": "Bétera"})) == []

    def test_ficha(self, servidor):
        d = get(servidor, f"/api/ficha/{servidor.bid}")
        assert d["negocio"]["name"] == "Panadería Pepe"
        assert d["auditoria"]["signals"] == ["no consta web"]
        assert d["pipeline"]["stage"] == "nuevo"

    def test_ficha_inexistente_da_404(self, servidor):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(servidor, "/api/ficha/9999")
        assert e.value.code == 404

    def test_embudo(self, servidor):
        e = get(servidor, "/api/embudo")
        assert e["totales"]["negocios"] == 1
        assert e["totales"]["contactables"] == 1
        assert {"track": "sin_web", "n": 1, "media": 88} in e["carriles"]

    def test_municipios(self, servidor):
        assert get(servidor, "/api/municipios") == ["Llíria"]

    def test_ruta_desconocida(self, servidor):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(servidor, "/api/inventado")
        assert e.value.code == 404


class TestEscritura:
    def test_registrar_una_cita_mueve_la_etapa(self, servidor):
        codigo, d = post(servidor, "/api/log", {
            "id": servidor.bid, "kind": "llamada", "outcome": "cita",
            "notes": "Quedamos el jueves", "next": "Visita con tablet",
            "fecha": "2026-09-10"})
        assert codigo == 200
        assert d["pipeline"]["stage"] == "reunion"
        assert d["pipeline"]["next_action"] == "Visita con tablet"
        assert d["historial"][0]["notes"] == "Quedamos el jueves"
        assert get(servidor, "/api/cola") == []  # sale de la cola

    def test_registrar_no_retrocede_de_etapa(self, servidor):
        """Misma regla que en el CLI, no una copia distinta."""
        post(servidor, "/api/log", {"id": servidor.bid, "kind": "llamada",
                                    "outcome": "cita"})
        _, d = post(servidor, "/api/log", {"id": servidor.bid, "kind": "llamada",
                                           "outcome": "no_contesta"})
        assert d["pipeline"]["stage"] == "reunion"

    def test_registrar_sin_next_no_borra_la_accion_pendiente(self, servidor):
        post(servidor, "/api/log", {"id": servidor.bid, "kind": "llamada",
                                    "outcome": "cita", "next": "Visita con tablet",
                                    "fecha": "2026-09-10"})
        _, d = post(servidor, "/api/log", {"id": servidor.bid, "kind": "llamada",
                                           "outcome": "no_contesta"})
        assert d["pipeline"]["next_action"] == "Visita con tablet"

    def test_maqueta(self, servidor):
        _, d = post(servidor, "/api/maqueta", {"id": servidor.bid, "path": "x/index.html"})
        assert d["pipeline"]["stage"] == "maqueta"
        assert d["pipeline"]["mockup_path"] == "x/index.html"

    def test_excluir(self, servidor):
        _, d = post(servidor, "/api/excluir", {"id": servidor.bid, "reason": "no llamar"})
        assert d["pipeline"]["stage"] == "descartado"
        assert servidor.con.execute(
            "SELECT reason FROM exclusions WHERE key='osm:node/1'").fetchone()[0] == "no llamar"

    def test_negocio_inexistente(self, servidor):
        codigo, d = post(servidor, "/api/log", {"id": 9999, "kind": "llamada",
                                                "outcome": "cita"})
        assert codigo == 404 and "9999" in d["error"]

    def test_json_invalido(self, servidor):
        req = urllib.request.Request(
            servidor.base + "/api/log", data=b"{no es json",
            headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 400

    def test_falta_un_campo(self, servidor):
        codigo, d = post(servidor, "/api/log", {"id": servidor.bid})
        assert codigo == 400 and "falta el campo" in d["error"]


class TestTareas:
    @pytest.fixture(autouse=True)
    def tarea_limpia(self):
        panel.TAREA.__init__()
        yield
        panel.TAREA.__init__()

    def test_solo_se_dejan_lanzar_las_de_la_lista(self, servidor):
        """El navegador no puede pedir que se ejecute cualquier cosa."""
        codigo, d = post(servidor, "/api/tarea", {"nombre": "rm -rf /", "limite": 1})
        assert codigo == 409 and "desconocida" in d["error"]

    def test_discover_no_esta_disponible(self, servidor):
        """Tarda veinte minutos: no es cosa de un botón."""
        assert "discover" not in panel.TAREAS
        assert post(servidor, "/api/tarea", {"nombre": "discover", "limite": 1})[0] == 409

    def test_una_tarea_cada_vez(self, servidor, monkeypatch):
        monkeypatch.setattr(panel.TAREA, "lanzar",
                            lambda *a, **k: (_ for _ in ()).throw(
                                RuntimeError("Ya hay una tarea en marcha: audit")))
        codigo, d = post(servidor, "/api/tarea", {"nombre": "audit", "limite": 1})
        assert codigo == 409 and "Ya hay una tarea" in d["error"]

    def test_estado_inicial(self, servidor):
        e = get(servidor, "/api/tarea")
        assert e["viva"] is False and e["nombre"] is None and e["lineas"] == []

    def test_la_tarea_trabaja_sobre_la_bd_que_enseña_el_panel(self, servidor,
                                                                monkeypatch):
        """El subproceso resolvía su propia BD. En una máquina con BD por
        defecto el panel podía enseñar una y sus botones tocar otra; lo cazó
        el CI, donde no hay ninguna y el comando reventaba."""
        capturado = {}
        real = panel.subprocess.Popen

        def popen(cmd, **kw):
            capturado.update(kw)
            return real(cmd, **kw)

        monkeypatch.setattr(panel.subprocess, "Popen", popen)
        post(servidor, "/api/tarea", {"nombre": "audit", "limite": 0})
        assert capturado["env"]["PROSPECTOR_DB"] == str(db.DB_PATH)

    def test_lanza_y_recoge_la_salida(self, servidor):
        """Se usa `audit` con tope 0: no hay nada auditable, así que no sale
        a la red, pero recorre el camino entero de lanzar y leer."""
        codigo, _ = post(servidor, "/api/tarea", {"nombre": "audit", "limite": 0})
        assert codigo == 200
        for _ in range(100):
            e = get(servidor, "/api/tarea")
            if not e["viva"] and e["codigo"] is not None:
                break
            time.sleep(0.1)
        assert e["codigo"] == 0
        assert any("terminado" in ln for ln in e["lineas"])
