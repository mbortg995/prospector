import pytest

from prospector import db, places


@pytest.fixture(autouse=True)
def red_cortada(monkeypatch):
    """Ningún test sale a internet.

    Overpass y archive.org son servicios comunitarios gratuitos: una suite que
    los golpea sin querer es exactamente lo que el proyecto no quiere hacer.
    Si un doble se coloca en el sitio equivocado, esto lo caza en vez de
    dejar que el test pase saliendo a la red de verdad.
    """
    def prohibido(*a, **kw):
        raise AssertionError(
            "Un test ha intentado salir a la red. Revisa dónde está el doble: "
            "cli.py importa `descargar` a su propio espacio de nombres."
        )
    monkeypatch.setattr("requests.sessions.Session.request", prohibido)


@pytest.fixture
def con(tmp_path):
    """BD limpia en disco temporal. Nunca toca la BD real."""
    c = db.init(tmp_path / "test.db")
    yield c
    c.close()


@pytest.fixture
def negocio():
    """Negocio base. Cada test cambia solo lo que le interesa."""
    return {
        "osm_type": "node", "osm_id": 1, "name": "Clínica Dental Ejemplo",
        "category": "dentist", "lat": 39.58, "lon": -0.53,
        "municipality": "Llíria", "address": "Calle Mayor 1",
        "phone": "+34961234567", "email": None, "website": None,
        "is_chain": 0, "dist_km": 5.0,
    }


@pytest.fixture(autouse=True)
def llavero_aislado(monkeypatch):
    """Ningún test lee el Llavero real de quien ejecuta la suite.

    Sin esto, los tests de "no hay clave configurada" pasan en CI (que no
    tiene Llavero) y fallan en el portátil de quien sí ha guardado la suya.
    Los tests del propio Llavero usan `_del_llavero_real`.
    """
    monkeypatch.setattr(places, "_del_llavero", lambda: None)
