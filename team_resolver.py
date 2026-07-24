"""
team_resolver.py
------------------
Resuelve, para cada equipo, su PAÍS REAL (no el país de la liga del
fixture) y lo cachea para siempre -- el país de un club no cambia, así
que esta es una petición que se paga una sola vez por equipo en toda la
vida del proyecto.

Por qué esto reemplaza el filtro anterior (país de la liga):
El código original filtraba el Elo por el país de la LIGA del fixture
(f["league"]["country"]). Eso funciona en ligas domésticas, pero se
rompe en torneos internacionales (Copa Libertadores, Champions League,
Sudamericana...) donde la liga puede reportar "World" o el país sede,
mientras cada equipo pertenece en realidad a un país distinto. Resolver
el país POR EQUIPO (vía team_id) corrige esto de raíz.

Verificación cruzada (opción B, confirmada explícitamente):
Si uno de los dos equipos de un partido no logra emparejarse con
confianza dentro de su propio país, se usa el país/confederación YA
resuelto del RIVAL como filtro adicional -- se descartan candidatos de
ClubElo que no pertenezcan a la misma confederación que el rival, en vez
de aceptar "el nombre más parecido" a ciegas en una búsqueda global.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_CACHE = DATA_DIR / "team_country_cache.json"

# Mapeo de país -> confederación, usado SOLO para la verificación cruzada
# (opción B): no hace falta que sea exhaustivo, solo lo suficientemente
# bueno para descartar candidatos evidentemente fuera de lugar (ej. un
# club europeo emparejado por error con un rival sudamericano en una
# competición de CONMEBOL).
CONFEDERACION_POR_PAIS = {
    # CONMEBOL
    "Brazil": "CONMEBOL", "Argentina": "CONMEBOL", "Uruguay": "CONMEBOL",
    "Paraguay": "CONMEBOL", "Chile": "CONMEBOL", "Colombia": "CONMEBOL",
    "Ecuador": "CONMEBOL", "Peru": "CONMEBOL", "Bolivia": "CONMEBOL",
    "Venezuela": "CONMEBOL",
    # UEFA (lista no exhaustiva, cubre las principales)
    "England": "UEFA", "Spain": "UEFA", "Italy": "UEFA", "Germany": "UEFA",
    "France": "UEFA", "Portugal": "UEFA", "Netherlands": "UEFA",
    "Belgium": "UEFA", "Scotland": "UEFA", "Turkey": "UEFA", "Greece": "UEFA",
    "Russia": "UEFA", "Ukraine": "UEFA", "Poland": "UEFA", "Austria": "UEFA",
    "Switzerland": "UEFA", "Sweden": "UEFA", "Norway": "UEFA", "Denmark": "UEFA",
    "Croatia": "UEFA", "Serbia": "UEFA", "Romania": "UEFA",
    # CONCACAF
    "Mexico": "CONCACAF", "USA": "CONCACAF", "Costa-Rica": "CONCACAF",
    "Honduras": "CONCACAF", "Panama": "CONCACAF",
    # AFC / CAF (básicos)
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


# --- Presupuesto de peticiones nuevas por corrida ---------------------
# CORRECCIÓN IMPORTANTE (detectada en producción): la primera versión
# llamaba a la API para resolver el país de CADA equipo de CADA partido
# del día, sin importar si hacía falta -- con cientos de fixtures en el
# mundo, eso agotaba el cupo diario de 100 peticiones en la primera
# corrida (error 429). La corrección real está en seleccionar_partidos.py
# (solo se llama a esta función cuando la liga NO es doméstica reconocida,
# es decir, el caso real que motivó el cambio: torneos internacionales).
# Este tope es una segunda red de seguridad, no la solución principal:
# aunque un día haya muchísimos partidos internacionales, nunca se debe
# gastar más de LIMITE_RESOLUCIONES_POR_CORRIDA peticiones solo en esto.
LIMITE_RESOLUCIONES_POR_CORRIDA = 25

_contador_resoluciones_esta_corrida = 0


def resetear_contador_corrida():
    global _contador_resoluciones_esta_corrida
    _contador_resoluciones_esta_corrida = 0


def resolver_pais_equipo(team_id, nombre_fallback, obtener_info_equipo_fn):
    """
    Devuelve el país real del equipo. Usa caché en disco; si no está
    cacheado, paga 1 petición (obtener_info_equipo_fn) y lo guarda para
    siempre -- PERO solo si todavía hay presupuesto disponible esta
    corrida (ver LIMITE_RESOLUCIONES_POR_CORRIDA) y cupo real restante
    del día. Si no hay presupuesto, devuelve None de inmediato SIN pagar
    ninguna petición -- el llamador cae de vuelta al comportamiento
    anterior (país de la liga, marcado como "sin verificar").
    """
    global _contador_resoluciones_esta_corrida

    cache = _cargar()
    entrada = cache.get(str(team_id))
    if entrada:
        return entrada["pais"]

    if _contador_resoluciones_esta_corrida >= LIMITE_RESOLUCIONES_POR_CORRIDA:
        return None

    try:
        from cuota_api_football import uso_de_hoy
        _usadas, disponibles = uso_de_hoy()
        if disponibles <= 15:  # deja margen para el resto de la Fase 1 y otras fases del día
            return None
    except Exception:
        pass  # si no se puede leer el contador local, seguimos con el tope de corrida como única red

    try:
        info = obtener_info_equipo_fn(team_id)
    except Exception as e:
        print(f"[AVISO] No se pudo resolver el país del equipo {nombre_fallback} (id {team_id}): {e}")
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

      1. Si se conoce pais_equipo Y ese país está en la tabla de ClubElo:
         búsqueda restringida a ESE país (el caso normal, más confiable).
      2. Si no se conoce pais_equipo pero sí pais_rival (verificación
         cruzada, opción B): búsqueda restringida a los países que
         comparten confederación con el rival, en vez de una búsqueda
         totalmente libre.
      3. Si no hay ninguna pista de país: búsqueda global (como antes),
         marcada como no verificada.

    Devuelve (elo, pais_verificado: bool, metodo: str)
    """
    if pais_equipo and pais_equipo in elo_por_pais:
        candidatos = list(elo_por_pais[pais_equipo].keys())
        match = buscar_similar_fn(nombre, candidatos, n=1, corte=0.6)
        if match:
            return elo_por_pais[pais_equipo][match[0]], True, "pais_propio"

    if pais_rival:
        confed_rival = confederacion_de(pais_rival)
        if confed_rival:
            paises_confed = [p for p, c in CONFEDERACION_POR_PAIS.items() if c == confed_rival]
            candidatos = []
            mapa_candidato_a_pais = {}
            for p in paises_confed:
                for club in elo_por_pais.get(p, {}):
                    candidatos.append(club)
                    mapa_candidato_a_pais[club] = p
            match = buscar_similar_fn(nombre, candidatos, n=1, corte=0.6)
            if match:
                pais_encontrado = mapa_candidato_a_pais[match[0]]
                return elo_por_pais[pais_encontrado][match[0]], True, "verificacion_cruzada"

    candidatos_global = list(elo_global.keys())
    match = buscar_similar_fn(nombre, candidatos_global, n=1, corte=0.6)
    if match:
        return elo_global[match[0]], False, "global_sin_verificar"

    return None, False, "sin_match"
