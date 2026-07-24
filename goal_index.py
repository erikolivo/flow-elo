"""
goal_index.py
-------------
Junta el Goal Index de las 38 ligas de football-data.co.uk + el cache
semanal de ligas extra (API-Football). Mezcla 60% forma reciente (6
partidos) + 40% temporada completa.

NUEVO: ademas del Goal Index, devuelve un mapa equipo->pais inferido del
codigo de liga de origen (ej. "E0" -> "England"). Esto permite usar el
Goal Index como fuente GRATUITA de pais (sin gastar peticiones de
API-Football) para equipos que aparecen en estas 38+16 ligas conocidas
-- ver CODIGO_LIGA_A_PAIS en fetch_data.py y su uso en
seleccionar_partidos.py.
"""

import json
from pathlib import Path

from fetch_data import (
    obtener_resultados_liga,
    obtener_resultados_liga_extra,
    calcular_goal_index,
    LIGAS_FOOTBALL_DATA,
    LIGAS_FOOTBALL_DATA_EXTRA,
    CODIGO_LIGA_A_PAIS,
)

DATA_DIR = Path(__file__).parent / "data"
CACHE_GOAL_INDEX_EXTRA = DATA_DIR / "goal_index_extra.json"

PESO_FORMA_RECIENTE = 0.6
PESO_TEMPORADA_COMPLETA = 0.4
PARTIDOS_FORMA_RECIENTE = 6


def _mezclar(temporada, reciente):
    mezcla = {}
    todos_los_equipos = set(temporada) | set(reciente)
    for equipo in todos_los_equipos:
        t = temporada.get(equipo)
        r = reciente.get(equipo)
        if t and r:
            gi = PESO_FORMA_RECIENTE * r["goal_index"] + PESO_TEMPORADA_COMPLETA * t["goal_index"]
            mezcla[equipo] = {"goal_index": round(gi, 2), "goal_index_temporada": t["goal_index"],
                               "goal_index_forma_reciente": r["goal_index"], "partidos_forma_reciente": r["partidos_jugados"]}
        elif t:
            mezcla[equipo] = {"goal_index": t["goal_index"], "goal_index_temporada": t["goal_index"],
                               "goal_index_forma_reciente": None, "partidos_forma_reciente": 0}
        elif r:
            mezcla[equipo] = {"goal_index": r["goal_index"], "goal_index_temporada": None,
                               "goal_index_forma_reciente": r["goal_index"], "partidos_forma_reciente": r["partidos_jugados"]}
    return mezcla


def construir_goal_index_global():
    """Devuelve (goal_index, equipo_pais)."""
    goal_index_temporada = {}
    goal_index_reciente = {}
    equipo_pais = {}

    def _registrar_paises(resultados, pais):
        if not pais:
            return
        for fila in resultados:
            home, away = fila.get("HomeTeam"), fila.get("AwayTeam")
            if home:
                equipo_pais.setdefault(home, pais)
            if away:
                equipo_pais.setdefault(away, pais)

    for codigo in LIGAS_FOOTBALL_DATA:
        try:
            resultados = obtener_resultados_liga(codigo)
            goal_index_temporada.update(calcular_goal_index(resultados))
            goal_index_reciente.update(calcular_goal_index(resultados, ultimos_n=PARTIDOS_FORMA_RECIENTE))
            _registrar_paises(resultados, CODIGO_LIGA_A_PAIS.get(codigo))
        except Exception as e:
            print(f"[AVISO] No se pudo procesar la liga {codigo}: {e}")

    for codigo in LIGAS_FOOTBALL_DATA_EXTRA:
        try:
            resultados = obtener_resultados_liga_extra(codigo)
            goal_index_temporada.update(calcular_goal_index(resultados))
            goal_index_reciente.update(calcular_goal_index(resultados, ultimos_n=PARTIDOS_FORMA_RECIENTE))
            _registrar_paises(resultados, CODIGO_LIGA_A_PAIS.get(codigo))
        except Exception as e:
            print(f"[AVISO] No se pudo procesar la liga extra {codigo}: {e}")

    goal_index = _mezclar(goal_index_temporada, goal_index_reciente)

    if CACHE_GOAL_INDEX_EXTRA.exists():
        try:
            extra = json.loads(CACHE_GOAL_INDEX_EXTRA.read_text(encoding="utf-8"))
            for equipo, datos in extra.items():
                goal_index.setdefault(equipo, {
                    "goal_index": datos["goal_index"], "goal_index_temporada": datos["goal_index"],
                    "goal_index_forma_reciente": None, "partidos_forma_reciente": 0,
                })
        except Exception as e:
            print(f"[AVISO] No se pudo leer el cache de goal_index_extra.json: {e}")

    return goal_index, equipo_pais
