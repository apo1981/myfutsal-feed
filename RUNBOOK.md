# Runbook del feed `league-feed-v1`

Operador: el propio humano (ADR-004). Este repositorio es la fuente de verdad del feed que lee la app; GitHub Pages sirve `public/`.
Todo cambio en `main` pasa por la puerta de `.github/workflows/publish.yml`: `validate` → `deploy` → `smoke`. Si `validate` falla, no se despliega y se sigue sirviendo la última versión buena.

Solo hay tres sitios donde editar: `public/v1/index.json`, `public/v1/seasons/<id>.json` y, en la carga inicial, los datos de la temporada (la política `public/privacy.html` se actualiza según «Actualizar la política»). El esquema (`schema/`) **no se edita aquí**: la copia canónica está en el repositorio de la app.

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

**Puerta de publicación del playoff:** No se cargan partidos en las rondas del playoff (`kind: knockout`) del feed real hasta que haya en TestFlight un build con US-009e (D-429).

## Cambiar hora, aplazar o suspender un partido

- **Cambio de hora:** edita `kickoff.time` (instante UTC con `Z`) **y** `kickoff.date` (fecha local de Madrid, `AAAA-MM-DD`). Deben ser coherentes: el 31 de octubre a las 19:00 en Madrid es `2026-10-31T18:00:00Z` (CET, UTC+1) y el 15 de julio a las 19:00 es `2026-07-15T17:00:00Z` (CEST, UTC+2). Una hora a las 00:30 en Madrid cae en el día anterior en UTC: la puerta lo comprueba.
- **Hora por confirmar:** `kickoff.time: null` con `kickoff.date` rellena. Sin fecha: las dos a `null`. Nunca omitas la clave.
- **Aplazado:** `status: "postponed"`, `score: null`, y si se sabe la nueva fecha, `kickoff.date` nueva; explica el motivo en `note`.
- **Suspendido:** `status: "suspended"` y `score: null`. El marcador parcial no se publica: la puerta rechaza un `score` con un estado distinto de `live` y `finished`.
- Cambia siempre `updatedAt`.

## Cambiar las equipaciones de un equipo

La app dibuja una camiseta con los colores de `teams[].kits` (ADR-010). Los escudos no se usan: `crestURL` sigue a `null`. Si `kits` falta o es `null`, la app muestra una camiseta gris neutra.

Dónde está cada cosa (rutas desde la raíz del repo de la app, no de este repo):

- Valores acordados por equipo (home y away, con sus supuestos): `docs/product/kits-2026-27.md`. Apunta ahí cualquier cambio de color antes o a la vez que lo publicas.
- Decisión y formato: `docs/arch/ADR-010-colores-equipacion-y-navegacion.md` (Enmienda 2026-10-26).
- Contrato del campo: `docs/arch/contracts/league-feed-v1.md`. Reglas ejecutables: `schema/league-feed-v1.season.schema.json` (`$defs/kits` y `$defs/kit`).

Forma del campo, dentro del equipo en `public/v1/seasons/<id>.json`:

```json
"kits": {
  "home": { "shirt": "#004D98", "accent": "#A50044", "pattern": "stripes", "shorts": "#004D98" },
  "away": { "shirt": "#3B1F5C", "shorts": "#3B1F5C" }
}
```

1. Edita el equipo en `public/v1/seasons/<id>.json` (misma mecánica que «Publicar un resultado»).
   Los `kits` van **dentro del objeto del equipo** (en `teams[]`), en el mismo fichero de la temporada. **No crees ficheros nuevos en `public/`**: la app solo lee `2026-27.json` y la lista permitida rechaza cualquier otro fichero, así que `validate` falla y no se despliega nada.
2. Reglas: `home` es obligatoria y `away` opcional. Cada equipación necesita `shirt` y `shorts`. Los colores van en `#RRGGBB` con **mayúsculas**. `accent` es opcional (`null` o ausente = sin valor). `pattern` es `solid` (por defecto) o `stripes`; con `stripes` el `accent` es obligatorio y es el segundo color de la raya.
3. Cambia `updatedAt` al instante actual en UTC (obligatorio, igual que con un resultado).
4. Si trabajas en local, antes de confirmar:
   ```
   scripts/verify
   ```
5. Confirma y publica con el tiempo dedicado en el mensaje (ver «Anotar el tiempo»):
   ```
   git add public/v1/seasons/<id>.json
   git commit -m "Equipaciones: <equipo> (tiempo: 5 min)"
   git push origin main
   ```
6. Tras el despliegue, tira para refrescar en la app y comprueba la camiseta (ver «Verificación en el iPhone»).

## Carga inicial

Se hace una sola vez, con los datos de la temporada completa (US-007b2). Nunca se escribe el JSON a mano: se transcribe a CSV y se convierte. **El PDF oficial del calendario no se sube nunca a este repositorio** (ni a `public/`, ni a ningún otro sitio: la lista permitida lo rechaza). Se lee fuera del repositorio y solo se transcriben los datos.

1. **Prepara los CSV** en `local-data/`, en la raíz del repositorio de la app (`../local-data/` desde este; está ignorado por Git en la app, así que ni el PDF ni los CSV se versionan en ninguno de los dos repositorios; tras la carga la fuente de verdad es el JSON; los de ejemplo, con datos ficticios, están en `examples/initial-load/`). Son UTF-8, separados por comas y con la cabecera exacta de los ejemplos:
   - `equipos.csv`: `id,name,shortName`. Los escudos no se cargan (`crestURL` siempre `null`).
   - `partidos.csv`: una fila por partido, con su fase y su ronda. La **hora es la local de Madrid** (`HH:MM`); el conversor la pasa a UTC.
   - Campos vacíos: hora vacía = hora por confirmar; fecha vacía (y hora vacía) = sin fecha; estado vacío = `scheduled`. Un partido sin marcador final no lleva `homeScore` ni `awayScore`: nunca `0-0`.
2. **Convierte**, fuera de `public/` (el conversor se niega a escribir dentro) y con el instante actual en UTC:
   ```
   scripts/convert-csv --teams ../local-data/equipos.csv --matches ../local-data/partidos.csv --season-id 2026-27 \
       --competition-name "Primera División de fútbol sala" \
       --updated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --out /tmp/2026-27.json
   ```
   Si hay errores, cada uno indica `fichero:línea` y no se escribe nada: corrige el CSV y repite.
3. **Copia y valida**, en este orden (`convert-csv` → `check-initial-load` → `verify` sin confirmar → commit → push):
   ```
   cp /tmp/2026-27.json public/v1/seasons/2026-27.json
   scripts/check-initial-load --cutoff AAAA-MM-DD
   scripts/verify
   ```
   `check-initial-load` comprueba la carga (sin marcador `pendiente`, partidos anteriores al corte nunca `scheduled` ni `finished` después del corte) y lista los `unknown` que hay que poner al día. `verify` aplica el esquema, las invariantes y la comprobación de `updatedAt` frente a `HEAD`; va **antes** del commit porque esa última comprobación no está en `check-initial-load` (D-140).
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

## Ensayo del operador (US-007b2c)

Tres tareas tuyas, en este orden o como te convenga: **A** publicar un resultado real desde el navegador, **B** comparar una jornada con la fuente oficial y **C** poner al día los resultados pendientes. Anota todo en la tabla del final. El token de push caduca hacia 2026-11-15: haz A antes; después puedes borrarlo (Settings → Developer settings → Fine-grained tokens → Delete). El editor web no necesita token.

### A. Publicar un resultado desde la web de GitHub y medir la propagación (criterios 6 y 7c(ii))

1. Apunta la hora de inicio (para el tiempo total). En el navegador (móvil o escritorio; si en el móvil el editor no se muestra bien, pide «Versión de escritorio») abre <https://github.com/apo1981/myfutsal-feed/blob/main/public/v1/seasons/2026-27.json> y pulsa el lápiz («Edit this file»).
2. Localiza el partido. Sin teclado (móvil) no hay búsqueda fiable en el editor, y el fichero tiene unas 3.450 líneas: ejecuta `scripts/round-sheet --round 1 --phase apertura` y mira la columna `L<línea>` del partido; abre `https://github.com/apo1981/myfutsal-feed/blob/main/public/v1/seasons/2026-27.json#L<línea>`, comprueba que es el partido y pulsa el lápiz. Para el ensayo basta un partido de la jornada 1 del Apertura (primeras ~250 líneas). En escritorio también sirve Ctrl/Cmd+F con el `id` (por ejemplo `a01-01`). Cambia `"status": "unknown"` (o `"scheduled"`) por `"status": "finished"` y `"score": null` por `"score": { "home": 4, "away": 2 }` (goles del local y del visitante). `decidedBy` y `note` quedan en `null`.
3. Cambia `"updatedAt"` (cerca del principio del fichero) al instante actual en **UTC**, formato `AAAA-MM-DDTHH:MM:SSZ`. Hasta las 03:00 del domingo 25-10-2026 (hora de Madrid) resta 2 h; desde ese momento, resta 1 h. Ejemplo: el 25-10 a las 10:00 en Madrid es `2026-10-25T09:00:00Z`. Si dudas, abre <https://time.is/UTC>. Debe ser posterior al anterior, y **no pongas una hora futura**: la puerta lo rechaza (`updated-at-en-el-futuro`, más de 15 min por delante de la hora real) y `apply-results` también. Si ya se publicó un `updatedAt` futuro, corrígelo con un commit que lo baje a la hora real: la puerta lo acepta, y `apply-results` usa la hora actual y lo avisa.
4. Pulsa «Commit changes…», deja «Commit directly to the `main` branch» y confirma. **Apunta la hora exacta de ese clic (hora t0)**; el mensaje puede llevar el tiempo, como en «Anotar el tiempo».
5. Mide la propagación (NFR-011, máximo 15 min). Desde Terminal, repite cada 30 s hasta que `updatedAt` sea el que has escrito (la hora t1 es la de la primera salida correcta):
   ```
   while true; do date +%T; curl -s "https://apo1981.github.io/myfutsal-feed/v1/seasons/2026-27.json?t=$(date +%s)" | grep '"updatedAt"'; sleep 30; done
   ```
   Si no tienes Terminal, abre esa URL en el navegador y recarga. Propagación = t1 − t0. En la pestaña Actions de GitHub se ve el flujo `validate` → `deploy` → `smoke`; si `validate` falla (rojo), se sigue sirviendo la versión anterior y se trata como en «Qué hacer si falla `validate`».
6. Comprueba la app: «Verificación en el iPhone».
7. **Committer web (criterio 7c(ii)).** En la carpeta del repositorio del feed, trae el commit y ejecuta la comprobación de estructura (comprueba que las direcciones del historial están permitidas, incluida `noreply@github.com` de las ediciones web):
   ```
   git pull --ff-only
   scripts/check-structure
   ```
   Si pasa, anótalo. Si falla nombrando otra dirección distinta de `noreply@github.com`, **no la cambies tú**: pásala al equipo (hay que ajustar la lista permitida y registrarlo en `decisions.md`).

### B. Comparar una jornada con la fuente oficial (criterio 7)

1. Elige una jornada (cualquiera, mejor una con resultados que conozcas) y obtén la hoja:
   ```
   scripts/round-sheet --round 3 --phase apertura
   ```
   (`--phase clausura` para la segunda vuelta; sin `--phase` salen las dos fases que tengan esa jornada.) Cada línea trae: id, fecha y hora en Europe/Madrid (o «sin hora»), local, visitante, estado y marcador (o «pendiente»).
2. Compara línea a línea con la web de la RFEF: fecha, hora, **quién es local y quién visitante**, y marcador. La localía invertida es el riesgo conocido (D-178): la columna local/visitante del PDF original no tenía rótulo. Si el local de la RFEF es el visitante del feed en un partido, la localía está invertida: anota el id y avisa al equipo (se regenera el CSV invirtiendo y se republica; no corrijas a mano 240 partidos).
3. Cada diferencia de fecha, hora o marcador es una discrepancia: corrígela con «Publicar un resultado» o «Cambiar hora, aplazar o suspender un partido» y repite la comparación hasta 0 discrepancias.

### C. Poner al día los resultados pendientes (criterio 8)

1. Lista lo que falta, por jornada (`unknown` y `scheduled` con fecha ya pasada):
   ```
   scripts/round-sheet --pending
   ```
2. **Pocos partidos (hasta unos 5):** edítalos en la web como en A (un solo commit con varios partidos y un solo `updatedAt` nuevo).
3. **Muchos partidos (los 42 `unknown`):** genera la plantilla, rellénala y aplícala.
   ```
   mkdir -p ../local-data
   scripts/round-sheet --pending --csv > ../local-data/resultados.csv
   ```
   Abre `../local-data/resultados.csv` en una hoja de cálculo o editor y rellena solo las columnas `home` y `away` (goles del local y del visitante tal como están en el feed; las demás columnas son de contexto). Una fila con `home` y `away` vacíos se ignora y el partido queda como está: **nunca rellenes un marcador que no conozcas**. `../local-data/` (en el repositorio de la app, como en la carga inicial) no se versiona; el `.gitignore` de este repositorio también ignora `local-data/` por si acaso. Después:
   ```
   scripts/apply-results ../local-data/resultados.csv
   ```
   El comando pone `finished`, el marcador y un `updatedAt` nuevo, y ejecuta `scripts/verify`. Si una fila es inválida (un solo gol, no numérico, id desconocido, partido ya finalizado, partido que aún no se ha jugado o sin fecha, partido de eliminatoria) no escribe nada y dice la fila. Si después `verify` falla, el propio comando restaura la temporada tal como estaba: no hagas commit y avisa al equipo. Revisa el cambio con `git diff --stat` y publica con los pasos 5 y 6 de «Publicar un resultado» (`git add`, `git commit` con el tiempo y `git push origin main`).
4. Cuando estén publicados, comprueba que ya no quedan pendientes: `scripts/round-sheet --pending`.
5. **Localía (D-178):** el primer resultado que contrastes con la RFEF debe coincidir en local y visitante. Si no coincide, avisa al equipo antes de seguir publicando.

**Registro de evidencias de US-007b2c** (rellénalo tú; el equipo lo pasa a la historia):

| Dato | Valor |
|---|---|
| Fecha del ensayo | |
| Dispositivo y navegador (móvil o escritorio) | |
| Partido publicado (id y marcador) | |
| t0 (clic en «Commit changes») | |
| t1 (primer `curl` con el `updatedAt` nuevo) | |
| Propagación t1 − t0 (máximo 15 min) | |
| Estado de `validate`, `deploy` y `smoke` | |
| Tiempo total dedicado al ensayo A | |
| `git pull --ff-only` y `scripts/check-structure` (resultado y dirección del committer web) | |
| Ambigüedades o dudas del runbook | |
| Incidente `da845c4`: `updatedAt` publicado con `2026-11-25T09:35:00Z` (un mes en el futuro) en el ensayo web; la puerta lo aceptó. Corrección: `updatedAt` a `2026-10-25T09:35:00Z` (D-543, D-544) | Pendiente de anotar el hash de la corrección |
| Jornada comparada (fase y número) | |
| Discrepancias encontradas y corregidas (id y qué) | |
| Discrepancias tras repetir (objetivo 0) | |
| Localía confirmada con el primer resultado contrastado (sí/no, id) | |
| Pendientes antes y después de C (`scripts/round-sheet --pending`) | |
| Tiempo total dedicado a B y C | |

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

## Registro de evidencias de US-007b2b

Carga inicial de la temporada 2026-27 sobre el commit `a13f601`, comprobada con `scripts/check-initial-load --cutoff 2026-10-17` (corte fijado por el humano, D-152). Los partidos `unknown` son los resultados que hay que poner al día con «Publicar un resultado» (NFR-011, ≤ 12 h):

```
temporada 2026-27, corte 2026-10-17: 16 equipos, 240 partidos, 3767 bytes con gzip (maximo 102400)
apertura: 15 jornadas, 120 partidos
clausura: 15 jornadas, 120 partidos
partidos unknown para el runbook: 42
  a01-01 (2026-09-13)
  a01-02 (2026-09-13)
  a01-03 (2026-09-13)
  a01-04 (2026-09-13)
  a01-05 (2026-09-13)
  a01-06 (2026-09-13)
  a01-07 (2026-09-13)
  a01-08 (2026-09-13)
  a02-01 (2026-09-20)
  a02-02 (2026-09-20)
  a02-03 (2026-09-20)
  a02-04 (2026-09-20)
  a02-05 (2026-09-20)
  a02-06 (2026-09-20)
  a02-07 (2026-09-20)
  a02-08 (2026-09-20)
  a03-01 (2026-09-27)
  a03-02 (2026-09-27)
  a03-03 (2026-09-27)
  a03-04 (2026-09-27)
  a03-05 (2026-09-27)
  a03-06 (2026-09-27)
  a03-07 (2026-09-27)
  a03-08 (2026-09-27)
  a04-01 (2026-10-04)
  a04-02 (2026-10-04)
  a04-03 (2026-10-04)
  a04-04 (2026-10-04)
  a04-05 (2026-10-04)
  a04-06 (2026-10-04)
  a04-07 (2026-10-04)
  a04-08 (2026-10-04)
  a05-01 (2026-10-11)
  a05-02 (2026-10-11)
  a05-03 (2026-10-11)
  a05-04 (2026-10-11)
  a05-05 (2026-10-11)
  a05-06 (2026-10-11)
  a05-07 (2026-10-11)
  a05-08 (2026-10-11)
  a07-01 (2026-09-29)
  a07-08 (2026-09-29)
carga inicial correcta
```

**Fechas provisionales (UFCL).** Los 8 partidos de Barça y ElPozo Murcia que siguen `scheduled` en jornadas con ventana de dos días llevan el primer día de la ventana, sin hora. Confirmar con el horario de la RFEF y corregir con «Cambiar hora, aplazar o suspender un partido»:
- a09-01 y a09-06 (17-11-2026)
- a11-01 y a11-02 (15-12-2026)
- c05-01 y c05-02 (23-02-2027)
- c07-07 y c07-08 (06-04-2027)

## Actualizar la política

La política de privacidad se publica en `<URL base>/privacy.html` (US-008b). El texto de referencia es `docs/legal/privacy-policy.md` del repositorio de la app; `public/privacy.html` es una copia a mano, fiel y con la misma fecha.

1. Edita el `.md` en el repositorio de la app y cambia «Última actualización».
2. Copia el cambio a `public/privacy.html` (mismas secciones y misma fecha). Sin scripts ni recursos de terceros.
3. Comprueba que no hay correos, nombres reales ni rutas locales; solo se admite el usuario público de GitHub `apo1981`.
4. Ejecuta `scripts/verify`. El fichero es obligatorio: borrarlo hace fallar `ficheros-obligatorios`.
5. Haz commit y push. `smoke` pide la página y exige 200, `text/html`, que mencione «GitHub» e «IP» y que sea idéntica byte a byte a la local; si la CDN aún sirve la versión anterior, reintenta como con `updatedAt`.

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
| `scripts/round-sheet` | Hoja de una jornada (`--round N`) y lista de pendientes (`--pending`, `--csv`); solo lectura |
| `scripts/apply-results` | Aplica en bloque un CSV `id,home,away` rellenado por el operador y ejecuta `verify` |
| `scripts/check-allowlist` | Solo `v1/index.json`, `privacy.html` y `v1/seasons/AAAA-AA.json` dentro de `public/` |
| `scripts/check-workflow` | Comprobación estática del workflow |
| `scripts/smoke-check` | Comprobación posterior al despliegue (feed y `privacy.html`) |
| `scripts/prepare-gate-rehearsal` | Prepara en local, sin publicar, el commit inválido del ensayo de la puerta |
