"""
mapeo_ligas_odds_api.py
-------------------------
The Odds API identifica cada liga con un "sport key" propio (ej.
"soccer_epl"), que no coincide con los codigos de football-data.co.uk
ni con los league_id de API-Football -- hace falta un mapeo manual.

Esta lista es curada y NO exhaustiva (el plan free de The Odds API
tampoco cubre todas las ligas del mundo). Si una liga de tu seleccion
diaria no aparece aqui, simplemente no se le busca cuota real -- el
partido sigue evaluandose igual que antes (Elo+GoalIndex+Glicko), sin
romper nada.

IMPORTANTE: antes de confiar en una sport key, se valida contra el
listado real de deportes activos ahora mismo (obtener_deportes_odds_api,
que NO cuesta cupo) -- si The Odds API cambio o desactivo una key, se
detecta y se salta esa liga con un aviso, en vez de gastar una peticion
que fallaria.
"""

import difflib

# (pais, nombre_de_liga_como_lo_da_api_football) -> sport_key de The Odds API
MAPEO_LIGA_A_SPORT_KEY = {
    ("England", "Premier League"): "soccer_epl",
    ("England", "Championship"): "soccer_efl_champ",
    ("Spain", "La Liga"): "soccer_spain_la_liga",
    ("Italy", "Serie A"): "soccer_italy_serie_a",
    ("Germany", "Bundesliga"): "soccer_germany_bundesliga",
    ("Germany", "2. Bundesliga"): "soccer_germany_bundesliga2",
    ("France", "Ligue 1"): "soccer_france_ligue_one",
    ("Netherlands", "Eredivisie"): "soccer_netherlands_eredivisie",
    ("Portugal", "Primeira Liga"): "soccer_portugal_primeira_liga",
    ("Belgium", "Jupiler Pro League"): "soccer_belgium_first_div",
    ("Turkey", "Super Lig"): "soccer_turkey_super_league",
    ("Greece", "Super League 1"): "soccer_greece_super_league",
    ("Brazil", "Serie A"): "soccer_brazil_campeonato",
    ("Argentina", "Liga Profesional Argentina"): "soccer_argentina_primera_division",
    ("Mexico", "Liga MX"): "soccer_mexico_ligamx",
    ("USA", "Major League Soccer"): "soccer_usa_mls",
    ("World", "UEFA Champions League"): "soccer_uefa_champs_league",
    ("World", "UEFA Europa League"): "soccer_uefa_europa_league",
    ("South-Korea", "K League 1"): "soccer_korea_kleague1",
    ("Japan", "J1 League"): "soccer_japan_j_league",
    ("Australia", "A-League"): "soccer_australia_aleague",
    ("Denmark", "Superliga"): "soccer_denmark_superliga",
    ("Sweden", "Allsvenskan"): "soccer_sweden_allsvenskan",
    ("Norway", "Eliteserien"): "soccer_norway_eliteserien",
    ("China", "Super League"): "soccer_china_superleague",
    ("Poland", "Ekstraklasa"): "soccer_poland_ekstraklasa",
    ("Switzerland", "Super League"): "soccer_switzerland_superleague",
    ("Austria", "Bundesliga"): "soccer_austria_bundesliga",
    ("Scotland", "Premiership"): "soccer_spl",
    ("Ireland", "Premier Division"): "soccer_league_of_ireland",
    ("Finland", "Veikkausliiga"): "soccer_finland_veikkausliiga",
    ("Romania", "Liga I"): "soccer_romania_liga_1",
    ("Russia", "Premier League"): "soccer_russia_premier_league",
    ("Chile", "Primera Division"): "soccer_chile_campeonato",
    ("Uruguay", "Primera Division"): "soccer_uruguay_primera_division",
    ("Colombia", "Primera A"): "soccer_colombia_primera_a",
    ("Peru", "Liga 1"): "soccer_peru_primera_division",
}


def sport_key_para(pais, liga_nombre, deportes_activos=None, corte=0.7):
    """
    Busca la sport_key para (pais, liga). Exige coincidencia exacta de
    pais y busqueda difusa del nombre de la liga (los nombres de
    API-Football y The Odds API no siempre coinciden letra por letra).

    Si se pasa 'deportes_activos' (set de keys confirmadas como activas
    ahora mismo, ver fetch_data.obtener_deportes_odds_api), se descarta
    cualquier key que ya no este activa -- evita gastar una peticion en
    una liga que The Odds API dejo de ofrecer.
    """
    candidatas = {liga: key for (p, liga), key in MAPEO_LIGA_A_SPORT_KEY.items() if p == pais}
    if not candidatas:
        return None

    match = difflib.get_close_matches(liga_nombre, list(candidatas.keys()), n=1, cutoff=corte)
    if not match:
        return None

    key = candidatas[match[0]]
    if deportes_activos is not None and key not in deportes_activos:
        return None
    return key
