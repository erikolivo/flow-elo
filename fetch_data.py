"""
fetch_data.py
--------------
Descarga datos crudos de fuentes gratuitas:

- ClubElo (clubes):     http://api.clubelo.com
- Eloratings (selecciones): https://www.eloratings.net
- football-data.co.uk (resultados historicos por liga, para el "goal index")
- API-Football (api-sports.io): vigilancia en vivo, pais de equipo, eventos

Si alguna funcion falla, revisa:
  1. Que la URL siga respondiendo igual (abrela en el navegador).
  2. Que el nombre de equipo que buscas este escrito como en la fuente.

COSTO DE CUPO DE API-FOOTBALL:
  - obtener_eventos_fixture(): 1 peticion por partido por revision en
    Fase 3 (tarjetas/penales), ademas de la de estadisticas.
  - obtener_info_equipo(): se paga como maximo una vez por equipo en la
    vida del proyecto (cacheado via team_resolver.py), y ahora ademas
    solo se llama cuando ni la liga domestica NI el Goal Index pudieron
    dar el pais gratis (ver seleccionar_partidos.py).
"""

import csv
import io
import os
import time
import difflib
import requests
from pathlib import Path

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EloPredictorBot/1.0)"}


def _get_con_reintentos(url, headers=None, params=None, timeout=35, intentos=5):
    ultimo_error = None
    for intento in range(1, intentos + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            ultimo_error = e
            print(f"[AVISO] Intento {intento}/{intentos} fallo para {url}: {e}")
            if intento < intentos:
                time.sleep(8 * intento)
    raise ultimo_error


API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"


def _headers_api_football():
    return {"x-apisports-key": API_FOOTBALL_KEY}


def _api_football_request(endpoint, params=None, timeout=20):
    """
    Punto unico por donde pasan TODAS las llamadas a API-Football.
    Si la API responde 429 (cupo diario agotado), se marca el contador
    LOCAL como agotado de inmediato -- sin esto, el resto de la misma
    corrida seguiria intentando peticiones que sabemos que van a fallar.
    """
    from cuota_api_football import registrar_uso, marcar_agotado

    r = requests.get(
        f"{API_FOOTBALL_BASE}/{endpoint}",
        headers=_headers_api_football(),
        params=params,
        timeout=timeout,
    )
    if r.status_code == 429:
        marcar_agotado()
    r.raise_for_status()
    registrar_uso()
    return r.json().get("response", [])


# ---------------------------------------------------------------------------
# 1. CLUB ELO
# ---------------------------------------------------------------------------

CACHE_FIXTURES = Path(__file__).parent / "data" / "_cache_fixtures.csv"
CACHE_FIXTURES.parent.mkdir(exist_ok=True)


def obtener_fixtures_clubelo():
    url = "http://api.clubelo.com/Fixtures"
    try:
        r = _get_con_reintentos(url, headers=HEADERS)
        CACHE_FIXTURES.write_text(r.text, encoding="utf-8")
        return list(csv.DictReader(io.StringIO(r.text)))
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        if CACHE_FIXTURES.exists():
            print(f"[AVISO] ClubElo no respondio tras varios intentos ({e}). Usando cache.")
            texto_cache = CACHE_FIXTURES.read_text(encoding="utf-8")
            return list(csv.DictReader(io.StringIO(texto_cache)))
        print("[AVISO] ClubElo no respondio y no hay cache previo disponible.")
        raise


CACHE_RANKING = Path(__file__).parent / "data" / "_cache_ranking.csv"


def obtener_ranking_clubelo(fecha="today"):
    url = f"http://api.clubelo.com/{fecha}"
    try:
        r = _get_con_reintentos(url, headers=HEADERS)
        CACHE_RANKING.write_text(r.text, encoding="utf-8")
        return list(csv.DictReader(io.StringIO(r.text)))
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        if CACHE_RANKING.exists():
            print(f"[AVISO] ClubElo no respondio tras varios intentos ({e}). Usando cache.")
            return list(csv.DictReader(io.StringIO(CACHE_RANKING.read_text(encoding="utf-8"))))
        print("[AVISO] ClubElo no respondio y no hay cache previo disponible.")
        raise


def obtener_historial_club(nombre_club):
    url = f"http://api.clubelo.com/{nombre_club}"
    r = _get_con_reintentos(url, headers=HEADERS)
    reader = csv.DictReader(io.StringIO(r.text))
    return list(reader)


# ---------------------------------------------------------------------------
# 2. ELORATINGS.NET
# ---------------------------------------------------------------------------

def obtener_ranking_selecciones():
    url = "https://www.eloratings.net/World.tsv"
    r = _get_con_reintentos(url, headers=HEADERS)
    if r.status_code == 200 and "\t" in r.text:
        filas = [l.split("\t") for l in r.text.strip().split("\n")]
        return filas
    raise RuntimeError("No se pudo leer el ranking de selecciones desde eloratings.net.")


# ---------------------------------------------------------------------------
# 3. FOOTBALL-DATA.CO.UK
# ---------------------------------------------------------------------------

LIGAS_FOOTBALL_DATA = {
    "E0": "Premier League", "E1": "Championship", "E2": "League One",
    "E3": "League Two", "EC": "Conference / National League",
    "SC0": "Scottish Premiership", "SC1": "Scottish Championship",
    "SC2": "Scottish League One", "SC3": "Scottish League Two",
    "D1": "Bundesliga", "D2": "2. Bundesliga",
    "I1": "Serie A", "I2": "Serie B",
    "SP1": "La Liga", "SP2": "La Liga 2",
    "F1": "Ligue 1", "F2": "Ligue 2",
    "N1": "Eredivisie (Paises Bajos)",
    "B1": "Pro League (Belgica)",
    "P1": "Primeira Liga (Portugal)",
    "T1": "Super Lig (Turquia)",
    "G1": "Super League (Grecia)",
}

LIGAS_FOOTBALL_DATA_EXTRA = {
    "ARG": "Argentina - Primera Division",
    "AUT": "Austria - Bundesliga",
    "BRA": "Brasil - Serie A",
    "CHN": "China - Super League",
    "DNK": "Dinamarca - Superliga",
    "FIN": "Finlandia - Veikkausliiga",
    "IRL": "Irlanda - Premier Division",
    "JPN": "Japon - J1 League",
    "MEX": "Mexico - Liga MX",
    "NOR": "Noruega - Eliteserien",
    "POL": "Polonia - Ekstraklasa",
    "ROU": "Rumania - Liga I",
    "RUS": "Rusia - Premier League",
    "SWE": "Suecia - Allsvenskan",
    "SWZ": "Suiza - Super League",
    "USA": "Estados Unidos - MLS",
}

# NUEVO: codigo de liga -> pais (nombre en ingles, MISMA convencion que
# team_resolver.PAIS_A_CODIGO_CLUBELO), para poder usar el Goal Index
# como fuente GRATUITA de pais de cada equipo -- ver goal_index.py y su
# uso en seleccionar_partidos.py.
CODIGO_LIGA_A_PAIS = {
    "E0": "England", "E1": "England", "E2": "England", "E3": "England", "EC": "England",
    "SC0": "Scotland", "SC1": "Scotland", "SC2": "Scotland", "SC3": "Scotland",
    "D1": "Germany", "D2": "Germany",
    "I1": "Italy", "I2": "Italy",
    "SP1": "Spain", "SP2": "Spain",
    "F1": "France", "F2": "France",
    "N1": "Netherlands",
    "B1": "Belgium",
    "P1": "Portugal",
    "T1": "Turkey",
    "G1": "Greece",
    "ARG": "Argentina", "AUT": "Austria", "BRA": "Brazil", "CHN": "China",
    "DNK": "Denmark", "FIN": "Finland", "IRL": "Ireland", "JPN": "Japan",
    "MEX": "Mexico", "NOR": "Norway", "POL": "Poland", "ROU": "Romania",
    "RUS": "Russia", "SWE": "Sweden", "SWZ": "Switzerland", "USA": "USA",
}


def obtener_resultados_liga(codigo_liga, temporada="2526"):
    url = f"https://www.football-data.co.uk/mmz4281/{temporada}/{codigo_liga}.csv"
    r = _get_con_reintentos(url, headers=HEADERS)
    reader = csv.DictReader(io.StringIO(r.text))
    return list(reader)


def obtener_resultados_liga_extra(codigo_liga):
    url = f"https://www.football-data.co.uk/new/{codigo_liga}.csv"
    r = _get_con_reintentos(url, headers=HEADERS)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.text))
    filas = list(reader)
    if filas and "Season" in filas[0]:
        temporada_reciente = sorted({f["Season"] for f in filas if f.get("Season")})[-1]
        filas = [f for f in filas if f.get("Season") == temporada_reciente]
    return filas


def obtener_resultados_liga_multi_temporada(codigo_liga, temporadas):
    todos = []
    for temporada in temporadas:
        try:
            filas = obtener_resultados_liga(codigo_liga, temporada)
            todos.extend(filas)
        except Exception as e:
            print(f"[AVISO] No se pudo descargar {codigo_liga} temporada {temporada}: {e}")
    todos.sort(key=lambda f: _parsear_fecha_football_data(f.get("Date", "")))
    return todos


def calcular_goal_index(resultados, ultimos_n=None):
    partidos_por_equipo = {}

    def _agregar(equipo, fecha, gf, gc):
        partidos_por_equipo.setdefault(equipo, []).append((fecha, gf, gc))

    for partido in resultados:
        home, away = partido.get("HomeTeam"), partido.get("AwayTeam")
        if not home or not away:
            continue
        try:
            gh, ga = int(partido["FTHG"]), int(partido["FTAG"])
        except (KeyError, ValueError):
            continue
        fecha = _parsear_fecha_football_data(partido.get("Date", ""))
        _agregar(home, fecha, gh, ga)
        _agregar(away, fecha, ga, gh)

    resultado = {}
    for equipo, partidos in partidos_por_equipo.items():
        partidos.sort(key=lambda x: x[0], reverse=True)
        if ultimos_n:
            partidos = partidos[:ultimos_n]
        if not partidos:
            continue
        gf_prom = sum(p[1] for p in partidos) / len(partidos)
        gc_prom = sum(p[2] for p in partidos) / len(partidos)
        resultado[equipo] = {
            "partidos_jugados": len(partidos),
            "goles_favor_prom": round(gf_prom, 2),
            "goles_contra_prom": round(gc_prom, 2),
            "goal_index": round(gf_prom - gc_prom, 2),
        }
    return resultado


def _parsear_fecha_football_data(texto_fecha):
    import datetime as _dt
    for formato in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return _dt.datetime.strptime(texto_fecha, formato)
        except (ValueError, TypeError):
            continue
    return _dt.datetime(1900, 1, 1)


# ---------------------------------------------------------------------------
# 4. MATCHING DE NOMBRES
# ---------------------------------------------------------------------------

def buscar_equipo_similar(nombre, lista_nombres, n=3, corte=0.5):
    return difflib.get_close_matches(nombre, lista_nombres, n=n, cutoff=corte)


# ---------------------------------------------------------------------------
# 5. API-FOOTBALL (api-sports.io)
# ---------------------------------------------------------------------------

def obtener_fixtures_por_fecha(fecha_iso):
    return _api_football_request("fixtures", params={"date": fecha_iso})


def obtener_partidos_en_vivo():
    return _api_football_request("fixtures", params={"live": "all"})


def obtener_estadisticas_fixture(fixture_id):
    return _api_football_request("fixtures/statistics", params={"fixture": fixture_id})


def obtener_eventos_fixture(fixture_id):
    return _api_football_request("fixtures/events", params={"fixture": fixture_id})


def extraer_xg(stats_equipo):
    for item in stats_equipo.get("statistics", []):
        if item.get("type", "").lower() in ("expected_goals", "xg"):
            v = item.get("value")
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def obtener_info_equipo(team_id):
    respuesta = _api_football_request("teams", params={"id": team_id})
    if not respuesta:
        return None
    equipo = respuesta[0]["team"]
    return {"nombre": equipo["name"], "country": equipo.get("country")}


def obtener_resultado_fixture(fixture_id):
    respuesta = _api_football_request("fixtures", params={"id": fixture_id})
    return respuesta[0] if respuesta else None


def obtener_prediccion_fixture(fixture_id):
    respuesta = _api_football_request("predictions", params={"fixture": fixture_id})
    return respuesta[0] if respuesta else None


LIGAS_API_FOOTBALL_EXTRA = {}
TEMPORADA_API_FOOTBALL = 2026


def buscar_id_liga_api_football(nombre_busqueda):
    resultados = _api_football_request("leagues", params={"search": nombre_busqueda})
    return [
        {"id": x["league"]["id"], "nombre": x["league"]["name"], "pais": x["country"]["name"]}
        for x in resultados
    ]


def obtener_standings_liga(league_id, temporada=TEMPORADA_API_FOOTBALL):
    respuesta = _api_football_request("standings", params={"league": league_id, "season": temporada})
    if not respuesta:
        return []
    grupos = respuesta[0]["league"]["standings"]
    return [equipo for grupo in grupos for equipo in grupo]


def calcular_goal_index_desde_standings(standings):
    resultado = {}
    for equipo in standings:
        jugados = equipo["all"]["played"]
        if not jugados:
            continue
        gf_prom = equipo["all"]["goals"]["for"] / jugados
        gc_prom = equipo["all"]["goals"]["against"] / jugados
        resultado[equipo["team"]["name"]] = {
            "partidos_jugados": jugados,
            "goles_favor_prom": round(gf_prom, 2),
            "goles_contra_prom": round(gc_prom, 2),
            "goal_index": round(gf_prom - gc_prom, 2),
        }
    return resultado


# ---------------------------------------------------------------------------
# 6. THE ODDS API (cuotas reales) -- opcional, solo si ODDS_API_KEY esta
#    configurada. Plan free: cupo MENSUAL (no diario) -- ver
#    cuota_odds_api.py, que lee el cupo real de los headers de cada
#    respuesta en vez de adivinar.
# ---------------------------------------------------------------------------

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"


def obtener_deportes_odds_api():
    """
    Lista los deportes/ligas actualmente activos en The Odds API. Este
    endpoint NO consume cupo (segun la documentacion oficial) -- se usa
    para validar que una sport_key mapeada sigue vigente antes de gastar
    una peticion real en ella.
    Devuelve un set de sport_keys activas (vacio si falla, nunca lanza
    excepcion hacia arriba: la ausencia de cuotas reales no debe tumbar
    el resto de Fase 1).
    """
    if not ODDS_API_KEY:
        return set()
    try:
        r = requests.get(f"{ODDS_API_BASE}/sports", params={"apiKey": ODDS_API_KEY}, timeout=20)
        r.raise_for_status()
        return {d["key"] for d in r.json() if d.get("group") == "Soccer"}
    except Exception as e:
        print(f"[AVISO] No se pudo consultar deportes activos de The Odds API: {e}")
        return set()


def obtener_cuotas_liga(sport_key, regions="uk,eu", markets="h2h"):
    """
    1 peticion (cuesta cupo real, ver cuota_odds_api.py). Devuelve la
    lista de eventos de esa liga con cuotas h2h (local/empate/visitante)
    de los bookmakers disponibles en las regiones pedidas.
    """
    from cuota_odds_api import actualizar_desde_headers

    r = requests.get(
        f"{ODDS_API_BASE}/sports/{sport_key}/odds",
        params={"apiKey": ODDS_API_KEY, "regions": regions, "markets": markets, "oddsFormat": "decimal"},
        timeout=20,
    )
    actualizar_desde_headers(r.headers)
    r.raise_for_status()
    return r.json()
