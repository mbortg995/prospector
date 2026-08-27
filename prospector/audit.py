"""Auditoría de la web del negocio y puntuación de oportunidad (0-100)."""
import re
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

from .discover import VALOR_CATEGORIA

UA = {"User-Agent": "Mozilla/5.0 (compatible; auditoria-web-local/1.0)"}
WAYBACK = "http://archive.org/wayback/available"

LEGACY = [
    (re.compile(r"<frameset|<frame\s", re.I), "frames HTML", 14),
    (re.compile(r"\.swf\b|shockwave-flash", re.I), "Flash", 16),
    (re.compile(r"<marquee|<blink|<font\s", re.I), "etiquetas obsoletas", 10),
    (re.compile(r"jquery[.-]1\.[0-8]", re.I), "jQuery 1.x antiguo", 6),
    (re.compile(r"bootstrap[/.-]?2\.", re.I), "Bootstrap 2", 8),
]


def _fetch(url: str, timeout=12):
    """Devuelve (status, html, https_ok, error)."""
    try:
        r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        https_ok = r.url.lower().startswith("https://")
        return r.status_code, r.text[:400_000], https_ok, None
    except requests.exceptions.SSLError:
        try:  # existe pero con TLS roto: señal fortísima
            r = requests.get(url, headers=UA, timeout=timeout, verify=False)
            return r.status_code, r.text[:400_000], False, "ssl_roto"
        except Exception as e:
            return None, "", False, f"ssl:{type(e).__name__}"
    except Exception as e:
        return None, "", False, type(e).__name__


def _wayback_last(url: str):
    try:
        r = requests.get(WAYBACK, params={"url": url}, headers=UA, timeout=15)
        snap = r.json().get("archived_snapshots", {}).get("closest", {})
        ts = snap.get("timestamp")
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if ts else None
    except Exception:
        return None


def _copyright_year(text: str):
    found = []
    for m in re.finditer(r"(?:©|&copy;|Copyright)[^0-9]{0,20}((?:19|20)\d{2})", text, re.I):
        found.append(int(m.group(1)))
    return max(found) if found else None


def auditar(biz: dict) -> dict:
    """Puntúa. Más alto = mejor oportunidad comercial."""
    hoy = datetime.now(UTC)
    señales, score = [], 0
    cat_val = VALOR_CATEGORIA.get(biz.get("category"), 5)

    tiene_email = bool(biz.get("email"))
    tiene_tlf = bool(biz.get("phone"))
    if not (tiene_email or tiene_tlf):
        return {"track": "web_ok", "score": 0, "signals": ["sin vía de contacto"]}

    res = {"http_status": None, "https_ok": None, "has_viewport": None,
           "generator": None, "copyright_year": None, "wayback_last": None}

    # --- Carril A: sin web ---
    if not biz.get("website"):
        track = "sin_web"
        score = 50
        señales.append("no consta web")
    else:
        status, html, https_ok, err = _fetch(biz["website"])
        res["http_status"], res["https_ok"] = status, int(https_ok)

        # --- Carril B: web caída / dominio muerto ---
        if status is None or status >= 500 or status == 404:
            track, score = "web_caida", 62
            señales.append(f"web inaccesible ({err or status})")
        else:
            track = "web_obsoleta"
            soup = BeautifulSoup(html, "html.parser")

            vp = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
            res["has_viewport"] = int(bool(vp))
            if not vp:
                score += 26
                señales.append("sin viewport → no responsive")

            if not https_ok:
                score += 15
                señales.append("sin HTTPS o certificado roto")

            gen = soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})
            if gen and gen.get("content"):
                res["generator"] = gen["content"][:120]
                g = res["generator"].lower()
                if "joomla" in g or "drupal 7" in g:
                    score += 10
                    señales.append(f"CMS antiguo: {res['generator']}")
                elif m := re.search(r"wordpress\s+(\d+)\.(\d+)", g):
                    if int(m.group(1)) < 6:
                        score += 8
                        señales.append(f"WordPress {m.group(1)}.{m.group(2)} sin actualizar")

            year = _copyright_year(html)
            res["copyright_year"] = year
            if year:
                edad = hoy.year - year
                if edad >= 6:
                    score += 12; señales.append(f"copyright de {year}")
                elif edad >= 3:
                    score += 6; señales.append(f"copyright de {year}")

            for rx, etiqueta, pts in LEGACY:
                if rx.search(html):
                    score += pts
                    señales.append(etiqueta)

            # Web anémica: menos de 1200 caracteres de texto real
            if len(soup.get_text(strip=True)) < 1200:
                score += 8
                señales.append("contenido mínimo")

            wb = _wayback_last(biz["website"])
            res["wayback_last"] = wb
            if wb:
                años = (hoy - datetime.fromisoformat(wb).replace(tzinfo=UTC)).days / 365
                if años >= 5:
                    score += 18; señales.append(f"sin cambios desde {wb[:4]}")
                elif años >= 3:
                    score += 11; señales.append(f"último rastro {wb[:4]}")

            if score < 20:
                track = "web_ok"

    # --- Modificadores comunes ---
    score += cat_val
    señales.append(f"sector {biz.get('category')} (+{cat_val})")

    if tiene_tlf and tiene_email:
        score += 10; señales.append("teléfono + email")
    elif tiene_tlf:
        score += 8; señales.append("teléfono directo")
    else:
        score += 2

    d = biz.get("dist_km") or 0
    if d <= 10:
        score += 8; señales.append(f"a {d} km, visita fácil")
    elif d <= 20:
        score += 4
    elif d > 35:
        score -= 6; señales.append(f"a {d} km, lejos")

    res.update({"track": track, "score": max(0, min(100, round(score))), "signals": señales})
    return res
