"""
cuota_odds_api.py
------------------
Lleva la cuenta del cupo de The Odds API (plan free). A diferencia de
API-Football (limite DIARIO fijo, sin info en la respuesta), The Odds
API SI devuelve el cupo real en los headers de cada respuesta
('x-requests-remaining', 'x-requests-used') -- por eso aqui se usa esa
fuente como AUTORIDAD, en vez de solo contar peticiones a ciegas. El
contador local es un respaldo para cuando todavia no se ha hecho ninguna
peticion en la sesion (antes de tener un header real que leer).

IMPORTANTE: el limite del plan free es MENSUAL, no diario (a diferencia
de API-Football). LIMITE_MENSUAL_RESPALDO es un valor de referencia
(confirma el numero exacto en tu dashboard de The Odds API) usado SOLO
como respaldo si por algun motivo no se pudo leer el header real.
"""

import json
import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_USO = DATA_DIR / "uso_odds_api.json"

LIMITE_MENSUAL_RESPALDO = 500  # ajustar segun lo que confirmes en tu dashboard


def _mes_actual():
    return datetime.date.today().strftime("%Y-%m")


def _cargar_estado():
    mes = _mes_actual()
    if ARCHIVO_USO.exists():
        try:
            estado = json.loads(ARCHIVO_USO.read_text(encoding="utf-8"))
            if estado.get("mes") == mes:
                return estado
        except Exception:
            pass
    return {"mes": mes, "usadas_estimadas": 0, "restante_conocido": None, "ultima_actualizacion": None}


def _guardar_estado(estado):
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVO_USO.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def registrar_peticion_sin_headers():
    """Respaldo: si por algun motivo la respuesta no trajo headers de
    cupo, al menos se cuenta localmente la peticion realizada."""
    estado = _cargar_estado()
    estado["usadas_estimadas"] = estado.get("usadas_estimadas", 0) + 1
    _guardar_estado(estado)


def actualizar_desde_headers(headers):
    """Lee 'x-requests-remaining' / 'x-requests-used' de la respuesta
    real de The Odds API y los guarda como la fuente de verdad del cupo
    -- mas confiable que cualquier conteo local."""
    restante = headers.get("x-requests-remaining")
    usadas = headers.get("x-requests-used")
    if restante is None:
        registrar_peticion_sin_headers()
        return

    estado = _cargar_estado()
    try:
        estado["restante_conocido"] = int(restante)
    except (TypeError, ValueError):
        pass
    if usadas is not None:
        try:
            estado["usadas_estimadas"] = int(usadas)
        except (TypeError, ValueError):
            pass
    estado["ultima_actualizacion"] = datetime.datetime.now().isoformat()
    _guardar_estado(estado)


def cupo_restante():
    """Devuelve el cupo restante conocido (del ultimo header real), o
    una estimacion por resta contra LIMITE_MENSUAL_RESPALDO si todavia
    no se ha leido ningun header real este mes."""
    estado = _cargar_estado()
    if estado.get("restante_conocido") is not None:
        return estado["restante_conocido"]
    return max(0, LIMITE_MENSUAL_RESPALDO - estado.get("usadas_estimadas", 0))


def hay_cupo_suficiente(margen=30):
    """Deja SIEMPRE un margen sin tocar (por defecto 30 peticiones) para
    no arriesgarse a dejar el mes en cero por un solo dia con muchas
    ligas -- el cupo mensual debe alcanzar para el resto del mes."""
    return cupo_restante() > margen
