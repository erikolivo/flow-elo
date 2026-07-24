# Alertas de gol en vivo — v2 (rating propio Glicko-2 + momentum real)

Sistema automático que detecta partidos con un favorito claro, los
vigila en vivo, y avisa por Telegram con distintos tipos de alerta
según qué tan probable es que se anote un gol pronto. Corre solo,
gratis, en GitHub Actions.

## Qué cambió en esta versión (rediseño completo)

Esta versión corrige un problema real detectado en producción: las
alertas a veces atribuían peligro al favorito cuando en realidad era el
rival quien dominaba el juego. La causa era de diseño (la fórmula de
alerta mezclaba la expectativa PRE-PARTIDO con la actividad EN VIVO), no
un bug puntual — por eso el rediseño toca dos partes centrales del
sistema: cómo se calcula el rating de cada equipo, y cómo se decide cada
alerta en vivo.

### 1. Rating propio (Glicko-2) con seguimiento continuo

- **ClubElo sigue siendo la semilla de arranque**, pero ya no es la
  única fuente para siempre. El sistema lleva su **propio rating
  Glicko-2** por equipo, que se alimenta con cada resultado real que
  observa (`cerrar_resultados.py`, tras cada partido vigilado).
- **Glicko-2 en vez de Elo puro** porque modela la incertidumbre (RD) y
  la volatilidad de cada equipo explícitamente — un equipo con pocos
  datos automáticamente pesa menos en la comparación, sin reglas
  manuales ni banderas aparte.
- **Blend progresivo, no un interruptor todo/nada**: el peso del rating
  propio crece con los partidos observados (0% / 20% / 50% / 75% / 100%
  según 0, 1-3, 4-8, 9-15, >15 partidos). Se prioriza cobertura rápida:
  un equipo con 1 solo partido observado YA influye en la decisión.
- **Bootstrap histórico** (`bootstrap_ligas.py`, uso manual): al agregar
  una liga nueva, se reproducen sus últimas ~2 temporadas en orden
  cronológico a través de Glicko-2 antes de empezar a vigilarla en vivo,
  para no arrancar todos los equipos "en blanco".

### 2. Emparejamiento de equipos por país PROPIO + verificación cruzada

- Antes se filtraba el Elo por el país de la LIGA del fixture — se
  rompía en torneos internacionales (Copa Libertadores, Champions
  League...) donde la liga no representa el país real de cada equipo.
- Ahora **cada equipo resuelve su propio país** (`team_resolver.py`),
  cacheado para siempre (1 sola petición en la vida del equipo).
- **Verificación cruzada (opción B)**: si un equipo no se empareja con
  confianza en su propio país, se usa el país/confederación YA resuelto
  del RIVAL para restringir la búsqueda, en vez de una búsqueda global
  sin ningún filtro.

### 3. Momentum en vivo, separado por completo de la expectativa pre-partido

- **`momentum.py`** calcula presión y probabilidad de gol usando
  EXCLUSIVAMENTE eventos ocurridos dentro del partido (tiros, córners,
  xG si el plan lo expone) — la expectativa pre-partido ya nunca entra
  en ese cálculo, solo se muestra como contexto informativo en el
  mensaje.
- **Zona de paridad**: cuando ambos equipos generan peligro real y
  ninguno domina claramente (momentum 35%-65%), se manda un mensaje
  honesto de "partido abierto" en vez de forzar un ganador.
- **Tarjeta roja y penal** como escenarios nuevos (eventos discretos de
  alto impacto que antes se ignoraban).
- **Ventana 15'-75'** para los escenarios de momentum general (antes
  algunos no tenían ningún límite). "Gol de cierre" tiene su propia
  ventana extendida a 75'-90'+ (incluye descuento).
- **Techo de diferencia**: con 3+ goles de diferencia, se manda un único
  aviso de "seguimiento cerrado" en vez de seguir alertando sobre un
  partido ya prácticamente resuelto.

### 4. Ciclo de retroalimentación real

- Cada alerta individual queda **auditada** tras el partido
  (`cerrar_resultados.py`): ¿hubo de verdad un gol del lado que la
  alerta predijo dentro de los siguientes 15 minutos? Antes solo se
  medía el acierto del favorito al final del partido completo.
- El reporte de las 6am ahora incluye **acierto por tipo de alerta** y
  **acierto por madurez del rating propio** — la base real para poder
  ajustar umbrales con evidencia, no a ojo.

## Las 5 fases

| Fase | Cuándo | Qué hace |
|---|---|---|
| 1. Selección | Desde las 04:00, reintenta cada 5 min | Resuelve país por equipo, calcula rating combinado (ClubElo+Glicko), filtra favoritos ≥60% |
| 2. Resumen | 07:00 | Manda a Telegram la lista de partidos de hoy |
| 3. Vigilancia | Cada 5-15 min (adaptativo) | Calcula momentum real y manda la alerta que aplique |
| 4. Cierre | 23:30 | Resuelve resultados, actualiza Glicko-2, audita cada alerta, archiva el día |
| Reporte diario | 06:00 (día siguiente) | Resultados + acierto por tipo de alerta + acierto por madurez + cupo usado |

## Los tipos de alerta

| Situación | Alerta |
|---|---|
| Favorito perdiendo por 1, momentum a favor del favorito | 🟠 Posible empate |
| Empatando, momentum a favor del favorito | 🟢 Posible victoria del favorito |
| 0-0, antes del min 30, favorito con el momentum | ⏱️ Gana favorito 1er tiempo |
| Empatando o perdiendo, momentum a favor del rival | 🔴 Posible gol del no favorito / ⚠️ Cuidado rival presiona |
| Favorito ganando, momentum sigue a su favor | 🔵 Posible ampliación de marcador |
| Momentum parejo (35%-65%), peligro real de cualquier lado | ⚡ Partido abierto |
| Tarjeta roja detectada | 🟥 Tarjeta roja |
| Penal detectado | 🎯 Penal |
| Min 75-90+, empatado o -1, dominancia acumulada ≥75% | ⏰ Posible gol de cierre |
| Diferencia ≥3 goles | 🏁 Seguimiento cerrado (una sola vez) |

**Sin límite de alertas por partido** (salvo el techo de diferencia) —
narración progresiva a lo largo de los 90+ minutos.

## Decisiones importantes de esta versión

- **El rating final que alimenta el modelo de Poisson es un BLEND**
  (ClubElo semilla + Glicko-2 propio), nunca 100% ClubElo puro salvo la
  primera vez que se ve a un equipo.
- **El "momentum" ya NO usa nada de la expectativa pre-partido.** Es la
  corrección central de esta versión — ver `momentum.py`.
- **Costo de cupo mayor**: se agregó 1 petición extra por partido por
  revisión (eventos, para tarjetas/penales), sobre la ya existente de
  estadísticas. Aceptado a propósito por el valor de la señal — ver
  comentario en `fetch_data.py`. La frecuencia adaptativa sigue siendo
  la herramienta principal para mantenerse dentro de 100/día.
- **"Cuota inicial" sigue siendo un proxy** (Elo+GoalIndex+Glicko vía
  Poisson), no una cuota real de casa de apuestas — no existe una fuente
  gratuita y legal de cuotas reales desde tu ubicación (ver
  `filosofia_proyecto.md`).
- **Nunca se duplican cuentas de API-Football** para esquivar el límite
  — prohibido explícitamente por sus términos de servicio.

## Cómo agregar una liga nueva (bootstrap)

```bash
# Ligas principales (football-data.co.uk), ej. Bundesliga y Serie A
python bootstrap_ligas.py D1 I1

# Ligas "extra" (football-data.co.uk/new/, ej. Argentina y Brasil)
python bootstrap_ligas.py --extra ARG BRA
```

Esto reproduce las últimas ~2 temporadas de esa liga a través de
Glicko-2, dejando a cada equipo con un RD mucho más bajo desde el primer
día que el sistema empieza a vigilarlo en vivo — evita esperar 15-20
partidos reales (varios meses) para que el rating propio sea confiable.

## Cómo ponerlo en línea

### 1. Crear el repositorio
1. `https://github.com/new` → nombre sugerido `alertas-apuestas` → **Public** → Create

### 2. Subir los archivos
```bash
cd alertas-apuestas
git init
git add .
git commit -m "v2: rating propio Glicko-2 + momentum real"
git branch -M main
git remote add origin https://github.com/TU-USUARIO/alertas-apuestas.git
git push -u origin main
```

### 3. Permisos de escritura
`.../settings/actions` → "Workflow permissions" → **Read and write permissions** → Save

### 4. Credenciales (Secrets)
`.../settings/secrets/actions` → crea 3:

| Name | Valor |
|---|---|
| `API_FOOTBALL_KEY` | tu API key de https://dashboard.api-football.com |
| `TELEGRAM_BOT_TOKEN` | el token de tu bot (de @BotFather) |
| `TELEGRAM_CHAT_ID` | tu chat id |

### 5. Configurar los workflows de GitHub Actions

Este ZIP no incluye los archivos `.github/workflows/*.yml` (no estaban
en el proyecto original que se compartió) — hay que recrearlos igual que
en la versión anterior: 4 disparos diarios para Fase 3 (~5.5h cada uno,
con `sleep` interno cada 5 min), y ventanas de reintento de 15 min para
Fase 2, Fase 4, y el Reporte diario. Si no los tienes a mano, dímelo y
te los preparo también.

### 6. Probar manualmente
`.../actions` → "Fase 1 - Selección de partidos" → Run workflow →
revisa que salga ✅ → luego "Fase 2 - Resumen diario".

## Estructura de archivos

```
glicko2.py               -> algoritmo Glicko-2 (validado contra el ejemplo oficial del paper)
ratings_store.py         -> rating propio por equipo + blend con ClubElo + fusión bootstrap→id real
team_resolver.py         -> país por equipo (cacheado) + verificación cruzada por confederación
momentum.py               -> presión/momentum/probabilidad de gol SOLO con datos en vivo
fetch_data.py             -> ClubElo, football-data.co.uk, API-Football (stats, eventos, info equipo)
poisson_model.py          -> rating combinado -> probabilidad pre-partido (Poisson)
goal_index.py              -> Goal Index mezclado (forma reciente 60% + temporada 40%)
cuota_api_football.py      -> contador de uso diario de API-Football
bootstrap_ligas.py        -> carga histórica manual del rating propio para ligas nuevas
seleccionar_partidos.py  -> Fase 1
resumen.py                 -> Fase 2
monitor.py                 -> Fase 3 (motor de alertas con momentum real)
cerrar_resultados.py      -> Fase 4 (actualiza Glicko-2 + audita cada alerta + Excel)
reporte_diario.py          -> Reporte de las 6am (+ desglose por tipo de alerta y madurez)
telegram_utils.py          -> envío de mensajes
estado_diario.py            -> control de "ya se hizo hoy" para las fases de disparo único
data/partidos_hoy.json     -> selección + memoria del partido (snapshots con marcador)
data/ratings_propios.json  -> rating Glicko-2 de cada equipo conocido
data/team_country_cache.json -> país de cada equipo (cacheado para siempre)
data/historial_dias/       -> un archivo JSON por día
data/estadisticas.xlsx     -> 3 pestañas: resultados, resumen por día, acierto por tipo de alerta
```

## Limitaciones que debes saber

- **Cupo de API-Football (100/día, una sola cuenta):** el costo por
  partido vigilado subió (eventos + estadísticas = 2 peticiones por
  revisión). En un día con muchos partidos simultáneos podrías acercarte
  más rápido al límite — el reporte de las 6am te avisa cuánto quedó.
- **El feed de eventos y de xG dependen del plan/liga**: si tu plan de
  API-Football no expone `expected_goals` o los eventos de penal para
  cierta liga, el sistema no falla — simplemente usa la aproximación por
  tiros a puerta (xG) o no genera la alerta de penal para ese partido
  (eventos). Revisado en `extraer_xg()` y `_extraer_eventos_nuevos()`.
- **El rating propio tarda semanas, no meses, en ganar peso real** — por
  diseño explícito, prioriza cobertura sobre pureza estadística. Usa el
  desglose por madurez del reporte de las 6am para decidir si los
  umbrales de blend (20/50/75/100%) necesitan ajuste con el tiempo.
- **BeSoccer y Ecuabet:** links de búsqueda de Google, no URLs exactas.
- **ClubElo** tiene reintentos y caché de respaldo por si el sitio se
  sobrecarga.
