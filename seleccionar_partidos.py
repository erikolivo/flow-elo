"""
seleccionar_partidos.py
------------------------
FASE 1, versión 6 -- rediseño del emparejamiento de equipos y de la
fuente de rating.

Cambios de fondo respecto a la versión anterior:

1. PAÍS POR EQUIPO, NO POR LIGA DEL FIXTURE. Antes se filtraba el Elo
   por el país de la liga del fixture (f["league"]["country"]), lo cual
   se rompe en torneos internacionales (Copa Libertadores, Champions
   League...) donde la liga puede reportar "World" y cada equipo es en
   realidad de un país distinto. Ahora cada equipo resuelve SU PROPIO
   país vía team_resolver.py (cacheado para siempre, 1 sola petición en
   la vida del equipo).

2. VERIFICACIÓN CRUZADA (opción B). Si un equipo no logra emparejarse
   con confianza dentro de su propio país, se usa el país/confederación
   YA RESUELTO del rival para restringir la búsqueda, en vez de una
   búsqueda global sin filtro.

3. RATING COMBINADO (ClubElo semilla + Glicko-2 propio). Ya no se usa
   Elo de ClubElo puro -- se usa ratings_store.rating_combinado(), que
   mezcla ambas fuentes según cuántos partidos propios se han observado
   de cada equipo (0%/20%/50%/75%/100%, ver ratings_store.py). Se
   prioriza cobertura rápida: un equipo con 1-3 partidos observados YA
   pesa en la decisión, no hay que esperar meses.

Sigue implementando "reintentar cada 5 min hasta lograrlo" de forma
eficiente: si ya se completó hoy, termina de inmediato.
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

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
ARCHIVO_SALIDA = DATA_DIR / "partidos_hoy.json"

ZONA_HORARIA_LOCAL = datetime.timezone(datetime.timedelta(hours=-5))

# Se conserva como respaldo: si el país del equipo no se pudo resolver
# vía team_resolver (ej. falló la petición), se cae al comportamiento
# anterior (país de la liga) en vez de descartar el partido de plano.
PAIS_A_CODIGO_CLUBELO = {
    "England": "ENG", "Scotland": "SCO", "Wales": "WAL", "Northern-Ireland": "NIR",
    "Spain": "ESP", "Italy": "ITA", "Germany": "GER", "France": "FRA",
    "Portugal": "POR", "Netherlands": "NED", "Belgium": "BEL", "Turkey": "TUR",
    "Greece": "GRE", "Russia": "RUS", "Ukraine": "UKR", "Poland": "POL",
    "Austria": "AUT", "Switzerland": "SUI", "Sweden": "SWE", "Norway": "NOR",
    "Denmark": "DEN", "Finland": "FIN", "Iceland": "ISL", "Ireland": "IRL",
    "Croatia": "CRO", "Serbia": "SRB", "Romania": "ROU", "Bulgaria": "BUL",
    "Hungary": "HUN", "Czech-Republic": "CZE", "Slovakia": "SVK", "Slovenia": "SVN",
    "Bosnia": "BIH", "Israel": "ISR", "Cyprus": "CYP", "Luxembourg": "LUX",
    "Brazil": "BRA", "Argentina": "ARG", "Mexico": "MEX", "USA": "USA",
    "Colombia": "COL", "Chile": "CHI", "Peru": "PER", "Uruguay": "URU",
    "Ecuador": "ECU", "Paraguay": "PAR", "Bolivia": "BOL", "Venezuela": "VEN",
    "Australia": "AUS", "Japan": "JPN", "South-Korea": "KOR", "China": "CHN",
    "Saudi-Arabia": "KSA", "Qatar": "QAT", "Egypt": "EGY", "South-Africa": "RSA",
}


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


def _resolver_pais_con_respaldo(team_id, nombre, pais_liga):
    """
    CORRECCIÓN IMPORTANTE (post-producción): la primera versión llamaba
    a la API para resolver el país de CADA equipo de CADA partido del
    día, sin importar si hacía falta -- eso agotaba el cupo diario en la
    primera corrida. La corrección: en un partido DOMÉSTICO (liga con
    país reconocido en el mapeo), el país de la liga YA es correcto para
    ambos equipos y no hace falta pagar ninguna petición -- el caso real
    que motivó resolver por equipo es el de torneos INTERNACIONALES
    (liga sin país reconocido, ej. "World"), y es ahí, solo ahí, donde
    vale la pena pagar la petición nueva.
    """
    if pais_liga in PAIS_A_CODIGO_CLUBELO:
        return pais_liga, True  # doméstico: país de la liga es confiable, gratis

    pais = team_resolver.resolver_pais_equipo(team_id, nombre, obtener_info_equipo)
    if pais:
        return pais, True
    return None, False


def seleccionar():
    if ya_se_completo_hoy():
        print("La selección de hoy ya se generó antes. Nada que hacer (0 peticiones gastadas).")
        return

    hoy = fecha_local_hoy()
    print(f"Buscando partidos de hoy ({hoy})...")
    team_resolver.resetear_contador_corrida()

    fixtures_api = obtener_fixtures_por_fecha(hoy)
    print(f"Partidos de hoy en API-Football (todas las ligas): {len(fixtures_api)}")

    ranking = obtener_ranking_clubelo(hoy)
    if not ranking:
        ayer = (datetime.datetime.now(ZONA_HORARIA_LOCAL).date() - datetime.timedelta(days=1)).isoformat()
        print(f"[AVISO] Ranking de hoy vacío, probando con el de ayer ({ayer})...")
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

    print("Construyendo Goal Index (38 ligas, forma reciente + temporada)...")
    goal_index = construir_goal_index_global()
    print(f"Equipos con Goal Index disponible: {len(goal_index)}")

    seleccionados = []
    sin_elo_ni_rating_propio = 0
    sin_pais_verificado = 0

    for f in fixtures_api:
        home_id = f["teams"]["home"]["id"]
        away_id = f["teams"]["away"]["id"]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        pais_liga = f.get("league", {}).get("country", "")
        liga_nombre = f.get("league", {}).get("name", "")

        pais_home, home_verificado_directo = _resolver_pais_con_respaldo(home_id, home, pais_liga)
        pais_away, away_verificado_directo = _resolver_pais_con_respaldo(away_id, away, pais_liga)

        # Verificación cruzada (opción B): si uno de los dos no se
        # resolvió directo, se usa el país del otro (ya resuelto) para
        # restringir la búsqueda por confederación.
        elo_home, home_verificado, metodo_home = team_resolver.elegir_candidato_verificado(
            home, pais_home, elo_por_pais, elo_global_ultimo, buscar_equipo_similar,
            pais_rival=pais_away if not home_verificado_directo else None,
        )
        elo_away, away_verificado, metodo_away = team_resolver.elegir_candidato_verificado(
            away, pais_away, elo_por_pais, elo_global_ultimo, buscar_equipo_similar,
            pais_rival=pais_home if not away_verificado_directo else None,
        )

        pais_verificado = home_verificado and away_verificado
        if not pais_verificado:
            sin_pais_verificado += 1

        # --- Rating combinado (ClubElo semilla + Glicko-2 propio) ---
        llave_home = ratings_store.llave_equipo(home_id, pais_home, home)
        llave_away = ratings_store.llave_equipo(away_id, pais_away, away)
        ratings_store.migrar_bootstrap_a_id(home, home_id, liga=liga_nombre)
        ratings_store.migrar_bootstrap_a_id(away, away_id, liga=liga_nombre)

        rating_home, n_home, rd_home = ratings_store.rating_combinado(
            llave_home, elo_home, nombre=home, pais=pais_home, liga=liga_nombre)
        rating_away, n_away, rd_away = ratings_store.rating_combinado(
            llave_away, elo_away, nombre=away, pais=pais_away, liga=liga_nombre)

        if elo_home is None and elo_away is None and n_home == 0 and n_away == 0:
            # Ninguna fuente disponible para ninguno de los dos -- no hay
            # con qué evaluar este partido de forma responsable.
            sin_elo_ni_rating_propio += 1
            continue

        gi_home = gi_away = None
        cand_gi_home = buscar_equipo_similar(home, list(goal_index.keys()), n=1, corte=0.6)
        cand_gi_away = buscar_equipo_similar(away, list(goal_index.keys()), n=1, corte=0.6)
        if cand_gi_home:
            gi_home = goal_index[cand_gi_home[0]]["goal_index"]
        if cand_gi_away:
            gi_away = goal_index[cand_gi_away[0]]["goal_index"]

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
            "pais_verificado": pais_verificado,
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
    print(f"Partidos evaluados SIN poder verificar el país de ambos equipos: {sin_pais_verificado}")

    ARCHIVO_SALIDA.write_text(
        json.dumps({"fecha": hoy, "partidos": seleccionados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sin_verificar_seleccionados = sum(1 for p in seleccionados if not p["pais_verificado"])
    print(f"Guardado en {ARCHIVO_SALIDA}. {len(seleccionados)} partidos seleccionados "
          f"(probabilidad inicial >= 60%), de los cuales {sin_verificar_seleccionados} "
          f"sin verificación de país (revisar con más cuidado).")


if __name__ == "__main__":
    seleccionar()
