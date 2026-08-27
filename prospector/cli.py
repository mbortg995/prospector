"""CLI del prospector. `prospector <comando>` o `python -m prospector.cli <comando>`."""
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

from . import db, places
from .audit import auditar
from .discover import (
    COMARCA,
    LA_POBLA,
    _dist_km,
    descargar,
    descargar_comarca,
    parse_overpass,
)

CACHE = Path(
    os.environ.get("PROSPECTOR_CACHE")
    or Path(__file__).resolve().parent.parent / ".cache"
).expanduser()
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


def resumen_censo(negocios: list) -> None:
    """Lo que hace falta ver tras una pasada: si el censo da para trabajar."""
    if not negocios:
        print("\nNingún negocio. Revisa el radio o la cobertura de OSM en la zona.")
        return
    utiles = [b for b in negocios if not b["is_chain"]
              and (b["phone"] or b["email"])]
    sin_web = [b for b in utiles if not b["website"]]
    print(f"\n{len(negocios)} negocios · {sum(b['is_chain'] for b in negocios)} cadenas")
    print(f"{len(utiles)} con vía de contacto (los únicos auditables)")
    print(f"  de ellos {len(sin_web)} sin web")

    print("\nPOR MUNICIPIO")
    for m, n in Counter(b["municipality"] or "sin dato" for b in negocios).most_common(12):
        print(f"  {m[:24]:<24} {n:>4}")

    print("\nPOR SECTOR (top 15)")
    for cat, n in Counter(b["category"] for b in negocios).most_common(15):
        print(f"  {cat[:24]:<24} {n:>4}")


def cmd_discover(a):
    centro = (a.lat, a.lon)

    aviso = lambda m: print(f"  {m}", file=sys.stderr)  # noqa: E731

    if a.desde_json:
        # Reparsear un volcado no gasta ni una consulta a Overpass.
        print(f"Leyendo {a.desde_json} (sin red)...", file=sys.stderr)
        datos = json.loads(Path(a.desde_json).read_text(encoding="utf-8"))
    elif a.radius:
        # Modo círculo: para salirse de la comarca a propósito.
        print(f"Consultando Overpass en {a.radius/1000:.0f} km...", file=sys.stderr)
        datos = descargar(a.radius, centro, espera=a.espera, aviso=aviso)
        if a.guardar_json:
            Path(a.guardar_json).write_text(
                json.dumps(datos, ensure_ascii=False), encoding="utf-8")
            print(f"  crudo guardado en {a.guardar_json}", file=sys.stderr)
    else:
        print(f"Censando «{a.comarca}» municipio a municipio...", file=sys.stderr)
        datos = descargar_comarca(
            a.comarca, espera=a.espera, aviso=aviso, solo=a.municipios,
            cache=CACHE / "municipios.json", refrescar=a.refrescar_municipios)
        if datos.get("_fallidos"):
            print(f"  reintenta con: --municipios {' '.join(datos['_fallidos'])}",
                  file=sys.stderr)
        if a.guardar_json:
            Path(a.guardar_json).write_text(
                json.dumps(datos, ensure_ascii=False), encoding="utf-8")
            print(f"  crudo guardado en {a.guardar_json}", file=sys.stderr)

    crudos = len(datos.get("elements", []))
    negocios = parse_overpass(datos, centro, radio_m=a.radius)
    print(f"{crudos} elementos crudos → {len(negocios)} negocios tras filtrar",
          file=sys.stderr)

    if a.simular:
        print("\n(simulacro: no se ha tocado la BD)")
        resumen_censo(negocios)
        return

    con = db.init()
    nuevos = 0
    for b in negocios:
        cur = con.execute("SELECT 1 FROM businesses WHERE osm_type=? AND osm_id=?",
                          (b["osm_type"], b["osm_id"])).fetchone()
        db.upsert_business(con, b)
        nuevos += 0 if cur else 1
    con.commit()
    tot = con.execute("SELECT COUNT(*) c FROM businesses").fetchone()["c"]
    print(f"{len(negocios)} encontrados · {nuevos} nuevos · {tot} en BD")
    resumen_censo(negocios)


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
        if i > 1:
            time.sleep(a.espera)  # cortesía: cada web son 2 peticiones ajenas
        res = auditar(dict(r))
        db.save_audit(con, r["id"], res)
        if i % 10 == 0:
            con.commit()
            print(f"  {i}/{len(filas)}", file=sys.stderr)
    con.commit()
    print("Hecho. Mira `cola`.")


SQL_HUECOS = """
SELECT b.* FROM businesses b
WHERE b.is_chain = 0
  AND (b.phone IS NULL OR b.website IS NULL)
  AND NOT EXISTS (SELECT 1 FROM place_lookups p WHERE p.business_id = b.id)
  AND NOT EXISTS (SELECT 1 FROM exclusions e
                  WHERE e.key = 'osm:' || b.osm_type || '/' || b.osm_id)
"""


def cmd_clave(a):
    """Gestiona la clave de Google Places sin que pase por aquí en claro."""
    if a.guardar:
        print(f"Se guardará en el Llavero bajo «{places.LLAVERO}».")
        print("Pégala cuando la pida (no se verá al escribir ni queda en el "
              "historial del shell).")
        places.guardar_en_llavero()
        print("Guardada.")
    elif a.borrar:
        print("Borrada del Llavero." if places.borrar_del_llavero()
              else "No había ninguna clave en el Llavero.")
        return

    origen = places.origen_clave()
    if not origen:
        print("No hay clave configurada. `prospector clave --guardar` la pide "
              "por teclado y la mete en el Llavero.")
        raise SystemExit(1)
    # Nunca se imprime el valor, ni siquiera un trozo.
    print(f"Clave disponible · origen: {origen}")
    if origen == "variable de entorno" and places._del_llavero():
        print(f"  (también hay una en el Llavero; manda {places.VARIABLE})")


def cmd_enriquecer(a):
    """Rellena teléfono y web con Google Places. Cada consulta se paga."""
    con = db.init()  # asegura place_lookups en BD creadas antes
    q, p = SQL_HUECOS, []
    if a.municipio:
        q += " AND b.municipality = ?"
        p.append(a.municipio)
    # Primero los que ahora mismo son invisibles: sin teléfono no hay venta.
    q += " ORDER BY (b.phone IS NULL AND b.email IS NULL) DESC, b.dist_km LIMIT ?"
    p.append(a.limite)
    filas = con.execute(q, p).fetchall()

    if not filas:
        print("No hay huecos que rellenar. ¿Ya lo has pasado todo?")
        return

    sin_contacto = sum(1 for r in filas if not r["phone"] and not r["email"])
    print(f"{len(filas)} negocios a consultar · {sin_contacto} sin vía de contacto",
          file=sys.stderr)
    if a.simular:
        print("\n(simulacro: 0 consultas hechas, 0 € gastados)")
        print(f"Una pasada real haría {len(filas)} búsquedas de texto contra "
              f"Places, una por negocio.")
        print("Consulta la tarifa vigente en Google Cloud antes de lanzarla: los "
              "campos de contacto se facturan en el nivel más caro.")
        for r in filas[:15]:
            falta = "sin contacto" if not r["phone"] and not r["email"] else "sin web"
            print(f"  #{r['id']:>4} {r['name'][:34]:<34} {r['municipality'] or '?':<20} {falta}")
        if len(filas) > 15:
            print(f"  ... y {len(filas) - 15} más")
        return

    # Comprobar la clave antes de empezar: sin ella no hay nada que hacer y
    # es absurdo informar de "parado en 0/N".
    try:
        places.clave()
    except places.SinClave as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from None

    puestos = casados = 0
    for i, r in enumerate(filas, 1):
        if i > 1:
            time.sleep(a.espera)
        texto = " ".join(filter(None, [r["name"], r["address"], r["municipality"]]))
        try:
            candidatos = places.buscar(texto, r["lat"], r["lon"])
        except places.PlacesError as e:
            print(f"\n{e}", file=sys.stderr)
            print(f"Parado en {i-1}/{len(filas)}. Lo consultado queda guardado.",
                  file=sys.stderr)
            break
        elegido, motivo = places.elegir(candidatos, dict(r), _dist_km)
        if elegido:
            casados += 1
            pl = elegido["place"]
            datos = {
                "matched": 1, "motivo": None, "place_id": pl.get("id"),
                "similitud": elegido["similitud"],
                "distancia_km": elegido["distancia_km"],
                "phone": pl.get("nationalPhoneNumber"),
                "website": pl.get("websiteUri"),
            }
            nuevos = db.rellenar_contacto(con, r["id"], datos["phone"], datos["website"])
            puestos += bool(nuevos)
            marca = "+" + "+".join(nuevos) if nuevos else "ya lo teníamos"
            print(f"  #{r['id']:>4} {r['name'][:30]:<30} → {marca}")
        else:
            datos = {"matched": 0, "motivo": motivo}
            print(f"  #{r['id']:>4} {r['name'][:30]:<30} → {motivo}")
        db.save_lookup(con, r["id"], datos)
        con.commit()

    con.commit()
    print(f"\n{casados}/{len(filas)} emparejados · {puestos} con datos nuevos")
    audit = con.execute(
        "SELECT COUNT(*) c FROM businesses WHERE is_chain=0 "
        "AND (phone IS NOT NULL OR email IS NOT NULL)").fetchone()["c"]
    print(f"Auditables ahora en BD: {audit}")


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
    p.add_argument("--comarca", default=COMARCA,
                   help="ámbito por defecto; OSM la tiene como relación propia")
    p.add_argument("--municipios", nargs="+", metavar="NOMBRE",
                   help="censar solo estos, para reintentar los que fallaron")
    p.add_argument("--refrescar-municipios", action="store_true",
                   help="volver a preguntar a OSM qué municipios tiene la comarca")
    p.add_argument("--radius", type=int,
                   help="modo círculo en metros, en lugar de por comarca")
    p.add_argument("--lat", type=float, default=LA_POBLA[0],
                   help="centro desde el que se mide la cercanía")
    p.add_argument("--lon", type=float, default=LA_POBLA[1])
    p.add_argument("--espera", type=float, default=3.0,
                   help="segundos entre teselas (Overpass es gratuito)")
    p.add_argument("--guardar-json", metavar="RUTA",
                   help="volcar la respuesta cruda para reparsear sin gastar consultas")
    p.add_argument("--desde-json", metavar="RUTA",
                   help="parsear un volcado previo, sin salir a la red")
    p.add_argument("--simular", action="store_true",
                   help="enseñar el resumen sin escribir en la BD")
    p.set_defaults(f=cmd_discover)

    p = sub.add_parser("audit", help="auditar webs y puntuar")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--reaudit", action="store_true")
    p.add_argument("--espera", type=float, default=1.0,
                   help="segundos entre negocios")
    p.set_defaults(f=cmd_audit)

    p = sub.add_parser("clave", help="comprobar o guardar la clave de Google Places")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--guardar", action="store_true",
                   help="pedirla por teclado y meterla en el Llavero de macOS")
    g.add_argument("--borrar", action="store_true", help="quitarla del Llavero")
    p.set_defaults(f=cmd_clave)

    p = sub.add_parser("enriquecer", help="rellenar teléfono y web con Google Places")
    p.add_argument("--limite", type=int, default=25,
                   help="tope de consultas; cada una se paga")
    p.add_argument("--municipio", help="acotar a un municipio")
    p.add_argument("--espera", type=float, default=0.2)
    p.add_argument("--simular", action="store_true",
                   help="decir cuántas consultas haría, sin hacer ninguna")
    p.set_defaults(f=cmd_enriquecer)

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
