"""CLI del prospector. `prospector <comando>` o `python -m prospector.cli <comando>`."""
import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

from . import db
from .audit import auditar
from .discover import LA_POBLA, fetch

MOCKUPS = Path(
    os.environ.get("PROSPECTOR_MAQUETAS")
    or Path(__file__).resolve().parent.parent / "maquetas"
).expanduser()
ETAPAS = ["nuevo", "maqueta", "contactado", "reunion", "propuesta",
          "ganado", "perdido", "descartado"]
# El embudo solo avanza. `perdido` y `descartado` no son un paso más: son
# salidas, y mandan siempre.
AVANCE = ["nuevo", "maqueta", "contactado", "reunion", "propuesta", "ganado"]
TERMINALES = {"perdido", "descartado"}


def etapa_resultante(actual: str, propuesta: str) -> str:
    """Un 'no contesta' después de una cita no puede devolverte a 'contactado'."""
    if propuesta in TERMINALES or actual in TERMINALES:
        return propuesta
    if actual not in AVANCE or propuesta not in AVANCE:
        return propuesta
    return max(actual, propuesta, key=AVANCE.index)


def negocio_o_salir(con, bid: int):
    b = con.execute("SELECT * FROM businesses WHERE id=?", (bid,)).fetchone()
    if not b:
        print(f"No existe el negocio #{bid}.", file=sys.stderr)
        raise SystemExit(1)
    return b


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:50]


def cmd_init(a):
    db.init()
    MOCKUPS.mkdir(parents=True, exist_ok=True)
    print(f"BD lista en {db.DB_PATH}")


def cmd_discover(a):
    con = db.init()
    print(f"Consultando Overpass en {a.radius/1000:.0f} km...", file=sys.stderr)
    negocios = fetch(a.radius, (a.lat, a.lon))
    nuevos = 0
    for b in negocios:
        cur = con.execute("SELECT 1 FROM businesses WHERE osm_type=? AND osm_id=?",
                          (b["osm_type"], b["osm_id"])).fetchone()
        db.upsert_business(con, b)
        nuevos += 0 if cur else 1
    con.commit()
    tot = con.execute("SELECT COUNT(*) c FROM businesses").fetchone()["c"]
    print(f"{len(negocios)} encontrados · {nuevos} nuevos · {tot} en BD")


def cmd_audit(a):
    con = db.connect()
    q = """SELECT b.* FROM businesses b
           WHERE b.is_chain = 0 AND (b.phone IS NOT NULL OR b.email IS NOT NULL)"""
    if not a.reaudit:
        q += " AND NOT EXISTS (SELECT 1 FROM audits x WHERE x.business_id = b.id)"
    q += " ORDER BY b.dist_km LIMIT ?"
    filas = con.execute(q, (a.limit,)).fetchall()
    print(f"Auditando {len(filas)}...", file=sys.stderr)
    for i, r in enumerate(filas, 1):
        res = auditar(dict(r))
        db.save_audit(con, r["id"], res)
        if i % 10 == 0:
            con.commit()
            print(f"  {i}/{len(filas)}", file=sys.stderr)
    con.commit()
    print("Hecho. Mira `cola`.")


def cmd_cola(a):
    con = db.connect()
    q = "SELECT * FROM v_cola"
    p = []
    if a.track:
        q += " WHERE track = ?"; p.append(a.track)
    q += " LIMIT ?"; p.append(a.n)
    filas = con.execute(q, p).fetchall()
    if not filas:
        print("Cola vacía."); return
    print(f"{'ID':>4} {'PT':>3} {'CARRIL':<12} {'NOMBRE':<32} {'MUNICIPIO':<16} {'TLF':<12} KM")
    print("-" * 100)
    for r in filas:
        print(f"{r['id']:>4} {r['score']:>3} {r['track']:<12} {(r['name'] or '')[:32]:<32} "
              f"{(r['municipality'] or '-')[:16]:<16} {(r['phone'] or '-'):<12} {r['dist_km']}")


def cmd_ficha(a):
    con = db.connect()
    b = negocio_o_salir(con, a.id)
    au = con.execute("SELECT * FROM audits WHERE business_id=? ORDER BY run_at DESC LIMIT 1",
                     (a.id,)).fetchone()
    p = con.execute("SELECT * FROM pipeline WHERE business_id=?", (a.id,)).fetchone()
    print(f"\n{b['name']}  [#{b['id']}]")
    print(f"  {b['category']} · {b['address'] or '?'} · "
          f"{b['municipality'] or '?'} · {b['dist_km']} km")
    print(f"  tlf {b['phone'] or '—'}   email {b['email'] or '—'}")
    print(f"  web {b['website'] or '—'}")
    if au:
        print(f"\n  PUNTUACIÓN {au['score']}  ({au['track']})")
        for s in json.loads(au["signals_json"]):
            print(f"    · {s}")
    print(f"\n  etapa: {p['stage']}"
          + (f"   maqueta: {p['mockup_path']}" if p["mockup_path"] else "")
          + (f"\n  siguiente: {p['next_action']} ({p['next_action_date']})"
             if p["next_action"] else ""))
    hist = con.execute("SELECT * FROM interactions WHERE business_id=? ORDER BY happened_at",
                       (a.id,)).fetchall()
    for h in hist:
        print(f"    {h['happened_at'][:10]} {h['kind']}: {h['outcome']} {h['notes'] or ''}")
    print()


def cmd_brief(a):
    """Vuelca el contexto de N negocios en JSON para pegarlo en Claude y generar maquetas."""
    con = db.connect()
    filas = con.execute("SELECT * FROM v_cola LIMIT ?", (a.n,)).fetchall()
    salida = []
    for r in filas:
        b = con.execute("SELECT * FROM businesses WHERE id=?", (r["id"],)).fetchone()
        au = con.execute("SELECT signals_json FROM audits WHERE business_id=? "
                         "ORDER BY run_at DESC LIMIT 1", (r["id"],)).fetchone()
        salida.append({
            "id": b["id"], "slug": slug(b["name"]), "nombre": b["name"],
            "sector": b["category"], "municipio": b["municipality"],
            "direccion": b["address"], "telefono": b["phone"],
            "email": b["email"], "web_actual": b["website"],
            "puntuacion": r["score"], "problemas": json.loads(au["signals_json"]),
        })
    print(json.dumps(salida, ensure_ascii=False, indent=2))


def cmd_maqueta(a):
    con = db.connect()
    b = negocio_o_salir(con, a.id)
    ruta = a.path or str(MOCKUPS / slug(b["name"]) / "index.html")
    db.set_stage(con, a.id, "maqueta", mockup_path=ruta, mockup_built_at=db.now())
    con.commit()
    print(f"#{a.id} {b['name']} → maqueta en {ruta}")


def cmd_log(a):
    con = db.connect()
    b = negocio_o_salir(con, a.id)
    db.log_interaction(con, a.id, a.kind, a.outcome, a.notes or "")
    etapa = {"cita": "reunion", "interesado": "contactado", "no_interesado": "perdido",
             "no_contesta": "contactado", "volver_a_llamar": "contactado"}.get(a.outcome)
    destino = "sin cambio de etapa"
    if etapa:
        actual = con.execute("SELECT stage FROM pipeline WHERE business_id=?",
                             (a.id,)).fetchone()["stage"]
        destino = etapa_resultante(actual, etapa)
        # Solo se tocan los campos que se han pasado: registrar una llamada no
        # puede borrar la visita que ya tenías agendada.
        extra = {}
        if a.next is not None:
            extra["next_action"] = a.next
        if a.fecha is not None:
            extra["next_action_date"] = a.fecha
        db.set_stage(con, a.id, destino, **extra)
    con.commit()
    print(f"Registrado. #{a.id} {b['name']} → {destino}")


def cmd_excluir(a):
    con = db.connect()
    b = negocio_o_salir(con, a.id)
    con.execute("INSERT OR REPLACE INTO exclusions VALUES (?,?,?)",
                (f"osm:{b['osm_type']}/{b['osm_id']}", a.reason, db.now()))
    db.set_stage(con, a.id, "descartado")
    con.commit()
    print(f"Excluido: {b['name']}")


def cmd_embudo(a):
    con = db.connect()
    print("\nEMBUDO")
    for e in ETAPAS:
        c = con.execute("SELECT COUNT(*) c FROM pipeline WHERE stage=?", (e,)).fetchone()["c"]
        print(f"  {e:<12} {'█' * min(c, 40)} {c}")
    print("\nPOR CARRIL (sin contactar aún)")
    for r in con.execute("SELECT track, COUNT(*) c, ROUND(AVG(score)) m FROM v_cola "
                         "GROUP BY track ORDER BY c DESC"):
        print(f"  {r['track']:<14} {r['c']:>4} negocios · media {r['m']}")
    pend = con.execute("SELECT b.name, p.next_action, p.next_action_date FROM pipeline p "
                       "JOIN businesses b ON b.id=p.business_id "
                       "WHERE p.next_action_date IS NOT NULL "
                       "AND p.stage NOT IN ('ganado','perdido','descartado') "
                       "ORDER BY p.next_action_date LIMIT 10").fetchall()
    if pend:
        print("\nPRÓXIMAS ACCIONES")
        for r in pend:
            print(f"  {r['next_action_date']}  {r['name'][:30]:<30} {r['next_action']}")
    print()


def cmd_export(a):
    con = db.connect()
    cur = con.execute("SELECT * FROM v_cola LIMIT ?", (a.n,))
    filas = cur.fetchall()
    w = csv.writer(sys.stdout)
    # Las columnas salen del cursor, no de la primera fila: un CSV sin filas
    # sigue siendo un CSV con cabecera.
    w.writerow([d[0] for d in cur.description])
    for r in filas:
        w.writerow(list(r))


def main():
    ap = argparse.ArgumentParser(prog="prospector")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(f=cmd_init)

    p = sub.add_parser("discover", help="censar negocios vía OSM")
    p.add_argument("--radius", type=int, default=15000)
    p.add_argument("--lat", type=float, default=LA_POBLA[0])
    p.add_argument("--lon", type=float, default=LA_POBLA[1])
    p.set_defaults(f=cmd_discover)

    p = sub.add_parser("audit", help="auditar webs y puntuar")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--reaudit", action="store_true")
    p.set_defaults(f=cmd_audit)

    p = sub.add_parser("cola", help="mejores oportunidades sin trabajar")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--track", choices=["sin_web", "web_caida", "web_obsoleta"])
    p.set_defaults(f=cmd_cola)

    p = sub.add_parser("ficha"); p.add_argument("id", type=int); p.set_defaults(f=cmd_ficha)

    p = sub.add_parser("brief", help="JSON de los N mejores para generar maquetas")
    p.add_argument("-n", type=int, default=3); p.set_defaults(f=cmd_brief)

    p = sub.add_parser("maqueta", help="marcar maqueta creada")
    p.add_argument("id", type=int); p.add_argument("--path")
    p.set_defaults(f=cmd_maqueta)

    p = sub.add_parser("log", help="registrar llamada o visita")
    p.add_argument("id", type=int)
    p.add_argument("kind", choices=["llamada", "visita", "email", "whatsapp", "linkedin"])
    p.add_argument("outcome", choices=["interesado", "no_interesado", "no_contesta",
                                       "volver_a_llamar", "cita"])
    p.add_argument("--notes"); p.add_argument("--next"); p.add_argument("--fecha")
    p.set_defaults(f=cmd_log)

    p = sub.add_parser("excluir"); p.add_argument("id", type=int)
    p.add_argument("--reason", default="no molestar"); p.set_defaults(f=cmd_excluir)

    sub.add_parser("embudo").set_defaults(f=cmd_embudo)

    p = sub.add_parser("export"); p.add_argument("-n", type=int, default=500)
    p.set_defaults(f=cmd_export)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
