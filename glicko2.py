"""
glicko2.py
----------
Implementación del sistema Glicko-2 (Mark Glickman), usado como el
"rating propio" del proyecto. Se eligió sobre Elo puro porque modela
explícitamente la INCERTIDUMBRE (RD) y la VOLATILIDAD (sigma) de cada
equipo, no solo su fuerza — lo cual es justo lo que se necesita para
that un equipo con poca información no se trate con la misma confianza
que uno con historial largo, sin tener que inventar reglas manuales de
"cuántos partidos necesita".

Referencia del algoritmo: http://www.glicko.net/glicko/glicko2.pdf

Valores por defecto (los que usa este proyecto, ver ratings_store.py):
  rating inicial = 1500
  RD inicial     = 350   (máxima incertidumbre)
  volatilidad    = 0.06
  tau            = 0.5   (recomendado para deportes de resultado
                           relativamente estable, como fútbol; en
                           ajedrez -donde se diseñó el sistema- se usa
                           más bajo, 0.2-0.3)

Este módulo NO decide cuándo actualizar (eso lo hace cerrar_resultados.py
tras cada partido real observado) ni cómo mezclar con ClubElo (eso lo
hace ratings_store.py). Aquí solo vive la matemática del algoritmo.
"""

import math

ESCALA_GLICKO = 173.7178  # constante fija del algoritmo (paper de Glickman)
RATING_BASE = 1500.0
RD_INICIAL = 350.0
VOL_INICIAL = 0.06
TAU = 0.5
EPSILON = 0.000001


def _a_escala_glicko2(rating, rd):
    mu = (rating - RATING_BASE) / ESCALA_GLICKO
    phi = rd / ESCALA_GLICKO
    return mu, phi


def _de_escala_glicko2(mu, phi):
    rating = mu * ESCALA_GLICKO + RATING_BASE
    rd = phi * ESCALA_GLICKO
    return rating, rd


def _g(phi):
    return 1 / math.sqrt(1 + 3 * phi ** 2 / math.pi ** 2)


def _E(mu, mu_j, phi_j):
    return 1 / (1 + math.exp(-_g(phi_j) * (mu - mu_j)))


def probabilidad_victoria(rating_a, rd_a, rating_b, rd_b):
    """
    Probabilidad de que el equipo A le gane al equipo B, tomando en
    cuenta la incertidumbre (RD) de AMBOS. Esta es la pieza clave que
    resuelve el caso "equipo con historial vs equipo sin datos": si
    rd_b es alto, _g(phi_b) se acerca a 0 y la probabilidad se acerca
    a 50/50 automáticamente, sin ninguna regla manual.
    """
    mu_a, phi_a = _a_escala_glicko2(rating_a, rd_a)
    mu_b, phi_b = _a_escala_glicko2(rating_b, rd_b)
    phi_comb = math.sqrt(phi_a ** 2 + phi_b ** 2)
    return _E(mu_a, mu_b, phi_comb)


def _incrementar_rd_por_inactividad(rd, periodos_inactivos, vol):
    """
    Si un equipo no jugó en 'periodos_inactivos' periodos de rating (en
    este proyecto, 1 periodo = 1 semana), su incertidumbre debe crecer
    con el tiempo -- es la regla estándar de Glicko-2 para inactividad.
    Resuelve de forma natural el caso de un equipo recién ascendido o
    en pretemporada larga, sin necesitar una bandera especial.
    """
    _, phi = _a_escala_glicko2(RATING_BASE, rd)
    for _ in range(max(0, periodos_inactivos)):
        phi = math.sqrt(phi ** 2 + vol ** 2)
    _, rd_nuevo = _de_escala_glicko2(0, phi)
    return min(rd_nuevo, RD_INICIAL)


def actualizar_rating(rating, rd, vol, resultados, tau=TAU):
    """
    Actualiza el rating de UN equipo dado el resultado de UN periodo de
    rating (puede incluir 1 o varios partidos jugados en ese periodo).

    resultados: lista de tuplas (rating_oponente, rd_oponente, score)
                donde score es 1.0 (victoria), 0.5 (empate) o 0.0 (derrota)

    Devuelve (rating_nuevo, rd_nuevo, vol_nuevo).

    Si 'resultados' está vacío, solo se aplica el crecimiento de RD por
    inactividad (ver _incrementar_rd_por_inactividad), sin tocar rating
    ni volatilidad.
    """
    if not resultados:
        rd_nuevo = _incrementar_rd_por_inactividad(rd, 1, vol)
        return rating, rd_nuevo, vol

    mu, phi = _a_escala_glicko2(rating, rd)

    v_inv = 0.0
    suma_delta = 0.0
    for rating_j, rd_j, score in resultados:
        mu_j, phi_j = _a_escala_glicko2(rating_j, rd_j)
        g_j = _g(phi_j)
        E_j = _E(mu, mu_j, phi_j)
        v_inv += (g_j ** 2) * E_j * (1 - E_j)
        suma_delta += g_j * (score - E_j)

    v = 1 / v_inv if v_inv > 0 else 1e9
    delta = v * suma_delta

    # --- Resolver la nueva volatilidad (algoritmo de Illinois, tal
    # como lo describe el paper original de Glickman) ---
    a = math.log(vol ** 2)

    def f(x):
        ex = math.exp(x)
        num = ex * (delta ** 2 - phi ** 2 - v - ex)
        den = 2 * (phi ** 2 + v + ex) ** 2
        return (num / den) - (x - a) / (tau ** 2)

    A = a
    if delta ** 2 > phi ** 2 + v:
        B = math.log(delta ** 2 - phi ** 2 - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fA, fB = f(A), f(B)
    while abs(B - A) > EPSILON:
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB < 0:
            A, fA = B, fB
        else:
            fA = fA / 2
        B, fB = C, fC

    vol_nuevo = math.exp(A / 2)

    phi_estrella = math.sqrt(phi ** 2 + vol_nuevo ** 2)
    phi_nuevo = 1 / math.sqrt(1 / phi_estrella ** 2 + 1 / v)
    mu_nuevo = mu + phi_nuevo ** 2 * suma_delta

    rating_nuevo, rd_nuevo = _de_escala_glicko2(mu_nuevo, phi_nuevo)
    return round(rating_nuevo, 2), round(rd_nuevo, 2), round(vol_nuevo, 6)
