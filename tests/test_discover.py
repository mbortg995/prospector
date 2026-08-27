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


class TestFetchOverpass:
    """Overpass es un servicio comunitario gratuito y se cae a menudo.
    `fetch` es lo único que separa una pasada de censo de perderla entera."""

    def _respuesta(self, status=200, payload=None):
        class R:
            status_code = status
            def json(self):
                return payload or {"elements": []}
        return R()

    def test_devuelve_los_negocios_a_la_primera(self, monkeypatch):
        from prospector import discover
        payload = {"elements": [_nodo(name="Bar Ejemplo", amenity="bar")]}
        monkeypatch.setattr(discover.requests, "post",
                            lambda *a, **k: self._respuesta(200, payload))
        assert len(discover.fetch(1000)) == 1

    def test_reintenta_tras_un_error_de_red(self, monkeypatch):
        """Un timeout en el intento 1 no puede tumbar el censo entero."""
        from prospector import discover
        intentos = []

        def post(*a, **k):
            intentos.append(1)
            if len(intentos) == 1:
                raise discover.requests.exceptions.ConnectTimeout("timeout")
            return self._respuesta(200, {"elements": [_nodo(name="Bar", amenity="bar")]})

        monkeypatch.setattr(discover.requests, "post", post)
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        assert len(discover.fetch(1000)) == 1
        assert len(intentos) == 2

    def test_reintenta_tras_un_429(self, monkeypatch):
        from prospector import discover
        intentos = []

        def post(*a, **k):
            intentos.append(1)
            return self._respuesta(429 if len(intentos) == 1 else 200)

        monkeypatch.setattr(discover.requests, "post", post)
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        discover.fetch(1000)
        assert len(intentos) == 2

    def test_error_de_red_persistente_da_mensaje_claro(self, monkeypatch):
        """Antes esto reventaba con UnboundLocalError sobre `r`."""
        from prospector import discover
        monkeypatch.setattr(discover.requests, "post", lambda *a, **k: (_ for _ in ()).throw(
            discover.requests.exceptions.ConnectionError("sin red")))
        monkeypatch.setattr(discover.time, "sleep", lambda s: None)
        with pytest.raises(RuntimeError, match="Overpass"):
            discover.fetch(1000)

    def test_espera_mas_en_cada_reintento(self, monkeypatch):
        from prospector import discover
        esperas = []
        monkeypatch.setattr(discover.requests, "post", lambda *a, **k: self._respuesta(504))
        monkeypatch.setattr(discover.time, "sleep", esperas.append)
        with pytest.raises(RuntimeError):
            discover.fetch(1000)
        assert esperas == sorted(esperas) and len(esperas) >= 2
