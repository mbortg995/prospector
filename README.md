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

## Panel de control

```bash
prospector panel
```

Abre el navegador en `http://127.0.0.1:8765`. Desde ahí: la cola filtrable por
carril y municipio, la ficha de cada negocio con sus señales, registrar
llamadas y visitas, marcar maquetas, excluir, y lanzar `audit` y `enriquecer`
viendo su salida en vivo.

Escucha **solo en 127.0.0.1**: la BD es el estado comercial entero y no tiene
por qué asomarse a la red. Los botones solo pueden lanzar `audit` y
`enriquecer`, y una tarea cada vez. `discover` no está: tarda veinte minutos y
no es cosa de un botón.

La página no pide nada a internet —ni tipografías ni scripts de CDN—, igual que
las maquetas.

## Ciclo semanal (por consola)

```bash
# 1. Censar (una vez al mes basta; OSM cambia despacio)
prospector discover --guardar-json censo.json

# Si algún municipio se queda sin respuesta, reintentar solo ese
prospector discover --municipios Bétera Llíria

# Afinar el parser sobre el volcado, sin gastar consultas a Overpass
prospector discover --desde-json censo.json --simular

# 2. Rellenar teléfonos que OSM no trae (Google Places, se paga)
prospector enriquecer --simular          # cuántas consultas haría
prospector enriquecer --limite 50

# 3. Auditar por tandas. Cada web tarda ~3 s entre HTTP y Wayback.
python3 -m prospector.cli audit --limit 150

# 4. Ver a quién atacar
python3 -m prospector.cli cola -n 25
python3 -m prospector.cli cola --track sin_web
python3 -m prospector.cli ficha 47

# 5. Sacar el contexto de los 3 mejores para generar maquetas
python3 -m prospector.cli brief -n 3 > /tmp/lote.json

# 6. Registrar que ya tienen maqueta
python3 -m prospector.cli maqueta 47

# 7. Registrar llamadas y visitas
python3 -m prospector.cli log 47 llamada cita --next "Visita con tablet" --fecha 2026-09-03
python3 -m prospector.cli log 51 visita no_interesado --notes "Se lo lleva un familiar"
python3 -m prospector.cli excluir 51 --reason "pidió no volver a llamar"

# 8. Estado
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

## Rellenar huecos con Google Places

OSM trae teléfono en el 21% de los casos: el censo de la comarca da 519
negocios y solo 95 con vía de contacto. `enriquecer` busca en Places los que
están censados pero incompletos y les pone el teléfono y la web que faltan.

**Se paga por consulta**, así que todo está montado para gastar lo mínimo:

- Solo se consultan negocios **ya censados** a los que falta algo. No se barre
  la comarca a ciegas.
- Cada consulta queda anotada en `place_lookups`, **también las que no casan**.
  Nunca se pregunta dos veces por el mismo negocio.
- `--limite` topa las consultas por pasada (25 por defecto).
- `--simular` dice cuántas haría sin hacer ninguna.
- Se piden solo los campos que se usan: la máscara de campos decide el nivel de
  facturación.

El emparejamiento es **estricto a propósito**, y cercanía y parecido se
compensan: encima del punto basta un 55% de parecido, a 400 m se exige un 85%,
y a más de un kilómetro prácticamente el nombre exacto. Nunca basta uno solo de
los dos: con solo cercanía se cuela el bar de al lado, y con solo el nombre, la
Panadería Pepe del pueblo siguiente. Un teléfono mal asignado hace que llames a
otro negocio, y eso es peor que no tener teléfono.

Lo que no casa se guarda **con el candidato descartado, su parecido y su
distancia**. Esa consulta ya está pagada: así se puede revisar el criterio
sin volver a pagarla. Y los negocios que Google da por cerrados se dicen con
esas palabras, porque eso no es un fallo de emparejamiento sino un negocio que
ya no existe.

Un dato que ya venía de OSM **nunca se pisa**.

### Configurar la clave

En Google Cloud: crear proyecto, habilitar **Places API (New)**, crear una clave
y ponerle restricciones.

Para guardarla, el Llavero de macOS:

```bash
prospector clave --guardar
```

La pide por teclado (no se ve al escribir, se teclea dos veces) y la mete en el
Llavero bajo `prospector-google-places`. **No queda en el historial del shell ni
en ningún fichero**, y va cifrada en disco con el resto del Llavero.

Para comprobar que está, sin enseñarla:

```bash
prospector clave
```

`prospector clave --borrar` la quita.

También se acepta la variable `GOOGLE_PLACES_API_KEY`, que manda sobre el
Llavero: útil en un servidor o para probar otra clave un rato sin tocar la
guardada. Si la usas, exportala en la sesión y no la escribas en `.zshrc`.

Consulta la tarifa vigente antes de lanzar una pasada grande: los campos de
contacto se facturan en el nivel más caro.

## Teléfonos

Todos se guardan en forma canónica `+34XXXXXXXXX`, vengan de OSM (`+34 961 23 45 67`)
o de Places (`962 76 04 85`). Sin una sola forma, el mismo número de las dos
fuentes no casa consigo mismo, ni con las exclusiones, ni sirve para detectar
duplicados. Los nueve dígitos sueltos se asumen españoles; lo que trae otro
prefijo internacional se respeta.

`prospector normalizar` deja en forma canónica los que se guardaron antes de
que existiera la normalización. Es idempotente y tiene `--simular`.

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
