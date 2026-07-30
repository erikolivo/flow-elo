"""
cuotas_reales.py
------------------
Orquesta el uso de The Odds API para detectar favoritos por CUOTA REAL
(no proxy), y cruzarlos contra los fixtures del dia (API-Football) para
poder inyectarlos al mismo flujo de seleccion y vigilancia en vivo.

Filosofia (igual que con API-Football): nunca gastar una peticion si se
puede evitar. Por eso:
  - Solo se consultan las ligas que TIENEN partidos hoy en tu seleccion
    de fixtures (nunca "todas las ligas" a ciegas).
  - Se valida cada sport_key contra el listado de deportes activos
    (gratis) antes de gastar una peticion real en ella.
  - Tope diario configurable (TOPE_LIGAS_POR_DIA) para no arriesgar el
    cupo MENSUAL en un solo dia con muchas ligas.
  - Se corta de inmediato si el cupo restante baja del margen de
    seguridad (ver cuota_odds_api.hay_cupo_suficiente).

Si ODDS_API_KEY no esta configurada, todo este modulo se comporta como
si no existiera (devuelve resultados vacios) -- el resto del sistema
sigue funcionando exactamente igual que antes.
"""

from fetch_data import obtener_deportes_odds_api, obtener_cuotas_liga, buscar_equipo_similar, ODDS_API_KEY
from mapeo_ligas_odds_api import sport_key_para
import cuota_odds_api

TOPE_LIGAS_POR_DIA = 20
UMBRAL_FAVORITO_CUOTA_REAL = 0.60  # mismo umbral que el modelo propio, para comparar manzanas con manzanas


def _favorito_desde_evento(evento):
    """
    A partir de un evento de The Odds API (con sus bookmakers), calcula
    el lado favorito (home/away, se ignora el empate para esta decision,
    igual que el resto del sistema) y su probabilidad implicita
    (1/cuota), SIN quitar el margen de la casa (overround) -- es una
    aproximacion simple a proposito, igual de simple que el resto de
    proxies del proyecto.
    Devuelve (lado, probabilidad_implicita, cuota, casa_apuestas) o None
    si el evento no trae mercado h2h utilizable.
    """
    bookmakers = evento.get("bookmakers", [])
    if not bookmakers:
        return None

    bk = bookmakers[0]  # primera casa disponible en la region pedida
    mercado_h2h = next((m for m in bk.get("markets", []) if m.get("key") == "h2h"), None)
    if not mercado_h2h:
        return None

    precios = {o["name"]: o["price"] for o in mercado_h2h.get("outcomes", [])}
    home_team = evento.get("home_team")
    away_team = evento.get("away_team")
    cuota_home = precios.get(home_team)
    cuota_away = precios.get(away_team)
    if not cuota_home or not cuota_away:
        return None

    if cuota_home <= cuota_away:
        lado, cuota = "local", cuota_home
    else:
        lado, cuota = "visitante", cuota_away

    probabilidad = round(1 / cuota, 4)
    return lado, probabilidad, cuota, bk.get("title", "?")


def obtener_favoritos_cuota_real(fixtures_api):
    """
    Devuelve un dict {fixture_id: {"lado":..., "probabilidad":...,
    "cuota":..., "casa_apuestas":...}} para los partidos de HOY donde la
    cuota real muestra un favorito claro (>=60% implicito). Vacio si
    ODDS_API_KEY no esta configurada, si no hay cupo, o si ninguna liga
    de hoy esta cubierta por el plan.
    """
    if not ODDS_API_KEY:
        return {}

    if not cuota_odds_api.hay_cupo_suficiente():
        print("[AVISO] Cupo de The Odds API insuficiente este mes, se omite la verificacion por cuota real.")
        return {}

    # Ligas distintas presentes en los fixtures de hoy (pais, nombre_liga)
    ligas_hoy = {}
    for f in fixtures_api:
        pais = f.get("league", {}).get("country", "")
        liga = f.get("league", {}).get("name", "")
        ligas_hoy.setdefault((pais, liga), []).append(f)

    deportes_activos = obtener_deportes_odds_api()  # gratis, valida antes de gastar cupo

    resultado = {}
    ligas_consultadas = 0

    for (pais, liga), fixtures_de_la_liga in ligas_hoy.items():
        if ligas_consultadas >= TOPE_LIGAS_POR_DIA:
            print(f"[INFO] Tope diario de {TOPE_LIGAS_POR_DIA} ligas para The Odds API alcanzado, "
                  f"se omiten las ligas restantes hoy.")
            break
        if not cuota_odds_api.hay_cupo_suficiente():
            print("[AVISO] Cupo de The Odds API se agoto durante esta corrida, se detiene aqui.")
            break

        sport_key = sport_key_para(pais, liga, deportes_activos)
        if not sport_key:
            continue  # liga no cubierta por el plan o no mapeada -- no cuesta nada, se salta

        try:
            eventos = obtener_cuotas_liga(sport_key)
        except Exception as e:
            print(f"[AVISO] No se pudo obtener cuotas de {pais} - {liga} ({sport_key}): {e}")
            continue
        ligas_consultadas += 1

        nombres_fixtures_liga = {}
        for f in fixtures_de_la_liga:
            nombres_fixtures_liga[f["teams"]["home"]["name"]] = f
            nombres_fixtures_liga[f["teams"]["away"]["name"]] = f

        for evento in eventos:
            favorito = _favorito_desde_evento(evento)
            if not favorito:
                continue
            lado, probabilidad, cuota, casa = favorito
            if probabilidad < UMBRAL_FAVORITO_CUOTA_REAL:
                continue

            # Cruce con el fixture real de API-Football (necesitamos el
            # fixture_id para poder vigilarlo en vivo despues)
            home_odds = evento.get("home_team", "")
            away_odds = evento.get("away_team", "")
            match_home = buscar_equipo_similar(home_odds, list(nombres_fixtures_liga.keys()), n=1, corte=0.75)
            match_away = buscar_equipo_similar(away_odds, list(nombres_fixtures_liga.keys()), n=1, corte=0.75)
            fixture = None
            if match_home:
                fixture = nombres_fixtures_liga[match_home[0]]
            elif match_away:
                fixture = nombres_fixtures_liga[match_away[0]]
            if not fixture:
                continue  # no se pudo cruzar con un fixture real -- sin fixture_id no se puede vigilar en vivo

            resultado[fixture["fixture"]["id"]] = {
                "lado": lado, "probabilidad": probabilidad, "cuota": cuota, "casa_apuestas": casa,
            }

    print(f"Cuotas reales: {ligas_consultadas} liga(s) consultada(s), "
          f"{len(resultado)} favorito(s) claro(s) detectado(s) por cuota real.")
    return resultado
