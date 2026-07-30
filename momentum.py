"""
momentum.py
-----------
Calculo de momentum en vivo, separado por completo de la expectativa
pre-partido (ver monitor.py). Version revisada con criterio de analista
de casa de apuestas -- cambios de esta version:

1. TIROS POR UBICACION (insidebox/outsidebox), no solo "tiros totales".
   Un tiro dentro del area convierte mucho mas que uno de fuera -- ya
   veniamos pagando por este dato en el mismo endpoint de estadisticas,
   simplemente no se estaba leyendo.

2. SUAVIZADO (tipo Laplace) en el ratio de momentum. Sin esto, "1 tiro
   del favorito, 0 del rival" da un momentum de 1.0 (dominio total) con
   una muestra minuscula -- una mesa de apuestas real nunca deja que un
   ratio con tan poca muestra hable con esa confianza. Con suavizado,
   los casos de muestra chica se acercan a la zona de paridad en vez de
   dar lecturas extremas prematuras.

3. SE QUITA EL BONUS DE POSESION. Para el caso de uso de este proyecto
   (vigilar SIEMPRE al favorito claro), un favorito que va ganando y se
   repliega suele tener MENOS posesion mientras es MAS peligroso a la
   contra -- y el rival que empuja buscando el empate suele tener MAS
   posesion sin generar mas peligro real. Sumarle presion a quien tiene
   mas posesion podia estar empujando el momentum en la direccion
   equivocada justo en los partidos que mas importan aqui. La posesion
   se sigue mostrando en el mensaje como dato de contexto, pero ya no
   pesa en el calculo.

4. FACTOR DE URGENCIA POR MINUTO. Los goles se concentran
   estadisticamente en los ultimos 15-20 minutos de cada tiempo (mas
   cansancio, mas riesgo asumido). El mismo volumen de tiros vale un
   poco mas en el minuto 82 que en el minuto 20 -- se aplica un
   multiplicador leve a la probabilidad de gol de la ventana segun el
   minuto del partido.

5. SUSTITUCIONES COMO SEÑAL BLANDA (no "sustitucion ofensiva"). El plan
   gratuito de API-Football no expone la posicion del jugador que entra,
   asi que NO se puede confirmar con certeza que un cambio sea ofensivo
   sin pagar una fuente de datos adicional. Por honestidad, esto se
   trata como lo que realmente es: "hubo un cambio reciente, posible
   ajuste tactico" -- una senal debil que suma un poco de presion, no
   una alerta de alta confianza como tarjeta roja o penal.

Punto 6 (pesos exactos y umbrales) queda SIN TOCAR a proposito -- se
calibraran con evidencia real una vez que haya suficientes alertas
auditadas (ver cerrar_resultados.py).
"""

import math

# --- Pesos de presion (punto 6: sin tocar los valores relativos, solo
#     se agregan las nuevas categorias insidebox/outsidebox) ---
PESO_TIRO_PUERTA = 3
PESO_TIRO_AREA = 2       # tiro dentro del area (mayor probabilidad de gol que uno de fuera)
PESO_TIRO_FUERA_AREA = 0.5
PESO_CORNER = 1

# --- Suavizado del ratio de momentum (nuevo) ---
ALPHA_SUAVIZADO = 1.0

# --- Conversion a probabilidad de gol de la ventana ---
TASA_CONVERSION_TIRO_PUERTA = 0.11
TASA_CONVERSION_TIRO_AREA = 0.03
TASA_CONVERSION_CORNER = 0.02

# --- Factor de urgencia por minuto (nuevo) ---
FACTOR_URGENCIA_TRAMO_FINAL = 1.2
MINUTO_INICIO_URGENCIA_1ER_TIEMPO = 30
MINUTO_FIN_URGENCIA_1ER_TIEMPO = 45
MINUTO_INICIO_URGENCIA_2DO_TIEMPO = 75

# --- Sustituciones como senal blanda (nuevo) ---
BONUS_POR_CAMBIO_RECIENTE = 0.5
TOPE_BONUS_CAMBIOS = 2.0

VENTANA_MINUTOS_DEFECTO = 15
ZONA_PARIDAD_BAJA = 0.35
ZONA_PARIDAD_ALTA = 0.65


def _delta_stat(actual, anterior, campo):
    if anterior is None:
        return max(0.0, float(actual.get(campo, 0)))
    return max(0.0, float(actual.get(campo, 0)) - float(anterior.get(campo, 0)))


def _minutos_transcurridos(snap_actual, snap_anterior):
    if snap_anterior is None:
        return VENTANA_MINUTOS_DEFECTO
    diff = snap_actual["minuto"] - snap_anterior["minuto"]
    return max(1, diff)


def _factor_urgencia(minuto_actual):
    """Multiplicador leve para los tramos donde estadisticamente caen
    mas goles (ultimos 15-20 min de cada tiempo)."""
    if minuto_actual is None:
        return 1.0
    if MINUTO_INICIO_URGENCIA_1ER_TIEMPO <= minuto_actual <= MINUTO_FIN_URGENCIA_1ER_TIEMPO:
        return FACTOR_URGENCIA_TRAMO_FINAL
    if minuto_actual >= MINUTO_INICIO_URGENCIA_2DO_TIEMPO:
        return FACTOR_URGENCIA_TRAMO_FINAL
    return 1.0


def calcular_presion(snap_actual, snap_anterior, lado, xg_disponible=False):
    """
    Score de presion SOLO con datos reales del partido, para un lado
    ('local' o 'visitante'), usando la ventana REAL entre snapshots.
    Diferencia tiros por ubicacion (dentro/fuera del area) en vez de
    tratarlos todos igual. La posesion se calcula y se devuelve solo
    como dato informativo -- ya no pesa en el score.
    """
    sufijo = "local" if lado == "local" else "visitante"

    tp = _delta_stat(snap_actual, snap_anterior, f"tiros_puerta_{sufijo}")
    t_area = _delta_stat(snap_actual, snap_anterior, f"tiros_area_{sufijo}")
    t_fuera = _delta_stat(snap_actual, snap_anterior, f"tiros_fuera_area_{sufijo}")
    corners = _delta_stat(snap_actual, snap_anterior, f"corners_{sufijo}")
    posesion = float(snap_actual.get(f"posesion_{sufijo}", 0))

    score = (tp * PESO_TIRO_PUERTA) + (t_area * PESO_TIRO_AREA) + \
            (t_fuera * PESO_TIRO_FUERA_AREA) + (corners * PESO_CORNER)

    if xg_disponible:
        dxg = _delta_stat(snap_actual, snap_anterior, f"xg_{sufijo}")
        score += dxg * 10

    detalle = {
        "tiros_puerta": tp, "tiros_area": t_area, "tiros_fuera_area": t_fuera,
        "corners": corners, "posesion": posesion,
    }
    return score, detalle


def bonus_sustituciones(n_cambios_recientes):
    """
    Senal BLANDA a partir de sustituciones recientes (ultimos ~10 min
    reales). No se puede confirmar si un cambio es "ofensivo" sin datos
    de posicion del jugador (no disponibles en el plan gratuito) -- por
    eso este bonus es deliberadamente pequeno y con tope bajo: aporta,
    no decide.
    """
    return min(TOPE_BONUS_CAMBIOS, n_cambios_recientes * BONUS_POR_CAMBIO_RECIENTE)


def momentum_relativo(presion_a, presion_b, alpha=ALPHA_SUAVIZADO):
    """
    Fraccion (0-1) del momentum que le corresponde a 'a', con suavizado
    tipo Laplace: evita que una muestra minuscula (ej. 1 tiro contra 0)
    de una lectura de dominio total (1.0). Sin presion de ningun lado,
    devuelve exactamente 0.5 (neutro), igual que antes.
    """
    return (presion_a + alpha) / (presion_a + presion_b + 2 * alpha)


def probabilidad_gol_ventana(snap_actual, snap_anterior, lado, minuto_actual, xg_disponible=False):
    """
    Probabilidad de que ESE lado anote pronto, calculada SOLO con datos
    reales de la ventana (Poisson). Incluye tiros dentro del area como
    señal adicional de calidad de ocasion, y un factor de urgencia segun
    el minuto del partido (los goles se concentran en los tramos finales
    de cada tiempo).
    """
    sufijo = "local" if lado == "local" else "visitante"
    tp = _delta_stat(snap_actual, snap_anterior, f"tiros_puerta_{sufijo}")
    t_area = _delta_stat(snap_actual, snap_anterior, f"tiros_area_{sufijo}")
    corners = _delta_stat(snap_actual, snap_anterior, f"corners_{sufijo}")

    if xg_disponible:
        dxg = _delta_stat(snap_actual, snap_anterior, f"xg_{sufijo}")
        lam = dxg
    else:
        lam = (tp * TASA_CONVERSION_TIRO_PUERTA) + (t_area * TASA_CONVERSION_TIRO_AREA) + \
              (corners * TASA_CONVERSION_CORNER)

    lam *= _factor_urgencia(minuto_actual)
    return 1 - math.exp(-lam)


def zona_momentum(momentum_favorito):
    if momentum_favorito >= ZONA_PARIDAD_ALTA:
        return "favorito"
    if momentum_favorito <= ZONA_PARIDAD_BAJA:
        return "rival"
    return "paridad"
