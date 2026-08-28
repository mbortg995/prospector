"""Reunir qué sabemos de cada negocio, para poder maquetarle algo suyo.

Una maqueta genérica no vende: el dueño tiene que reconocer su negocio en la
pantalla. El material sale de tres sitios según el carril:

- `web_obsoleta`: de su web actual. Textos, servicios, horarios.
- `web_caida`: de Wayback. Su web sigue existiendo en el archivo aunque el
  dominio esté muerto, y es el mejor material que hay: el antes/después con
  su propia web al lado no admite discusión.
- `sin_web`: no hay nada que raspar. Queda el nombre, el sector, el municipio
  y lo que trajo Places.
"""
import re
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; prospector-local/1.0)"}
WAYBACK = "http://archive.org/wayback/available"

# Palabras que delatan un listado de servicios en un menú o un encabezado.
RUIDO_MENU = {"inicio", "home", "contacto", "contact", "aviso legal", "cookies",
              "política de privacidad", "privacidad", "mapa web", "blog",
              "sitemap", "buscar", "menú", "menu"}


def _texto(nodo, limite=400):
    t = re.sub(r"\s+", " ", nodo.get_text(" ", strip=True))
    return t[:limite].strip()


def extraer(html: str) -> dict:
    """Lo aprovechable de una web para escribir una maqueta."""
    sopa = BeautifulSoup(html, "html.parser")
    for basura in sopa(["script", "style", "noscript"]):
        basura.decompose()

    titulo = _texto(sopa.title, 200) if sopa.title else None
    meta = sopa.find("meta", attrs={"name": re.compile("^description$", re.I)})
    descripcion = (meta.get("content") or "").strip()[:400] if meta else None

    encabezados = []
    for h in sopa.find_all(["h1", "h2", "h3"]):
        t = _texto(h, 120)
        if t and t.lower() not in RUIDO_MENU and t not in encabezados:
            encabezados.append(t)

    parrafos = []
    for p in sopa.find_all("p"):
        t = _texto(p, 400)
        if len(t) > 60 and t not in parrafos:   # los cortos son pies y avisos
            parrafos.append(t)

    # Los servicios suelen vivir en listas cortas fuera del menú principal
    servicios = []
    for li in sopa.find_all("li"):
        t = _texto(li, 80)
        if 3 < len(t) < 60 and t.lower() not in RUIDO_MENU and t not in servicios:
            servicios.append(t)

    horario = None
    for m in re.finditer(r"(?:horario|abierto|abrimos)[^.]{0,160}", sopa.get_text(" "), re.I):
        horario = re.sub(r"\s+", " ", m.group(0)).strip()[:200]
        break

    texto_plano = re.sub(r"\s+", " ", sopa.get_text(" ", strip=True))

    # Webs de una sola página: la dirección y el «(c) 2011» son todo lo que
    # hay, y con el umbral de párrafo largo se tiraban enteras. Rozalén hnos
    # dio 144 caracteres en total, y ahí estaban su polígono y su teléfono.
    lineas = []
    for t in re.split(r"(?:\s{2,}|\n|·|\|)", sopa.get_text("\n", strip=True)):
        t = re.sub(r"\s+", " ", t).strip()
        if 3 < len(t) <= 60 and t.lower() not in RUIDO_MENU and t not in lineas:
            lineas.append(t)

    anio = None
    m = re.search(r"(?:©|\(c\)|&copy;|copyright)[^0-9]{0,20}((?:19|20)\d{2})",
                  texto_plano, re.I)
    if m:
        anio = int(m.group(1))

    # Mucho HTML y casi nada de texto significa que lo pinta JavaScript, y
    # Wayback no lo ejecuta. No es un fallo del extractor: no hay nada que leer.
    pintada_con_js = len(html) > 15_000 and len(texto_plano) < 250
    return {
        "lineas": lineas[:25],
        "copyright": anio,
        "pintada_con_js": pintada_con_js,
        "titulo": titulo,
        "descripcion": descripcion,
        "encabezados": encabezados[:15],
        "parrafos": parrafos[:12],
        "servicios": servicios[:20],
        "horario": horario,
        "emails": sorted(set(re.findall(
            r"[\w.+-]+@[\w-]+\.[\w.]{2,}", texto_plano)))[:5],
        "telefonos": sorted(set(re.findall(
            r"(?:\+34[\s.-]?)?(?:\d[\s.-]?){8}\d", texto_plano)))[:5],
        "redes": sorted({a["href"] for a in sopa.find_all("a", href=True)
                         if re.search(r"facebook|instagram|twitter|linkedin|tiktok",
                                      a["href"], re.I)})[:6],
        "longitud": len(texto_plano),
        "muestra": texto_plano[:1500],
    }


def aprovechable(material: dict | None) -> bool:
    """¿Hay aquí con qué escribir algo suyo, o solo el nombre del negocio?"""
    if not material:
        return False
    return (sum(len(p) for p in material.get("parrafos") or []) >= 200
            or len(material.get("servicios") or []) >= 3
            or len(material.get("lineas") or []) >= 4)


def _bajar(url: str, timeout=15):
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        return r.text[:600_000] if r.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None


def de_la_web(url: str) -> dict | None:
    html = _bajar(url)
    return {"fuente": "web", "url": url, **extraer(html)} if html else None


def de_wayback(url: str) -> dict | None:
    """La web archivada. Para un dominio muerto es el único material que queda,
    y además es exactamente lo que hay que enseñar en el antes/después."""
    try:
        r = requests.get(WAYBACK, params={"url": url}, headers=UA, timeout=20)
        snap = r.json().get("archived_snapshots", {}).get("closest", {})
    except (requests.exceptions.RequestException, ValueError):
        return None
    if not snap.get("url"):
        return None
    html = _bajar(snap["url"], timeout=25)
    if not html:
        return None
    ts = snap.get("timestamp", "")
    return {"fuente": "wayback", "url": snap["url"],
            "capturada": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else None,
            **extraer(html)}


def reunir(negocio: dict, track: str | None = None) -> dict:
    """Todo lo que se sabe del negocio, listo para escribirle una maqueta."""
    material = None
    if negocio.get("website"):
        material = de_la_web(negocio["website"])
        if material is None:
            # El dominio no responde: su web sigue en el archivo.
            material = de_wayback(negocio["website"])
    return {
        "obtenido_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "aprovechable": aprovechable(material),
        "nombre": negocio.get("name"),
        "sector": negocio.get("category"),
        "municipio": negocio.get("municipality"),
        "direccion": negocio.get("address"),
        "telefono": negocio.get("phone"),
        "email": negocio.get("email"),
        "web_actual": negocio.get("website"),
        "carril": track,
        "material": material,
    }
