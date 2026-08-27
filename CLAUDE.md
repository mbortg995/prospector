# Prospector — contexto del proyecto

## Qué es

Herramienta de prospección comercial local para vender diseño web a pequeños
negocios de la comarca del Camp de Túria (Valencia, España).

Ciclo: censar negocios cercanos → auditar el estado de su web → puntuar la
oportunidad → generar una maqueta de muestra → **vender por teléfono y visita
presencial**, no por email.

## Decisión clave: nada de email frío

En España el email comercial no solicitado está prohibido por el art. 21 de la
LSSICE salvo relación contractual previa, y aplica aunque el destinatario sea
una empresa y el correo sea el `info@` público. El canal elegido es teléfono +
visita con la maqueta en tablet. El campo `email` se guarda solo como dato de
contacto para cuando ya exista conversación previa.

**No implementes envío automático de correo en este proyecto.**

## Estado actual

Funciona y está probado con datos simulados:

- `discover.py` — Overpass API (OpenStreetMap), sin API key. Filtra franquicias
  por el tag `brand`. Facebook/Instagram como "web" cuenta como *no tener web*.
  Ámbito por comarca: OSM tiene `el Camp de Túria` como relación admin_level=7,
  así que se pregunta por sus 16 municipios y se consulta uno a uno. De ahí sale
  el municipio de cada negocio. Rotación de espejos, tolerancia a municipios
  caídos y volcado del crudo (`--guardar-json` / `--desde-json`).
- `audit.py` — descarga la web, extrae señales, puntúa 0-100 y asigna carril.
- `db.py` + `schema.sql` — SQLite. `discover` re-ejecutado nunca pisa el estado
  del pipeline.
- `places.py` — relleno de huecos con Google Places. La clave vive en el
  Llavero de macOS (`prospector clave --guardar`); `GOOGLE_PLACES_API_KEY`
  manda sobre él si está. Nunca en fichero, nunca en el historial, nunca
  impresa ni en los mensajes de error. Emparejamiento estricto (300 m y
  60% de parecido de nombre) porque un teléfono mal puesto hace que llames a
  otro negocio. Todo se cachea en `place_lookups`, fallos incluidos.
- `cli.py` — comandos: `init discover enriquecer audit cola ficha brief maqueta log excluir embudo export`
- `tests/` — 212 tests, con cortafuegos que hace fallar cualquier salida a la red. CI en Actions (3.11/3.12/3.13),
  ruff en verde. Antes de tocar scoring o el parser, `make test`.

**Censo real hecho el 2026-08-27.** Por comarca: 601 elementos crudos → 519
negocios → **95 auditables**, 44 de ellos sin web. Ver "Pendiente".

## Pendiente

1. **OSM no da para vivir de él.** El censo por comarca da 519 negocios pero
   solo **95 auditables** (44 sin web): OSM trae `phone` en el 21% de los
   casos. Llíria, capital de comarca con 23.000 habitantes, tiene 40 negocios
   en todo OSM. **Decidido: complementar con Google Places** (clave propia,
   teselado ~1 km). Es el siguiente PR y lo que desbloquea el proyecto.
2. **Calibrar pesos** de `VALOR_CATEGORIA` y del umbral `score < 20` con datos
   reales, no con los simulados.
3. **Generador de maquetas** — sin decidir todavía:
   - Opción A: una plantilla parametrizable por sector. Reproducible, barata,
     escala. Menos impacto.
   - Opción B: generación libre por negocio con la API. Impresiona más, no escala.
   - Requisito en ambos casos: **HTML autocontenido** (CSS inline, imágenes en
     base64, sin CDN) para que funcione sin cobertura en un polígono industrial.
   - La pantalla que vende es el **antes/después lado a lado en móvil**.
4. **Barrido en rejilla con Places** para encontrar negocios que OSM ni
   siquiera tiene censados (el caso de Llíria: 40 negocios en todo OSM para
   23.000 habitantes). Más caro que `enriquecer` y necesita clave de negocio
   nueva en el esquema (`place_id` como identidad alternativa a `osm_id`).

5. **Scraper de contenido** del negocio (textos, servicios, horarios) para
   alimentar la maqueta. Aún no existe.

## Convenciones

- Python 3.11+, solo `requests` y `beautifulsoup4`. No añadir dependencias
  pesadas sin motivo.
- Todo en español: nombres de comandos, salidas por consola, etapas del pipeline.
- La BD es un fichero local. Nada de servicios en la nube ni cuentas.
- Ser conservador con Overpass: es un servicio comunitario gratuito. Respetar
  los reintentos con espera y no lanzar consultas en bucle.

## Entorno

macOS. Usar venv (Homebrew Python bloquea `pip install` global por PEP 668):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
