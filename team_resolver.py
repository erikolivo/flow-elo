"""
team_resolver.py
------------------
Resuelve, para cada equipo, su PAIS REAL (no el pais de la liga del
fixture) y lo cachea para siempre -- el pais de un club no cambia, asi
que esta es una peticion que se paga como maximo una vez por equipo en
toda la vida del proyecto (y en la practica, mucho menos: ver el ahorro
por liga domestica y por Goal Index en seleccionar_partidos.py).

CORRECCION IMPORTANTE (encontrada en produccion): la version anterior
comparaba el pais del equipo (en ingles, ej. "England") directamente
contra las llaves de elo_por_pais -- pero esas llaves vienen del CSV de
ClubElo y usan CODIGOS DE 3 LETRAS (ej. "ENG"), nunca el nombre en
ingles. Esto significaba que el emparejamiento "por pais propio" NUNCA
coincidia, ni siquiera para partidos 100% domesticos -- todo terminaba
cayendo en la busqueda global sin verificar. Se corrige convirtiendo
SIEMPRE el pais (ingles) a su codigo de ClubElo antes de indexar
elo_por_pais. PAIS_A_CODIGO_CLUBELO ahora vive aqui (antes estaba
duplicado en seleccionar_partidos.py) para que exista una sola fuente
de verdad de esa conversion.

Verificacion cruzada (opcion B, confirmada explicitamente):
Si uno de los dos equipos de un partido no logra emparejarse con
confianza dentro de su propio pais, se usa el pais/confederacion YA
resuelto del RIVAL como filtro adicional -- se descartan candidatos de
ClubElo que no pertenezcan a la misma confederacion que el rival, en vez
de aceptar "el nombre mas parecido" a ciegas en una busqueda global.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_CACHE = DATA_DIR / "team_country_cache.json"

# Pais (como lo reporta la liga en API-Football, en texto en ingles) ->
# codigo de 3 letras que usa ClubElo. Unica fuente de esta conversion en
# todo el proyecto -- si un pais no esta aqui, no se puede indexar
# elo_por_pais para el, y el llamador debe caer al comportamiento de
# respaldo (busqueda global, marcada como no verificada).
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

# Pais -> confederacion, usado SOLO para la verificacion cruzada (opcion
# B): no hace falta que sea exhaustivo, solo lo suficientemente bueno
# para descartar candidatos evidentemente fuera de lugar.
CONFEDERACION_POR_PAIS = {
    "Brazil": "CONMEBOL", "Argentina": "CONMEBOL", "Uruguay": "CONMEBOL",
    "Paraguay": "CONMEBOL", "Chile": "CONMEBOL", "Colombia": "CONMEBOL",
    "Ecuador": "CONMEBOL", "Peru": "CONMEBOL", "Bolivia": "CONMEBOL",
    "Venezuela": "CONMEBOL",
    "England": "UEFA", "Spain": "UEFA", "Italy": "UEFA", "Germany": "UEFA",
    "France": "UEFA", "Portugal": "UEFA", "Netherlands": "UEFA",
    "Belgium": "UEFA", "Scotland": "UEFA", "Turkey": "UEFA", "Greece": "UEFA",
    "Russia": "UEFA", "Ukraine": "UEFA", "Poland": "UEFA", "Austria": "UEFA",
    "Switzerland": "UEFA", "Sweden": "UEFA", "Norway": "UEFA", "Denmark": "UEFA",
    "Croatia": "UEFA", "Serbia": "UEFA", "Romania": "UEFA",
    "Mexico": "CONCACAF", "USA": "CONCACAF", "Costa-Rica": "CONCACAF",
    "Honduras": "CONCACAF", "Panama": "CONCACAF",
    "Japan": "AFC", "South-Korea": "AFC", "China": "AFC", "Saudi-Arabia": "AFC",
    "Qatar": "AFC", "Egypt": "CAF", "South-Africa": "CAF", "Morocco": "CAF",
}


def _cargar():
    if ARCHIVO_CACHE.exists():
        try:
            return json.loads(ARCHIVO_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _guardar(cache):
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVO_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def confederacion_de(pais):
    return CONFEDERACION_POR_PAIS.get(pais)


def codigo_clubelo_de(pais):
    return PAIS_A_CODIGO_CLUBELO.get(pais)


# --- Presupuesto de peticiones nuevas por corrida ---------------------
# CORRECCION IMPORTANTE (detectada en produccion): la primera version
# llamaba a la API para resolver el pais de CADA equipo de CADA partido
# del dia, sin importar si hacia falta -- con cientos de fixtures en el
# mundo, eso agotaba el cupo diario de 100 peticiones en la primera
# corrida (error 429). La correccion real esta en seleccionar_partidos.py
# (solo se llama a esta funcion cuando la liga NO es domestica reconocida
# NI se pudo inferir gratis via Goal Index -- ver ese archivo). Este tope
# es una segunda red de seguridad, configurable por corrida: el llamador
# (seleccionar_partidos.py) decide el presupuesto real disponible segun
# el limite total acordado (50 peticiones para toda la Fase 1).
LIMITE_RESOLUCIONES_POR_CORRIDA_DEFECTO = 25

_limite_efectivo_esta_corrida = LIMITE_RESOLUCIONES_POR_CORRIDA_DEFECTO
_contador_resoluciones_esta_corrida = 0


def resetear_contador_corrida(limite=None):
    """Reinicia el contador de resoluciones-por-API al empezar una
    corrida de Fase 1. 'limite' permite que el llamador fije el
    presupuesto real de esta corrida (ver TOPE_PETICIONES_FASE1 en
    seleccionar_partidos.py); si no se pasa, usa el valor por defecto."""
    global _contador_resoluciones_esta_corrida, _limite_efectivo_esta_corrida
    _contador_resoluciones_esta_corrida = 0
    _limite_efectivo_esta_corrida = limite if limite is not None else LIMITE_RESOLUCIONES_POR_CORRIDA_DEFECTO


def resolver_pais_equipo(team_id, nombre_fallback, obtener_info_equipo_fn):
    """
    Devuelve el pais real del equipo. Usa cache en disco; si no esta
    cacheado, paga 1 peticion (obtener_info_equipo_fn) y lo guarda para
    siempre -- PERO solo si todavia hay presupuesto disponible esta
    corrida (ver resetear_contador_corrida) y cupo real restante del
    dia. Si no hay presupuesto, devuelve None de inmediato SIN pagar
    ninguna peticion -- el llamador cae de vuelta al comportamiento
    anterior (pais de la liga o Goal Index, marcado como "sin verificar"
    si ninguno de los dos aplica).
    """
    global _contador_resoluciones_esta_corrida

    cache = _cargar()
    entrada = cache.get(str(team_id))
    if entrada:
        return entrada["pais"]

    if _contador_resoluciones_esta_corrida >= _limite_efectivo_esta_corrida:
        return None

    try:
        from cuota_api_football import uso_de_hoy
        _usadas, disponibles = uso_de_hoy()
        if disponibles <= 10:  # deja margen para el resto del dia (Fase 3/4)
            return None
    except Exception:
        pass  # si no se puede leer el contador local, seguimos solo con el tope de corrida

    try:
        info = obtener_info_equipo_fn(team_id)
    except Exception as e:
        print(f"[AVISO] No se pudo resolver el pais del equipo {nombre_fallback} (id {team_id}): {e}")
        _contador_resoluciones_esta_corrida += 1
        return None

    _contador_resoluciones_esta_corrida += 1

    if not info:
        return None

    pais = info.get("country")
    cache[str(team_id)] = {"nombre": nombre_fallback, "pais": pais}
    _guardar(cache)
    return pais


def elegir_candidato_verificado(nombre, pais_equipo, elo_por_pais, elo_global, buscar_similar_fn,
                                 pais_rival=None):
    """
    Busca el mejor candidato de ClubElo para 'nombre', en este orden:

      1. Si se conoce pais_equipo (nombre en ingles) Y su codigo de
         ClubElo tiene tabla propia: busqueda restringida a ESE pais.
      2. Si no se conoce pais_equipo pero si pais_rival (verificacion
         cruzada, opcion B): busqueda restringida a los paises que
         comparten confederacion con el rival.
      3. Si no hay ninguna pista de pais: busqueda global, marcada como
         no verificada.

    Devuelve (elo, pais_verificado: bool, metodo: str)
    """
    codigo_equipo = codigo_clubelo_de(pais_equipo) if pais_equipo else None
    if codigo_equipo and codigo_equipo in elo_por_pais:
        candidatos = list(elo_por_pais[codigo_equipo].keys())
        match = buscar_similar_fn(nombre, candidatos, n=1, corte=0.6)
        if match:
            return elo_por_pais[codigo_equipo][match[0]], True, "pais_propio"

    if pais_rival:
        confed_rival = confederacion_de(pais_rival)
        if confed_rival:
            paises_confed = [p for p, c in CONFEDERACION_POR_PAIS.items() if c == confed_rival]
            candidatos = []
            mapa_candidato_a_codigo = {}
            for p in paises_confed:
                codigo_p = codigo_clubelo_de(p)
                if not codigo_p:
                    continue
                for club in elo_por_pais.get(codigo_p, {}):
                    candidatos.append(club)
                    mapa_candidato_a_codigo[club] = codigo_p
            match = buscar_similar_fn(nombre, candidatos, n=1, corte=0.6)
            if match:
                codigo_encontrado = mapa_candidato_a_codigo[match[0]]
                return elo_por_pais[codigo_encontrado][match[0]], True, "verificacion_cruzada"

    candidatos_global = list(elo_global.keys())
    match = buscar_similar_fn(nombre, candidatos_global, n=1, corte=0.6)
    if match:
        return elo_global[match[0]], False, "global_sin_verificar"

    return None, False, "sin_match"
