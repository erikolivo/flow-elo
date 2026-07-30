"""
monitor.py
----------
FASE 3, versión 4 -- REDISEÑO COMPLETO del motor de alertas.

Motivo del rediseño (confirmado con casos reales): la versión anterior
mezclaba la expectativa PRE-PARTIDO (Elo+Goal Index) con la actividad
reciente en una sola fórmula, lo que causaba alertas de "el favorito va
a meter gol" cuando en realidad el RIVAL era quien dominaba el juego en
ese momento. Ver momentum.py para el detalle de la corrección.

Cambios de esta versión:

  - MOMENTUM EN VIVO (momentum.py), no probabilidad pre-partido. La
    expectativa inicial solo se muestra como contexto informativo en el
    mensaje, nunca decide la alerta.
  - ZONA DE PARIDAD: cuando ambos equipos generan peligro real y ninguno
    domina claramente (momentum entre 35%-65%), se manda un mensaje
    honesto de "partido abierto" en vez de forzar un ganador.
  - TARJETA ROJA: nuevo escenario, evento discreto de alto impacto que
    antes se ignoraba a pesar de que ya se recolectaba el dato.
  - PENAL: nuevo escenario basado en eventos del fixture (si el feed los
    expone), altísimo valor por ser el evento de mayor conversión a gol
    del fútbol.
  - VENTANA 15'-75' para los escenarios de momentum general (antes no
    existía ningún límite salvo para 2 de los 7 tipos). Tarjeta roja y
    penal quedan EXENTOS de esta ventana -- son eventos discretos
    valiosos a cualquier minuto. "Gol de cierre" tiene su propia ventana
    extendida (ver abajo).
  - GOL DE CIERRE extendido a 75'-90'+ (incluye tiempo añadido), antes
    se cortaba en el minuto 85 ignorando el descuento, que
    estadísticamente concentra muchos goles.
  - TECHO DE DIFERENCIA: con diferencia >= 3 goles se considera el
    partido prácticamente resuelto y se manda UNA sola alerta de cierre
    de seguimiento, en vez de seguir generando alertas de bajo valor
    (ampliación de marcador, etc.) hasta el final.
  - xG: si el plan de API-Football expone Expected Goals en las
    estadísticas, se usa en vez de la aproximación por tiros a puerta
    (más preciso porque pondera calidad de la ocasión, no solo cantidad).

NOTA DE CUPO: esta versión agrega 1 petición extra por partido por
revisión (eventos, para tarjetas rojas/penales), sobre la que ya existía
de estadísticas. Es un costo aceptado a propósito -- ver el porqué en
fetch_data.py.
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

from fetch_data import (
    obtener_partidos_en_vivo, obtener_estadisticas_fixture, obtener_eventos_fixture, extraer_xg,
)
from telegram_utils import enviar_mensaje_telegram, escapar_html
import momentum

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_PARTIDOS = DATA_DIR / "partidos_hoy.json"
ARCHIVO_ESTADO_MONITOR = DATA_DIR / "estado_monitor.json"

MINUTOS_ANTES_DEL_INICIO = 10
MINUTOS_DURACION_MAXIMA = 130

UMBRAL_POCOS_PARTIDOS = 5
INTERVALO_POCOS = 10
INTERVALO_MUCHOS = 15

MINUTO_MIN_ALERTA_GENERAL = 15
MINUTO_MAX_ALERTA_GENERAL = 75
VENTANA_GOL_DE_CIERRE = (75, 200)  # 200 = sin límite práctico, cubre cualquier tiempo añadido

UMBRAL_GOL_INMINENTE = 0.35
MINUTO_TEMPRANO_1ER_TIEMPO = 30
DOMINANCIA_HISTORICA_MINIMA = 0.75
DIFERENCIA_TECHO = 3


def _en_ventana(kickoff_utc_iso, ahora=None):
    if not kickoff_utc_iso:
        return False
    ahora = ahora or datetime.now(timezone.utc)
    kickoff = datetime.fromisoformat(kickoff_utc_iso.replace("Z", "+00:00"))
    return (kickoff - timedelta(minutes=MINUTOS_ANTES_DEL_INICIO)) <= ahora <= \
           (kickoff + timedelta(minutes=MINUTOS_DURACION_MAXIMA))


def _debe_revisar_ahora(cantidad_en_ventana):
    """
    CORRECCION IMPORTANTE (bug detectado en produccion): la version
    anterior decidia "toca revisar" comparando minuto_actual % intervalo
    == 0. Como el loop interno de Fase 3 avanza de 5 en 5 minutos desde
    un minuto de arranque arbitrario (segun cuando el job realmente
    empieza a correr, no necesariamente alineado a :00), el residuo
    podia quedar atrapado para siempre en un valor que nunca es multiplo
    del intervalo (ej. arrancando en minuto 2: la secuencia 2,7,12,17,22...
    modulo 10 alterna entre 2 y 7 ETERNAMENTE, nunca toca 0) -- el
    resultado real fue que Fase 3 nunca revisaba nada.

    La correccion: en vez de depender del reloj, se guarda la hora real
    de la ULTIMA revision efectiva (data/estado_monitor.json) y se
    compara cuanto tiempo transcurrio de verdad desde entonces. Esto
    funciona sin importar en que minuto arranco el job.
    """
    intervalo_min = INTERVALO_POCOS if cantidad_en_ventana <= UMBRAL_POCOS_PARTIDOS else INTERVALO_MUCHOS
    ahora = datetime.now(timezone.utc)

    estado = {}
    if ARCHIVO_ESTADO_MONITOR.exists():
        try:
            estado = json.loads(ARCHIVO_ESTADO_MONITOR.read_text(encoding="utf-8"))
        except Exception:
            estado = {}

    ultima_str = estado.get("ultima_revision_utc")
    if ultima_str:
        try:
            ultima = datetime.fromisoformat(ultima_str)
            minutos_transcurridos = (ahora - ultima).total_seconds() / 60
            if minutos_transcurridos < intervalo_min:
                return False
        except Exception:
            pass  # estado corrupto: mejor revisar ahora que quedarse atascado

    estado["ultima_revision_utc"] = ahora.isoformat()
    DATA_DIR.mkdir(exist_ok=True)
    ARCHIVO_ESTADO_MONITOR.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _valor_stat(stats_equipo, nombre_stat):
    for item in stats_equipo.get("statistics", []):
        if item.get("type") == nombre_stat:
            v = item.get("value")
            if v is None:
                return 0
            if isinstance(v, str) and v.endswith("%"):
                return float(v.replace("%", ""))
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0
    return 0


def _snapshot(stats_local, stats_visitante, minuto, goles_local=0, goles_visitante=0):
    xg_local = extraer_xg(stats_local)
    xg_visitante = extraer_xg(stats_visitante)
    return {
        "minuto": minuto,
        "goles_local": goles_local,
        "goles_visitante": goles_visitante,
        "tiros_local": _valor_stat(stats_local, "Total Shots"),
        "tiros_visitante": _valor_stat(stats_visitante, "Total Shots"),
        "tiros_puerta_local": _valor_stat(stats_local, "Shots on Goal"),
        "tiros_puerta_visitante": _valor_stat(stats_visitante, "Shots on Goal"),
        "tiros_area_local": _valor_stat(stats_local, "Shots insidebox"),
        "tiros_area_visitante": _valor_stat(stats_visitante, "Shots insidebox"),
        "tiros_fuera_area_local": _valor_stat(stats_local, "Shots outsidebox"),
        "tiros_fuera_area_visitante": _valor_stat(stats_visitante, "Shots outsidebox"),
        "corners_local": _valor_stat(stats_local, "Corner Kicks"),
        "corners_visitante": _valor_stat(stats_visitante, "Corner Kicks"),
        "posesion_local": _valor_stat(stats_local, "Ball Possession"),
        "posesion_visitante": _valor_stat(stats_visitante, "Ball Possession"),
        "rojas_local": _valor_stat(stats_local, "Red Cards"),
        "rojas_visitante": _valor_stat(stats_visitante, "Red Cards"),
        "xg_local": xg_local if xg_local is not None else 0,
        "xg_visitante": xg_visitante if xg_visitante is not None else 0,
        "xg_disponible": xg_local is not None and xg_visitante is not None,
    }


def _domina_snapshot(snap, favorito_es_local):
    if favorito_es_local:
        tiros_fav, tiros_riv = snap["tiros_local"], snap["tiros_visitante"]
        corn_fav, corn_riv = snap["corners_local"], snap["corners_visitante"]
    else:
        tiros_fav, tiros_riv = snap["tiros_visitante"], snap["tiros_local"]
        corn_fav, corn_riv = snap["corners_visitante"], snap["corners_local"]
    return (tiros_fav + corn_fav) > (tiros_riv + corn_riv)


def _dominancia_historica(historial_snapshots, favorito_es_local):
    if not historial_snapshots:
        return 0.0
    dominados = sum(1 for s in historial_snapshots if _domina_snapshot(s, favorito_es_local))
    return dominados / len(historial_snapshots)


def _link_busqueda(nombre_local, nombre_visitante, sitio):
    consulta = f"{nombre_local} vs {nombre_visitante} {sitio}".replace(" ", "+")
    return f"https://www.google.com/search?q={consulta}"


def _sustituciones_recientes(eventos_crudos, equipo_nombre, minuto_actual, ventana=10):
    """
    Cuenta cambios del equipo indicado en los ultimos 'ventana' minutos
    reales. No se puede confirmar si un cambio es ofensivo o defensivo
    sin datos de posicion del jugador (no disponibles en el plan
    gratuito de API-Football) -- se usa solo como senal blanda de
    "hubo actividad tactica reciente" (ver momentum.bonus_sustituciones).
    """
    if minuto_actual is None:
        return 0
    contador = 0
    for ev in eventos_crudos:
        if ev.get("type") != "subst":
            continue
        if ev.get("team", {}).get("name") != equipo_nombre:
            continue
        minuto_evento = ev.get("time", {}).get("elapsed", 0)
        if 0 <= (minuto_actual - minuto_evento) <= ventana:
            contador += 1
    return contador


def _extraer_eventos_nuevos(eventos_crudos, ya_procesados):
    """
    Filtra del feed crudo de eventos SOLO tarjetas rojas y penales que
    todavía no se hayan alertado (evita duplicar la misma alerta en
    revisiones sucesivas). 'ya_procesados' es la lista guardada en
    p['eventos_procesados'] (strings "tipo|equipo|minuto").
    """
    nuevos = []
    for ev in eventos_crudos:
        tipo = ev.get("type", "")
        detalle = (ev.get("detail") or "").lower()
        equipo = ev.get("team", {}).get("name", "")
        minuto = ev.get("time", {}).get("elapsed", 0)

        es_roja = tipo == "Card" and "red" in detalle
        es_penal = "penalty" in detalle  # cubre "Penalty", "Missed Penalty", "Penalty confirmed"

        if not (es_roja or es_penal):
            continue

        firma = f"{'roja' if es_roja else 'penal'}|{equipo}|{minuto}"
        if firma in ya_procesados:
            continue

        nuevos.append({
            "tipo": "tarjeta_roja" if es_roja else "penal",
            "equipo": equipo,
            "minuto": minuto,
            "detalle": detalle,
            "firma": firma,
        })
    return nuevos


def _evaluar_escenarios(p, minuto, goles_local, goles_visitante, snap_actual, snap_anterior,
                         historial_snapshots, eventos_nuevos, eventos_crudos):
    favorito_es_local = p["favorito_es_local"]
    goles_favorito = goles_local if favorito_es_local else goles_visitante
    goles_rival = goles_visitante if favorito_es_local else goles_local
    diferencia = goles_favorito - goles_rival

    lado_favorito = "local" if favorito_es_local else "visitante"
    lado_rival = "visitante" if favorito_es_local else "local"

    xg_disp = snap_actual.get("xg_disponible", False)
    presion_favorito, detalle_favorito = momentum.calcular_presion(snap_actual, snap_anterior, lado_favorito, xg_disp)
    presion_rival, detalle_rival = momentum.calcular_presion(snap_actual, snap_anterior, lado_rival, xg_disp)

    # Señal blanda de sustituciones recientes (ver nota de honestidad en
    # momentum.py: no se puede confirmar si son ofensivas sin datos de
    # posicion del jugador, no disponibles gratis).
    cambios_favorito = _sustituciones_recientes(eventos_crudos, p["favorito"], minuto)
    cambios_rival = _sustituciones_recientes(eventos_crudos, p["no_favorito"], minuto)
    presion_favorito += momentum.bonus_sustituciones(cambios_favorito)
    presion_rival += momentum.bonus_sustituciones(cambios_rival)

    momentum_favorito = momentum.momentum_relativo(presion_favorito, presion_rival)
    zona = momentum.zona_momentum(momentum_favorito)

    prob_gol_favorito = momentum.probabilidad_gol_ventana(snap_actual, snap_anterior, lado_favorito, minuto, xg_disp)
    prob_gol_rival = momentum.probabilidad_gol_ventana(snap_actual, snap_anterior, lado_rival, minuto, xg_disp)

    escenarios = []

    # --- 0. EVENTOS DISCRETOS (tarjeta roja, penal) -- exentos de la
    #        ventana de minutos, siempre tienen prioridad porque son
    #        hechos, no estimaciones ---
    for ev in eventos_nuevos:
        if ev["tipo"] == "tarjeta_roja":
            equipo_con_roja = ev["equipo"]
            es_favorito_con_roja = (equipo_con_roja == p["favorito"])
            # ¿quién venía rindiendo mejor ANTES de la roja? usamos la
            # dominancia acumulada hasta este momento como referencia.
            dominancia_favorito = _dominancia_historica(historial_snapshots, favorito_es_local)
            if es_favorito_con_roja:
                texto = (f"{p['favorito']} se queda con uno menos ({ev['minuto']}'). "
                         f"Venía {'dominando' if dominancia_favorito >= 0.5 else 'sin dominar'} el partido.")
            else:
                texto = (f"{p['no_favorito']} se queda con uno menos ({ev['minuto']}'). "
                         f"{p['favorito']} {'venía dominando' if dominancia_favorito >= 0.5 else 'no dominaba hasta ahora'}.")
            escenarios.append(("tarjeta_roja", 10, 0.9, texto, ev["firma"]))

        elif ev["tipo"] == "penal":
            equipo_penal = ev["equipo"]
            a_favor_favorito = (equipo_penal == p["favorito"])
            texto = f"Penal para {equipo_penal} ({ev['minuto']}') -- altísima probabilidad de gol."
            escenarios.append(("penal", 10, 0.95, texto, ev["firma"]))

    # --- techo de diferencia: partido prácticamente resuelto ---
    if abs(diferencia) >= DIFERENCIA_TECHO and not p.get("marcado_resuelto"):
        texto = f"Diferencia de {abs(diferencia)} goles -- el partido está prácticamente resuelto."
        escenarios.append(("partido_resuelto", 5, 0.5, texto, None))
        return escenarios  # no tiene sentido seguir evaluando nada más

    if p.get("marcado_resuelto"):
        return escenarios  # ya se avisó que el partido está resuelto; solo eventos discretos de arriba aplican

    # --- escenarios de momentum general: solo dentro de la ventana 15-75 ---
    dentro_ventana_general = MINUTO_MIN_ALERTA_GENERAL <= minuto <= MINUTO_MAX_ALERTA_GENERAL

    if dentro_ventana_general:
        # Zona de paridad: ambos generan peligro real, no forzamos un lado.
        if zona == "paridad" and max(prob_gol_favorito, prob_gol_rival) >= UMBRAL_GOL_INMINENTE:
            texto = (f"Partido parejo: {p['favorito']} genera peligro "
                      f"({detalle_favorito['tiros_puerta']:.0f} tiros a puerta recientes) y "
                      f"{p['no_favorito']} también responde "
                      f"({detalle_rival['tiros_puerta']:.0f} tiros a puerta recientes).")
            escenarios.append(("partido_abierto", 2, max(prob_gol_favorito, prob_gol_rival), texto, None))

        elif zona == "favorito":
            if diferencia == -1 and prob_gol_favorito >= UMBRAL_GOL_INMINENTE:
                escenarios.append(("posible_empate", 1, prob_gol_favorito,
                                    f"{p['favorito']} va perdiendo pero domina el momentum (prob. {prob_gol_favorito*100:.0f}%)", None))
            if diferencia == 0:
                if minuto <= MINUTO_TEMPRANO_1ER_TIEMPO and goles_local == 0 and goles_visitante == 0:
                    escenarios.append(("gana_favorito_1er_tiempo", 3, prob_gol_favorito,
                                        f"{p['favorito']} presionando fuerte y temprano, 0-0 (prob. {prob_gol_favorito*100:.0f}%)", None))
                elif prob_gol_favorito >= UMBRAL_GOL_INMINENTE:
                    escenarios.append(("posible_victoria_favorito", 1, prob_gol_favorito,
                                        f"{p['favorito']} domina el momentum empatando (prob. {prob_gol_favorito*100:.0f}%)", None))
            if diferencia >= 1 and prob_gol_favorito >= UMBRAL_GOL_INMINENTE:
                escenarios.append(("ampliacion_marcador", 1, prob_gol_favorito,
                                    f"{p['favorito']} sigue dominando, puede ampliar el marcador (prob. {prob_gol_favorito*100:.0f}%)", None))

        elif zona == "rival":
            if diferencia == 0 and prob_gol_rival >= UMBRAL_GOL_INMINENTE:
                escenarios.append(("posible_gol_no_favorito", 1, prob_gol_rival,
                                    f"{p['no_favorito']} tomó el control del partido (prob. {prob_gol_rival*100:.0f}%)", None))
            if diferencia >= 1 and prob_gol_rival >= UMBRAL_GOL_INMINENTE:
                escenarios.append(("cuidado_rival_presiona", 1, prob_gol_rival,
                                    f"{p['no_favorito']} presionando, puede complicar el resultado (prob. {prob_gol_rival*100:.0f}%)", None))

    # --- gol de cierre: ventana propia extendida, independiente de la general ---
    if VENTANA_GOL_DE_CIERRE[0] <= minuto <= VENTANA_GOL_DE_CIERRE[1] and diferencia in (0, -1):
        dominancia = _dominancia_historica(historial_snapshots, favorito_es_local)
        if dominancia >= DOMINANCIA_HISTORICA_MINIMA:
            escenarios.append(("gol_de_cierre", 3, prob_gol_favorito,
                                f"{p['favorito']} dominó el {dominancia*100:.0f}% del partido y quedan pocos minutos", None))

    return escenarios


MENSAJES_POR_TIPO = {
    "posible_empate": "🟠 Posible empate",
    "posible_victoria_favorito": "🟢 Posible victoria de {favorito}",
    "gana_favorito_1er_tiempo": "⏱️ Gana {favorito} 1er tiempo",
    "posible_gol_no_favorito": "🔴 Posible gol del no favorito",
    "cuidado_rival_presiona": "⚠️ Cuidado, {no_favorito} presionando",
    "ampliacion_marcador": "🔵 Posible ampliación de marcador",
    "gol_de_cierre": "⏰ Posible gol de cierre",
    "partido_abierto": "⚡ Partido abierto, puede caer de cualquier lado",
    "tarjeta_roja": "🟥 Tarjeta roja",
    "penal": "🎯 Penal",
    "partido_resuelto": "🏁 Seguimiento cerrado (diferencia amplia)",
}


def _construir_mensaje(p, tipo, motivo, minuto, goles_local, goles_visitante, snap_actual):
    favorito = escapar_html(p["favorito"])
    no_favorito = escapar_html(p["no_favorito"])
    local = escapar_html(p["local"])
    visitante = escapar_html(p["visitante"])
    motivo_seguro = escapar_html(motivo)

    titulo = MENSAJES_POR_TIPO[tipo].format(favorito=favorito, no_favorito=no_favorito)
    link_besoccer = _link_busqueda(p["local"], p["visitante"], "besoccer")
    link_ecuabet = _link_busqueda(p["local"], p["visitante"], "ecuabet")

    xg_linea = ""
    if snap_actual.get("xg_disponible"):
        xg_linea = f"xG: {snap_actual['xg_local']:.2f}-{snap_actual['xg_visitante']:.2f}\n"

    return (
        f"{titulo}\n\n"
        f"{local} vs {visitante}\n"
        f"Minuto: {minuto} · Marcador: {goles_local}-{goles_visitante}\n\n"
        f"Motivo: {motivo_seguro}\n\n"
        f"📊 Estadísticas:\n"
        f"Tiros: {snap_actual['tiros_local']}-{snap_actual['tiros_visitante']} "
        f"(a puerta: {snap_actual['tiros_puerta_local']}-{snap_actual['tiros_puerta_visitante']})\n"
        f"Córners: {snap_actual['corners_local']}-{snap_actual['corners_visitante']}\n"
        f"Posesión: {snap_actual['posesion_local']:.0f}%-{snap_actual['posesion_visitante']:.0f}%\n"
        f"{xg_linea}\n"
        f"Contexto pre-partido: favorito {favorito} (expectativa inicial {p['probabilidad_inicial']}%, "
        f"cuota proxy {p['cuota_inicial']}) -- esto NO decide la alerta, solo es referencia.\n\n"
        f"Ver en BeSoccer: {link_besoccer}\n"
        f"Ver en Ecuabet: {link_ecuabet}"
    )


def revisar():
    if not ARCHIVO_PARTIDOS.exists():
        print("No hay partidos_hoy.json todavía (Fase 1 no ha corrido con éxito hoy).")
        return

    datos = json.loads(ARCHIVO_PARTIDOS.read_text(encoding="utf-8"))
    con_fixture = [p for p in datos["partidos"] if p["fixture_id"] is not None]
    en_ventana = [p for p in con_fixture if _en_ventana(p.get("kickoff_utc"))]

    if not en_ventana:
        print("Ningún partido vigilado está en su ventana horaria ahora (0 peticiones gastadas).")
        return

    if not _debe_revisar_ahora(len(en_ventana)):
        print(f"Frecuencia adaptativa: con {len(en_ventana)} partido(s) en ventana, "
              f"todavía no toca revisar en este ciclo de 5 min.")
        return

    print(f"Consultando partidos en vivo ({len(en_ventana)} en ventana, 1 petición)...")
    en_vivo = obtener_partidos_en_vivo()
    en_vivo_por_id = {f["fixture"]["id"]: f for f in en_vivo}

    cambios = False

    for p in en_ventana:
        fixture = en_vivo_por_id.get(p["fixture_id"])
        if not fixture:
            continue

        minuto = fixture["fixture"]["status"].get("elapsed")
        if minuto is None:
            continue

        goles_local = fixture["goals"]["home"] or 0
        goles_visitante = fixture["goals"]["away"] or 0

        try:
            stats = obtener_estadisticas_fixture(p["fixture_id"])
        except Exception as e:
            print(f"[AVISO] No se pudieron obtener estadísticas de {p['partido']}: {e}")
            continue
        if len(stats) != 2:
            continue

        eventos_nuevos = []
        eventos_crudos = []
        try:
            eventos_crudos = obtener_eventos_fixture(p["fixture_id"])
            eventos_nuevos = _extraer_eventos_nuevos(eventos_crudos, p.setdefault("eventos_procesados", []))
        except Exception as e:
            print(f"[AVISO] No se pudieron obtener eventos de {p['partido']} (se sigue sin tarjetas/penales esta vez): {e}")

        stats_local, stats_visitante = stats[0], stats[1]
        snap_actual = _snapshot(stats_local, stats_visitante, minuto, goles_local, goles_visitante)
        snap_anterior = p["historial_snapshots"][-1] if p["historial_snapshots"] else None

        diferencia_actual = abs((goles_local if p["favorito_es_local"] else goles_visitante) -
                                 (goles_visitante if p["favorito_es_local"] else goles_local))
        p["diferencia_maxima_alcanzada"] = max(p.get("diferencia_maxima_alcanzada", 0), diferencia_actual)

        escenarios = _evaluar_escenarios(
            p, minuto, goles_local, goles_visitante, snap_actual, snap_anterior,
            p["historial_snapshots"], eventos_nuevos, eventos_crudos)

        p["historial_snapshots"].append(snap_actual)
        cambios = True

        if escenarios:
            escenarios.sort(key=lambda e: (e[1], e[2]), reverse=True)  # prioridad primero, luego probabilidad
            tipo, _prioridad, probabilidad, motivo, firma_evento = escenarios[0]

            mensaje = _construir_mensaje(p, tipo, motivo, minuto, goles_local, goles_visitante, snap_actual)
            if enviar_mensaje_telegram(mensaje):
                p["alertas_enviadas"].append({
                    "tipo": tipo, "minuto": minuto, "probabilidad": round(probabilidad, 2),
                    "goles_local_en_alerta": goles_local, "goles_visitante_en_alerta": goles_visitante,
                })
                if firma_evento:
                    p["eventos_procesados"].append(firma_evento)
                if tipo == "partido_resuelto":
                    p["marcado_resuelto"] = True
                print(f"Alerta '{tipo}' enviada: {p['partido']} (min {minuto}, prob {probabilidad*100:.0f}%)")

    if cambios:
        ARCHIVO_PARTIDOS.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print("Sin cambios en esta revisión.")


if __name__ == "__main__":
    revisar()
