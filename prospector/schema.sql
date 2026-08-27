-- Prospector local: censo + auditoría + pipeline comercial
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS businesses (
    id            INTEGER PRIMARY KEY,
    osm_type      TEXT,
    osm_id        INTEGER,
    source        TEXT DEFAULT 'osm',
    name          TEXT NOT NULL,
    category      TEXT,
    lat           REAL,
    lon           REAL,
    municipality  TEXT,
    address       TEXT,
    phone         TEXT,
    email         TEXT,
    website       TEXT,
    is_chain      INTEGER DEFAULT 0,
    dist_km       REAL,
    first_seen    TEXT,
    last_seen     TEXT,
    UNIQUE (osm_type, osm_id)
);

-- Histórico: puedes re-auditar dentro de 6 meses y comparar
CREATE TABLE IF NOT EXISTS audits (
    id             INTEGER PRIMARY KEY,
    business_id    INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    run_at         TEXT,
    track          TEXT,      -- sin_web | web_caida | web_obsoleta | web_ok
    score          INTEGER,
    http_status    INTEGER,
    https_ok       INTEGER,
    has_viewport   INTEGER,
    generator      TEXT,
    copyright_year INTEGER,
    wayback_last   TEXT,
    signals_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_biz ON audits(business_id, run_at DESC);

-- Estado vivo. Una fila por negocio, se actualiza in place.
CREATE TABLE IF NOT EXISTS pipeline (
    business_id      INTEGER PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
    stage            TEXT DEFAULT 'nuevo',
    -- nuevo | maqueta | contactado | reunion | propuesta | ganado | perdido | descartado
    mockup_path      TEXT,
    mockup_built_at  TEXT,
    contact_name     TEXT,
    next_action      TEXT,
    next_action_date TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS interactions (
    id           INTEGER PRIMARY KEY,
    business_id  INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    happened_at  TEXT,
    kind         TEXT,   -- llamada | visita | email | whatsapp | linkedin
    outcome      TEXT,   -- interesado | no_interesado | no_contesta | volver_a_llamar | cita
    notes        TEXT
);

-- Cada consulta a Google Places se paga. Se anota lo que devolvió, incluidos
-- los fallos, para no preguntar dos veces por el mismo negocio.
CREATE TABLE IF NOT EXISTS place_lookups (
    business_id  INTEGER PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
    queried_at   TEXT,
    matched      INTEGER,   -- 0 = se consultó y ningún candidato era fiable
    motivo       TEXT,      -- por qué no casó, para poder revisar el criterio
    place_id     TEXT,
    similitud    REAL,
    distancia_km REAL,
    phone        TEXT,
    website      TEXT
);

-- Nunca más volver a molestar a estos. Se respeta en todos los listados.
CREATE TABLE IF NOT EXISTS exclusions (
    key        TEXT PRIMARY KEY,  -- dominio, teléfono o "osm:node/123"
    reason     TEXT,
    created_at TEXT
);

-- Vista de trabajo: lo mejor sin maqueta todavía.
-- Se recrea en cada init: no guarda datos y así las BD viejas se actualizan.
DROP VIEW IF EXISTS v_cola;
CREATE VIEW v_cola AS
SELECT b.id, b.name, b.category, b.municipality, b.dist_km,
       b.phone, b.email, b.website,
       a.track, a.score, p.stage
FROM businesses b
JOIN pipeline p ON p.business_id = b.id
JOIN audits a ON a.id = (
    -- run_at solo tiene precisión de segundo: el id desempata.
    SELECT id FROM audits WHERE business_id = b.id ORDER BY run_at DESC, id DESC LIMIT 1
)
WHERE p.stage = 'nuevo'
  AND b.is_chain = 0
  AND a.track != 'web_ok'
  AND NOT EXISTS (SELECT 1 FROM exclusions e WHERE e.key = 'osm:' || b.osm_type || '/' || b.osm_id)
ORDER BY a.score DESC;
