"""
bootstrap_ligas.py
--------------------
Se corre MANUALMENTE (no es parte del ciclo diario automático) cuando
aparece una liga nueva que el sistema todavía no conoce, o cuando se
quiere reforzar el rating propio de una liga existente con más historia.

Qué hace: descarga las últimas 1-2 temporadas de resultados de una liga
(football-data.co.uk para las 38 principales, o standings de
API-Football para las "extra") y los reproduce EN ORDEN CRONOLÓGICO a
través de Glicko-2, actualizando el rating propio de cada equipo
partido a partido -- exactamente como si el sistema los hubiera estado
vigilando en vivo todo ese tiempo.

Por qué esto importa (decisión explícita): sin este paso, un equipo
nuevo arranca en 1500/RD=350 y tarda 15-20 partidos reales (varios
meses) en volverse confiable. Con el bootstrap, arranca con RD mucho más
bajo desde el primer día que el sistema empieza a vigilarlo -- prioriza
cobertura rápida (semanas, no meses) sobre esperar a acumular partidos
uno por uno.

IMPORTANTE: el bootstrap no reemplaza el seguimiento diario -- solo lo
adelanta. Los partidos cargados aquí se cuentan en 'partidos_bootstrap',
separados de 'partidos_reales' (los que el sistema vigiló en vivo), así
el reporte de las 6am puede distinguir cuánta confianza viene de datos
históricos vs. de observación directa.

Uso:
    python bootstrap_ligas.py E0 SP1 I1          # ligas principales
    python bootstrap_ligas.py --extra ARG BRA    # ligas "extra"
"""

import sys
import argparse

import ratings_store
from fetch_data import (
    obtener_resultados_liga_multi_temporada,
    obtener_resultados_liga_extra,
    LIGAS_FOOTBALL_DATA,
    LIGAS_FOOTBALL_DATA_EXTRA,
)

TEMPORADAS_BOOTSTRAP = ["2425", "2526"]  # últimas ~2 temporadas


def _llave_bootstrap(nombre_equipo, liga):
    # Durante el bootstrap todavía no tenemos team_id de API-Football
    # (esa resolución solo pasa cuando el equipo aparece en un fixture
    # real, vía team_resolver.py). Se usa una llave temporal por
    # nombre+liga; cuando el equipo aparezca en un fixture real, el
    # emparejamiento de seleccionar_partidos.py debe intentar fusionar
    # esta entrada con la llave definitiva "id:<team_id>" -- ver nota en
    # seleccionar_partidos.py (_fusionar_llave_bootstrap).
    return f"boot:{liga}|{nombre_equipo}"


def bootstrap_liga_principal(codigo_liga):
    print(f"Bootstrap de {codigo_liga} ({LIGAS_FOOTBALL_DATA.get(codigo_liga, codigo_liga)})...")
    partidos = obtener_resultados_liga_multi_temporada(codigo_liga, TEMPORADAS_BOOTSTRAP)
    _reproducir_partidos(partidos, codigo_liga)


def bootstrap_liga_extra(codigo_liga):
    print(f"Bootstrap de liga extra {codigo_liga} ({LIGAS_FOOTBALL_DATA_EXTRA.get(codigo_liga, codigo_liga)})...")
    partidos = obtener_resultados_liga_extra(codigo_liga)
    _reproducir_partidos(partidos, codigo_liga)


def _reproducir_partidos(partidos, liga):
    procesados = 0
    for p in partidos:
        home, away = p.get("HomeTeam"), p.get("AwayTeam")
        if not home or not away:
            continue
        try:
            gh, ga = int(p["FTHG"]), int(p["FTAG"])
        except (KeyError, ValueError):
            continue

        llave_home = _llave_bootstrap(home, liga)
        llave_away = _llave_bootstrap(away, liga)

        eq_home = ratings_store.obtener_o_crear(llave_home, nombre=home, liga=liga)
        eq_away = ratings_store.obtener_o_crear(llave_away, nombre=away, liga=liga)

        if gh > ga:
            resultado_home, resultado_away = 1.0, 0.0
        elif gh < ga:
            resultado_home, resultado_away = 0.0, 1.0
        else:
            resultado_home, resultado_away = 0.5, 0.5

        # se actualiza con el rating del rival ANTES de este partido
        # (correcto cronológicamente: ambos deben verse como estaban
        # justo antes de jugar, no después)
        rating_home_antes, rd_home_antes = eq_home["rating"], eq_home["rd"]
        rating_away_antes, rd_away_antes = eq_away["rating"], eq_away["rd"]

        ratings_store.actualizar_tras_partido(llave_home, rating_away_antes, rd_away_antes,
                                               resultado_home, es_bootstrap=True, fecha=p.get("Date"))
        ratings_store.actualizar_tras_partido(llave_away, rating_home_antes, rd_home_antes,
                                               resultado_away, es_bootstrap=True, fecha=p.get("Date"))
        procesados += 1

    print(f"  {procesados} partidos reproducidos.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bootstrap del rating propio para una o más ligas.")
    parser.add_argument("codigos", nargs="+", help="Códigos de liga (ej. E0 SP1) o de liga extra con --extra")
    parser.add_argument("--extra", action="store_true", help="Trata los códigos como ligas 'extra' (LIGAS_FOOTBALL_DATA_EXTRA)")
    args = parser.parse_args()

    for codigo in args.codigos:
        if args.extra:
            bootstrap_liga_extra(codigo)
        else:
            bootstrap_liga_principal(codigo)

    print("Bootstrap completo.")
