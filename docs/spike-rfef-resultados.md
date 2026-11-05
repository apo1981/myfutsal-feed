# SPIKE US-033: estructura de los resultados de la RFEF

Veredicto: **VIABLE** para US-034, con una condición de diseño: la jornada se pide al endpoint XHR de la propia web, no a la URL de la página (ver 1 y 2).

## Cómo se comprobó (escenario «Respeto a la fuente»)
- `robots.txt` consultado antes de cualquier página (2026-11-05): no hay grupo `User-agent: *`; solo hay grupos para Mediapartners-Google, Adsbot-Google, Googlebot y Slurp, que vetan `/index.php`, `/ajax` y `/soccer_widget`. Ni las páginas de resultados ni `/futsal_xhr/` están vetadas.
- User-Agent: `myfutsal-feed-spike/1.0 (offline fixture tool; non-personal)`. Sin cookies ni sesión. Peticiones en serie, ≥ 11 s entre ellas.
- Peticiones de datos (todas UTC, 2026-11-05): `GET /competicion/liga-prime-futsal/2027/resultados/1` (15:42:17), `GET .../2027/resultados/2` (15:43:23), `POST /futsal_xhr/get_matchs_round` con jornada 1 (15:43:53), jornada 7 (15:44:36) y jornada 8 (15:44:48). Además 2 ficheros estáticos (`/media/js/custom.js` y `/media/js/categories.js`) para leer cómo cambia de jornada la web. Total: 7 peticiones, 1 por jornada.
- HTML/JSON crudo solo en `../local-data/rfef-spike/` (ignorado por git). No hay datos de la RFEF versionados; los ejemplos de abajo están escritos a mano.

## Pregunta 1: significado de `2027` y `<n>`
URL: `https://futsal.rfef.es/competicion/liga-prime-futsal/<año>/resultados/<grupo>`
- `2027` = **año de fin de temporada** (2026/27). Aparece como `year_competition` y en los enlaces de partido.
- `<n>` = **grupo = torneo**, no jornada ni página: `total_groups=2`. `1` = Torneo Apertura (`active_league=1297`), `2` = Torneo Clausura (`active_league=1298`). Comprobado: `/resultados/1` devuelve `1297`, `/resultados/2` devuelve `1298`.
- La jornada mostrada **no está en la URL**: es la «jornada actual» del torneo según el servidor. `/resultados/1` mostró la jornada 8 (7, 14 y 15-nov, sin marcador); `/resultados/2` mostró la jornada 1 de Clausura. Por eso no coincidía con `a01-01` (13-09).
- Ejemplos por jornada de Apertura: la URL es la misma para las 15 (`.../2027/resultados/1`); lo que cambia es el cuerpo del POST (pregunta 2).

## Pregunta 2: seleccionar una jornada de forma determinista (1 petición)
El desplegable de la web llama por AJAX a `POST https://futsal.rfef.es/futsal_xhr/get_matchs_round` (form-urlencoded):
`category=25&league=<liga>&round=<jornada>&action=competicion&year=2027&group=<grupo>`
- `league`: `1297` Apertura, `1298` Clausura. Cada `<option>` del desplegable lo trae como `value="<liga>-<jornada>-<jornada>-<grupo>"`.
- `category=25` = Liga Prime Futsal. Respuesta: JSON (aunque el Content-Type es `text/html`), 200, ~1-6 KB, sin HTML que parsear.
- Una petición = una jornada completa. Observado con Apertura 1, 7 y 8. **No verificado:** Clausura por POST y playoff (la liga/grupo del playoff no se ha visto; el selector de la página de resultados solo tiene 2 grupos de 15 jornadas, así que el playoff probablemente va por otra sección).
- Riesgo: es un endpoint interno no documentado; puede cambiar sin aviso. Alternativa de respaldo: `--from-file` sobre el HTML de `/resultados/<n>` (solo cubre la jornada actual).

## Pregunta 2b: identificar jornada y fase en la respuesta
- Cada partido trae `league_id` (`1297`/`1298` = fase), `group_code` (`1`/`2`), `category` (texto `Liga Prime Futsal : Jornada N`) e `year`. La jornada se lee de `category` (regex `Jornada (\d+)$`).
- En el HTML de la página: `<input id="active_round">`, `active_league` y `<option ... selected>`.
- Página no corresponde a lo pedido: si `league_id`/`group_code` o el número de jornada de algún partido no coinciden con lo solicitado, o la respuesta no es un JSON con claves de fecha, o no hay ningún partido, el CLI debe abortar sin generar CSV. (No verificado qué responde la web a una jornada fuera de rango.)

## Pregunta 3: campos por partido y estados
Estructura (resumida a mano): objeto por fecha ISO `{"2026-09-12": {"date": "Sábado, 12 de Septiembre", "descansa": [], "matches": [...]}}`. Ojo: `matches` es **lista o dict indexado por cadena** según el día (hay que aceptar ambos).
Campos útiles por partido: `team_local`, `team_visit` (nombre), `datateam1`/`datateam2` (id numérico de equipo RFEF, estable), `homeTeamAlias`/`awayTeamAlias` (slug; **inestable**, p. ej. `movistar-inter` para «Inter JP Financial»), `hour` (`HH:MM`, Madrid), `no_hour` (`"0"`/`"1"`), `status_txt`, `status`, `status_reason`, `r_local`/`r_visit` (goles como cadena; vacía si no hay), `penaltis1`/`penaltis2` (`99` = sin penaltis), `id` del partido RFEF. La fecha es la clave del día.

| Estado | Representación | Verificado |
|---|---|---|
| Finalizado | `status_txt="Finalizado"`, `status="Finalizado"`, `r_local`/`r_visit` enteros en cadena | Sí (jornadas 1 y 7) |
| Sin marcador / no jugado | `status_txt="Sin comenzar"`, `status="Pendiente"`, goles `""` | Sí (jornada 8) |
| Sin hora | `no_hour="1"` previsiblemente | **No verificado** (todos `"0"`) |
| Aplazado | — | **No verificado** |
| Suspendido | — | **No verificado** |
| En juego | — | **No verificado** |
| Playoff / penaltis | `penaltis1/2` distinto de `99` previsiblemente | **No verificado** |
Regla para US-034 (ADR-011 §3): rellenar marcador solo si `status_txt=="Finalizado"` y ambos goles son enteros; cualquier otro estado o valor desconocido, vacío y avisado.

Ejemplo mínimo (redactado a mano):
```json
{"2026-09-12": {"date": "Sábado, 12 de Septiembre", "descansa": [],
  "matches": [{"id": "000", "status_txt": "Finalizado", "status": "Finalizado", "hour": "13:00", "no_hour": "0",
    "team_local": "Equipo A", "datateam1": "23", "r_local": "3",
    "team_visit": "Equipo B", "datateam2": "31", "r_visit": "5", "category": "Liga Prime Futsal : Jornada 1",
    "league_id": "1297", "group_code": "1", "year": "2027"}]}}
```

## Hallazgos que afectan a US-034..US-036
- **Fechas del feed distintas de la web.** Jornada 1 en la web: partidos el 11, 12 y 13-09 con hora; en el feed `a01-*` están todos el 13-09 y sin hora. La jornada 7 de la web tiene partidos del 29-09, 30-09 y del 30-10 al 01-11 (adelantados); el feed tiene 2 en 29-09 y 6 `scheduled` al 01-11, que la web ya da por jugados. Hacen falta el informe de diferencias de fecha/hora (US-036) y casar por **pareja local-visitante**, nunca por orden ni posición (el orden de la web no coincide con `a07-01..`).
- Localía: coincide en los casos contrastados (jornada 1: a01-01 Jimbee 2-6 Inter, a01-02 Sur Seed 4-1 Córdoba, a01-03 Catgas 2-4 Palma coinciden con el feed; jornada 7 comprobada solo por nombres).
- Los nombres traen espacios finales o dobles («Catgas Energia␣», «Quesos␣␣Hidalgo ...»): normalizar espacios antes de buscar el alias.

## Pregunta 4: alias candidatos RFEF → teamId (16 de 16 equipos)
Clave preferida: `datateam` (id RFEF numérico); el nombre es respaldo.

| datateam | Nombre en la web | teamId del feed |
|---|---|---|
| 4 | Córdoba Patrimonio Humanidad | cordoba-patrimonio |
| 9 | Quesos Hidalgo Manzanares FS | quesos-el-hidalgo-manzanares |
| 10 | Noia Portus Apostoli | noia-portus-apostoli |
| 18 | Barça | barca |
| 20 | Magna Xota FS | osasuna-magna |
| 21 | Jimbee Cartagena Costa Cálida | jimbee-cartagena |
| 22 | ElPozo Murcia Costa Cálida | elpozo-murcia |
| 23 | Wanapix | wanapix-sala-10 |
| 24 | Catgas Energia | fs-garcia |
| 25 | Jaén Paraíso Interior | jaen-paraiso-interior |
| 27 | Inter JP Financial | inter-jp-financial |
| 29 | O Parrulo Ferrol | o-parrulo-ferrol |
| 30 | Illes Balears Palma Futsal | palma-futsal |
| 31 | Servigroup Peñíscola FS | servigroup-peniscola |
| 32 | Viña Albali Valdepeñas | vina-albali-valdepenas |
| 2883 | Sur Seeds El Ejido Futsal | sur-seed-el-ejido |

Son nombres de patrocinio: pueden cambiar (Osasuna Magna aparece como «Magna Xota FS» y «C.A. Osasuna Magna» en el feed). Por eso se prefiere el id numérico. Los id coinciden con las URLs `/equipo/<slug>/<id>/info` de la página (p. ej. 10, 30).

## Riesgos
1. Endpoint XHR interno, sin contrato: puede cambiar o limitarse. Mitigación: fallo explícito y vuelta al CSV manual.
2. Estados aplazado/suspendido/sin hora/playoff sin observar: tratar todo lo que no sea «Finalizado» con goles enteros como pendiente y avisar.
3. Discrepancias de fecha entre feed y web (arriba): no se aplican solas.
4. Jurídico: igual que ADR-011 (uso personal, base de datos; puerta NFR-010 intacta). Ritmo ≥ 10 s, 1 petición por jornada.
