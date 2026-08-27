"""Censo de negocios vía Overpass (OpenStreetMap). Gratis, sin API key."""
import json
import math
import re
import time

import requests

# Espejos de Overpass, en orden de preferencia. Son servicios comunitarios y
# se caen por su cuenta: en agosto de 2026, dos de estos tres devolvían 500
# hasta con una consulta trivial, y tardaban 26 s en hacerlo. Por eso no se
# rota a ciegas (eso gastaba los reintentos del bueno en servidores muertos):
# se prueban en orden y el que falla se degrada al final durante esta
# ejecución. Tras `_TOLERANCIA` fallos se deja de intentar con él.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
_TOLERANCIA = 2
_fallos_espejo: dict = {}
OVERPASS = MIRRORS[0]
UA = {"User-Agent": "prospector-local/1.0"}
LA_POBLA = (39.5878, -0.5397)

# OSM tiene la comarca como relación administrativa propia (admin_level=7),
# así que el ámbito no se aproxima con un círculo: se pregunta.
COMARCA = "el Camp de Túria"

# Categorías que pagan por una web. El resto es ruido.
# Se consulta por bbox y no por `around` para poder partir el área en teselas
# cuando Overpass no aguanta la consulta entera.
# {sel} es el selector de área: una bbox o `area.a`.
CATEGORIAS = """
  nwr["shop"]({sel});
  nwr["craft"]({sel});
  nwr["office"]({sel});
  nwr["healthcare"]({sel});
  nwr["amenity"~"^(restaurant|cafe|bar|pub|dentist|clinic|doctors|veterinary|driving_school|pharmacy|childcare|kindergarten)$"]({sel});
  nwr["tourism"~"^(hotel|guest_house|apartment|camp_site)$"]({sel});
  nwr["leisure"~"^(fitness_centre|sports_centre)$"]({sel});
"""

QUERY_TMPL = """
[out:json][timeout:{t}];
(
""" + CATEGORIAS + """);
out center tags;
"""

# Los municipios de la comarca, según la propia OSM.
Q_MUNICIPIOS = """
[out:json][timeout:{t}];
rel["admin_level"="7"]["name"="{comarca}"];
map_to_area->.c;
rel(area.c)["admin_level"="8"]["boundary"="administrative"];
out tags;
"""

# Por id de área, NUNCA por nombre: `area["name"="Serra"]` casa con cualquier
# área del mundo que se llame así, y Serra (Espírito Santo, Brasil) tiene medio
# millón de habitantes. En la primera pasada por comarca metió 536 negocios
# brasileños en el censo.
Q_MUNICIPIO = """
[out:json][timeout:{t}];
area({aid})->.a;
(
""" + CATEGORIAS.replace("{sel}", "area.a") + """);
out center tags;
"""

# Overpass numera las áreas de relaciones como 3600000000 + id de la relación.
AREA_BASE = 3_600_000_000


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
            # El municipio derivado del área manda sobre addr:city: este
            # falta en el 75% de los casos y trae variantes ("l'Eliana" /
            # "L'Eliana") que romperían cualquier agrupación.
            "municipality": el.get("_municipio") or tags.get("addr:city"),
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


def _espejos_vivos() -> list:
    """Los que aún no han agotado la paciencia en esta ejecución."""
    vivos = [m for m in MIRRORS if _fallos_espejo.get(m, 0) < _TOLERANCIA]
    return vivos or list(MIRRORS)  # si han fallado todos, volver a probarlos


def _consulta(q: str, timeout_q: int = 180, reintentos: int = 3,
              espera: float = 3.0) -> dict:
    """Una consulta a Overpass. Cada vuelta prueba todos los espejos vivos."""
    ultimo = "sin intentos"
    for i in range(reintentos):
        for espejo in _espejos_vivos():
            try:
                r = requests.post(espejo, data={"data": q},
                                  timeout=timeout_q + 30, headers=UA)
            except requests.exceptions.RequestException as e:
                ultimo = f"{espejo}: {type(e).__name__}: {e}"
            else:
                if r.status_code == 200:
                    try:
                        datos = r.json()
                    except ValueError:
                        # Overpass corta la respuesta a medias cuando va saturado
                        ultimo = f"{espejo}: respuesta incompleta o no es JSON"
                    else:
                        _fallos_espejo[espejo] = 0
                        return datos
                else:
                    ultimo = f"{espejo}: HTTP {r.status_code}"
            _fallos_espejo[espejo] = _fallos_espejo.get(espejo, 0) + 1
        if i < reintentos - 1:
            time.sleep(espera * 5 * (i + 1))
    raise OverpassError(f"Overpass falló tras {reintentos} vueltas ({ultimo})")


def _pedir(bbox, timeout_q: int = 180, **kw) -> dict:
    """Una tesela rectangular."""
    return _consulta(
        QUERY_TMPL.format(t=timeout_q, sel="{:.6f},{:.6f},{:.6f},{:.6f}".format(*bbox)),
        timeout_q=timeout_q, **kw)


def municipios(comarca: str = COMARCA, timeout_q: int = 120, **kw) -> list:
    """Los municipios de la comarca: [(nombre, id de área), ...].

    Se devuelve el id de área y no solo el nombre porque las consultas
    posteriores tienen que ir por id: hay municipios homónimos en otros países.
    """
    datos = _consulta(Q_MUNICIPIOS.format(t=timeout_q, comarca=comarca),
                      timeout_q=timeout_q, **kw)
    munis = sorted(
        (e["tags"]["name"], AREA_BASE + e["id"])
        for e in datos.get("elements", [])
        if e.get("tags", {}).get("name") and e.get("id")
    )
    if not munis:
        raise OverpassError(f"OSM no conoce la comarca «{comarca}»")
    return munis


def municipios_cacheados(comarca: str = COMARCA, cache=None, refrescar: bool = False,
                         aviso=None, **kw) -> list:
    """La lista de municipios cambia cada varios años; la consulta que la saca
    (`map_to_area`) es de las caras. Se guarda en disco."""
    aviso = aviso or (lambda _: None)
    if cache and not refrescar and cache.exists():
        guardado = json.loads(cache.read_text(encoding="utf-8"))
        if guardado.get("comarca") == comarca:
            aviso(f"{len(guardado['municipios'])} municipios (de caché)")
            return [tuple(m) for m in guardado["municipios"]]
    munis = municipios(comarca, **kw)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"comarca": comarca, "municipios": munis},
                                    ensure_ascii=False), encoding="utf-8")
    return munis


def descargar_comarca(comarca: str = COMARCA, reintentos: int = 3,
                      espera: float = 3.0, aviso=None, solo=None,
                      cache=None, refrescar: bool = False) -> dict:
    """Censo por municipios de la comarca.

    Una consulta por municipio en vez de un círculo: el ámbito sale exacto y
    cada negocio queda etiquetado con su municipio, que es justo lo que OSM no
    trae en el 75% de los casos. Son consultas pequeñas, no una gigante.

    Un municipio que falle no tira el censo: son 16 consultas, o sea 16
    ocasiones de que Overpass devuelva un 502. Los fallidos se listan en
    `_fallidos` para poder reintentarlos con `--municipios` sin repetir el
    resto. Solo se rinde si no ha salido ni uno.
    """
    aviso = aviso or (lambda _: None)
    munis = municipios_cacheados(comarca, cache=cache, refrescar=refrescar,
                                 aviso=aviso, reintentos=reintentos, espera=espera)
    if solo:
        pedidos = {n.casefold() for n in solo}
        munis = [m for m in munis if m[0].casefold() in pedidos]
        if not munis:
            raise OverpassError(f"ninguno de {list(solo)} está en «{comarca}»")
        aviso(f"{len(munis)} municipios pedidos a mano")
    else:
        aviso(f"{len(munis)} municipios en «{comarca}»")

    elementos, vistos, fallidos = [], set(), []
    for i, (muni, aid) in enumerate(munis):
        if i:
            time.sleep(espera)  # cortesía con un servicio gratuito
        try:
            datos = _consulta(Q_MUNICIPIO.format(t=180, aid=aid),
                              reintentos=reintentos, espera=espera)
        except OverpassError as e:
            fallidos.append(muni)
            aviso(f"{muni}: SIN RESPUESTA ({e})")
            continue
        nuevos = 0
        for el in datos.get("elements", []):
            clave = (el.get("type"), el.get("id"))
            if clave in vistos:
                continue
            vistos.add(clave)
            el["_municipio"] = muni  # viaja en el volcado, sobrevive a --desde-json
            elementos.append(el)
            nuevos += 1
        aviso(f"{muni}: {nuevos}")

    if fallidos and len(fallidos) == len(munis):
        raise OverpassError(f"ningún municipio respondió ({', '.join(fallidos)})")
    if fallidos:
        aviso(f"quedan pendientes: {', '.join(fallidos)}")
    return {"elements": elementos, "_fallidos": fallidos}


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
