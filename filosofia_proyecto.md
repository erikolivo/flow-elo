# Filosofía y lógica del proyecto: Alertas de gol en vivo

## 1. Por qué existe este proyecto

Nació con un objetivo distinto al actual: detectar oportunidades de apuesta
en vivo sobre equipos favoritos (cuota inicial ≤1.35), usando cuotas reales
de casas de apuestas y su evolución durante el partido.

Ese diseño original se abandonó por una razón concreta, no por capricho:
**no existe una fuente de cuotas de fútbol en vivo, gratuita, y de acceso
legal desde tu ubicación.** Se investigaron tres caminos y los tres
resultaron cerrados:

- **The Odds API y proveedores similares**: su nivel gratuito no cubre
  fútbol (solo NBA/MLB) o tiene límites de cupo demasiado bajos para uso
  diario continuo.
- **Betfair Exchange**: sí tiene API gratuita técnicamente viable, pero
  Betfair no está disponible legalmente desde tu país.
- **Casas de apuestas legales locales** (Rushbet, Betsson, Codere, etc.):
  ninguna ofrece una API pública — son productos de consumo, no
  proveedores de datos.

Ante esto, el proyecto se adaptó: en vez de cuotas reales, usa **Elo +
Goal Index** como sustituto de "cuánto favorito es un equipo", y
**estadísticas en vivo** (tiros, córners, posesión) como sustituto de "el
mercado está reaccionando". Esta sustitución quedó documentada
explícitamente desde el principio para que nunca se confunda con datos
reales de mercado.

## 2. Qué se quiere del proyecto — y cómo cambió

**Versión original:** pocas apuestas de "mucho valor" (cuota ≤1.35),
alertadas solo cuando el marcador y el contexto sugerían que la cuota real
habría subido mucho.

**Versión actual (la vigente):** el objetivo cambió deliberadamente a
**seguimiento del mayor número de partidos posible**, con alertas que
avisan **cuándo es probable que se anote un gol pronto** — no solo "va a
ganar el favorito", sino un seguimiento más rico: quién está generando
peligro, en qué momento del partido, y con qué intensidad.

La filosofía de fondo en esta versión es: **más cobertura y más matices
de alerta, a cambio de aceptar que el modelo es una aproximación que se
irá afinando con datos reales** — no un sistema que se declara "correcto"
desde el día uno.

## 3. Cuándo debe usarse — el ciclo diario

El sistema no está pensado para que lo revises constantemente; está
diseñado para que **Telegram te avise solo cuando hay algo que valga la
pena ver**:

| Momento | Qué recibes | Por qué a esa hora |
|---|---|---|
| 04:00 en adelante | (nada visible) | Fase 1 arma la lista del día, reintentando cada 5 min hasta lograrlo — antes de que arranque cualquier partido |
| 06:00 | Reporte de resultados de AYER (✅/❌ por partido, % de aciertos, cupo de API usado) | Para que empieces el día sabiendo cómo le fue al sistema, sin tener que revisar nada tú mismo |
| 07:00 | Resumen de los partidos de HOY | Una vez que ya hubo tiempo de armar la lista completa |
| Durante los partidos | Alertas de gol en vivo (7 tipos distintos) | Solo mientras el partido está en su ventana horaria real |
| 23:30 | (nada visible) | Fase 4 cierra resultados, archiva el día, actualiza el Excel |

La idea es que en un día normal recibas: 1 reporte de ayer + 1 resumen de
hoy + N alertas en vivo (puede ser 0, puede ser varias) — no más ruido que
eso.

## 4. Cómo se gestionan las peticiones a las APIs

Este es uno de los ejes centrales del diseño, porque **todo corre sobre
cupos gratuitos con límites duros** (100 peticiones/día de API-Football,
principalmente). La filosofía aplicada, en orden de importancia:

1. **Nunca gastar una petición si se puede evitar.** Ejemplo: antes de
   consultar "¿hay algo en vivo?", primero se revisa localmente (sin
   ninguna petición) si algún partido está siquiera dentro de su ventana
   horaria. Si no, el chequeo termina gratis.
2. **1 petición sirve para TODOS los partidos a la vez, cuando es
   posible.** Ver "todos los partidos en vivo del mundo" cuesta 1 sola
   petición sin importar si vigilas 1 o 50 partidos simultáneos.
3. **Frecuencia adaptativa según la carga real:** se revisa cada 10
   minutos si hay pocos partidos en juego, cada 15 si hay muchos — el
   propio sistema decide esto solo, sin que tengas que tocar nada.
4. **Nunca duplicar cuentas para esquivar el límite.** Se investigó y se
   descartó explícitamente: los términos de servicio de API-Football lo
   prohíben. Se trabaja con una sola cuenta y se acepta esa limitación
   como una restricción real del proyecto, no algo a evadir.
5. **Reintentos con paciencia, no con fuerza bruta.** Fuentes gratuitas
   pequeñas (como ClubElo) a veces se sobrecargan — el sistema reintenta
   varias veces con esperas crecientes, y si aun así falla, usa el último
   dato bueno que tenga guardado en caché antes que fallar por completo.
6. **El propio sistema se audita a sí mismo.** Cada día se cuenta cuántas
   peticiones se usaron y cuántas quedaron, y ese número te llega en el
   reporte de las 6am — para que veas con datos reales, no con
   suposiciones, qué tan cerca está el sistema del límite.

## 5. Filosofía de los mensajes de Telegram

Cada tipo de mensaje tiene un propósito distinto y deliberado:

- **Resumen de las 7am** — panorama del día, para decidir si hay algo que
  valga la pena seguir de cerca hoy.
- **Alertas en vivo (7 tipos)** — cada una nombra una situación
  específica y reconocible ("posible empate", "cuidado rival presiona",
  "gol de cierre"...), no un genérico "algo está pasando". La idea es que
  con solo leer el título ya sepas qué está pasando en el partido sin
  tener que interpretar números.
- **Sin límite de alertas por partido** — se decidió a propósito que un
  mismo partido pueda mandar varias alertas a lo largo de los 90 minutos,
  como una narración progresiva, en vez de una sola alerta "de una vez y
  ya".
- **Cada alerta trae sus propias estadísticas y links** (BeSoccer,
  Ecuabet) — para que puedas verificar y decidir con tu propio criterio,
  no para que confíes ciegamente en el mensaje.
- **Reporte de las 6am** — el mensaje más importante para la salud del
  proyecto a largo plazo: te dice qué tan bien está funcionando el
  sistema (aciertos reales) y cuánto cupo queda, todos los días, sin que
  tengas que pedirlo.

## 6. Principios de diseño que se han aplicado consistentemente

Estos son los criterios que se han usado, una y otra vez, para tomar
decisiones a lo largo de todo el proyecto:

- **Nunca fallar en silencio.** Cuando algo no se puede calcular o
  consultar, el sistema lo dice explícitamente en los logs, en vez de
  simplemente omitirlo sin explicación.
- **Preferir la solución gratuita que ya se tiene, antes que sumar una
  fuente de pago o una técnica riesgosa.** (Ejemplos: Poisson con datos
  que ya se descargaban, en vez de pagar por cuotas reales; una segunda
  cuenta de API se descartó por ir contra los términos de servicio,
  aunque técnicamente funcionara.)
- **Todo cambio de lógica queda documentado con el porqué**, no solo el
  qué — para que dentro de unos meses se pueda entender por qué el
  sistema decide lo que decide, no solo qué decide.
- **Los números de arranque (umbrales, pesos, tasas de conversión) se
  tratan como puntos de partida razonables, no como verdades
  calibradas.** El Excel que se llena cada noche existe precisamente para
  poder ajustarlos con evidencia real más adelante, no a ojo.
- **Cuando aparece un resultado sorprendente (como Vestri o Ceara), se
  investiga con datos reales antes de tocar el código** — nunca se ajusta
  el modelo reaccionando a una sola muestra.

## 7. Addendum v2 — por qué se rediseñó el rating y el motor de alertas

**El problema detectado en producción:** las alertas en vivo a veces
atribuían peligro al favorito cuando en realidad, mirando las
estadísticas del momento, era el rival quien dominaba. La causa raíz no
era un bug puntual sino de diseño: la fórmula de "probabilidad de gol
inminente" sumaba la expectativa PRE-PARTIDO (fija, calculada antes de
que arrancara el juego) con la actividad reciente, y para los favoritos
claros (los únicos que este sistema vigila) esa parte pre-partido pesaba
lo suficiente como para que un solo tiro del favorito disparara una
alerta, sin importar que el rival estuviera generando mucho más peligro
real en la cancha.

**La corrección de fondo:** separar por completo "lo que se esperaba
antes del partido" de "lo que está pasando ahora" (ver `momentum.py`).
La expectativa pre-partido pasó a ser solo un dato de contexto que se
muestra en el mensaje para que el usuario decida con su propio criterio
— nunca vuelve a pesar en el cálculo que decide si se dispara una
alerta.

**Por qué Glicko-2 y no seguir solo con Elo de ClubElo:** el mismo
principio de fondo aplicaba al rating de los equipos, no solo al
momentum en vivo. Elo puro no distingue "un equipo fuerte con historial
sólido" de "un equipo con un rating igual de alto pero calculado con muy
pocos datos" — los trata igual. Glicko-2 modela esa incertidumbre
explícitamente (RD), lo cual resuelve de raíz el caso que motivó el
cambio: un equipo A con mucho historial contra un equipo D recién
llegado no debería aparecer como "A es netamente favorito" solo porque D
no tiene datos — debería aparecer como "no lo sabemos bien todavía", y
eso es exactamente lo que la fórmula de Glicko-2 hace sola, sin reglas
manuales.

**Decisión explícita sobre velocidad vs. pureza:** se priorizó
cobertura rápida (semanas) sobre esperar a que el rating propio madure
del todo (meses) — de ahí el blend progresivo con ClubElo como respaldo
mientras el rating propio gana confianza, en vez de un interruptor
todo/nada. Esto se revisará con evidencia real (ver el desglose de
acierto por madurez en el reporte de las 6am) y podrá ajustarse si los
datos muestran que alertar tan temprano (n=1-3 partidos) no compensa.

## 8. Corrección post-producción — resolución de país agotaba el cupo diario

**Lo que pasó:** en la primera corrida real de Fase 1 con la v2, el
sistema agotó el cupo de 100 peticiones/día de API-Football (error 429)
antes de terminar de seleccionar los partidos del día.

**Causa raíz:** la resolución de país-por-equipo (`team_resolver.py`,
agregada para corregir el emparejamiento en torneos internacionales) se
estaba llamando para CADA equipo de CADA partido del mundo ese día
(cientos de fixtures), en vez de solo para los casos que de verdad la
necesitaban. Esto contradecía el principio ya establecido en la sección
4 de este documento ("nunca gastar una petición si se puede evitar").

**La corrección:** la gran mayoría de partidos son domésticos (misma
liga = mismo país para ambos equipos), donde el método anterior (país de
la liga del fixture) ya es confiable y no cuesta ninguna petición. Ahora
solo se paga la petición de país-por-equipo cuando la liga del fixture
NO es reconocible como doméstica (el caso real que motivó el cambio:
Copa Libertadores, Champions League, etc.). Además se agregó un tope
duro por corrida (25 resoluciones nuevas) y una verificación del cupo
restante antes de cada llamada, como redes de seguridad adicionales.

**Lección de fondo:** una corrección de calidad (mejorar el
emparejamiento) puede introducir sin querer un problema de otro tipo
(cupo) si no se piensa en el costo marginal de cuándo se dispara. Ambas
cosas -- calidad del dato y costo de obtenerlo -- se revisan juntas de
aquí en adelante para cualquier cambio que toque `fetch_data.py`.
