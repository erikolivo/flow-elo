"""
momentum.py
-----------
REDISEÑO COMPLETO de cómo se mide "quién está generando peligro ahora".

Por qué existe este módulo (el porqué queda documentado, no solo el
qué): la versión anterior (poisson_model.probabilidad_gol_inminente)
sumaba la expectativa PRE-PARTIDO (Elo+Goal Index, fija desde antes de
que arrancara el juego) con la actividad reciente. Para los favoritos
claros (los únicos que este sistema vigila, todos con probabilidad
inicial >=60%), la parte pre-partido pesaba tanto que un solo tiro a
puerta del favorito alcanzaba para disparar una alerta de "el favorito
va a meter gol" -- incluso en revisiones donde el RIVAL era quien
realmente dominaba el juego. Esto se comprobó con casos reales.

La corrección de fondo: el momentum EN VIVO se calcula usando SOLO
eventos ocurridos dentro del partido. La expectativa pre-partido se
sigue mostrando en el mensaje como contexto informativo (para que el
usuario decida con su propio criterio, principio ya establecido en la
filosofía del proyecto), pero nunca vuelve a pesar en el cálculo que
decide si se dispara una alerta.
"""

TASA_CONVERSION_TIRO_PUERTA = 0.11
TASA_CONVERSION_CORNER = 0.02

PESO_TIRO_PUERTA = 3
PESO_TIRO_TOTAL = 1
PESO_CORNER = 1
BONUS_POSESION_DOMINANTE = 1
UMBRAL_POSESION_DOMINANTE = 55

VENTANA_MINUTOS_DEFECTO = 15  # solo si no hay minuto previo real (primera revisión)

ZONA_PARIDAD_BAJA = 0.35
ZONA_PARIDAD_ALTA = 0.65


def _delta_stat(actual, anterior, campo):
    """Diferencia real desde el snapshot anterior. Si no hay snapshot
    anterior (primera revisión del partido), usa el valor acumulado tal
    cual -- puede sobreestimar un poco la primera vez, pero ya no se
    mezcla con la expectativa pre-partido, así que el impacto es menor
    y queda contenido a una sola revisión."""
    if anterior is None:
        return max(0.0, float(actual.get(campo, 0)))
    return max(0.0, float(actual.get(campo, 0)) - float(anterior.get(campo, 0)))


def _minutos_transcurridos(snap_actual, snap_anterior):
    if snap_anterior is None:
        return VENTANA_MINUTOS_DEFECTO
    diff = snap_actual["minuto"] - snap_anterior["minuto"]
    return max(1, diff)  # nunca 0, para no dividir por cero


def calcular_presion(snap_actual, snap_anterior, lado, xg_disponible=False):
    """
    Calcula un score de presión SOLO con datos reales del partido, para
    un lado ('local' o 'visitante'), usando la ventana REAL entre
    snapshots (no una ventana fija de 15 min asumida). Si el feed trae
    xG (Expected Goals) para ese equipo, se usa directo en vez de
    aproximarlo con tiros a puerta -- es una señal más precisa porque ya
    pondera calidad de la ocasión, no solo cantidad.
    """
    sufijo = "local" if lado == "local" else "visitante"

    tp = _delta_stat(snap_actual, snap_anterior, f"tiros_puerta_{sufijo}")
    tt = _delta_stat(snap_actual, snap_anterior, f"tiros_{sufijo}")
    corners = _delta_stat(snap_actual, snap_anterior, f"corners_{sufijo}")
    posesion = float(snap_actual.get(f"posesion_{sufijo}", 0))

    score = (tp * PESO_TIRO_PUERTA) + (tt * PESO_TIRO_TOTAL) + (corners * PESO_CORNER)
    if posesion >= UMBRAL_POSESION_DOMINANTE:
        score += BONUS_POSESION_DOMINANTE

    if xg_disponible:
        dxg = _delta_stat(snap_actual, snap_anterior, f"xg_{sufijo}")
        # el xG ya es una probabilidad acumulada de gol -- se le da peso
        # fuerte porque es la señal más informativa disponible
        score += dxg * 10

    return score, {"tiros_puerta": tp, "tiros_totales": tt, "corners": corners, "posesion": posesion}


def momentum_relativo(presion_a, presion_b):
    """Devuelve qué fracción (0-1) del momentum total le corresponde a
    'a'. 0.5 = exactamente parejo. Si no hay presión de ningún lado
    (nadie generó nada en la ventana), se devuelve 0.5 (neutro) para no
    forzar una lectura direccional sin datos."""
    total = presion_a + presion_b
    if total <= 0:
        return 0.5
    return presion_a / total


def probabilidad_gol_ventana(snap_actual, snap_anterior, lado, minutos_transcurridos, xg_disponible=False):
    """
    Probabilidad de que ESE lado anote pronto, calculada SOLO con la
    tasa real de tiros/córners/xG de la ventana reciente (Poisson) --
    ya NO incluye ningún término de expectativa pre-partido.
    """
    sufijo = "local" if lado == "local" else "visitante"
    tp = _delta_stat(snap_actual, snap_anterior, f"tiros_puerta_{sufijo}")
    corners = _delta_stat(snap_actual, snap_anterior, f"corners_{sufijo}")

    if xg_disponible:
        dxg = _delta_stat(snap_actual, snap_anterior, f"xg_{sufijo}")
        lam = dxg
    else:
        lam = tp * TASA_CONVERSION_TIRO_PUERTA + corners * TASA_CONVERSION_CORNER

    import math
    return 1 - math.exp(-lam)


def zona_momentum(momentum_favorito):
    """Clasifica el momentum en 3 zonas, en vez de forzar un ganador
    binario -- ver decisión explícita: cuando el partido está parejo, la
    alerta correcta es avisar que ambos generan peligro, no atribuírselo
    a un solo lado."""
    if momentum_favorito >= ZONA_PARIDAD_ALTA:
        return "favorito"
    if momentum_favorito <= ZONA_PARIDAD_BAJA:
        return "rival"
    return "paridad"
