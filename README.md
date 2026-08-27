# Prospector local

Censo de negocios cercanos → auditoría de obsolescencia web → cola priorizada → seguimiento comercial.
Todo en un fichero SQLite. Sin servidores, sin cuentas, sin planes free.

## Instalación

macOS bloquea `pip install` global (PEP 668), así que venv:

```bash
make install
source .venv/bin/activate
prospector init
```

Sin `make`:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

`prospector <comando>` y `python3 -m prospector.cli <comando>` son equivalentes.
La BD se crea en la raíz del proyecto; `PROSPECTOR_DB` la mueve a otro sitio.

## Ciclo semanal

```bash
# 1. Censar (una vez al mes basta; OSM cambia despacio)
python3 -m prospector.cli discover --radius 15000

# 2. Auditar por tandas. Cada web tarda ~3 s entre HTTP y Wayback.
python3 -m prospector.cli audit --limit 150

# 3. Ver a quién atacar
python3 -m prospector.cli cola -n 25
python3 -m prospector.cli cola --track sin_web
python3 -m prospector.cli ficha 47

# 4. Sacar el contexto de los 3 mejores para generar maquetas
python3 -m prospector.cli brief -n 3 > /tmp/lote.json

# 5. Registrar que ya tienen maqueta
python3 -m prospector.cli maqueta 47

# 6. Registrar llamadas y visitas
python3 -m prospector.cli log 47 llamada cita --next "Visita con tablet" --fecha 2026-09-03
python3 -m prospector.cli log 51 visita no_interesado --notes "Se lo lleva un familiar"
python3 -m prospector.cli excluir 51 --reason "pidió no volver a llamar"

# 7. Estado
python3 -m prospector.cli embudo
```

## Los cuatro carriles

| Carril | Qué significa | Argumento de venta |
|---|---|---|
| `web_caida` | Dominio muerto o error 5xx | "Su web no carga. ¿Lo sabía?" — el mejor de todos |
| `sin_web` | No consta web (Facebook no cuenta) | "Le he montado esto por si le sirve" |
| `web_obsoleta` | Existe pero no responsive / abandonada | Antes/después en el móvil, sin decir palabra |
| `web_ok` | Fuera de la cola automáticamente | — |

## Señales y pesos

El peso mayor es **ausencia de `<meta viewport>`** (+26): significa que en un móvil se
ve minúscula, y el 70% de sus visitas son móviles. Es demostrable en tres segundos
delante del dueño y no admite discusión.

Después: web sin cambios en Wayback desde hace 5+ años (+18), TLS roto (+15),
copyright antiguo (+12), Joomla/Drupal 7 (+10), Flash o frames (+14/+16).

Modificadores: sector con capacidad de pago (dentista +18, gestoría +14, taller +11,
peluquería +8), teléfono directo (+8), cercanía (+8 si está a menos de 10 km).

Las franquicias se descartan solas por el tag `brand` de OSM: no deciden en local.

## Ajustar

- Pesos de sector: `VALOR_CATEGORIA` en `discover.py`
- Detección de tecnología antigua: `LEGACY` en `audit.py`
- Umbral de "web_ok": la línea `if score < 20` en `audit.py`
