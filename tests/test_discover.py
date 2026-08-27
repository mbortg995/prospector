"""El parser de Overpass. Nunca se ha ejecutado contra la API real:
estos tests son la única red antes de la primera pasada de verdad."""
import pytest

from prospector.discover import (
    LA_POBLA,
    _categoria,
    _dist_km,
    _email,
    _es_cadena,
    _telefono,
    _web,
    parse_overpass,
)


def _nodo(osm_id=1, lat=39.60, lon=-0.54, **tags):
    return {"type": "node", "id": osm_id, "lat": lat, "lon": lon, "tags": tags}


class TestDistancia:
    def test_mismo_punto_es_cero(self):
        assert _dist_km(*LA_POBLA, *LA_POBLA) == 0.0

    def test_distancia_conocida(self):
        # La Pobla de Vallbona → Llíria, ~7 km en línea recta
        assert 5 < _dist_km(39.5878, -0.5397, 39.6275, -0.5983) < 9

    def test_redondea_a_dos_decimales(self):
        d = _dist_km(39.5878, -0.5397, 39.6275, -0.5983)
        assert d == round(d, 2)


class TestCadenas:
    @pytest.mark.parametrize("tags", [
        {"brand": "Mercadona"},
        {"brand:wikidata": "Q377705"},
        {"operator:wikidata": "Q377705"},
        {"operator": "Talleres Ejemplo S.A."},
        {"operator": "Sociedad Cooperativa Valenciana"},
        {"operator": "Example Group"},
        {"operator": "Ejemplo Holding"},
    ])
    def test_detecta_franquicia(self, tags):
        assert _es_cadena(tags) is True

    @pytest.mark.parametrize("tags", [
        {},
        {"name": "Panadería Pepe"},
        {"operator": "Pepe Martínez"},
    ])
    def test_negocio_local_no_es_cadena(self, tags):
        assert _es_cadena(tags) is False


class TestCategoria:
    def test_prioriza_shop_sobre_amenity(self):
        assert _categoria({"shop": "bakery", "amenity": "cafe"}) == "bakery"

    def test_cae_a_otro_sin_tags_conocidos(self):
        assert _categoria({"name": "X"}) == "otro"


class TestContacto:
    def test_email_valido_se_normaliza(self):
        assert _email({"contact:email": "  Info@Ejemplo.ES "}) == "info@ejemplo.es"

    def test_email_malformado_se_descarta(self):
        assert _email({"email": "info(arroba)ejemplo.es"}) is None

    def test_telefono_se_limpia(self):
        assert _telefono({"phone": "+34 961 23 45 67"}) == "+34961234567"

    def test_telefono_multiple_toma_el_primero(self):
        assert _telefono({"phone": "961234567;+34600111222"}) == "961234567"

    def test_sin_telefono(self):
        assert _telefono({"name": "X"}) is None


class TestWeb:
    def test_anade_esquema_si_falta(self):
        assert _web({"website": "ejemplo.es"}) == "https://ejemplo.es"

    def test_respeta_http_existente(self):
        assert _web({"website": "http://ejemplo.es"}) == "http://ejemplo.es"

    @pytest.mark.parametrize("url", [
        "https://facebook.com/negocio",
        "https://www.instagram.com/negocio",
        "https://paginasamarillas.es/negocio",
    ])
    def test_red_social_cuenta_como_no_tener_web(self, url):
        """Decisión del proyecto: una página de Facebook no es una web."""
        assert _web({"website": url}) is None

    def test_sin_web(self):
        assert _web({"name": "X"}) is None


class TestParseOverpass:
    def test_nodo_completo(self):
        datos = {"elements": [_nodo(
            osm_id=42, name="Panadería Pepe", shop="bakery",
            phone="961234567", website="ejemplo.es",
            **{"addr:street": "Calle Mayor", "addr:housenumber": "3", "addr:city": "Llíria"},
        )]}
        (b,) = parse_overpass(datos)
        assert b["osm_type"] == "node"
        assert b["osm_id"] == 42
        assert b["name"] == "Panadería Pepe"
        assert b["category"] == "bakery"
        assert b["address"] == "Calle Mayor 3"
        assert b["municipality"] == "Llíria"
        assert b["website"] == "https://ejemplo.es"
        assert b["is_chain"] == 0
        assert b["dist_km"] > 0

    def test_way_usa_el_centro(self):
        datos = {"elements": [
            {"type": "way", "id": 7, "center": {"lat": 39.60, "lon": -0.54},
             "tags": {"name": "Hotel Ejemplo", "tourism": "hotel"}},
        ]}
        (b,) = parse_overpass(datos)
        assert (b["lat"], b["lon"]) == (39.60, -0.54)

    def test_descarta_sin_nombre(self):
        assert parse_overpass({"elements": [_nodo(shop="bakery")]}) == []

    def test_descarta_ruido(self):
        datos = {"elements": [_nodo(name="Cajero", amenity="atm")]}
        assert parse_overpass(datos) == []

    def test_descarta_sin_coordenadas(self):
        datos = {"elements": [{"type": "way", "id": 9,
                               "tags": {"name": "Sin geo", "shop": "bakery"}}]}
        assert parse_overpass(datos) == []

    def test_marca_la_cadena_pero_no_la_descarta(self):
        """El filtrado de cadenas es del pipeline, no del parser."""
        datos = {"elements": [_nodo(name="Mercadona", shop="supermarket", brand="Mercadona")]}
        (b,) = parse_overpass(datos)
        assert b["is_chain"] == 1

    def test_sin_direccion_deja_none_no_cadena_vacia(self):
        datos = {"elements": [_nodo(name="Bar Ejemplo", amenity="bar")]}
        (b,) = parse_overpass(datos)
        assert b["address"] is None

    def test_respuesta_vacia(self):
        assert parse_overpass({"elements": []}) == []
        assert parse_overpass({}) == []


class TestBboxYTeselas:
    def test_bbox_circunscribe_el_circulo(self):
        from prospector.discover import _bbox, _dist_km
        s, w, n, e = _bbox(LA_POBLA, 10_000)
        # Los lados quedan justo a 10 km; las esquinas, más lejos.
        assert _dist_km(LA_POBLA[0], LA_POBLA[1], n, LA_POBLA[1]) == pytest.approx(10, abs=0.2)
        assert _dist_km(LA_POBLA[0], LA_POBLA[1], n, e) > 13

    def test_cuadrantes_cubren_la_tesela_sin_solaparse(self):
        from prospector.discover import _cuadrantes
        bbox = (39.0, -1.0, 40.0, 0.0)
        cs = _cuadrantes(bbox)
        assert len(cs) == 4
        assert min(c[0] for c in cs) == 39.0 and max(c[2] for c in cs) == 40.0
        assert min(c[1] for c in cs) == -1.0 and max(c[3] for c in cs) == 0.0


class TestFiltroPorRadio:
    def test_descarta_lo_que_cae_fuera_del_circulo(self):
        """La bbox llega hasta un 41% más lejos por las esquinas."""
        cerca = _nodo(osm_id=1, lat=39.5900, lon=-0.5420, name="Cerca", shop="bakery")
        lejos = _nodo(osm_id=2, lat=39.7000, lon=-0.7000, name="Lejos", shop="bakery")
        datos = {"elements": [cerca, lejos]}
        assert len(parse_overpass(datos, LA_POBLA)) == 2
        nombres = [b["name"] for b in parse_overpass(datos, LA_POBLA, radio_m=5000)]
        assert nombres == ["Cerca"]

    def test_sin_radio_no_filtra(self):
        datos = {"elements": [_nodo(lat=40.5, lon=-1.5, name="Muy lejos", shop="bakery")]}
        assert len(parse_overpass(datos, LA_POBLA)) == 1


class TestDeduplicacion:
    def test_una_via_en_dos_teselas_cuenta_una_vez(self):
        """Overpass devuelve la vía en cada tesela que su geometría toca."""
        via = {"type": "way", "id": 7, "center": {"lat": 39.59, "lon": -0.54},
               "tags": {"name": "Hotel Ejemplo", "tourism": "hotel"}}
        assert len(parse_overpass({"elements": [via, dict(via)]})) == 1

    def test_mismo_id_distinto_tipo_son_negocios_distintos(self):
        nodo = _nodo(osm_id=7, name="Bar", amenity="bar")
        via = {"type": "way", "id": 7, "center": {"lat": 39.59, "lon": -0.54},
               "tags": {"name": "Hotel", "tourism": "hotel"}}
        assert len(parse_overpass({"elements": [nodo, via]})) == 2


class TestPedirTesela:
    """Overpass es un servicio comunitario gratuito y se cae a menudo."""

    def _respuesta(self, status=200, payload=None):
        class R:
            status_code = status
            def json(self):
                if payload == "roto":
                    raise ValueError("truncado")
                return payload or {"elements": []}
        return R()

    def test_devuelve_el_json_a_la_primera(self, monkeypatch):
        from prospector import discover
        monkeypatch.setattr(discover.requests, "post",
                            lambda *a, **k: self._respuesta(200, {"elements": [1]}))
        assert discover._pedir((39.0, -1.0, 40.0, 0.0)) == {"elements": [1]}

    def test_rota_de_espejo_en_cada_reintento(self, monkeypatch):
        """No castigar siempre al mismo servidor."""
        from prospector import discover
        usados = []

        def post(url, *a, **k):
            usados.append(url)
            return self._respuesta(504 if len(usados) < 3 else 200)

        monkeypatch.setattr(discover.requests, "post", post)
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        discover._pedir((39.0, -1.0, 40.0, 0.0))
        assert usados == discover.MIRRORS[:3]

    def test_reintenta_tras_un_error_de_red(self, monkeypatch):
        from prospector import discover
        intentos = []

        def post(*a, **k):
            intentos.append(1)
            if len(intentos) == 1:
                raise discover.requests.exceptions.ConnectTimeout("timeout")
            return self._respuesta(200)

        monkeypatch.setattr(discover.requests, "post", post)
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        discover._pedir((39.0, -1.0, 40.0, 0.0))
        assert len(intentos) == 2

    def test_reintenta_tras_respuesta_truncada(self, monkeypatch):
        """Overpass saturado devuelve 200 con el JSON cortado a medias."""
        from prospector import discover
        intentos = []

        def post(*a, **k):
            intentos.append(1)
            return self._respuesta(200, "roto" if len(intentos) == 1 else None)

        monkeypatch.setattr(discover.requests, "post", post)
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        discover._pedir((39.0, -1.0, 40.0, 0.0))
        assert len(intentos) == 2

    def test_espera_mas_en_cada_reintento(self, monkeypatch):
        from prospector import discover
        esperas = []
        monkeypatch.setattr(discover.requests, "post", lambda *a, **k: self._respuesta(504))
        monkeypatch.setattr(discover.time, "sleep", esperas.append)
        with pytest.raises(discover.OverpassError, match="Overpass"):
            discover._pedir((39.0, -1.0, 40.0, 0.0))
        assert esperas == sorted(esperas) and len(esperas) == 2

    def test_error_persistente_da_mensaje_claro(self, monkeypatch):
        """Antes esto reventaba con UnboundLocalError sobre `r`."""
        from prospector import discover
        monkeypatch.setattr(discover.requests, "post", lambda *a, **k: (_ for _ in ()).throw(
            discover.requests.exceptions.ConnectionError("sin red")))
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        with pytest.raises(discover.OverpassError, match="sin red"):
            discover._pedir((39.0, -1.0, 40.0, 0.0))


class TestDescargar:
    def test_una_sola_consulta_si_overpass_responde(self, monkeypatch):
        """No partir en teselas cuando no hace falta: 1 consulta, no 4."""
        from prospector import discover
        llamadas = []
        monkeypatch.setattr(discover, "_pedir",
                            lambda bbox, **k: llamadas.append(bbox) or {"elements": []})
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        discover.descargar(15000)
        assert len(llamadas) == 1

    def test_parte_en_cuatro_si_la_tesela_falla(self, monkeypatch):
        from prospector import discover
        llamadas = []

        def pedir(bbox, **k):
            llamadas.append(bbox)
            if len(llamadas) == 1:
                raise discover.OverpassError("504")
            return {"elements": []}

        monkeypatch.setattr(discover, "_pedir", pedir)
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        discover.descargar(15000)
        assert len(llamadas) == 5  # la entera + sus cuatro cuadrantes

    def test_se_rinde_al_llegar_al_fondo(self, monkeypatch):
        from prospector import discover
        monkeypatch.setattr(discover, "_pedir", lambda bbox, **k: (_ for _ in ()).throw(
            discover.OverpassError("504")))
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        with pytest.raises(discover.OverpassError):
            discover.descargar(15000, profundidad_max=1)

    def test_combina_y_deduplica_las_teselas(self, monkeypatch):
        from prospector import discover
        respuestas = [
            {"elements": [{"type": "node", "id": 1}, {"type": "node", "id": 2}]},
            {"elements": [{"type": "node", "id": 2}, {"type": "node", "id": 3}]},
        ]
        llamadas = []

        def pedir(bbox, **k):
            llamadas.append(bbox)
            if len(llamadas) == 1:
                raise discover.OverpassError("504")
            return respuestas[min(len(llamadas) - 2, 1)]

        monkeypatch.setattr(discover, "_pedir", pedir)
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        ids = [e["id"] for e in discover.descargar(15000)["elements"]]
        assert sorted(ids) == [1, 2, 3]

    def test_espera_entre_teselas_pero_no_antes_de_la_primera(self, monkeypatch):
        from prospector import discover
        esperas, llamadas = [], []

        def pedir(bbox, **k):
            llamadas.append(bbox)
            if len(llamadas) == 1:
                raise discover.OverpassError("504")
            return {"elements": []}

        monkeypatch.setattr(discover, "_pedir", pedir)
        monkeypatch.setattr(discover.time, "sleep", esperas.append)
        discover.descargar(15000, espera=3.0)
        assert esperas == [3.0] * 4  # cuatro cuadrantes, ninguna antes de empezar


class TestFetch:
    def test_descarga_parsea_y_filtra(self, monkeypatch):
        from prospector import discover
        monkeypatch.setattr(discover, "descargar", lambda *a, **k: {"elements": [
            _nodo(osm_id=1, lat=39.5900, lon=-0.5420, name="Cerca", shop="bakery"),
            _nodo(osm_id=2, lat=39.7000, lon=-0.7000, name="Lejos", shop="bakery"),
        ]})
        assert [b["name"] for b in discover.fetch(5000)] == ["Cerca"]
