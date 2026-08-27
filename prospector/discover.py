"""Censo de negocios vía Overpass (OpenStreetMap). Gratis, sin API key."""
import math
import re
import time

import requests

# Espejos de Overpass. Son servicios comunitarios gratuitos: se rota entre
# ellos ante fallo para no castigar siempre al mismo.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS = MIRRORS[0]
UA = {"User-Agent": "prospector-local/1.0"}
LA_POBLA = (39.5878, -0.5397)

# Categorías que pagan por una web. El resto es ruido.
# Se consulta por bbox y no por `around` para poder partir el área en teselas
# cuando Overpass no aguanta la consulta entera.
QUERY_TMPL = """
[out:json][timeout:{t}];
(
  nwr["shop"]({bbox});
  nwr["craft"]({bbox});
  nwr["office"]({bbox});
  nwr["healthcare"]({bbox});
  nwr["amenity"~"^(restaurant|cafe|bar|pub|dentist|clinic|doctors|veterinary|driving_school|pharmacy|childcare|kindergarten)$"]({bbox});
  nwr["tourism"~"^(hotel|guest_house|apartment|camp_site)$"]({bbox});
  nwr["leisure"~"^(fitness_centre|sports_centre)$"]({bbox});
);
out center tags;
"""


class OverpassError(RuntimeError):
    """Overpass no ha podido servir una tesela."""

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


def parse_overpass(data: dict, centro=LA_POBLA, radio_m: int | None = None) -> list:
    """Convierte la respuesta de Overpass en negocios.

    Deduplica por (tipo, id): una vía que cruza el borde de dos teselas
    aparece en las dos. Si se pasa `radio_m`, descarta lo que caiga fuera del
    círculo: la bbox de la consulta es el cuadrado que lo circunscribe y sus
    esquinas se van hasta un 41% más lejos de lo pedido.
    """
    out, vistos = [], set()
    for el in data.get("elements", []):
        clave = (el.get("type"), el.get("id"))
        if clave in vistos:
            continue
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
        dist = _dist_km(centro[0], centro[1], lat, lon)
        if radio_m is not None and dist * 1000 > radio_m:
            continue
        vistos.add(clave)
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
            "dist_km": dist,
        })
    return out


def _bbox(centro, radius_m: int):
    """Cuadrado que circunscribe el círculo de radio `radius_m`."""
    lat, lon = centro
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def _cuadrantes(bbox):
    s, w, n, e = bbox
    mlat, mlon = (s + n) / 2, (w + e) / 2
    return [(s, w, mlat, mlon), (s, mlon, mlat, e),
            (mlat, w, n, mlon), (mlat, mlon, n, e)]


def _pedir(bbox, timeout_q: int = 180, reintentos: int = 3, espera: float = 3.0) -> dict:
    """Una tesela, rotando de espejo en cada reintento."""
    q = QUERY_TMPL.format(t=timeout_q, bbox="{:.6f},{:.6f},{:.6f},{:.6f}".format(*bbox))
    ultimo = "sin intentos"
    for i in range(reintentos):
        try:
            r = requests.post(MIRRORS[i % len(MIRRORS)], data={"data": q},
                              timeout=timeout_q + 30, headers=UA)
        except requests.exceptions.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    # Overpass corta la respuesta a medias cuando va saturado
                    ultimo = "respuesta incompleta o no es JSON"
            else:
                ultimo = f"HTTP {r.status_code}"
        if i < reintentos - 1:
            time.sleep(espera * 5 * (i + 1))
    raise OverpassError(f"Overpass falló tras {reintentos} intentos ({ultimo})")


def descargar(radius_m: int = 15000, centro=LA_POBLA, reintentos: int = 3,
              espera: float = 3.0, profundidad_max: int = 2, aviso=None) -> dict:
    """Baja el área entera y devuelve el JSON crudo combinado.

    Empieza por una sola consulta. Si Overpass no la aguanta, parte la tesela
    en cuatro y reintenta cada cuadrante, hasta `profundidad_max` niveles.
    Así no se castiga al servicio con 16 consultas cuando basta con una.
    """
    aviso = aviso or (lambda _: None)
    elementos, vistos = [], set()
    pendientes = [(_bbox(centro, radius_m), 0)]
    primera = True

    while pendientes:
        bbox, prof = pendientes.pop(0)
        if not primera:
            time.sleep(espera)  # cortesía con un servicio gratuito
        primera = False
        try:
            datos = _pedir(bbox, reintentos=reintentos, espera=espera)
        except OverpassError as e:
            if prof >= profundidad_max:
                raise
            aviso(f"tesela sin respuesta ({e}); partiéndola en cuatro")
            pendientes.extend((c, prof + 1) for c in _cuadrantes(bbox))
            continue
        nuevos = 0
        for el in datos.get("elements", []):
            clave = (el.get("type"), el.get("id"))
            if clave not in vistos:
                vistos.add(clave)
                elementos.append(el)
                nuevos += 1
        aviso(f"tesela con {nuevos} elementos nuevos ({len(elementos)} acumulados)")

    return {"elements": elementos}


def fetch(radius_m: int = 15000, centro=LA_POBLA, **kw) -> list:
    """Censo completo: descarga y parsea."""
    return parse_overpass(descargar(radius_m, centro, **kw), centro, radio_m=radius_m)
