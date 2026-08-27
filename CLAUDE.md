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
  Consulta por bbox con teselado solo si falla, rotación de espejos y volcado
  del crudo (`--guardar-json` / `--desde-json`).
- `audit.py` — descarga la web, extrae señales, puntúa 0-100 y asigna carril.
- `db.py` + `schema.sql` — SQLite. `discover` re-ejecutado nunca pisa el estado
  del pipeline.
- `cli.py` — comandos: `init discover audit cola ficha brief maqueta log excluir embudo export`
- `tests/` — 143 tests, con cortafuegos que hace fallar cualquier salida a la red. CI en Actions (3.11/3.12/3.13),
  ruff en verde. Antes de tocar scoring o el parser, `make test`.

**Primera pasada real hecha el 2026-08-27** (radio 15 km desde La Pobla):
6382 elementos crudos → 1363 negocios → **301 auditables**. Ver "Pendiente".

## Pendiente

1. **Lo que dijo la primera pasada real** (2026-08-27, 15 km, 15 s, una sola
   consulta):
   - Solo el **21% de OSM trae `phone`**. De 1363 negocios, 348 con teléfono y
     72 con email → **301 auditables**, 131 de ellos sin web.
   - **El radio apunta al sitio equivocado.** La Pobla está en el borde sur de
     la comarca, así que 15 km se comen l'Horta: Burjassot 58, Manises 44,
     Paterna 31, Godella 28. Camp de Túria se queda en ~95 auditables, y
     **Llíria da 5**. Confirmada la sospecha de cobertura floja.
   - **`addr:city` falta en el 75%.** La columna `municipality` es casi
     inservible tal cual; hay que derivarla de las coordenadas.
   Pendiente decidir: acotar a la comarca y/o complementar con Google Places.
2. **Calibrar pesos** de `VALOR_CATEGORIA` y del umbral `score < 20` con datos
   reales, no con los simulados.
3. **Generador de maquetas** — sin decidir todavía:
   - Opción A: una plantilla parametrizable por sector. Reproducible, barata,
     escala. Menos impacto.
   - Opción B: generación libre por negocio con la API. Impresiona más, no escala.
   - Requisito en ambos casos: **HTML autocontenido** (CSS inline, imágenes en
     base64, sin CDN) para que funcione sin cobertura en un polígono industrial.
   - La pantalla que vende es el **antes/después lado a lado en móvil**.
4. **Scraper de contenido** del negocio (textos, servicios, horarios) para
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
