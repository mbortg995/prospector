"""Capa SQLite. Un fichero, cero servidores."""
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

# El esquema viaja con el paquete; la BD vive fuera de él (es estado, no código).
SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def _db_por_defecto() -> Path:
    """PROSPECTOR_DB si está definida, si no la raíz del proyecto."""
    env = os.environ.get("PROSPECTOR_DB")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / "prospector.db"


DB_PATH = _db_por_defecto()


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    # Se resuelve al llamar, no al importar: si no, DB_PATH queda congelada.
    con = sqlite3.connect(path or DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init(path: Path | None = None) -> sqlite3.Connection:
    con = connect(path)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    return con


def upsert_business(con: sqlite3.Connection, biz: dict) -> int:
    """Inserta o refresca. Nunca pisa el estado del pipeline."""
    cur = con.execute(
        "SELECT id FROM businesses WHERE osm_type = ? AND osm_id = ?",
        (biz["osm_type"], biz["osm_id"]),
    )
    row = cur.fetchone()
    if row:
        bid = row["id"]
        con.execute(
            """UPDATE businesses SET name=?, category=?, lat=?, lon=?,
               municipality=?, address=?, phone=?, email=?, website=?,
               is_chain=?, dist_km=?, last_seen=? WHERE id=?""",
            (biz["name"], biz["category"], biz["lat"], biz["lon"],
             biz["municipality"], biz["address"], biz["phone"], biz["email"],
             biz["website"], biz["is_chain"], biz["dist_km"], now(), bid),
        )
        return bid

    cur = con.execute(
        """INSERT INTO businesses
           (osm_type, osm_id, name, category, lat, lon, municipality, address,
            phone, email, website, is_chain, dist_km, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (biz["osm_type"], biz["osm_id"], biz["name"], biz["category"],
         biz["lat"], biz["lon"], biz["municipality"], biz["address"],
         biz["phone"], biz["email"], biz["website"], biz["is_chain"],
         biz["dist_km"], now(), now()),
    )
    bid = cur.lastrowid
    con.execute(
        "INSERT OR IGNORE INTO pipeline (business_id, stage, updated_at) VALUES (?, 'nuevo', ?)",
        (bid, now()),
    )
    return bid


def save_audit(con: sqlite3.Connection, business_id: int, a: dict) -> None:
    import json
    con.execute(
        """INSERT INTO audits (business_id, run_at, track, score, http_status,
           https_ok, has_viewport, generator, copyright_year, wayback_last, signals_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (business_id, now(), a["track"], a["score"], a.get("http_status"),
         a.get("https_ok"), a.get("has_viewport"), a.get("generator"),
         a.get("copyright_year"), a.get("wayback_last"),
         json.dumps(a.get("signals", []), ensure_ascii=False)),
    )


def set_stage(con: sqlite3.Connection, business_id: int, stage: str, **kw) -> None:
    fields = {"stage": stage, "updated_at": now(), **kw}
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(
        f"UPDATE pipeline SET {sets} WHERE business_id=?",
        (*fields.values(), business_id),
    )


def log_interaction(con, business_id: int, kind: str, outcome: str, notes: str = "") -> None:
    con.execute(
        "INSERT INTO interactions (business_id, happened_at, kind, outcome, notes) "
        "VALUES (?,?,?,?,?)",
        (business_id, now(), kind, outcome, notes),
    )


def save_lookup(con: sqlite3.Connection, business_id: int, r: dict) -> None:
    """Anota lo que devolvió Places, casara o no. Se paga igual."""
    con.execute(
        """INSERT OR REPLACE INTO place_lookups
           (business_id, queried_at, matched, motivo, place_id, similitud,
            distancia_km, phone, website)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (business_id, now(), int(r.get("matched", 0)), r.get("motivo"),
         r.get("place_id"), r.get("similitud"), r.get("distancia_km"),
         r.get("phone"), r.get("website")),
    )


def rellenar_contacto(con: sqlite3.Connection, business_id: int,
                      phone: str = None, website: str = None) -> list:
    """Completa solo lo que falte. Nunca pisa un dato que ya venía de OSM."""
    b = con.execute("SELECT phone, website FROM businesses WHERE id=?",
                    (business_id,)).fetchone()
    puestos = []
    if phone and not b["phone"]:
        con.execute("UPDATE businesses SET phone=? WHERE id=?", (phone, business_id))
        puestos.append("teléfono")
    if website and not b["website"]:
        con.execute("UPDATE businesses SET website=? WHERE id=?", (website, business_id))
        puestos.append("web")
    if puestos:
        con.execute("UPDATE businesses SET last_seen=? WHERE id=?", (now(), business_id))
    return puestos
