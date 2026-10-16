# Runbook del feed `league-feed-v1`

Operador: el propio humano (ADR-004). Este repositorio es la fuente de verdad del feed que lee la app; GitHub Pages sirve `public/`.
Todo cambio en `main` pasa por la puerta de `.github/workflows/publish.yml`: `validate` → `deploy` → `smoke`. Si `validate` falla, no se despliega y se sigue sirviendo la última versión buena.

Solo hay tres sitios donde editar: `public/v1/index.json`, `public/v1/seasons/<id>.json` y, en la carga inicial, los datos de la temporada. El esquema (`schema/`) **no se edita aquí**: la copia canónica está en el repositorio de la app.

## Primera vez

Una sola vez, y antes del primer *push*. Requiere tu cuenta de GitHub (historia US-007b).

1. **Identidad Git local.** Hasta ahora los commits usan una dirección no personal (`feed@example.invalid`). Antes del primer push, cambia la identidad local a la dirección `noreply` de tu cuenta (en GitHub: Settings → Emails → «Keep my email addresses private»; la dirección tiene la forma `<id>+<usuario>@users.noreply.github.com`):
   ```
   git config --local user.name "<tu usuario>"
   git config --local user.email "<id>+<usuario>@users.noreply.github.com"
   ```
   Un email personal publicado en el historial de un repositorio público no se puede retirar sin reescribirlo.
2. **Crear el repositorio** público `myfutsal-feed` en GitHub, sin README ni licencia (vacío), y añadir el remoto: `git remote add origin https://github.com/<usuario>/myfutsal-feed.git`.
3. **Activar Pages:** Settings → Pages → Source: «GitHub Actions».
4. **Protección contra push de secretos** (*push protection*): Settings → Code security. Suele estar activada por defecto en repositorios públicos nuevos; comprueba que lo está. Nada de claves ni tokens en este repositorio. Activa también «Block command line pushes that expose my email» (Settings → Emails): es la red de seguridad en el servidor para que no se publique un email personal.
5. **Primer push:** `git push -u origin main`. Observa la pestaña Actions: deben pasar `validate`, `deploy` y `smoke`.

## Publicar un resultado

1. Abre `public/v1/seasons/<id>.json` en el editor web de GitHub (o en local).
2. En el partido, cambia `status` a `finished` y rellena `score` (`home` y `away`, goles al final incluida la prórroga). En eliminatorias, `decidedBy` es `null`, `extraTime` o `penalties`.
3. Cambia `updatedAt` al instante actual en UTC con formato `AAAA-MM-DDTHH:MM:SSZ`. **Es obligatorio si cambias algo más**: la puerta rechaza un contenido cambiado con el mismo `updatedAt`.
4. Confirma en `main` con un mensaje que incluya el tiempo dedicado (ver «Anotar el tiempo»).
5. Si trabajas en local, antes de subir ejecuta la verificación completa:
   ```
   scripts/verify
   ```
6. Espera a que termine el workflow (normalmente unos minutos) y comprueba el iPhone (ver «Verificación en el iPhone»).

## Cambiar hora, aplazar o suspender un partido

- **Cambio de hora:** edita `kickoff.time` (instante UTC con `Z`) **y** `kickoff.date` (fecha local de Madrid, `AAAA-MM-DD`). Deben ser coherentes: el 31 de octubre a las 19:00 en Madrid es `2026-10-31T18:00:00Z` (CET, UTC+1) y el 15 de julio a las 19:00 es `2026-07-15T17:00:00Z` (CEST, UTC+2). Una hora a las 00:30 en Madrid cae en el día anterior en UTC: la puerta lo comprueba.
- **Hora por confirmar:** `kickoff.time: null` con `kickoff.date` rellena. Sin fecha: las dos a `null`. Nunca omitas la clave.
- **Aplazado:** `status: "postponed"`, `score: null`, y si se sabe la nueva fecha, `kickoff.date` nueva; explica el motivo en `note`.
- **Suspendido:** `status: "suspended"` y `score: null`. El marcador parcial no se publica: la puerta rechaza un `score` con un estado distinto de `live` y `finished`.
- Cambia siempre `updatedAt`.

## Carga inicial

Se hace una sola vez, con los datos de la temporada completa (US-007b). Nunca se escribe el JSON a mano: se transcribe a CSV y se convierte. **El PDF oficial del calendario no se sube nunca a este repositorio** (ni a `public/`, ni a ningún otro sitio: la lista permitida lo rechaza). Se lee fuera del repositorio y solo se transcriben los datos.

1. **Prepara los CSV** fuera de `public/` (los de ejemplo, con datos ficticios, están en `examples/initial-load/`). Son UTF-8, separados por comas y con la cabecera exacta de los ejemplos:
   - `equipos.csv`: `id,name,shortName`. Los escudos no se cargan (`crestURL` siempre `null`).
   - `partidos.csv`: una fila por partido, con su fase y su ronda. La **hora es la local de Madrid** (`HH:MM`); el conversor la pasa a UTC.
   - Campos vacíos: hora vacía = hora por confirmar; fecha vacía (y hora vacía) = sin fecha; estado vacío = `scheduled`. Un partido sin marcador final no lleva `homeScore` ni `awayScore`: nunca `0-0`.
2. **Convierte**, fuera de `public/` (el conversor se niega a escribir dentro) y con el instante actual en UTC:
   ```
   scripts/convert-csv --teams equipos.csv --matches partidos.csv --season-id 2026-27 \
       --competition-name "Primera División de fútbol sala" \
       --updated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --out /tmp/2026-27.json
   ```
   Si hay errores, cada uno indica `fichero:línea` y no se escribe nada: corrige el CSV y repite.
3. **Copia y valida:**
   ```
   cp /tmp/2026-27.json public/v1/seasons/2026-27.json
   scripts/verify
   ```
   `verify` aplica el esquema, las invariantes y la comprobación de `updatedAt` frente a `HEAD`.
4. **Publica** como en «Publicar un resultado» (paso 4): confirma en `main` con el tiempo dedicado en el mensaje y espera al workflow.

Para corregir los CSV y regenerar el JSON, **vuelve a pasar un `--updated-at` nuevo** (el instante actual): así no hace falta editar `updatedAt` a mano y la puerta acepta el cambio aunque el commit anterior fuera legible pero inválido.

## Qué hacer si falla `validate`

El workflow queda en rojo, GitHub te avisa por email y **no se despliega nada**: la app sigue viendo la última versión buena.

1. Abre el job `validate` en la pestaña Actions y localiza el paso en rojo (el nombre del paso dice qué comprobación falló).
2. Reproduce el fallo en local, con los mismos scripts que el workflow:
   ```
   scripts/check-allowlist
   scripts/validate-schema public/v1/index.json public/v1/seasons/*.json
   scripts/check-invariants
   ```
   Cada mensaje nombra la invariante incumplida y el identificador afectado (partido, equipo, fase o ronda).
3. Corrige el JSON, pon de nuevo `updatedAt` en el instante actual y vuelve a confirmar. Si el JSON salió del conversor, corrige los CSV y vuelve a convertir con un `--updated-at` nuevo (ver «Carga inicial»): no hace falta editarlo a mano. Si no ves la causa, revierte el cambio (ver «Revertir»).
4. Si falla la paridad del esquema (`shasum`), alguien ha editado `schema/`: restáuralo desde el repositorio de la app; no lo corrijas a mano.

## Qué hacer si falla la comprobación posterior

`deploy` terminó pero `smoke` falló: la versión nueva está publicada, pero no se ha podido confirmar que se sirve bien.

1. Lee el motivo en el job `smoke`: `ETag` ausente, sin `304`, `Content-Type` que no es JSON, o `updatedAt` que no coincide.
2. Si es `updatedAt` que no coincide tras 12 minutos: la CDN de GitHub Pages cachea hasta 600 s (`max-age=600`) y puede tardar; vuelve a lanzar el workflow (Actions → Run workflow) pasados unos minutos. Si pasa de forma habitual de 10 minutos, avisa al Architect (ADR-004, disparador 3).
3. Para repetir la comprobación a mano contra la URL real:
   ```
   scripts/smoke-check --base https://<usuario>.github.io/myfutsal-feed
   ```
4. Si el fallo no es de propagación (por ejemplo, `Content-Type`), repórtalo: es un cambio del hosting.

## Revertir (rollback) a la versión anterior

El historial de Git es el rollback. Localiza el commit malo con `git log --oneline` y revierte **sin reescribir el historial** y **con un `updatedAt` nuevo**: un `git revert` a secas devuelve el contenido anterior con su `updatedAt` anterior, y la puerta lo rechaza (`updated-at-sin-avanzar`) porque el contenido cambia sin que `updatedAt` avance. Además, la app usa `updatedAt` para la antigüedad de los datos (FR-005), que tras un rollback debe reflejar el momento de la corrección.

```
git revert --no-commit <commit>
```

Edita `public/v1/seasons/<id>.json` y pon `updatedAt` en el instante actual en UTC (`date -u +%Y-%m-%dT%H:%M:%SZ`). `git revert --no-commit` deja el contenido revertido en el índice y tu edición solo en el árbol de trabajo: **sin `git add` el commit se lleva el `updatedAt` antiguo** y la puerta lo rechaza aunque `scripts/verify` salga en verde en local. Después:

```
git add public/v1/seasons/<id>.json
scripts/verify
git commit -m "Revertir <commit> (tiempo: 5 min)"
git push origin main
```

El commit pasa por la misma puerta y despliega la versión anterior con el `updatedAt` nuevo. Si no puedes usar la línea de comandos, edita el JSON en la web de GitHub: restaura el contenido bueno y cambia `updatedAt`. Si el commit malo contenía además un cambio bueno, edita el JSON a mano en lugar de revertir.

## Verificación en el iPhone

1. Abre la app y haz *pull to refresh* en la pantalla de resultados.
2. Comprueba que la antigüedad de los datos (FR-005) corresponde al `updatedAt` que acabas de publicar.
3. Si ves la versión anterior, espera hasta 10 minutos (caché de la CDN) y repite. Anota cuánto tardó: es el dato del tiempo de propagación (NFR-011).

## Anotar el tiempo

Para vigilar que el trabajo por día de partido no pasa de 30 minutos (ADR-004, disparador 5), escribe el tiempo en el mensaje de cada commit:

```
Jornada 7: resultados (tiempo: 12 min)
```

Para sumarlo por día de partido:

```
git log --grep='tiempo:' --format='%ad %s' --date=short
```

## Comandos de este repositorio

| Comando | Qué hace |
|---|---|
| `scripts/verify` | Verificación completa en local, sin red |
| `scripts/validate-schema` | Valida ficheros contra el esquema |
| `scripts/check-invariants` | Invariantes que el esquema no expresa |
| `scripts/check-updated-at` | Comprueba que `updatedAt` avanza respecto a `HEAD` |
| `scripts/convert-csv` | Convierte los CSV de equipos y partidos en el JSON de temporada |
| `scripts/check-allowlist` | Solo `v1/index.json` y `v1/seasons/AAAA-AA.json` dentro de `public/` |
| `scripts/check-workflow` | Comprobación estática del workflow |
| `scripts/smoke-check` | Comprobación posterior al despliegue |
