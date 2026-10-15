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
4. **Protección contra push de secretos** (*push protection*): Settings → Code security. Suele estar activada por defecto en repositorios públicos nuevos; comprueba que lo está. Nada de claves ni tokens en este repositorio.
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
3. Corrige el JSON y vuelve a confirmar. Si no ves la causa, revierte el cambio (ver «Revertir»).
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

El historial de Git es el rollback. Localiza el commit malo con `git log --oneline` y revierte **sin reescribir el historial**:

```
git revert <commit>
git push origin main
```

El revert pasa por la misma puerta, así que despliega la versión anterior. Si el commit malo contenía además un cambio bueno, edita el JSON a mano en lugar de revertir.

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
| `scripts/check-allowlist` | Solo `v1/index.json` y `v1/seasons/AAAA-AA.json` dentro de `public/` |
| `scripts/check-workflow` | Comprobación estática del workflow |
| `scripts/smoke-check` | Comprobación posterior al despliegue |
