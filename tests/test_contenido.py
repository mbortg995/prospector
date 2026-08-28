"""Material para las maquetas. Una maqueta genérica no vende: el dueño tiene
que reconocer su negocio en la pantalla."""
import pytest

from prospector import contenido

WEB = """<!doctype html><html><head>
<title>Panadería Pepe — Pan artesano en Llíria</title>
<meta name="description" content="Horno de leña desde 1974 en el centro de Llíria.">
</head><body>
<nav><ul><li>Inicio</li><li>Contacto</li><li>Aviso legal</li></ul></nav>
<h1>Panadería Pepe</h1>
<h2>Nuestro obrador</h2>
<p>Amasamos cada día con masa madre y horneamos en horno de leña, como hacía
el abuelo cuando abrió el obrador en la calle Mayor en 1974.</p>
<p>Corto.</p>
<h3>Especialidades</h3>
<ul><li>Pan de masa madre</li><li>Coca de llanda</li><li>Empanadas</li></ul>
<p>Horario: de lunes a sábado de 7:00 a 14:00 y de 17:00 a 20:00.</p>
<a href="https://facebook.com/panaderiapepe">Facebook</a>
<footer>info@panaderiapepe.es · 961 23 45 67</footer>
</body></html>"""


class TestExtraer:
    @pytest.fixture
    def d(self):
        return contenido.extraer(WEB)

    def test_titulo_y_descripcion(self, d):
        assert "Panadería Pepe" in d["titulo"]
        assert "1974" in d["descripcion"]

    def test_encabezados_sin_el_menu(self, d):
        assert "Panadería Pepe" in d["encabezados"]
        assert "Nuestro obrador" in d["encabezados"]
        assert "Inicio" not in d["encabezados"]

    def test_parrafos_largos_si_cortos_no(self, d):
        assert any("masa madre" in p for p in d["parrafos"])
        assert "Corto." not in d["parrafos"]

    def test_servicios_desde_las_listas(self, d):
        assert "Pan de masa madre" in d["servicios"]
        assert "Coca de llanda" in d["servicios"]

    def test_el_menu_no_cuenta_como_servicio(self, d):
        for basura in ("Inicio", "Contacto", "Aviso legal"):
            assert basura not in d["servicios"]

    def test_horario(self, d):
        assert d["horario"] and "7:00" in d["horario"]

    def test_contacto_y_redes(self, d):
        assert "info@panaderiapepe.es" in d["emails"]
        assert any("facebook" in r for r in d["redes"])

    def test_no_se_cuela_javascript(self):
        d = contenido.extraer(
            "<html><body><script>var x = 'texto oculto';</script>"
            "<p>" + "Contenido real de la web. " * 5 + "</p></body></html>")
        assert not any("var x" in p for p in d["parrafos"])

    def test_web_vacia_no_revienta(self):
        d = contenido.extraer("<html></html>")
        assert d["titulo"] is None and d["parrafos"] == []

    def test_no_repite(self):
        d = contenido.extraer("<html><body>" + "<h2>Servicios</h2>" * 3 + "</body></html>")
        assert d["encabezados"] == ["Servicios"]


class TestWebsMinimas:
    """El caso real de «Rozalén hnos»: 144 caracteres en toda la web, y ahí
    dentro su polígono, su email y un «(c) 2011». El umbral de párrafo largo
    la tiraba entera."""

    MINIMA = """<html><head><title>Home</title></head><body>
    <div>Home</div><div>Pol. Ind. "Les Eres" C/ La Caiguda, 12</div>
    <div>46180 Benaguasil</div><div>info@rozalen.es</div>
    <div>96 273 81 39</div><div>(c) 2011 Rozalen Hermanos C.B.</div>
    </body></html>"""

    def test_rescata_las_lineas_sueltas(self):
        d = contenido.extraer(self.MINIMA)
        assert 'Pol. Ind. "Les Eres" C/ La Caiguda, 12' in d["lineas"]
        assert "46180 Benaguasil" in d["lineas"]

    def test_las_lineas_no_traen_el_menu(self):
        assert "Home" not in contenido.extraer(self.MINIMA)["lineas"]

    def test_saca_el_año_del_copyright(self):
        """Un «(c) 2011» es el argumento de venta más directo que hay."""
        assert contenido.extraer(self.MINIMA)["copyright"] == 2011

    def test_sin_copyright_queda_a_none(self):
        assert contenido.extraer("<html><body>Hola</body></html>")["copyright"] is None

    def test_una_web_minima_sigue_sirviendo(self):
        assert contenido.aprovechable(contenido.extraer(self.MINIMA))


class TestWebsPintadasConJs:
    """El caso real de «Babalù»: 42 KB de HTML y 6 caracteres de texto.
    Wayback archiva el JavaScript pero no lo ejecuta."""

    def test_se_detecta(self):
        html = "<html><body><div>Inicio</div>" + "<link href='x'>" * 2000 + "</body></html>"
        assert contenido.extraer(html)["pintada_con_js"] is True

    def test_una_web_normal_no_se_marca(self):
        assert contenido.extraer(WEB)["pintada_con_js"] is False

    def test_no_se_da_por_aprovechable(self):
        html = "<html><body><div>Inicio</div>" + "<link href='x'>" * 2000 + "</body></html>"
        assert contenido.aprovechable(contenido.extraer(html)) is False


class TestAprovechable:
    """Contar fuentes engañaba: 10 de 12 venían de Wayback pero solo 5 traían
    algo con lo que escribir."""

    def test_sin_material(self):
        assert contenido.aprovechable(None) is False

    def test_una_web_completa_si(self):
        assert contenido.aprovechable(contenido.extraer(WEB))

    def test_una_pagina_vacia_no(self):
        assert contenido.aprovechable(contenido.extraer("<html><body></body></html>")) is False

    def test_reunir_lo_marca(self):
        d = contenido.reunir({"name": "X", "category": "bakery", "website": None})
        assert d["aprovechable"] is False


class TestFuentes:
    def test_de_la_web(self, monkeypatch):
        monkeypatch.setattr(contenido, "_bajar", lambda url, timeout=15: WEB)
        d = contenido.de_la_web("https://ejemplo.es")
        assert d["fuente"] == "web" and "Panadería" in d["titulo"]

    def test_web_que_no_responde(self, monkeypatch):
        monkeypatch.setattr(contenido, "_bajar", lambda url, timeout=15: None)
        assert contenido.de_la_web("https://ejemplo.es") is None

    def test_wayback_trae_la_web_archivada(self, monkeypatch):
        """Para un dominio muerto es el único material que queda, y es
        exactamente lo que se enseña en el antes/después."""
        class R:
            status_code = 200
            def json(self):
                return {"archived_snapshots": {"closest": {
                    "url": "http://web.archive.org/web/20130521/http://ejemplo.es",
                    "timestamp": "20130521120000"}}}
        monkeypatch.setattr(contenido.requests, "get", lambda *a, **k: R())
        monkeypatch.setattr(contenido, "_bajar", lambda url, timeout=25: WEB)
        d = contenido.de_wayback("https://ejemplo.es")
        assert d["fuente"] == "wayback"
        assert d["capturada"] == "2013-05-21"
        assert "Panadería Pepe" in d["encabezados"]

    def test_wayback_sin_capturas(self, monkeypatch):
        class R:
            status_code = 200
            def json(self):
                return {"archived_snapshots": {}}
        monkeypatch.setattr(contenido.requests, "get", lambda *a, **k: R())
        assert contenido.de_wayback("https://ejemplo.es") is None


class TestReunir:
    NEGOCIO = {"name": "Panadería Pepe", "category": "bakery",
               "municipality": "Llíria", "address": "Calle Mayor 3",
               "phone": "+34961234567", "email": None, "website": None}

    def test_sin_web_no_sale_a_la_red(self, monkeypatch):
        """122 de los 162 en cola no tienen web: ahí no hay nada que raspar."""
        monkeypatch.setattr(contenido, "_bajar",
                            lambda *a, **k: pytest.fail("no debe descargar nada"))
        d = contenido.reunir(dict(self.NEGOCIO), "sin_web")
        assert d["material"] is None
        assert d["nombre"] == "Panadería Pepe" and d["carril"] == "sin_web"

    def test_con_web_usa_su_web(self, monkeypatch):
        monkeypatch.setattr(contenido, "de_la_web",
                            lambda url: {"fuente": "web", "titulo": "X"})
        d = contenido.reunir({**self.NEGOCIO, "website": "https://ejemplo.es"},
                             "web_obsoleta")
        assert d["material"]["fuente"] == "web"

    def test_si_la_web_no_responde_cae_a_wayback(self, monkeypatch):
        """Es justo el caso `web_caida`, que es el mejor carril de todos."""
        monkeypatch.setattr(contenido, "de_la_web", lambda url: None)
        monkeypatch.setattr(contenido, "de_wayback",
                            lambda url: {"fuente": "wayback", "capturada": "2013-05-21"})
        d = contenido.reunir({**self.NEGOCIO, "website": "https://muerta.es"},
                             "web_caida")
        assert d["material"]["fuente"] == "wayback"

    def test_lleva_los_datos_del_negocio(self):
        d = contenido.reunir(dict(self.NEGOCIO))
        assert (d["sector"], d["municipio"], d["telefono"]) == (
            "bakery", "Llíria", "+34961234567")
