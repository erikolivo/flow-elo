"""
poisson_model.py
-----------------
Calcula la expectativa PRE-PARTIDO (goles esperados, probabilidad 1X2)
usando Poisson, a partir de un "rating" de cada equipo -- que ya NO es
solo Elo de ClubElo: es el rating COMBINADO que entrega
ratings_store.rating_combinado() (blend de ClubElo semilla + Glicko-2
propio, según cuántos partidos propios se hayan observado). Este módulo
no necesita saber de dónde vino el rating -- solo que está en la misma
escala (~1500-2100), que es como se diseñó el blend a propósito.

IMPORTANTE -- separación de responsabilidades tras el rediseño de
alertas en vivo: este módulo YA NO se usa para decidir alertas durante
el partido (eso ahora vive en momentum.py, calculado solo con eventos
reales del partido). Aquí solo vive la expectativa PRE-PARTIDO: sirve
para (a) decidir qué partidos entran a la selección diaria, y (b)
mostrarse como contexto informativo en los mensajes de alerta ("según
la expectativa inicial, X% era favorito"), nunca como parte del cálculo
de momentum en vivo.

Dos fuentes se combinan para estimar los goles esperados de cada equipo:
  1. El rating combinado (ClubElo + Glicko propio), siempre disponible
     una vez que el equipo tiene aunque sea un rating de arranque.
  2. El Goal Index del equipo, si lo tenemos (ataque/defensa reciente).
"""

import math
from functools import lru_cache

VENTAJA_LOCAL_ELO = 70
PROMEDIO_GOLES_LIGA = 1.35
PESO_ELO_EN_GOLES = 1 / 200  # cada 200 pts de rating de diferencia ~ 1 gol de ventaja

PROB_MINIMA_FAVORITO = 0.60


def cumple_filtro_cuota(evaluacion):
    """True si el favorito cumple el filtro de probabilidad inicial >= 60%."""
    return evaluacion["probabilidad"] >= PROB_MINIMA_FAVORITO


def goles_esperados(rating_local, rating_visitante, goal_index_local=None, goal_index_visitante=None,
                     ventaja_local=VENTAJA_LOCAL_ELO, promedio_liga=PROMEDIO_GOLES_LIGA):
    """
    Devuelve (lambda_local, lambda_visitante): los goles esperados de
    cada equipo en ESTE partido, combinando rating (siempre) y Goal
    Index (si está disponible).
    """
    diff_rating = (rating_local + ventaja_local) - rating_visitante
    ajuste_rating = diff_rating * PESO_ELO_EN_GOLES

    gi_local = goal_index_local or 0
    gi_visitante = goal_index_visitante or 0

    lambda_local = max(0.15, promedio_liga + ajuste_rating / 2 + gi_local / 2 - gi_visitante / 4)
    lambda_visitante = max(0.15, promedio_liga - ajuste_rating / 2 + gi_visitante / 2 - gi_local / 4)
    return lambda_local, lambda_visitante


@lru_cache(maxsize=None)
def _poisson_pmf(k, lam):
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def matriz_marcadores(lambda_local, lambda_visitante, max_goles=6):
    matriz = {}
    for gl in range(max_goles + 1):
        for gv in range(max_goles + 1):
            matriz[(gl, gv)] = _poisson_pmf(gl, round(lambda_local, 4)) * _poisson_pmf(gv, round(lambda_visitante, 4))
    return matriz


def probabilidades_1x2(matriz):
    p_local = sum(p for (gl, gv), p in matriz.items() if gl > gv)
    p_empate = sum(p for (gl, gv), p in matriz.items() if gl == gv)
    p_visitante = sum(p for (gl, gv), p in matriz.items() if gl < gv)
    return p_local, p_empate, p_visitante


def evaluar_favorito(rating_local, rating_visitante, goal_index_local=None, goal_index_visitante=None):
    """
    Devuelve quién es favorito, su probabilidad real (con empate
    incluido) y la "cuota equivalente" (proxy, no cuota real de mercado),
    más los goles esperados.
    """
    lam_local, lam_visitante = goles_esperados(rating_local, rating_visitante, goal_index_local, goal_index_visitante)
    matriz = matriz_marcadores(lam_local, lam_visitante)
    p_local, p_empate, p_visitante = probabilidades_1x2(matriz)

    if p_local >= p_visitante:
        lado, prob = "local", p_local
    else:
        lado, prob = "visitante", p_visitante

    return {
        "lado": lado,
        "probabilidad": prob,
        "cuota_inicial": round(1 / prob, 2) if prob > 0 else None,
        "lambda_local": round(lam_local, 3),
        "lambda_visitante": round(lam_visitante, 3),
    }


def probabilidad_favorito_en_vivo(lambda_local, lambda_visitante, goles_local_actual, goles_visitante_actual,
                                   minuto_actual, favorito_es_local, minutos_partido=90):
    """
    Recalcula con Poisson la probabilidad de que el favorito termine
    ganando el partido dado el marcador y minuto actuales, escalando los
    goles esperados originales por los minutos restantes. Se conserva
    como dato de CONTEXTO adicional (ej. para el reporte de acierto), no
    como disparador de alertas -- eso ahora es responsabilidad exclusiva
    de momentum.py.
    """
    minutos_restantes = max(0, minutos_partido - minuto_actual)
    fraccion = minutos_restantes / minutos_partido

    lam_local_restante = lambda_local * fraccion
    lam_visitante_restante = lambda_visitante * fraccion

    matriz_restante = matriz_marcadores(lam_local_restante, lam_visitante_restante, max_goles=6)

    prob_favorito_gana = 0.0
    for (gl_restante, gv_restante), p in matriz_restante.items():
        gl_final = goles_local_actual + gl_restante
        gv_final = goles_visitante_actual + gv_restante
        if favorito_es_local:
            gana_favorito = gl_final > gv_final
        else:
            gana_favorito = gv_final > gl_final
        if gana_favorito:
            prob_favorito_gana += p

    return prob_favorito_gana
