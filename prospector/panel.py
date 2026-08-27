"""Panel de control local. Solo biblioteca estándar: nada de framework web.

Sirve en 127.0.0.1 y solo ahí: la BD es el estado comercial entero y no tiene
por qué escuchar en la red. Los comandos largos (`audit`, `enriquecer`) se
lanzan como subproceso y su salida se va leyendo en vivo, uno cada vez.
"""
import json
import os
import subprocess
import sys
import threading
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import cli, db

PLANTILLA = Path(__file__).resolve().parent / "panel.html"

# Solo se deja lanzar lo que no destruye nada. `discover` no está: tarda 20
# minutos y no es cosa de un botón.
TAREAS = {
    "audit": ["audit", "--limit"],
    "enriquecer": ["enriquecer", "--limite"],
}


class Tarea:
    """Un comando en marcha. Uno como mucho: los dos salen a servicios ajenos
    y `enriquecer` además cuesta dinero."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.nombre = None
        self.lineas = deque(maxlen=400)
        self.arrancada = None
        self.codigo = None

    @property
    def viva(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def lanzar(self, nombre: str, limite: int) -> None:
        with self.lock:
            if self.viva:
                raise RuntimeError(f"Ya hay una tarea en marcha: {self.nombre}")
            if nombre not in TAREAS:
                raise RuntimeError(f"Tarea desconocida: {nombre}")
            cmd, flag = TAREAS[nombre]
            self.nombre, self.codigo = nombre, None
            self.lineas.clear()
            self.arrancada = datetime.now(UTC).isoformat(timespec="seconds")
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "prospector.cli", cmd, flag, str(limite)],
                # La BD va explícita: si no, el subproceso resuelve la suya y
                # el panel podría estar enseñando una y sus botones tocando otra.
                env={**os.environ, "PROSPECTOR_DB": str(db.DB_PATH)},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self) -> None:
        for linea in self.proc.stdout:
            self.lineas.append(linea.rstrip())
        self.codigo = self.proc.wait()
        self.lineas.append(f"— terminado (código {self.codigo}) —")

    def estado(self) -> dict:
        return {"nombre": self.nombre, "viva": self.viva, "codigo": self.codigo,
                "arrancada": self.arrancada, "lineas": list(self.lineas)}


TAREA = Tarea()


def _cola(con, track=None, municipio=None, n=100):
    q, p = "SELECT * FROM v_cola", []
    condiciones = []
    if track:
        condiciones.append("track = ?"); p.append(track)
    if municipio:
        condiciones.append("municipality = ?"); p.append(municipio)
    if condiciones:
        q += " WHERE " + " AND ".join(condiciones)
    q += " LIMIT ?"; p.append(n)
    return [dict(r) for r in con.execute(q, p)]


SQL_LEADS = """
SELECT b.id, b.name, b.category, b.municipality, b.dist_km, b.phone, b.website,
       p.stage, p.next_action, p.next_action_date, p.mockup_path, p.updated_at,
       a.track, a.score,
       (SELECT COUNT(*) FROM interactions i WHERE i.business_id = b.id) contactos,
       (SELECT MAX(happened_at) FROM interactions i WHERE i.business_id = b.id) ultimo
FROM businesses b
JOIN pipeline p ON p.business_id = b.id
LEFT JOIN audits a ON a.id = (
    SELECT id FROM audits WHERE business_id = b.id
    ORDER BY run_at DESC, id DESC LIMIT 1)
WHERE p.stage IN ({marcas})
"""


def _leads(con, etapas):
    """Todo lo que está en marcha. La cola solo enseña los `nuevo`, así que
    sin esto un negocio desaparece del panel en cuanto lo tocas."""
    q = SQL_LEADS.format(marcas=",".join("?" * len(etapas)))
    q += " ORDER BY p.next_action_date IS NULL, p.next_action_date, a.score DESC"
    return [dict(r) for r in con.execute(q, etapas)]


def _ficha(con, bid):
    b = con.execute("SELECT * FROM businesses WHERE id=?", (bid,)).fetchone()
    if not b:
        return None
    au = con.execute("SELECT * FROM audits WHERE business_id=? "
                     "ORDER BY run_at DESC, id DESC LIMIT 1", (bid,)).fetchone()
    pi = con.execute("SELECT * FROM pipeline WHERE business_id=?", (bid,)).fetchone()
    hist = con.execute("SELECT * FROM interactions WHERE business_id=? "
                       "ORDER BY happened_at", (bid,)).fetchall()
    lk = con.execute("SELECT * FROM place_lookups WHERE business_id=?", (bid,)).fetchone()
    return {
        "negocio": dict(b),
        "auditoria": (dict(au) | {"signals": json.loads(au["signals_json"] or "[]")}
                      if au else None),
        "pipeline": dict(pi) if pi else None,
        "historial": [dict(h) for h in hist],
        "places": dict(lk) if lk else None,
    }


def _embudo(con):
    # Las etapas salen del CLI: tenerlas copiadas aquí ya hizo que `en_curso`
    # y `aparcado` no aparecieran en el embudo del panel.
    return {
        "etapas": [{"etapa": e, "n": con.execute(
            "SELECT COUNT(*) c FROM pipeline WHERE stage=?", (e,)).fetchone()["c"]}
            for e in cli.ETAPAS],
        "carriles": [dict(r) for r in con.execute(
            "SELECT track, COUNT(*) n, ROUND(AVG(score)) media FROM v_cola "
            "GROUP BY track ORDER BY n DESC")],
        "totales": dict(con.execute(
            "SELECT COUNT(*) negocios,"
            " SUM(CASE WHEN phone IS NOT NULL OR email IS NOT NULL THEN 1 ELSE 0 END) contactables,"
            " SUM(CASE WHEN website IS NULL THEN 1 ELSE 0 END) sin_web"
            " FROM businesses WHERE is_chain = 0").fetchone()),
        "pendientes": [dict(r) for r in con.execute(
            "SELECT b.id, b.name, p.next_action, p.next_action_date FROM pipeline p "
            "JOIN businesses b ON b.id = p.business_id "
            "WHERE p.next_action_date IS NOT NULL "
            "AND p.stage NOT IN ('ganado','perdido','descartado') "
            "ORDER BY p.next_action_date LIMIT 15")],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "prospector-panel"

    def log_message(self, *a):
        pass  # la consola es para la tarea en marcha, no para el tráfico

    def _responder(self, datos, codigo=200):
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        uno = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731

        if u.path == "/":
            cuerpo = PLANTILLA.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return

        if u.path == "/api/tarea":
            return self._responder(TAREA.estado())

        con = db.connect()
        try:
            if u.path == "/api/cola":
                return self._responder(_cola(
                    con, uno("track"), uno("municipio"), int(uno("n", 100))))
            if u.path == "/api/leads":
                return self._responder(_leads(con, list(cli.EN_JUEGO)))
            if u.path == "/api/archivo":
                return self._responder(_leads(
                    con, ["aparcado", "perdido", "descartado"]))
            if u.path == "/api/embudo":
                return self._responder(_embudo(con))
            if u.path == "/api/municipios":
                return self._responder([r[0] for r in con.execute(
                    "SELECT DISTINCT municipality FROM businesses "
                    "WHERE municipality IS NOT NULL ORDER BY municipality")])
            if u.path.startswith("/api/ficha/"):
                f = _ficha(con, int(u.path.rsplit("/", 1)[1]))
                return self._responder(f or {"error": "no existe"}, 200 if f else 404)
        finally:
            con.close()
        self._responder({"error": "ruta desconocida"}, 404)

    def do_POST(self):
        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo) or b"{}")
        except ValueError:
            return self._responder({"error": "JSON inválido"}, 400)
        u = urlparse(self.path)

        if u.path == "/api/tarea":
            try:
                TAREA.lanzar(datos.get("nombre"), int(datos.get("limite", 10)))
            except (RuntimeError, ValueError, TypeError) as e:
                return self._responder({"error": str(e)}, 409)
            return self._responder(TAREA.estado())

        con = db.connect()
        try:
            bid = int(datos.get("id", 0))
            if not con.execute("SELECT 1 FROM businesses WHERE id=?", (bid,)).fetchone():
                return self._responder({"error": f"no existe el negocio #{bid}"}, 404)

            if u.path == "/api/log":
                db.log_interaction(con, bid, datos["kind"], datos["outcome"],
                                   datos.get("notes") or "")
                etapa = {"cita": "reunion", "interesado": "contactado",
                         "no_interesado": "perdido", "no_contesta": "contactado",
                         "volver_a_llamar": "contactado"}.get(datos["outcome"])
                if etapa:
                    actual = con.execute("SELECT stage FROM pipeline WHERE business_id=?",
                                         (bid,)).fetchone()["stage"]
                    extra = {}
                    if datos.get("next"):
                        extra["next_action"] = datos["next"]
                    if datos.get("fecha"):
                        extra["next_action_date"] = datos["fecha"]
                    db.set_stage(con, bid, cli.etapa_resultante(actual, etapa), **extra)
                con.commit()
                return self._responder(_ficha(con, bid))

            if u.path == "/api/etapa":
                etapa = datos.get("etapa")
                # Lista blanca: el navegador no escribe cualquier cosa en la BD.
                if etapa not in cli.ETAPAS:
                    return self._responder({"error": f"etapa desconocida: {etapa}"}, 400)
                extra = {}
                if etapa == "aparcado":
                    extra = {"next_action": datos.get("motivo") or "Retomar",
                             "next_action_date": datos.get("fecha")}
                elif etapa in cli.TERMINALES:
                    extra = {"next_action": None, "next_action_date": None}
                db.set_stage(con, bid, etapa, **extra)
                if etapa == "descartado":
                    b = con.execute("SELECT osm_type, osm_id FROM businesses "
                                    "WHERE id=?", (bid,)).fetchone()
                    con.execute("INSERT OR REPLACE INTO exclusions VALUES (?,?,?)",
                                (f"osm:{b['osm_type']}/{b['osm_id']}",
                                 datos.get("motivo") or "no molestar", db.now()))
                con.commit()
                return self._responder(_ficha(con, bid))

            if u.path == "/api/maqueta":
                db.set_stage(con, bid, "maqueta", mockup_path=datos.get("path"),
                             mockup_built_at=db.now())
                con.commit()
                return self._responder(_ficha(con, bid))

            if u.path == "/api/excluir":
                b = con.execute("SELECT osm_type, osm_id FROM businesses WHERE id=?",
                                (bid,)).fetchone()
                con.execute("INSERT OR REPLACE INTO exclusions VALUES (?,?,?)",
                            (f"osm:{b['osm_type']}/{b['osm_id']}",
                             datos.get("reason") or "no molestar", db.now()))
                db.set_stage(con, bid, "descartado")
                con.commit()
                return self._responder(_ficha(con, bid))
        except KeyError as e:
            return self._responder({"error": f"falta el campo {e}"}, 400)
        finally:
            con.close()
        self._responder({"error": "ruta desconocida"}, 404)


def servir(puerto: int = 8765) -> None:
    # 127.0.0.1 y no 0.0.0.0: la BD es el estado comercial entero.
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), Handler)
    print(f"Panel en http://127.0.0.1:{puerto}  ·  Ctrl-C para parar")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel parado.")
    finally:
        servidor.server_close()
