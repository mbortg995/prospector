"""Censo de negocios vía Overpass (OpenStreetMap). Gratis, sin API key."""
import math
import re
import time

import requests

OVERPASS = "https://overpass-api.de/api/interpreter"
LA_POBLA = (39.5878, -0.5397)

# Categorías que pagan por una web. El resto es ruido.
QUERY_TMPL = """
[out:json][timeout:300];
(
  nwr["shop"](around:{r},{lat},{lon});
  nwr["craft"](around:{r},{lat},{lon});
  nwr["office"](around:{r},{lat},{lon});
  nwr["healthcare"](around:{r},{lat},{lon});
  nwr["amenity"~"^(restaurant|cafe|bar|pub|dentist|clinic|doctors|veterinary|driving_school|pharmacy|childcare|kindergarten)$"](around:{r},{lat},{lon});
  nwr["tourism"~"^(hotel|guest_house|apartment|camp_site)$"](around:{r},{lat},{lon});
  nwr["leisure"~"^(fitness_centre|sports_centre)$"](around:{r},{lat},{lon});
);
out center tags;
"""

# Sectores con capacidad y motivo real de pagar una web
VALOR_CATEGORIA = {
    "dentist": 18, "clinic": 16, "doctors": 15, "veterinary": 15,
    "healthcare": 14, "lawyer": 14, "estate_agent": 14, "accountant": 14,
    "insurance": 12, "architect": 12, "driving_school": 12,
    "hotel": 14, "guest_house": 12, "apartment": 12,
    "car_repair": 11, "restaurant": 10, "fitness_centre": 10,
    "hairdresser": 8, "beauty": 8, "bakery": 6, "furniture": 8,
    "kitchen": 10, "doityourself": 8, "car": 10, "travel_agency": 12,
}

RUIDO = {"vacant", "atm", "vending_machine", "parking", "toilets", "bench"}


def _dist_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(a)), 2)


def _es_cadena(tags: dict) -> bool:
    """Franquicias y cadenas: no deciden en local, no compran web."""
    if tags.get("brand") or tags.get("brand:wikidata") or tags.get("operator:wikidata"):
        return True
    op = (tags.get("operator") or "").lower()
    return any(c in op for c in ("s.a.", "sociedad", "group", "holding"))


def _categoria(tags: dict) -> str:
    for k in ("shop", "craft", "office", "amenity", "healthcare", "tourism", "leisure"):
        if k in tags:
            return tags[k]
    return "otro"


def _email(tags: dict):
    for k in ("email", "contact:email", "operator:email"):
        v = tags.get(k)
        if v and re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", v.strip(), re.I):
            return v.strip().lower()
    return None


def _telefono(tags: dict):
    for k in ("phone", "contact:phone", "contact:mobile", "mobile"):
        if tags.get(k):
            return re.sub(r"[^\d+]", "", tags[k].split(";")[0])
    return None


def _web(tags: dict):
    for k in ("website", "contact:website", "url"):
        v = tags.get(k)
        if v and v.strip():
            v = v.strip()
            # Facebook/Instagram como "web" cuenta como NO tener web
            if any(d in v.lower() for d in ("facebook.com", "instagram.com", "paginasamarillas")):
                return None
            return v if v.startswith("http") else "https://" + v
    return None


def parse_overpass(data: dict, centro=LA_POBLA) -> list:
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        cat = _categoria(tags)
        if cat in RUIDO:
            continue
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None:
            continue
        addr = " ".join(filter(None, [
            tags.get("addr:street"), tags.get("addr:housenumber"),
        ])).strip() or None
        out.append({
            "osm_type": el["type"],
            "osm_id": el["id"],
            "name": name.strip(),
            "category": cat,
            "lat": lat, "lon": lon,
            "municipality": tags.get("addr:city"),
            "address": addr,
            "phone": _telefono(tags),
            "email": _email(tags),
            "website": _web(tags),
            "is_chain": int(_es_cadena(tags)),
            "dist_km": _dist_km(centro[0], centro[1], lat, lon),
        })
    return out


def fetch(radius_m: int = 15000, centro=LA_POBLA, retries: int = 3) -> list:
    """Consulta Overpass con reintentos. Overpass se cae y rate-limita a
    menudo: un fallo de red no puede tumbar la pasada de censo entera."""
    q = QUERY_TMPL.format(r=radius_m, lat=centro[0], lon=centro[1])
    ultimo = "sin intentos"
    for i in range(retries):
        try:
            r = requests.post(OVERPASS, data={"data": q}, timeout=320,
                              headers={"User-Agent": "prospector-local/1.0"})
        except requests.exceptions.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200:
                try:
                    datos = r.json()
                except ValueError:
                    # Overpass corta la respuesta a medias cuando va saturado
                    ultimo = "respuesta incompleta o no es JSON"
                else:
                    # El parseo va fuera del try: un fallo aquí es un bug
                    # nuestro y debe verse, no reintentarse en silencio.
                    return parse_overpass(datos, centro)
            else:
                ultimo = f"HTTP {r.status_code}"
        if i < retries - 1:
            time.sleep(20 * (i + 1))  # Overpass rate-limita con 429/504
    raise RuntimeError(f"Overpass falló tras {retries} intentos ({ultimo})")
