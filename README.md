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
prospector discover --guardar-json censo.json

# Si algún municipio se queda sin respuesta, reintentar solo ese
prospector discover --municipios Bétera Llíria

# Afinar el parser sobre el volcado, sin gastar consultas a Overpass
prospector discover --desde-json censo.json --simular

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

## El ámbito: la comarca, no un círculo

OSM tiene **`el Camp de Túria` como relación administrativa propia**
(`admin_level=7`), así que el ámbito no se aproxima con un radio: se pregunta.
`discover` saca de OSM los 16 municipios de la comarca y lanza una consulta por
cada uno.

Eso resuelve dos cosas de un golpe. El ámbito sale exacto —un círculo de 15 km
desde La Pobla metía dos tercios de l'Horta en la cola— y **cada negocio queda
etiquetado con su municipio**, que es justo lo que OSM no trae en el 75% de los
casos. El municipio derivado manda sobre `addr:city`, que además viene con
variantes (`l'Eliana` y `L'Eliana` como valores distintos).

`--radius N` mantiene el modo círculo para salirse de la comarca a propósito.

## Portarse bien con Overpass

Es un servicio comunitario gratuito. En modo círculo `discover` lanza **una
sola consulta** y solo parte el área en cuatro teselas si esa consulta falla,
hasta dos niveles. Los espejos se prueban en orden y el que falla se
degrada; tras dos fallos se deja de intentar con él durante esa ejecución. No
se rota a ciegas: en agosto de 2026 dos de los tres espejos devolvían 500 hasta
con una consulta trivial, así que rotar gastaba los reintentos del bueno en
servidores muertos. Espera `--espera` segundos entre consultas.

La lista de municipios de la comarca se cachea en disco: cambia cada varios
años y la consulta que la saca es de las caras. `--refrescar-municipios` la
vuelve a pedir.

Un municipio que no responda **no tira el censo**: se anota y al final se te
dice con qué `--municipios` reintentar solo esos. Son 16 consultas, o sea 16
ocasiones de comerse un 502.

`--guardar-json` vuelca la respuesta cruda y `--desde-json` la reparsea sin
red: para afinar el parser o los pesos no hace falta volver a preguntar. Los
tests tienen un cortafuegos que hace fallar cualquier intento de salir a
internet.

`audit` espera `--espera` segundos entre negocios (1 por defecto). Cada web son
dos peticiones ajenas: la suya y la de Wayback.

## Ajustar

- Pesos de sector: `VALOR_CATEGORIA` en `discover.py`
- Detección de tecnología antigua: `LEGACY` en `audit.py`
- Umbral de "web_ok": la línea `if score < 20` en `audit.py`

## Desarrollo

```bash
make test   # pytest
make lint   # ruff
make fmt    # ruff --fix
```

Los tests no salen a la red: el parser de Overpass se prueba con respuestas de
ejemplo, la auditoría con dobles de `_fetch` y `_wayback_last`, y el CLI de punta
a punta contra una BD temporal vía `PROSPECTOR_DB`. CI en GitHub Actions sobre
Python 3.11, 3.12 y 3.13.
