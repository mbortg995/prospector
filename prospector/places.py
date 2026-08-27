"""Relleno de huecos con Google Places. Requiere clave propia y cuesta dinero.

OSM trae teléfono en el 21% de los casos: de 519 negocios censados en la
comarca solo 95 son auditables. Places rellena ese hueco, pero se paga por
consulta, así que aquí todo está montado para gastar lo mínimo:

- Solo se consultan negocios que **ya están censados** y a los que les falta
  el contacto. No se barre la comarca a ciegas.
- Cada consulta se anota en `place_lookups`, incluidos los fallos. Nunca se
  pregunta dos veces por el mismo negocio.
- El emparejamiento es deliberadamente estricto: un teléfono equivocado es
  peor que ningún teléfono, porque acabas llamando a otro negocio.
"""
import difflib
import os
import re
import time
import unicodedata

import requests

BUSCAR = "https://places.googleapis.com/v1/places:searchText"

# Los campos piden facturación por niveles: los de contacto son los caros.
# Se piden solo los que se usan.
CAMPOS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.businessStatus",
])

# Un teléfono mal asignado hace que llames a otro negocio. Mejor no rellenar.
MAX_KM = 0.3
MIN_SIMILITUD = 0.6


class PlacesError(RuntimeError):
    """Algo ha ido mal hablando con Google Places."""


class SinClave(PlacesError):
    """No hay clave de API configurada."""


def clave() -> str:
    k = (os.environ.get("GOOGLE_PLACES_API_KEY") or "").strip()
    if not k:
        raise SinClave(
            "Falta GOOGLE_PLACES_API_KEY. Crea una clave en Google Cloud con la "
            "Places API (New) habilitada y expórtala en tu shell. No la escribas "
            "en ningún fichero del repositorio."
        )
    return k


def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip()


def similitud(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalizar(a), _normalizar(b)).ratio()


def buscar(texto: str, lat: float, lon: float, radio_m: int = 1500,
           timeout: int = 20, reintentos: int = 3, espera: float = 2.0) -> list:
    """Una búsqueda de texto sesgada a las coordenadas del negocio."""
    cuerpo = {
        "textQuery": texto,
        "languageCode": "es",
        "regionCode": "ES",
        "maxResultCount": 5,
        "locationBias": {"circle": {
            "center": {"latitude": lat, "longitude": lon},
            "radius": float(radio_m),
        }},
    }
    cabeceras = {"X-Goog-Api-Key": clave(), "X-Goog-FieldMask": CAMPOS}
    ultimo = "sin intentos"
    for i in range(reintentos):
        try:
            r = requests.post(BUSCAR, json=cuerpo, headers=cabeceras, timeout=timeout)
        except requests.exceptions.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200:
                return r.json().get("places", [])
            if r.status_code in (401, 403):
                # Reintentar no arregla una clave mal configurada, y cada
                # intento se factura igual.
                raise PlacesError(
                    f"Google rechaza la clave (HTTP {r.status_code}). Comprueba que "
                    f"la Places API (New) está habilitada y que la clave no tiene "
                    f"restricciones que bloqueen este uso. {r.text[:200]}")
            if r.status_code == 400:
                raise PlacesError(f"Consulta mal formada: {r.text[:300]}")
            ultimo = f"HTTP {r.status_code}: {r.text[:150]}"
        if i < reintentos - 1:
            time.sleep(espera * (i + 1))
    raise PlacesError(f"Places falló tras {reintentos} intentos ({ultimo})")


def elegir(candidatos: list, negocio: dict, dist_km, max_km: float = MAX_KM,
           min_similitud: float = MIN_SIMILITUD) -> tuple:
    """El mejor candidato, o (None, motivo) si ninguno es fiable.

    Exige cercanía **y** parecido de nombre. Con uno solo de los dos se cuela
    el bar de al lado.
    """
    if not candidatos:
        return None, "sin candidatos"
    mejor, mejor_sim, mejor_d = None, 0.0, None
    for c in candidatos:
        if c.get("businessStatus") == "CLOSED_PERMANENTLY":
            continue
        loc = c.get("location") or {}
        if loc.get("latitude") is None:
            continue
        d = dist_km(negocio["lat"], negocio["lon"], loc["latitude"], loc["longitude"])
        sim = similitud(negocio["name"], (c.get("displayName") or {}).get("text", ""))
        if d <= max_km and sim >= min_similitud and sim > mejor_sim:
            mejor, mejor_sim, mejor_d = c, sim, d
    if mejor is None:
        return None, f"ningún candidato pasa el filtro ({len(candidatos)} vistos)"
    return {"place": mejor, "similitud": round(mejor_sim, 3),
            "distancia_km": round(mejor_d, 3)}, "ok"
