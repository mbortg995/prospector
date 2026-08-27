import pytest

from prospector import db


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
