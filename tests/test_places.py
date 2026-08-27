"""Google Places. Cada consulta se paga, así que aquí importan dos cosas:
no gastar de más y no rellenar un teléfono equivocado."""
import pytest

from prospector import places
from prospector.discover import _dist_km


@pytest.fixture
def con_clave(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "clave-de-prueba")


def _sitio(nombre, lat=39.5878, lon=-0.5397, tlf="961234567",
           web="https://ejemplo.es", estado="OPERATIONAL", pid="places/abc"):
    return {"id": pid, "displayName": {"text": nombre},
            "location": {"latitude": lat, "longitude": lon},
            "nationalPhoneNumber": tlf, "websiteUri": web,
            "businessStatus": estado}


NEGOCIO = {"name": "Panadería Pepe", "lat": 39.5878, "lon": -0.5397}


class TestClave:
    def test_sin_clave_avisa_de_como_ponerla(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
        with pytest.raises(places.SinClave, match="GOOGLE_PLACES_API_KEY"):
            places.clave()

    def test_clave_vacia_cuenta_como_ausente(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "   ")
        with pytest.raises(places.SinClave):
            places.clave()


class TestSimilitud:
    def test_identicos(self):
        assert places.similitud("Panadería Pepe", "Panaderia Pepe") == 1.0

    def test_ignora_acentos_mayusculas_y_puntuacion(self):
        assert places.similitud("Bar El Rincón", "BAR EL RINCON!!") == 1.0

    def test_nombre_ampliado_sigue_pareciendose(self):
        assert places.similitud("Panadería Pepe", "Panadería Pepe e Hijos") > 0.7

    def test_negocios_distintos_no_se_parecen(self):
        assert places.similitud("Panadería Pepe", "Clínica Dental Sonrisa") < 0.4


class TestEmparejamiento:
    """Un teléfono mal asignado hace que llames a otro negocio."""

    def test_mismo_nombre_y_sitio_casa(self):
        elegido, motivo = places.elegir([_sitio("Panadería Pepe")], NEGOCIO, _dist_km)
        assert motivo == "ok"
        assert elegido["similitud"] == 1.0

    def test_mismo_nombre_pero_lejos_no_casa(self):
        """Hay una Panadería Pepe en cada pueblo."""
        lejos = _sitio("Panadería Pepe", lat=39.70, lon=-0.60)
        assert places.elegir([lejos], NEGOCIO, _dist_km)[0] is None

    def test_al_lado_pero_otro_negocio_no_casa(self):
        """El bar de al lado está a 20 metros."""
        vecino = _sitio("Bar Manolo", lat=39.5879, lon=-0.5398)
        assert places.elegir([vecino], NEGOCIO, _dist_km)[0] is None

    def test_cerrado_definitivamente_se_descarta(self):
        cerrado = _sitio("Panadería Pepe", estado="CLOSED_PERMANENTLY")
        assert places.elegir([cerrado], NEGOCIO, _dist_km)[0] is None

    def test_sin_coordenadas_se_descarta(self):
        malo = _sitio("Panadería Pepe")
        del malo["location"]
        assert places.elegir([malo], NEGOCIO, _dist_km)[0] is None

    def test_sin_candidatos(self):
        assert places.elegir([], NEGOCIO, _dist_km) == (None, "sin candidatos")

    def test_elige_el_mas_parecido_entre_varios_validos(self):
        cands = [_sitio("Panadería Pepe e Hijos", pid="places/1"),
                 _sitio("Panadería Pepe", pid="places/2")]
        elegido, _ = places.elegir(cands, NEGOCIO, _dist_km)
        assert elegido["place"]["id"] == "places/2"

    def test_el_motivo_dice_cuantos_se_vieron(self):
        _, motivo = places.elegir([_sitio("Otra Cosa")], NEGOCIO, _dist_km)
        assert "1 vistos" in motivo


class TestBuscar:
    def _respuesta(self, status=200, payload=None, texto=""):
        class R:
            status_code = status
            text = texto
            def json(self):
                return payload or {"places": []}
        return R()

    def test_pide_solo_los_campos_que_usa(self, monkeypatch, con_clave):
        """La máscara de campos decide el nivel de facturación."""
        capturado = {}

        def post(url, json=None, headers=None, timeout=None):
            capturado.update(url=url, json=json, headers=headers)
            return self._respuesta(200, {"places": [_sitio("X")]})

        monkeypatch.setattr(places.requests, "post", post)
        places.buscar("Panadería Pepe Llíria", 39.58, -0.53)
        assert capturado["headers"]["X-Goog-Api-Key"] == "clave-de-prueba"
        assert capturado["headers"]["X-Goog-FieldMask"] == places.CAMPOS
        assert "places.reviews" not in places.CAMPOS  # nada que no se use
        assert capturado["json"]["regionCode"] == "ES"
        assert capturado["json"]["locationBias"]["circle"]["center"]["latitude"] == 39.58

    def test_devuelve_los_sitios(self, monkeypatch, con_clave):
        monkeypatch.setattr(places.requests, "post",
                            lambda *a, **k: self._respuesta(200, {"places": [_sitio("X")]}))
        assert len(places.buscar("X", 39.58, -0.53)) == 1

    @pytest.mark.parametrize("status", [401, 403])
    def test_clave_rechazada_no_se_reintenta(self, monkeypatch, con_clave, status):
        """Reintentar no arregla una clave mal configurada y se factura igual."""
        intentos = []
        monkeypatch.setattr(places.requests, "post", lambda *a, **k: intentos.append(1) or
                            self._respuesta(status, texto="PERMISSION_DENIED"))
        with pytest.raises(places.PlacesError, match="rechaza la clave"):
            places.buscar("X", 39.58, -0.53)
        assert len(intentos) == 1

    def test_consulta_mal_formada_no_se_reintenta(self, monkeypatch, con_clave):
        intentos = []
        monkeypatch.setattr(places.requests, "post", lambda *a, **k: intentos.append(1) or
                            self._respuesta(400, texto="INVALID_ARGUMENT"))
        with pytest.raises(places.PlacesError, match="mal formada"):
            places.buscar("X", 39.58, -0.53)
        assert len(intentos) == 1

    def test_error_del_servidor_si_se_reintenta(self, monkeypatch, con_clave):
        intentos = []

        def post(*a, **k):
            intentos.append(1)
            return self._respuesta(200 if len(intentos) == 2 else 503)

        monkeypatch.setattr(places.requests, "post", post)
        monkeypatch.setattr(places.time, "sleep", lambda s: None)
        places.buscar("X", 39.58, -0.53)
        assert len(intentos) == 2

    def test_error_de_red_se_reintenta(self, monkeypatch, con_clave):
        intentos = []

        def post(*a, **k):
            intentos.append(1)
            if len(intentos) == 1:
                raise places.requests.exceptions.ConnectTimeout("timeout")
            return self._respuesta(200)

        monkeypatch.setattr(places.requests, "post", post)
        monkeypatch.setattr(places.time, "sleep", lambda s: None)
        places.buscar("X", 39.58, -0.53)
        assert len(intentos) == 2

    def test_la_clave_no_aparece_en_los_mensajes_de_error(self, monkeypatch, con_clave):
        monkeypatch.setattr(places.requests, "post",
                            lambda *a, **k: self._respuesta(403, texto="denegado"))
        with pytest.raises(places.PlacesError) as e:
            places.buscar("X", 39.58, -0.53)
        assert "clave-de-prueba" not in str(e.value)

    def test_sin_clave_no_llega_a_pedir_nada(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
        monkeypatch.setattr(places.requests, "post", lambda *a, **k: 1 / 0)
        with pytest.raises(places.SinClave):
            places.buscar("X", 39.58, -0.53)
