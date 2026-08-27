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
    @pytest.fixture
    def sin_llavero(self, monkeypatch):
        monkeypatch.setattr(places, "_del_llavero", lambda: None)

    def test_sin_clave_avisa_de_como_ponerla(self, monkeypatch, sin_llavero):
        monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
        with pytest.raises(places.SinClave, match="clave --guardar"):
            places.clave()

    def test_clave_vacia_cuenta_como_ausente(self, monkeypatch, sin_llavero):
        monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "   ")
        with pytest.raises(places.SinClave):
            places.clave()

    def test_cae_al_llavero_si_no_hay_variable(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
        monkeypatch.setattr(places, "_del_llavero", lambda: "del-llavero")
        assert places.clave() == "del-llavero"

    def test_la_variable_manda_sobre_el_llavero(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "de-la-variable")
        monkeypatch.setattr(places, "_del_llavero", lambda: "del-llavero")
        assert places.clave() == "de-la-variable"


class TestLlavero:
    def _security(self, monkeypatch, returncode=0, stdout="clave-guardada"):
        import subprocess
        vistos = []

        def run(cmd, **kw):
            vistos.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")

        monkeypatch.setattr(places.subprocess, "run", run)
        monkeypatch.setattr(places.sys, "platform", "darwin")
        return vistos

    def test_lee_del_llavero_por_nombre_de_servicio(self, monkeypatch):
        vistos = self._security(monkeypatch)
        assert places._del_llavero() == "clave-guardada"
        assert vistos[0] == ["security", "find-generic-password",
                             "-s", places.LLAVERO, "-w"]

    def test_llavero_vacio_devuelve_none(self, monkeypatch):
        self._security(monkeypatch, returncode=44, stdout="")
        assert places._del_llavero() is None

    def test_fuera_de_macos_no_intenta_el_llavero(self, monkeypatch):
        monkeypatch.setattr(places.sys, "platform", "linux")
        monkeypatch.setattr(places.subprocess, "run",
                            lambda *a, **k: pytest.fail("no debe llamar a security"))
        assert places._del_llavero() is None

    def test_sin_el_binario_security_no_revienta(self, monkeypatch):
        monkeypatch.setattr(places.sys, "platform", "darwin")
        monkeypatch.setattr(places.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert places._del_llavero() is None

    def test_guardar_no_captura_la_salida(self, monkeypatch):
        """La clave la teclea el usuario en su terminal: si capturásemos la
        salida, `security` no podría pedirla y pasaría por este proceso."""
        capturas = []

        def run(cmd, **kw):
            import subprocess
            capturas.append(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(places.sys, "platform", "darwin")
        monkeypatch.setattr(places.subprocess, "run", run)
        places.guardar_en_llavero()
        assert "capture_output" not in capturas[0]
        assert "input" not in capturas[0]

    def test_guardar_pasa_w_sin_valor(self, monkeypatch):
        """Con -w y sin valor, `security` la pide por teclado y no queda en
        el historial del shell."""
        vistos = self._security(monkeypatch)
        places.guardar_en_llavero()
        assert vistos[0][-1] == "-w"
        assert not any(a.startswith("-w") and len(a) > 2 for a in vistos[0])

    def test_origen_dice_de_donde_sale_sin_enseñarla(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
        monkeypatch.setattr(places, "_del_llavero", lambda: "secreto")
        assert places.origen_clave() == "Llavero de macOS"
        monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "otra")
        assert places.origen_clave() == "variable de entorno"

    def test_origen_none_si_no_hay_nada(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
        monkeypatch.setattr(places, "_del_llavero", lambda: None)
        assert places.origen_clave() is None


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
