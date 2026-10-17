# Runbook del feed `league-feed-v1`

Operador: el propio humano (ADR-004). Este repositorio es la fuente de verdad del feed que lee la app; GitHub Pages sirve `public/`.
Todo cambio en `main` pasa por la puerta de `.github/workflows/publish.yml`: `validate` → `deploy` → `smoke`. Si `validate` falla, no se despliega y se sigue sirviendo la última versión buena.

Solo hay tres sitios donde editar: `public/v1/index.json`, `public/v1/seasons/<id>.json` y, en la carga inicial, los datos de la temporada. El esquema (`schema/`) **no se edita aquí**: la copia canónica está en el repositorio de la app.

## Primera vez

Una sola vez, en este orden y antes del primer *push* (historia US-007b1). Los pasos 1 a 5, 7 y 8 los haces tú en tu cuenta de GitHub o en Terminal; el paso 6 lo hace el equipo cuando le pasas tu usuario y tu dirección `noreply`. **Nunca pegues el token en un fichero, en un chat ni en un commit**: solo en la pregunta de contraseña de `git`.

1. **Email privado.** En github.com: avatar → Settings → Emails. Marca «Keep my email addresses private» y «Block command line pushes that expose my email» (es la red de seguridad del servidor para que no se publique un email personal). Copia la dirección con la forma `<id>+<usuario>@users.noreply.github.com` y pásala al equipo junto con tu usuario. Un email personal publicado en el historial de un repositorio público no se puede retirar sin reescribirlo.
2. **Crear el repositorio** en <https://github.com/new>: nombre `myfutsal-feed`, **Public**, sin README, `.gitignore` ni licencia (vacío).
3. **Pages:** Settings → Pages → Build and deployment → Source: «GitHub Actions». Si con el repositorio vacío no aparece la opción, hazlo después del primer push y relanza el workflow (Actions → ejecución fallida → Re-run all jobs).
4. **Protección contra push de secretos** (*push protection*): Settings → Advanced Security → Secret Protection «Enable» y Push protection «Enable». Haz una captura de esa pantalla y de la de Pages (evidencia del criterio 1). Nada de claves ni tokens en este repositorio.
5. **Token para el push** (no hay `gh`; `git` guarda la credencial en el llavero del Mac). Avatar → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token: nombre `myfutsal-feed-push`, caducidad 30 días, «Only select repositories» → `myfutsal-feed`, permisos Contents «Read and write» y Workflows «Read and write» (el push incluye `.github/workflows/`).
6. **Identidad y remoto locales** (los hace el equipo con tu usuario y tu `noreply`; ver «Cómo se fija la dirección `noreply`»):
   ```
   cd myfutsal-feed   # desde la carpeta que contiene el repositorio
   git config --local user.name "<usuario>"
   git config --local user.email "<id>+<usuario>@users.noreply.github.com"
   git remote add origin https://github.com/<usuario>/myfutsal-feed.git
   scripts/verify
   ```
   La identidad va antes que el remoto: con `origin` presente, `scripts/verify` exige que la identidad local sea la `noreply`. La URL del remoto no lleva credenciales; `verify` lo comprueba.
7. **Primer push:**
   ```
   scripts/verify
   git push -u origin main
   ```
   Cuando `git` lo pida, en *Username* pon tu usuario de GitHub y en *Password* el token. En la pestaña Actions deben salir en verde `validate`, `deploy` y `smoke`. Abre el job `smoke` y copia al equipo las últimas líneas del log. Si `git` avisa de *workflow scope*, falta el permiso Workflows del token: amplíalo y repite.
8. **Después de editar en la web de GitHub**, antes de volver a trabajar en local: `git pull --ff-only`. Así tu copia local parte del commit publicado y la puerta no ve un historial divergente.

### Cómo se fija la dirección `noreply`

La dirección del operador **no está escrita en ningún fichero del repositorio**: es la identidad local de Git (`git config --local user.email`, que vive en `.git/config` y no se publica). `scripts/check-structure` (y por tanto `scripts/verify`) comprueba que:

- la identidad local es `feed@example.invalid` (mientras no haya remoto) o tiene la forma `<id>+<usuario>@users.noreply.github.com`, y con `origin` presente solo la segunda;
- es distinta del email global (la ausencia de email global cuenta como distinta);
- cada dirección de `git log --all --format='%ae%n%ce'` es `feed@example.invalid`, `noreply@github.com` (el *committer* de las ediciones web) o esa `noreply` local. Cualquier otra falla y se nombra.

El historial anterior, con `feed@example.invalid`, no se reescribe.

## Publicar un resultado

1. Abre `public/v1/seasons/<id>.json` en el editor web de GitHub (o en local; si antes editaste en la web, haz `git pull --ff-only` primero).
2. En el partido, cambia `status` a `finished` y rellena `score` (`home` y `away`, goles al final incluida la prórroga). En eliminatorias, `decidedBy` es `null`, `extraTime` o `penalties`.
3. Cambia `updatedAt` al instante actual en UTC con formato `AAAA-MM-DDTHH:MM:SSZ`. **Es obligatorio si cambias algo más**: la puerta rechaza un contenido cambiado con el mismo `updatedAt`.
4. Si trabajas en local, **antes de confirmar** ejecuta la verificación completa. Con el cambio aún sin confirmar, `verify` compara `updatedAt` con el de `HEAD`; si ya has confirmado, compara `HEAD` consigo mismo y no detecta un `updatedAt` olvidado:
   ```
   scripts/verify
   ```
5. Confirma en `main` con un mensaje que incluya el tiempo dedicado (ver «Anotar el tiempo»). Si editas en la web de GitHub, no hay verificación local: la puerta del workflow es la que comprueba.
   ```
   git add public/v1/seasons/<id>.json
   git commit -m "Jornada 7: resultados (tiempo: 12 min)"
   git push origin main
   ```
6. Espera a que termine el workflow (normalmente unos minutos) y comprueba el iPhone (ver «Verificación en el iPhone»).

## Cambiar hora, aplazar o suspender un partido

- **Cambio de hora:** edita `kickoff.time` (instante UTC con `Z`) **y** `kickoff.date` (fecha local de Madrid, `AAAA-MM-DD`). Deben ser coherentes: el 31 de octubre a las 19:00 en Madrid es `2026-10-31T18:00:00Z` (CET, UTC+1) y el 15 de julio a las 19:00 es `2026-07-15T17:00:00Z` (CEST, UTC+2). Una hora a las 00:30 en Madrid cae en el día anterior en UTC: la puerta lo comprueba.
- **Hora por confirmar:** `kickoff.time: null` con `kickoff.date` rellena. Sin fecha: las dos a `null`. Nunca omitas la clave.
- **Aplazado:** `status: "postponed"`, `score: null`, y si se sabe la nueva fecha, `kickoff.date` nueva; explica el motivo en `note`.
- **Suspendido:** `status: "suspended"` y `score: null`. El marcador parcial no se publica: la puerta rechaza un `score` con un estado distinto de `live` y `finished`.
- Cambia siempre `updatedAt`.

## Carga inicial

Se hace una sola vez, con los datos de la temporada completa (US-007b2). Nunca se escribe el JSON a mano: se transcribe a CSV y se convierte. **El PDF oficial del calendario no se sube nunca a este repositorio** (ni a `public/`, ni a ningún otro sitio: la lista permitida lo rechaza). Se lee fuera del repositorio y solo se transcriben los datos.

1. **Prepara los CSV** en un directorio fuera del repositorio, por ejemplo `~/myfutsal-carga/` (así no quedan como ficheros sin seguimiento; los de ejemplo, con datos ficticios, están en `examples/initial-load/`). Son UTF-8, separados por comas y con la cabecera exacta de los ejemplos:
   - `equipos.csv`: `id,name,shortName`. Los escudos no se cargan (`crestURL` siempre `null`).
   - `partidos.csv`: una fila por partido, con su fase y su ronda. La **hora es la local de Madrid** (`HH:MM`); el conversor la pasa a UTC.
   - Campos vacíos: hora vacía = hora por confirmar; fecha vacía (y hora vacía) = sin fecha; estado vacío = `scheduled`. Un partido sin marcador final no lleva `homeScore` ni `awayScore`: nunca `0-0`.
2. **Convierte**, fuera de `public/` (el conversor se niega a escribir dentro) y con el instante actual en UTC:
   ```
   scripts/convert-csv --teams ~/myfutsal-carga/equipos.csv --matches ~/myfutsal-carga/partidos.csv --season-id 2026-27 \
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
4. **Publica** solo después de que `verify` esté en verde. Añade únicamente la temporada (nunca `git add -A`), confirma con el tiempo dedicado en el mensaje y espera al workflow:
   ```
   git add public/v1/seasons/2026-27.json
   git commit -m "Carga inicial de la temporada 2026-27 (tiempo: 30 min)"
   git push origin main
   ```

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

## Ensayo de la puerta (una sola vez)

Historia US-007b1, criterios 3 y 4. Se hace **después** del primer despliegue verde y comprueba en el servidor real que un JSON inválido no llega a producción y que el rollback funciona. El equipo prepara el commit inválido en una rama local `ensayo-puerta` (sin pasar por `scripts/verify`, que lo rechazaría); no está publicado hasta que tú lo subes.

El commit cambia `phases[0].kind` de `league` a `legue` (un valor fuera del enumerado del esquema; el marcador actual no tiene partidos, así que no hay `status` que estropear) y avanza `updatedAt`, de modo que solo falla la validación contra el esquema. Si la rama está desfasada porque `main` ha avanzado, el equipo la regenera con `scripts/prepare-gate-rehearsal --recreate`.

1. **Sube el commit inválido:**
   ```
   cd myfutsal-feed   # desde la carpeta que contiene el repositorio
   git switch main
   git merge --ff-only ensayo-puerta
   git push origin main
   ```
2. **Comprueba** en Actions: `validate` en rojo (paso «Validate against the schema»), `deploy` y `smoke` sin ejecutarse, y el email de GitHub. Si no llega, revisa Settings → Notifications → System → Actions. El equipo comprueba con `curl` que la URL pública sigue sirviendo la versión anterior (`kind` = `league`, `updatedAt` anterior).
3. **Rollback** (la misma secuencia de «Revertir (rollback)» con el commit que acabas de subir, `HEAD`):
   ```
   git revert --no-commit HEAD
   sed -i '' "s/\"updatedAt\": \".*\"/\"updatedAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"/" public/v1/seasons/2026-27.json
   git add public/v1/seasons/2026-27.json
   scripts/verify
   git commit -m "Revertir el ensayo de la puerta (tiempo: 5 min)"
   git push origin main
   ```
   (`sed -i ''` es la forma de macOS.) La puerta debe aceptarlo y volver a desplegar.
4. Cuando `smoke` salga en verde, copia al equipo la línea `smoke-check correcto: version ... servida tras N s` del log (si no tienes sesión para ver los logs, sirven las marcas de tiempo de la API pública de GitHub): es el dato de NFR-011 (ver «Registro de evidencias»).
5. Limpieza: `git branch -d ensayo-puerta` (la rama ya está fusionada).

## URL base para la app

La app (US-005) usará como valor de configuración la URL base **con ruta**:

```
https://apo1981.github.io/myfutsal-feed
```

Valor real, sin barra final y con la ruta `/myfutsal-feed` (US-007b1, criterio 8; usuario de GitHub `apo1981`). El feed vive en `<URL base>/v1/index.json`, y la temporada vigente en `<URL base>/v1/seasons/<id>.json`.

## Registro de evidencias de US-007b1

Datos de ejecución real, no estimaciones. Fecha de las comprobaciones: 2026-10-16.

| Criterio | Evidencia | Estado |
|---|---|---|
| 1. Repositorio público, Pages con «GitHub Actions», Secret Protection y Push protection, `noreply` | Capturas del humano (2026-10-16): Settings → Pages con Source «GitHub Actions»; Settings → Advanced Security con Secret Protection y Push protection activadas; en Emails, «Keep my email addresses private» y «Block command line pushes that expose my email» marcados. Repositorio `apo1981/myfutsal-feed`, remoto `origin` = `https://github.com/apo1981/myfutsal-feed.git`. La dirección `noreply` no está escrita en ningún fichero versionado; sí aparece, como se pretende, en los metadatos de los commits | Hecho |
| 2. Primer despliegue: `validate`, `deploy` y `smoke` en verde; 200 + `ETag`; 304 con `If-None-Match` | Run 38923663846 «Publish feed» (commit `e02abaf`): `validate`, `deploy` y `smoke` en verde; creado 15:24:22Z, terminado 15:24:57Z (unos 35 s). `curl` desde fuera (~15:27Z): `/v1/index.json` → HTTP/2 200, `content-type: application/json; charset=utf-8`, `etag: "6ad241bd-b7"`, `cache-control: max-age=600`, 183 B; con `If-None-Match` → 304. `/v1/seasons/2026-27.json` → 200, `application/json`, `etag: "6ad241bc-1b8"`, 440 B (248 B con gzip); con `If-None-Match` → 304 | Hecho |
| 3. La puerta bloquea el commit inválido; rollback aceptado y desplegado | Push de `0bc5e9a` + `9027692` (ensayo): run 38925181617 (head `9027692`) en `failure`; `validate` falló en «Validate against the schema», `deploy` y `smoke` omitidos. El humano recibió el email de GitHub del fallo. `curl` tras el fallo: `/v1/seasons/2026-27.json` seguía con `etag: "6ad241bc-1b8"`, `updatedAt` 2026-10-15T00:00:00Z y `kind` `league` (versión anterior servida). Rollback siguiendo este runbook: commit `c0815d5` «Revertir el ensayo de la puerta (tiempo: 5 min)»; su run (head `c0815d5`) en `success`: `validate` 15:40:17–15:40:28, `deploy` 15:40:32–15:40:47, `smoke` 15:40:50–15:40:56. `curl` después: `etag: "6ad24578-1b8"`, `last-modified` 15:40:40 GMT, `updatedAt` 2026-10-16T15:37:45Z, `kind` `league` | Hecho |
| 4. Propagación (NFR-011, objetivo ≤ 15 min; revisar ADR-004 si es > 10 min de forma habitual) | Run del rollback (`c0815d5`): la versión nueva se sirvió en el primer intento de `smoke` (paso «Post-deploy check» 15:40:52–15:40:53), es decir, servida tras 6 s desde que terminó `deploy` (15:40:47) y unos 38 s desde la creación de la ejecución (15:40:15Z). Es un mejor caso, con el borde de la CDN sin copia anterior: no prueba que un despliegue purgue la CDN, que sigue sin verificar (peor caso por diseño ≈ 11 min). Fuente: marcas de tiempo de la API pública de GitHub; los logs exigen sesión y no se copió la línea literal `servida tras N s`. Por debajo del objetivo de 15 min; el disparador de ADR-004 (> 10 min de forma habitual) no se activa | Hecho |
| 7c I-2. `actions/upload-pages-artifact@v5` y `actions/deploy-pages@v5` sin avisos de runtime obsoleto | La API pública de check-runs del run 38923663846 muestra 0 anotaciones en los 3 jobs | Hecho: sin avisos |
| 7c S-4. Si el primer `smoke` da 404 o 5xx, reintentar dentro del plazo de 12 min; si no, se descarta | El paso «Post-deploy check» pasó en 0 s al primer intento, sin 404 ni 5xx. **Decisión: descartado**; no se añade reintento en `smoke-check`. Se reabre si algún `smoke` real da 404/5xx justo tras `deploy` | Descartado |
| A7. Pages permite elegir la fuente con el repositorio vacío | Confirmado: «GitHub Actions» se pudo elegir con el repositorio vacío, antes del primer push | Confirmado |
| A8. El push de `.github/workflows/` exige el permiso Workflows | El push funcionó con un token con Contents y Workflows en lectura y escritura; no se probó sin Workflows | Sin refutar |
| 8. URL base | `https://apo1981.github.io/myfutsal-feed` (ver «URL base para la app») | Hecho |

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
| `scripts/prepare-gate-rehearsal` | Prepara en local, sin publicar, el commit inválido del ensayo de la puerta |
