"""Capa SQLite. Un fichero, cero servidores."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "prospector.db"
SCHEMA = Path(__file__).parent / "schema.sql"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init(path: Path = DB_PATH) -> sqlite3.Connection:
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
        "INSERT INTO interactions (business_id, happened_at, kind, outcome, notes) VALUES (?,?,?,?,?)",
        (business_id, now(), kind, outcome, notes),
    )
