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
import subprocess
import sys
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
#
# Dos topes rígidos e independientes no valen: en la primera pasada real,
# «Azulejos Marna» salió con parecido 1.000 y se descartó por estar a 390 m
# del punto que OSM le pone (el tope eran 300). Las coordenadas de OSM son
# aproximadas; el nombre exacto en el mismo municipio no lo es.
#
# Por eso la regla es graduada: cuanto más lejos, más parecido se exige.
# Basta cumplir una de las parejas (distancia máxima en km, parecido mínimo).
REGLAS = [
    (0.15, 0.55),   # encima: el nombre puede venir abreviado
    (0.40, 0.85),   # a unas manzanas: se exige el nombre casi exacto
    (1.20, 0.95),   # lejos: solo si el nombre es prácticamente idéntico
]
MAX_KM = max(d for d, _ in REGLAS)


def _aceptable(d: float, sim: float) -> bool:
    return any(d <= dm and sim >= sm for dm, sm in REGLAS)


def _normalizar_telefono(t: str | None) -> str | None:
    """Places los da como «962 76 04 85» y OSM como «+34961234567».
    Se guardan igual para que casen entre sí y con las exclusiones."""
    if not t:
        return None
    limpio = re.sub(r"[^\d+]", "", t)
    return limpio or None


class PlacesError(RuntimeError):
    """Algo ha ido mal hablando con Google Places."""


class SinClave(PlacesError):
    """No hay clave de API configurada."""


# Servicio bajo el que vive la clave en el Llavero de macOS. Guardarla ahí
# evita tenerla en claro en .zshrc, en un .env o en el historial del shell.
LLAVERO = "prospector-google-places"
VARIABLE = "GOOGLE_PLACES_API_KEY"


def _del_llavero() -> str | None:
    """Lee la clave del Llavero de macOS. None si no está o no es macOS."""
    if sys.platform != "darwin":
        return None
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", LLAVERO, "-w"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def origen_clave() -> str | None:
    """De dónde saldría la clave, sin llegar a mirar su valor."""
    if (os.environ.get(VARIABLE) or "").strip():
        return "variable de entorno"
    if _del_llavero():
        return "Llavero de macOS"
    return None


def guardar_en_llavero() -> None:
    """Lanza `security` heredando el terminal: la clave la tecleas tú y no
    pasa por este proceso ni queda en el historial del shell."""
    if sys.platform != "darwin":
        raise PlacesError("El Llavero es de macOS. Usa la variable de entorno.")
    r = subprocess.run(
        ["security", "add-generic-password", "-a", os.environ.get("USER", "prospector"),
         "-s", LLAVERO, "-U", "-w"],
    )
    if r.returncode != 0:
        raise PlacesError(f"El Llavero rechazó la clave (código {r.returncode})")


def borrar_del_llavero() -> bool:
    if sys.platform != "darwin":
        return False
    r = subprocess.run(["security", "delete-generic-password", "-s", LLAVERO],
                       capture_output=True, text=True)
    return r.returncode == 0


def clave() -> str:
    """La variable de entorno manda; si no, el Llavero."""
    k = (os.environ.get(VARIABLE) or "").strip()
    if k:
        return k
    k = _del_llavero()
    if k:
        return k
    raise SinClave(
        f"No hay clave de Google Places. Guárdala en el Llavero con "
        f"`prospector clave --guardar` (te la pedirá por teclado y no queda en "
        f"el historial), o expórtala en {VARIABLE}. No la escribas nunca en un "
        f"fichero del repositorio."
    )


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


def elegir(candidatos: list, negocio: dict, dist_km) -> tuple:
    """El mejor candidato aceptable, o (None, motivo, descartado) si ninguno.

    Cercanía y parecido se compensan entre sí (ver REGLAS), pero nunca basta
    uno solo: con solo cercanía se cuela el bar de al lado y con solo el
    nombre, la Panadería Pepe del pueblo siguiente.

    Cuando no casa nada se devuelve igualmente el mejor descartado. Esa
    consulta ya se ha pagado: guardar por qué se quedó fuera permite revisar
    el criterio más adelante sin volver a pagarla.
    """
    if not candidatos:
        return None, "sin candidatos", None
    mejor = descartado = None
    cerrados = sin_coords = 0
    for c in candidatos:
        if c.get("businessStatus") == "CLOSED_PERMANENTLY":
            cerrados += 1
            continue
        loc = c.get("location") or {}
        if loc.get("latitude") is None:
            sin_coords += 1
            continue
        d = round(dist_km(negocio["lat"], negocio["lon"],
                          loc["latitude"], loc["longitude"]), 3)
        sim = round(similitud(negocio["name"],
                              (c.get("displayName") or {}).get("text", "")), 3)
        cand = {"place": c, "similitud": sim, "distancia_km": d}
        if _aceptable(d, sim):
            if mejor is None or sim > mejor["similitud"]:
                mejor = cand
        elif descartado is None or sim > descartado["similitud"]:
            descartado = cand
    if mejor is not None:
        return mejor, "ok", None
    if descartado is None:
        # Decir *por qué* no eran utilizables: un «cerrado definitivamente» es
        # información comercial (ese negocio ya no existe), no un fallo del
        # emparejamiento, y confundirlos lleva a tocar el criterio sin motivo.
        partes = []
        if cerrados:
            partes.append(f"{cerrados} cerrado{'s' if cerrados > 1 else ''} "
                          f"definitivamente")
        if sin_coords:
            partes.append(f"{sin_coords} sin coordenadas")
        return None, "; ".join(partes) or f"{len(candidatos)} vistos", None
    nombre = (descartado["place"].get("displayName") or {}).get("text", "?")
    return None, (f"«{nombre}» a {descartado['distancia_km']} km "
                  f"con parecido {descartado['similitud']}"), descartado
