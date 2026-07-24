"""
fetch_data.py
--------------
Descarga datos crudos de fuentes gratuitas:

- ClubElo (clubes):     http://api.clubelo.com
- Eloratings (selecciones): https://www.eloratings.net
- football-data.co.uk (resultados históricos por liga, para el "goal index" de clubes)
- API-Football (api-sports.io): vigilancia en vivo, país de equipo, eventos

IMPORTANTE (léelo antes de correr en producción):
Estas fuentes NO tienen un contrato de API estable/documentado oficialmente
(salvo ClubElo, que sí documenta su formato en http://clubelo.com/API).
Por eso este archivo aísla TODAS las llamadas de red en funciones pequeñas:
si un formato cambia, solo hay que tocar una función aquí, no todo el proyecto.

Si alguna función falla, revisa:
  1. Que la URL siga respondiendo igual (ábrela en el navegador).
  2. Que el nombre de equipo que buscas esté escrito como en la fuente
     (usa buscar_equipo_similar() para encontrar coincidencias aproximadas).

NOVEDAD DE ESTA VERSIÓN -- costo de cupo de API-Football:
Se agregaron dos llamadas nuevas usadas por monitor.py en cada revisión:
  - obtener_eventos_fixture(): necesaria para detectar tarjetas rojas y
    penales en tiempo real. Esto SUMA 1 petición por partido por
    revisión, además de la de estadísticas que ya existía -- es decir,
    duplica el costo por partido vigilado. Se decidió aceptar este costo
    a propósito porque tarjetas rojas y penales son señales de altísimo
    valor (cambian la probabilidad de gol de forma drástica) que antes
    se ignoraban por completo. La frecuencia adaptativa (10/15 min según
    carga) sigue siendo la herramienta principal para mantener esto
    dentro del cupo de 100/día.
  - obtener_info_equipo(): SOLO se paga una vez por equipo en la vida del
    proyecto (se cachea en team_country_cache.json vía team_resolver.py),
    así que su impacto en el cupo diario es mínimo y decreciente.
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
    """
    Wrapper de requests.get() con reintentos y backoff. ClubElo en
    particular es un sitio pequeño que documenta públicamente que a veces
    se sobrecarga ("Site overloaded, only cached pages available"). Con 1
    solo intento eso se traduce en una falla del workflow. Con esto, si un
    intento falla por timeout o error de conexión, esperamos un poco más
    cada vez y reintentamos, hasta 'intentos' veces, antes de rendirnos.
    """
    ultimo_error = None
    for intento in range(1, intentos + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            ultimo_error = e
            print(f"[AVISO] Intento {intento}/{intentos} falló para {url}: {e}")
            if intento < intentos:
                time.sleep(8 * intento)  # 8s, 16s, 24s, 32s... backoff creciente
    raise ultimo_error

# API-Football (api-sports.io) - vigilancia en vivo + resolución de país.
# Plan gratis: 100 peticiones/día. Todas las funciones están diseñadas
# para hacer el mínimo de peticiones posible.
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE = "https://v3.football.api-sports.io"


def _headers_api_football():
    return {"x-apisports-key": API_FOOTBALL_KEY}


def _api_football_request(endpoint, params=None, timeout=20):
    """
    Punto único por donde pasan TODAS las llamadas a API-Football.
    Usa una sola cuenta (API_FOOTBALL_KEY) y registra el uso del día vía
    cuota_api_football.py, para poder reportarlo en el resumen de las 6am.
    """
    from cuota_api_football import registrar_uso

    r = requests.get(
        f"{API_FOOTBALL_BASE}/{endpoint}",
        headers=_headers_api_football(),
        params=params,
        timeout=timeout,
    )
    r.raise_for_status()
    registrar_uso()
    return r.json().get("response", [])


# ---------------------------------------------------------------------------
# 1. CLUB ELO  (clubes de fútbol)
# ---------------------------------------------------------------------------

CACHE_FIXTURES = Path(__file__).parent / "data" / "_cache_fixtures.csv"
CACHE_FIXTURES.parent.mkdir(exist_ok=True)


def obtener_fixtures_clubelo():
    """
    Devuelve la lista de próximos partidos de clubes con las probabilidades
    YA calculadas por ClubElo (1X2 y probabilidad de cada resultado exacto).
    Fuente: http://api.clubelo.com/Fixtures

    Si ClubElo falla incluso tras todos los reintentos, usamos el último
    resultado que sí funcionó, guardado en caché, en vez de fallar por
    completo.
    """
    url = "http://api.clubelo.com/Fixtures"
    try:
        r = _get_con_reintentos(url, headers=HEADERS)
        CACHE_FIXTURES.write_text(r.text, encoding="utf-8")
        return list(csv.DictReader(io.StringIO(r.text)))
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        if CACHE_FIXTURES.exists():
            print(f"[AVISO] ClubElo no respondió tras varios intentos ({e}). "
                  f"Usando el último resultado exitoso guardado en caché.")
            texto_cache = CACHE_FIXTURES.read_text(encoding="utf-8")
            return list(csv.DictReader(io.StringIO(texto_cache)))
        print("[AVISO] ClubElo no respondió y no hay caché previo disponible.")
        raise


CACHE_RANKING = Path(__file__).parent / "data" / "_cache_ranking.csv"


def obtener_ranking_clubelo(fecha="today"):
    """
    Devuelve el ranking Elo completo de clubes para una fecha (YYYY-MM-DD)
    o 'today' para el más reciente. Con el mismo respaldo de caché.
    """
    url = f"http://api.clubelo.com/{fecha}"
    try:
        r = _get_con_reintentos(url, headers=HEADERS)
        CACHE_RANKING.write_text(r.text, encoding="utf-8")
        return list(csv.DictReader(io.StringIO(r.text)))
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        if CACHE_RANKING.exists():
            print(f"[AVISO] ClubElo no respondió tras varios intentos ({e}). "
                  f"Usando el último ranking guardado en caché.")
            return list(csv.DictReader(io.StringIO(CACHE_RANKING.read_text(encoding="utf-8"))))
        print("[AVISO] ClubElo no respondió y no hay caché previo disponible.")
        raise


def obtener_historial_club(nombre_club):
    """Devuelve el historial de Elo de un club específico."""
    url = f"http://api.clubelo.com/{nombre_club}"
    r = _get_con_reintentos(url, headers=HEADERS)
    reader = csv.DictReader(io.StringIO(r.text))
    return list(reader)


# ---------------------------------------------------------------------------
# 2. ELORATINGS.NET  (selecciones nacionales)
# ---------------------------------------------------------------------------

def obtener_ranking_selecciones():
    url = "https://www.eloratings.net/World.tsv"
    r = _get_con_reintentos(url, headers=HEADERS)
    if r.status_code == 200 and "\t" in r.text:
        filas = [l.split("\t") for l in r.text.strip().split("\n")]
        return filas
    raise RuntimeError(
        "No se pudo leer el ranking de selecciones desde eloratings.net. "
        "Es posible que la URL del archivo .tsv haya cambiado."
    )


# ---------------------------------------------------------------------------
# 3. FOOTBALL-DATA.CO.UK  (resultados históricos -> goal index de clubes)
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
    "N1": "Eredivisie (Países Bajos)",
    "B1": "Pro League (Bélgica)",
    "P1": "Primeira Liga (Portugal)",
    "T1": "Süper Lig (Turquía)",
    "G1": "Super League (Grecia)",
}

LIGAS_FOOTBALL_DATA_EXTRA = {
    "ARG": "Argentina - Primera División",
    "AUT": "Austria - Bundesliga",
    "BRA": "Brasil - Série A",
    "CHN": "China - Super League",
    "DNK": "Dinamarca - Superliga",
    "FIN": "Finlandia - Veikkausliiga",
    "IRL": "Irlanda - Premier Division",
    "JPN": "Japón - J1 League",
    "MEX": "México - Liga MX",
    "NOR": "Noruega - Eliteserien",
    "POL": "Polonia - Ekstraklasa",
    "ROU": "Rumania - Liga I",
    "RUS": "Rusia - Premier League",
    "SWE": "Suecia - Allsvenskan",
    "SWZ": "Suiza - Super League",
    "USA": "Estados Unidos - MLS",
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
    """
    NUEVO: para el bootstrap de Glicko-2 (últimas 1-2 temporadas), en vez
    de solo la temporada en curso. 'temporadas' es una lista tipo
    ["2425", "2526"]. Devuelve todos los partidos concatenados, en orden
    cronológico (necesario para reproducir el histórico correctamente).
    """
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
    """1 petición: todos los partidos programados en el mundo para 'fecha_iso'."""
    return _api_football_request("fixtures", params={"date": fecha_iso})


def obtener_partidos_en_vivo():
    """1 petición: TODOS los partidos en vivo en el mundo ahora mismo."""
    return _api_football_request("fixtures", params={"live": "all"})


def obtener_estadisticas_fixture(fixture_id):
    """
    1 petición por partido. Devuelve estadísticas de ambos equipos
    (tiros, córners, posesión, y "expected_goals" cuando la liga lo
    expone -- ver extraer_xg() más abajo para leerlo de forma segura).
    """
    return _api_football_request("fixtures/statistics", params={"fixture": fixture_id})


def obtener_eventos_fixture(fixture_id):
    """
    NUEVO -- 1 petición por partido (además de la de estadísticas).
    Devuelve la lista cruda de eventos (goles, tarjetas, sustituciones)
    de API-Football. Se usa para detectar tarjetas rojas y penales en
    tiempo real -- ver monitor.py: _extraer_eventos_relevantes().
    """
    return _api_football_request("fixtures/events", params={"fixture": fixture_id})


def extraer_xg(stats_equipo):
    """
    Intenta leer el xG (Expected Goals) del bloque de estadísticas de un
    equipo, si la liga/plan lo expone. Devuelve None si no está
    disponible -- el resto del sistema debe seguir funcionando sin xG
    (se cae a la aproximación por tiros a puerta), nunca fallar por
    su ausencia.
    """
    for item in stats_equipo.get("statistics", []):
        if item.get("type", "").lower() in ("expected_goals", "xg"):
            v = item.get("value")
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def obtener_info_equipo(team_id):
    """
    1 petición POR EQUIPO, pero SOLO la primera vez que se ve ese equipo
    (team_resolver.py lo cachea para siempre en team_country_cache.json).
    Devuelve {"nombre":..., "country":...} o None si no se encuentra.
    """
    respuesta = _api_football_request("teams", params={"id": team_id})
    if not respuesta:
        return None
    equipo = respuesta[0]["team"]
    return {"nombre": equipo["name"], "country": equipo.get("country")}


def obtener_resultado_fixture(fixture_id):
    """1 petición: consulta un partido específico por su fixture_id."""
    respuesta = _api_football_request("fixtures", params={"id": fixture_id})
    return respuesta[0] if respuesta else None


def obtener_prediccion_fixture(fixture_id):
    """1 petición por partido: la predicción propia de API-Football."""
    respuesta = _api_football_request("predictions", params={"fixture": fixture_id})
    return respuesta[0] if respuesta else None


LIGAS_API_FOOTBALL_EXTRA = {}
TEMPORADA_API_FOOTBALL = 2026


def buscar_id_liga_api_football(nombre_busqueda):
    """Ayuda a encontrar el league_id correcto en API-Football (uso manual, 1 vez)."""
    resultados = _api_football_request("leagues", params={"search": nombre_busqueda})
    return [
        {"id": x["league"]["id"], "nombre": x["league"]["name"], "pais": x["country"]["name"]}
        for x in resultados
    ]


def obtener_standings_liga(league_id, temporada=TEMPORADA_API_FOOTBALL):
    """1 petición: tabla de posiciones completa de una liga."""
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
