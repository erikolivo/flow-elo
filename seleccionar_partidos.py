"""
seleccionar_partidos.py
------------------------
FASE 1, version 7.

Cambios de esta version:

1. TOPE DURO DE 50 PETICIONES A API-FOOTBALL POR CORRIDA. 1 se reserva
   para el pedido de fixtures del dia; las 49 restantes son el
   presupuesto maximo para resolucion de pais por equipo. Nunca se pasa
   de 50 en total en Fase 1, sin importar cuantos partidos haya en el
   mundo ese dia.

2. BUG DE PAIS CORREGIDO: el emparejamiento "por pais propio" comparaba
   el pais en ingles (ej. "England") directamente contra las llaves de
   elo_por_pais, que usan codigos de ClubElo de 3 letras (ej. "ENG") --
   nunca coincidian. Ahora la conversion vive en team_resolver.py como
   unica fuente de verdad.

3. GOAL INDEX COMO FUENTE GRATUITA ADICIONAL:
   - Pais gratis: si un equipo aparece en el Goal Index (viene de una
     liga con codigo conocido), su pais se infiere sin gastar ninguna
     peticion -- antes de siquiera considerar la llamada a la API.
   - Elo estimado: si un equipo no tiene ClubElo pero si Goal Index, se
     estima su Elo via elo_desde_goal_index.py (regresion lineal
     calibrada con equipos que tienen ambos datos reales a la vez).

Orden de resolucion de pais (de gratis a costoso):
   a) Liga domestica reconocida -> pais de la liga (gratis)
   b) Goal Index -> pais de la liga de origen del Goal Index (gratis)
   c) Llamada a la API -> team_resolver.resolver_pais_equipo (paga,
      presupuestada)
"""

import json
import datetime
from pathlib import Path

from fetch_data import (
    obtener_ranking_clubelo, obtener_fixtures_por_fecha, buscar_equipo_similar,
    obtener_info_equipo,
)
from goal_index import construir_goal_index_global
from poisson_model import evaluar_favorito, cumple_filtro_cuota
import ratings_store
import team_resolver
import elo_desde_goal_index

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
ARCHIVO_SALIDA = DATA_DIR / "partidos_hoy.json"

ZONA_HORARIA_LOCAL = datetime.timezone(datetime.timedelta(hours=-5))

# Unica fuente de esta conversion: team_resolver.py (antes duplicada aqui).
PAIS_A_CODIGO_CLUBELO = team_resolver.PAIS_A_CODIGO_CLUBELO

# Tope total de peticiones a API-Football para toda la Fase 1 (confirmado
# explicitamente). 1 se reserva para obtener_fixtures_por_fecha(); el
# resto queda como presupuesto para resolucion de pais por equipo.
TOPE_PETICIONES_FASE1 = 50


def fecha_local_hoy():
    return datetime.datetime.now(ZONA_HORARIA_LOCAL).date().isoformat()


def ya_se_completo_hoy():
    if not ARCHIVO_SALIDA.exists():
        return False
    try:
        datos = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8"))
        return datos.get("fecha") == fecha_local_hoy()
    except Exception:
        return False


def _resolver_pais(team_id, nombre, pais_liga, equipo_pais_goal_index):
    """Orden: liga domestica (gratis) -> Goal Index (gratis) -> API (paga)."""
    if pais_liga in PAIS_A_CODIGO_CLUBELO:
        return pais_liga, True, "liga_domestica"

    match_gi = buscar_equipo_similar(nombre, list(equipo_pais_goal_index.keys()), n=1, corte=0.75)
    if match_gi:
        pais_gi = equipo_pais_goal_index[match_gi[0]]
        if pais_gi:
            return pais_gi, True, "goal_index_gratis"

    pais_api = team_resolver.resolver_pais_equipo(team_id, nombre, obtener_info_equipo)
    if pais_api:
        return pais_api, True, "api_pagada"

    return None, False, "sin_resolver"


def seleccionar():
    if ya_se_completo_hoy():
        print("La seleccion de hoy ya se genero antes. Nada que hacer (0 peticiones gastadas).")
        return

    hoy = fecha_local_hoy()
    print(f"Buscando partidos de hoy ({hoy})...")

    # Presupuesto de esta corrida: 50 total - 1 (fixtures) = 49 para pais por API.
    team_resolver.resetear_contador_corrida(limite=TOPE_PETICIONES_FASE1 - 1)

    fixtures_api = obtener_fixtures_por_fecha(hoy)
    print(f"Partidos de hoy en API-Football (todas las ligas): {len(fixtures_api)}")

    ranking = obtener_ranking_clubelo(hoy)
    if not ranking:
        ayer = (datetime.datetime.now(ZONA_HORARIA_LOCAL).date() - datetime.timedelta(days=1)).isoformat()
        print(f"[AVISO] Ranking de hoy vacio, probando con el de ayer ({ayer})...")
        ranking = obtener_ranking_clubelo(ayer)

    elo_por_pais = {}
    elo_global_ultimo = {}
    for fila in ranking:
        try:
            club = fila["Club"]
            elo = float(fila["Elo"])
            pais_club = fila.get("Country", "")
            elo_por_pais.setdefault(pais_club, {})[club] = elo
            elo_global_ultimo[club] = elo
        except (KeyError, ValueError):
            continue

    print(f"Equipos con Elo disponible en ClubElo: {len(elo_global_ultimo)}")

    print("Construyendo Goal Index (38+16 ligas, forma reciente + temporada)...")
    goal_index, equipo_pais_goal_index = construir_goal_index_global()
    print(f"Equipos con Goal Index disponible: {len(goal_index)} "
          f"(con pais inferido gratis: {len(equipo_pais_goal_index)})")

    pendiente, intercepto, n_muestra_calibracion = elo_desde_goal_index.calibrar(elo_global_ultimo, goal_index)
    if pendiente is not None:
        print(f"Calibracion Goal Index -> Elo lista (muestra: {n_muestra_calibracion} equipos, "
              f"pendiente={pendiente:.2f}, intercepto={intercepto:.1f})")
    else:
        print(f"[AVISO] Muestra insuficiente para calibrar Goal Index -> Elo "
              f"({n_muestra_calibracion} equipos, se necesitan >= {elo_desde_goal_index.MUESTRA_MINIMA}). "
              f"Equipos sin ClubElo arrancaran solo con Glicko-2 (1500/RD=350).")

    seleccionados = []
    sin_elo_ni_rating_propio = 0
    sin_pais_verificado = 0
    elo_estimados_van = 0

    for f in fixtures_api:
        home_id = f["teams"]["home"]["id"]
        away_id = f["teams"]["away"]["id"]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        pais_liga = f.get("league", {}).get("country", "")
        liga_nombre = f.get("league", {}).get("name", "")

        pais_home, home_ok, metodo_pais_home = _resolver_pais(home_id, home, pais_liga, equipo_pais_goal_index)
        pais_away, away_ok, metodo_pais_away = _resolver_pais(away_id, away, pais_liga, equipo_pais_goal_index)

        elo_home, home_verificado, metodo_home = team_resolver.elegir_candidato_verificado(
            home, pais_home, elo_por_pais, elo_global_ultimo, buscar_equipo_similar,
            pais_rival=pais_away if not home_ok else None,
        )
        elo_away, away_verificado, metodo_away = team_resolver.elegir_candidato_verificado(
            away, pais_away, elo_por_pais, elo_global_ultimo, buscar_equipo_similar,
            pais_rival=pais_home if not away_ok else None,
        )

        pais_verificado = home_ok and away_ok
        if not pais_verificado:
            sin_pais_verificado += 1

        gi_home = gi_away = None
        cand_gi_home = buscar_equipo_similar(home, list(goal_index.keys()), n=1, corte=0.6)
        cand_gi_away = buscar_equipo_similar(away, list(goal_index.keys()), n=1, corte=0.6)
        if cand_gi_home:
            gi_home = goal_index[cand_gi_home[0]]["goal_index"]
        if cand_gi_away:
            gi_away = goal_index[cand_gi_away[0]]["goal_index"]

        elo_home_estimado = elo_away_estimado = False
        if elo_home is None and gi_home is not None:
            elo_home = elo_desde_goal_index.estimar_elo(gi_home, pendiente, intercepto)
            elo_home_estimado = elo_home is not None
        if elo_away is None and gi_away is not None:
            elo_away = elo_desde_goal_index.estimar_elo(gi_away, pendiente, intercepto)
            elo_away_estimado = elo_away is not None
        if elo_home_estimado or elo_away_estimado:
            elo_estimados_van += 1

        llave_home = ratings_store.llave_equipo(home_id, pais_home, home)
        llave_away = ratings_store.llave_equipo(away_id, pais_away, away)
        ratings_store.migrar_bootstrap_a_id(home, home_id, liga=liga_nombre)
        ratings_store.migrar_bootstrap_a_id(away, away_id, liga=liga_nombre)

        rating_home, n_home, rd_home = ratings_store.rating_combinado(
            llave_home, elo_home, nombre=home, pais=pais_home, liga=liga_nombre)
        rating_away, n_away, rd_away = ratings_store.rating_combinado(
            llave_away, elo_away, nombre=away, pais=pais_away, liga=liga_nombre)

        if elo_home is None and elo_away is None and n_home == 0 and n_away == 0:
            sin_elo_ni_rating_propio += 1
            continue

        evaluacion = evaluar_favorito(rating_home, rating_away, gi_home, gi_away)
        if not cumple_filtro_cuota(evaluacion):
            continue

        favorito_nombre = home if evaluacion["lado"] == "local" else away
        no_favorito_nombre = away if evaluacion["lado"] == "local" else home

        seleccionados.append({
            "partido": f"{home} vs {away}",
            "local": home,
            "visitante": away,
            "favorito": favorito_nombre,
            "no_favorito": no_favorito_nombre,
            "favorito_es_local": evaluacion["lado"] == "local",
            "cuota_inicial": evaluacion["cuota_inicial"],
            "probabilidad_inicial": round(evaluacion["probabilidad"] * 100, 1),
            "lambda_local": evaluacion["lambda_local"],
            "lambda_visitante": evaluacion["lambda_visitante"],
            "goal_index_disponible": gi_home is not None and gi_away is not None,
            "elo_local_estimado_goal_index": elo_home_estimado,
            "elo_visitante_estimado_goal_index": elo_away_estimado,
            "pais_verificado": pais_verificado,
            "metodo_pais": f"local:{metodo_pais_home}/visitante:{metodo_pais_away}",
            "metodo_emparejamiento": f"local:{metodo_home}/visitante:{metodo_away}",
            "rating_propio_partidos_local": n_home,
            "rating_propio_partidos_visitante": n_away,
            "rd_local": rd_home,
            "rd_visitante": rd_away,
            "hora_inicio": f["fixture"]["date"],
            "fixture_id": f["fixture"]["id"],
            "home_id": home_id,
            "away_id": away_id,
            "kickoff_utc": f["fixture"]["date"],
            "resultado_final": None,
            "acierto": None,
            "historial_snapshots": [],
            "alertas_enviadas": [],
            "diferencia_maxima_alcanzada": 0,
        })

    print(f"Partidos sin ninguna fuente de rating disponible (no evaluables): {sin_elo_ni_rating_propio}")
    print(f"Partidos evaluados SIN poder verificar el pais de ambos equipos: {sin_pais_verificado}")
    print(f"Partidos con al menos un Elo estimado via Goal Index: {elo_estimados_van}")

    ARCHIVO_SALIDA.write_text(
        json.dumps({"fecha": hoy, "partidos": seleccionados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sin_verificar_seleccionados = sum(1 for p in seleccionados if not p["pais_verificado"])
    print(f"Guardado en {ARCHIVO_SALIDA}. {len(seleccionados)} partidos seleccionados "
          f"(probabilidad inicial >= 60%), de los cuales {sin_verificar_seleccionados} "
          f"sin verificacion de pais.")


if __name__ == "__main__":
    seleccionar()
