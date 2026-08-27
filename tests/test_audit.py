"""Auditoría y scoring. Todo con dobles: ningún test sale a la red."""
import pytest

from prospector import audit
from prospector.audit import _copyright_year, auditar

HTML_MODERNA = """<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head><body>%s</body></html>""" % ("Contenido de sobra. " * 100)

HTML_SIN_VIEWPORT = """<!doctype html><html><head><title>X</title></head>
<body>%s</body></html>""" % ("Texto largo del negocio. " * 100)


@pytest.fixture
def sin_red(monkeypatch):
    """Corta la red y deja que cada test decida qué devuelve."""
    estado = {"fetch": (200, HTML_MODERNA, True, None), "wayback": None}
    monkeypatch.setattr(audit, "_fetch", lambda url, timeout=12: estado["fetch"])
    monkeypatch.setattr(audit, "_wayback_last", lambda url: estado["wayback"])
    return estado


class TestCopyrightYear:
    def test_encuentra_el_ano(self):
        assert _copyright_year("<footer>© 2011 Talleres Pepe</footer>") == 2011

    def test_entidad_html(self):
        assert _copyright_year("&copy; 2008 Ejemplo") == 2008

    def test_se_queda_con_el_mas_reciente(self):
        assert _copyright_year("© 2009 uno ... Copyright 2015 dos") == 2015

    def test_sin_copyright(self):
        assert _copyright_year("<p>Bienvenidos</p>") is None

    def test_ano_suelto_sin_copyright_no_cuenta(self):
        assert _copyright_year("Fundada en 1998") is None


class TestCarriles:
    def test_sin_via_de_contacto_se_descarta(self, negocio, sin_red):
        """Sin teléfono ni email no hay forma de venderle: fuera."""
        negocio.update(phone=None, email=None)
        res = auditar(negocio)
        assert res["track"] == "web_ok"
        assert res["score"] == 0

    def test_sin_web(self, negocio, sin_red):
        res = auditar(negocio)
        assert res["track"] == "sin_web"
        assert "no consta web" in res["signals"]

    @pytest.mark.parametrize("status", [500, 503, 404])
    def test_web_caida(self, negocio, sin_red, status):
        negocio["website"] = "https://ejemplo.es"
        sin_red["fetch"] = (status, "", False, None)
        assert auditar(negocio)["track"] == "web_caida"

    def test_dominio_muerto_es_web_caida(self, negocio, sin_red):
        negocio["website"] = "https://ejemplo.es"
        sin_red["fetch"] = (None, "", False, "ConnectionError")
        res = auditar(negocio)
        assert res["track"] == "web_caida"
        assert any("inaccesible" in s for s in res["signals"])

    def test_web_moderna_sale_de_la_cola(self, negocio, sin_red):
        negocio["website"] = "https://ejemplo.es"
        assert auditar(negocio)["track"] == "web_ok"

    def test_web_sin_viewport_es_obsoleta(self, negocio, sin_red):
        negocio["website"] = "https://ejemplo.es"
        sin_red["fetch"] = (200, HTML_SIN_VIEWPORT, True, None)
        res = auditar(negocio)
        assert res["track"] == "web_obsoleta"
        assert res["has_viewport"] == 0
        assert any("viewport" in s for s in res["signals"])


class TestSenales:
    def _auditar(self, negocio, sin_red, html, **kw):
        negocio["website"] = "https://ejemplo.es"
        sin_red["fetch"] = (200, html, kw.pop("https_ok", True), None)
        sin_red["wayback"] = kw.pop("wayback", None)
        return auditar(negocio)

    def test_viewport_es_la_senal_de_mas_peso(self, negocio, sin_red):
        """+26: es lo que se demuestra en tres segundos delante del dueño."""
        con_vp = self._auditar(dict(negocio), sin_red, HTML_MODERNA)
        sin_vp = self._auditar(dict(negocio), sin_red, HTML_SIN_VIEWPORT)
        assert sin_vp["score"] - con_vp["score"] == 26

    def test_tls_roto_puntua(self, negocio, sin_red):
        res = self._auditar(negocio, sin_red, HTML_SIN_VIEWPORT, https_ok=False)
        assert any("HTTPS" in s for s in res["signals"])

    @pytest.mark.parametrize("marca,etiqueta", [
        ('<frameset cols="20%,80%">', "frames HTML"),
        ('<embed src="intro.swf">', "Flash"),
        ("<marquee>Novedades</marquee>", "etiquetas obsoletas"),
        ('<script src="jquery-1.7.2.js">', "jQuery 1.x antiguo"),
        ('<link href="bootstrap-2.3.css">', "Bootstrap 2"),
    ])
    def test_tecnologia_antigua(self, negocio, sin_red, marca, etiqueta):
        res = self._auditar(negocio, sin_red, HTML_SIN_VIEWPORT + marca)
        assert etiqueta in res["signals"]

    def test_cms_antiguo(self, negocio, sin_red):
        html = HTML_SIN_VIEWPORT.replace(
            "<title>", '<meta name="generator" content="Joomla! 1.5"><title>')
        res = self._auditar(negocio, sin_red, html)
        assert any("CMS antiguo" in s for s in res["signals"])
        assert res["generator"] == "Joomla! 1.5"

    def test_wordpress_viejo(self, negocio, sin_red):
        html = HTML_SIN_VIEWPORT.replace(
            "<title>", '<meta name="generator" content="WordPress 4.9.8"><title>')
        res = self._auditar(negocio, sin_red, html)
        assert any("WordPress 4.9" in s for s in res["signals"])

    def test_wordpress_actual_no_puntua(self, negocio, sin_red):
        html = HTML_SIN_VIEWPORT.replace(
            "<title>", '<meta name="generator" content="WordPress 6.5.2"><title>')
        res = self._auditar(negocio, sin_red, html)
        assert not any("WordPress" in s for s in res["signals"])

    def test_web_abandonada_en_wayback(self, negocio, sin_red):
        res = self._auditar(negocio, sin_red, HTML_SIN_VIEWPORT, wayback="2015-03-01")
        assert any("sin cambios desde 2015" in s for s in res["signals"])
        assert res["wayback_last"] == "2015-03-01"

    def test_contenido_minimo(self, negocio, sin_red):
        res = self._auditar(negocio, sin_red, "<html><body>Hola</body></html>")
        assert "contenido mínimo" in res["signals"]


class TestModificadores:
    def test_sector_suma_su_valor(self, negocio, sin_red):
        dentista = auditar({**negocio, "category": "dentist"})
        panaderia = auditar({**negocio, "category": "bakery"})
        assert dentista["score"] - panaderia["score"] == 18 - 6

    def test_sector_desconocido_usa_el_valor_base(self, negocio, sin_red):
        res = auditar({**negocio, "category": "inventado"})
        assert "sector inventado (+5)" in res["signals"]

    def test_telefono_y_email_suma_mas_que_solo_telefono(self, negocio, sin_red):
        ambos = auditar({**negocio, "email": "info@ejemplo.es"})
        solo = auditar(negocio)
        assert ambos["score"] - solo["score"] == 2

    def test_cercania_facilita_la_visita(self, negocio, sin_red):
        cerca = auditar({**negocio, "dist_km": 5})
        lejos = auditar({**negocio, "dist_km": 40})
        assert cerca["score"] > lejos["score"]
        assert any("lejos" in s for s in lejos["signals"])


class TestRangoDeScore:
    @pytest.mark.parametrize("dist", [0, 5, 25, 60])
    def test_score_siempre_entre_0_y_100(self, negocio, sin_red, dist):
        res = auditar({**negocio, "dist_km": dist})
        assert 0 <= res["score"] <= 100

    def test_score_es_entero(self, negocio, sin_red):
        assert isinstance(auditar(negocio)["score"], int)


class TestBugsDeAuditoria:
    def test_web_caida_no_afirma_que_no_hay_https(self, negocio, sin_red):
        """Si la web no responde no se sabe si tiene HTTPS: es NULL, no falso.
        Guardar 0 ensucia cualquier recuento posterior de webs sin TLS."""
        negocio["website"] = "https://ejemplo.es"
        sin_red["fetch"] = (None, "", False, "ConnectionError")
        res = auditar(negocio)
        assert res["track"] == "web_caida"
        assert res["https_ok"] is None

    def test_web_viva_si_afirma_su_estado_de_https(self, negocio, sin_red):
        negocio["website"] = "https://ejemplo.es"
        sin_red["fetch"] = (200, HTML_SIN_VIEWPORT, False, None)
        assert auditar(negocio)["https_ok"] == 0
