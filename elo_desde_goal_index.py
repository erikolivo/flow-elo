"""
elo_desde_goal_index.py
------------------------
Cuando un equipo no tiene Elo en ClubElo pero SI tiene Goal Index, se
estima un Elo base calibrando la relacion Goal_Index -> Elo con los
equipos que SI tienen ambos datos a la vez (regresion lineal simple
sobre datos reales del proyecto, no un numero inventado). Este Elo
estimado es solo una SEMILLA para el blend con el rating propio
(Glicko-2), igual que el Elo de ClubElo real -- nunca lo reemplaza.
"""

import difflib

MUESTRA_MINIMA = 8
ELO_MIN_RAZONABLE = 1150.0
ELO_MAX_RAZONABLE = 2150.0


def _regresion_lineal_simple(pares):
    n = len(pares)
    if n == 0:
        return None
    mean_x = sum(p[0] for p in pares) / n
    mean_y = sum(p[1] for p in pares) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in pares)
    den = sum((x - mean_x) ** 2 for x, _ in pares)
    if den == 0:
        return 0.0, mean_y
    pendiente = num / den
    intercepto = mean_y - pendiente * mean_x
    return pendiente, intercepto


def construir_muestra_calibracion(elo_global, goal_index, corte=0.85):
    nombres_elo = list(elo_global.keys())
    pares = []
    for equipo_gi, datos_gi in goal_index.items():
        match = difflib.get_close_matches(equipo_gi, nombres_elo, n=1, cutoff=corte)
        if match:
            pares.append((datos_gi["goal_index"], elo_global[match[0]]))
    return pares


def calibrar(elo_global, goal_index):
    """Devuelve (pendiente, intercepto, n_muestra). pendiente=None si la
    muestra es insuficiente para calibrar con confianza."""
    pares = construir_muestra_calibracion(elo_global, goal_index)
    if len(pares) < MUESTRA_MINIMA:
        return None, None, len(pares)
    resultado = _regresion_lineal_simple(pares)
    if resultado is None:
        return None, None, len(pares)
    pendiente, intercepto = resultado
    return pendiente, intercepto, len(pares)


def estimar_elo(goal_index_valor, pendiente, intercepto):
    if pendiente is None:
        return None
    elo = intercepto + pendiente * goal_index_valor
    return round(max(ELO_MIN_RAZONABLE, min(ELO_MAX_RAZONABLE, elo)), 1)
