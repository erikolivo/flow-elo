"""
ratings_store.py
-----------------
Guarda y actualiza el "rating propio" (Glicko-2) de cada equipo que el
sistema va conociendo, y decide cómo MEZCLARLO con el Elo de ClubElo.

Decisión de diseño (confirmada explícitamente): ClubElo es la SEMILLA de
arranque, nunca se reemplaza por completo -- pero a medida que el
sistema va observando partidos reales del equipo, el peso se desplaza
hacia el rating propio. Se prioriza cobertura rápida sobre pureza
estadística: un equipo con 1 solo partido observado YA aporta al blend,
no hay que esperar a que "madure" para empezar a usarlo.

Tabla de pesos (rating propio) según partidos propios observados (n):
    n == 0        ->   0%  (kilómetro cero: puro ClubElo)
    n in 1..3     ->  20%
    n in 4..8     ->  50%
    n in 9..15    ->  75%
    n > 15        -> 100%  (ClubElo se sigue guardando como respaldo,
                             por si el equipo deja de jugar y su RD
                             vuelve a subir)

Identidad de equipo: se usa el team_id de API-Football como llave
primaria (estable y sin ambigüedad de nombres). Para equipos que solo
se conocen por ClubElo/football-data.co.uk (sin team_id todavía), se
usa la llave "pais|nombre" hasta que aparezcan en un fixture y se les
pueda asignar su team_id real.
"""

import json
import datetime
from pathlib import Path

import glicko2

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_RATINGS = DATA_DIR / "ratings_propios.json"

TRAMOS_PESO = [
    (0, 0.0),
    (3, 0.20),
    (8, 0.50),
    (15, 0.75),
]
PESO_MAXIMO = 1.0  # para n > 15


def peso_rating_propio(n_partidos):
    """Devuelve qué fracción (0-1) del rating final debe venir del
    rating propio (Glicko-2), según cuántos partidos propios ya se
    observaron. El resto viene de ClubElo/Goal Index."""
    for tope, peso in TRAMOS_PESO:
        if n_partidos <= tope:
            return peso
    return PESO_MAXIMO


def _cargar():
    if ARCHIVO_RATINGS.exists():
        try:
            return json.loads(ARCHIVO_RATINGS.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"equipos": {}}


def _guardar(datos):
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVO_RATINGS.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def llave_equipo(team_id, pais=None, nombre=None):
    if team_id:
        return f"id:{team_id}"
    return f"np:{pais or '?'}|{nombre or '?'}"


def obtener_o_crear(llave, nombre=None, pais=None, liga=None):
    datos = _cargar()
    equipo = datos["equipos"].get(llave)
    if equipo is None:
        equipo = {
            "nombre": nombre, "pais": pais, "liga": liga,
            "rating": glicko2.RATING_BASE, "rd": glicko2.RD_INICIAL, "vol": glicko2.VOL_INICIAL,
            "partidos_jugados": 0, "partidos_bootstrap": 0, "partidos_reales": 0,
            "ultima_actualizacion": None,
        }
        datos["equipos"][llave] = equipo
        _guardar(datos)
    return equipo


def actualizar_tras_partido(llave, rating_rival, rd_rival, resultado, es_bootstrap=False, fecha=None):
    """
    resultado: 1.0 victoria, 0.5 empate, 0.0 derrota (desde el punto de
    vista del equipo 'llave').
    es_bootstrap: True si el partido viene del histórico de carga inicial
    (no de un partido vigilado en vivo por el sistema) -- se cuenta
    aparte en 'partidos_bootstrap' para poder distinguir en el reporte
    cuánta madurez viene de datos reales vigilados vs. carga histórica.
    """
    datos = _cargar()
    eq = datos["equipos"].get(llave)
    if eq is None:
        eq = obtener_o_crear(llave)
        datos = _cargar()
        eq = datos["equipos"][llave]

    nuevo_rating, nuevo_rd, nuevo_vol = glicko2.actualizar_rating(
        eq["rating"], eq["rd"], eq["vol"], [(rating_rival, rd_rival, resultado)]
    )
    eq["rating"], eq["rd"], eq["vol"] = nuevo_rating, nuevo_rd, nuevo_vol
    eq["partidos_jugados"] = eq.get("partidos_jugados", 0) + 1
    if es_bootstrap:
        eq["partidos_bootstrap"] = eq.get("partidos_bootstrap", 0) + 1
    else:
        eq["partidos_reales"] = eq.get("partidos_reales", 0) + 1
    eq["ultima_actualizacion"] = (fecha or datetime.date.today().isoformat())

    datos["equipos"][llave] = eq
    _guardar(datos)
    return eq


def rating_combinado(llave, elo_clubelo, nombre=None, pais=None, liga=None):
    """
    LA PIEZA CENTRAL DEL BLEND: devuelve el rating final a usar para el
    partido (en la MISMA escala que Elo, ~1500-2100), combinando ClubElo
    (semilla) con el rating propio según cuántos partidos propios ya se
    observaron -- ver tabla de pesos arriba.

    Si no hay elo_clubelo disponible para este equipo, se usa
    directamente el rating propio (aunque siga en 1500/RD=350 si es un
    equipo totalmente nuevo -- eso es correcto: sin ninguna fuente, el
    sistema debe admitir que no sabe nada de él, y el propio Glicko-2 ya
    refleja esa incertidumbre a través de su RD).

    Devuelve: (rating_final, n_partidos_propios, rd_propio)
    """
    eq = obtener_o_crear(llave, nombre=nombre, pais=pais, liga=liga)
    n = eq.get("partidos_reales", 0) + eq.get("partidos_bootstrap", 0)
    peso_propio = peso_rating_propio(n)

    if elo_clubelo is None:
        return eq["rating"], n, eq["rd"]

    rating_final = peso_propio * eq["rating"] + (1 - peso_propio) * elo_clubelo
    return round(rating_final, 2), n, eq["rd"]


def rd_de(llave):
    eq = obtener_o_crear(llave)
    return eq["rd"]


def migrar_bootstrap_a_id(nombre, team_id, liga=None, corte=0.85):
    """
    Cuando un equipo que fue cargado por bootstrap (llave 'boot:liga|nombre',
    ver bootstrap_ligas.py) aparece por primera vez en un fixture real con
    su team_id de API-Football, hay que FUSIONAR ambos registros -- si no,
    el sistema "olvidaría" todo el historial cargado y ese equipo volvería
    a arrancar en 1500/RD=350 la primera vez que se ve en vivo, perdiendo
    justamente el beneficio del bootstrap.

    Busca una entrada 'boot:*' cuyo nombre coincida de forma muy cercana
    (corte alto a propósito: mejor no fusionar que fusionar mal) y, si la
    encuentra, copia su rating/rd/vol/contadores a la llave definitiva
    'id:<team_id>'. La entrada bootstrap original se conserva (marcada
    como fusionada) para trazabilidad, no se borra.
    """
    import difflib as _difflib

    llave_final = llave_equipo(team_id)
    datos = _cargar()
    if llave_final in datos["equipos"] and datos["equipos"][llave_final].get("partidos_jugados", 0) > 0:
        return  # ya tiene vida propia, no pisar con el bootstrap

    candidatos = {
        k: v for k, v in datos["equipos"].items()
        if k.startswith("boot:") and not v.get("_fusionado")
        and (liga is None or v.get("liga") == liga)
    }
    if not candidatos:
        return

    nombres = {k: v.get("nombre", "") for k, v in candidatos.items()}
    match = _difflib.get_close_matches(nombre, list(nombres.values()), n=1, cutoff=corte)
    if not match:
        return

    llave_bootstrap = next(k for k, v in nombres.items() if v == match[0])
    origen = datos["equipos"][llave_bootstrap]

    datos["equipos"][llave_final] = {
        "nombre": nombre, "pais": origen.get("pais"), "liga": origen.get("liga"),
        "rating": origen["rating"], "rd": origen["rd"], "vol": origen["vol"],
        "partidos_jugados": origen.get("partidos_jugados", 0),
        "partidos_bootstrap": origen.get("partidos_bootstrap", 0),
        "partidos_reales": 0,
        "ultima_actualizacion": origen.get("ultima_actualizacion"),
    }
    datos["equipos"][llave_bootstrap]["_fusionado"] = llave_final
    _guardar(datos)
    print(f"[INFO] Rating de bootstrap fusionado para '{nombre}' (desde '{match[0]}').")
