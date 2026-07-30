"""
telegram_utils.py
------------------
Envio de mensajes a Telegram usando un bot propio (gratis).

CORRECCION (detectada en produccion): con muchos partidos seleccionados
en un dia (ej. 84), el resumen supera el limite de Telegram de 4096
caracteres por mensaje, lo que causaba un 400 Bad Request silencioso
-- el error no explicaba la causa real porque no se registraba el
cuerpo de la respuesta de Telegram. Ahora:
  1. Si el mensaje supera el limite, se divide en varias partes y se
     envian en orden, respetando saltos de linea (nunca corta una linea
     a la mitad).
  2. Si un envio falla por cualquier otro motivo, se imprime el detalle
     exacto que devuelve Telegram (ej. "chat not found", token invalido,
     etc.) en vez de solo el codigo HTTP generico.

Como crear tu bot (una sola vez):
  1. En Telegram, habla con @BotFather -> /newbot -> sigue los pasos.
     Te da un TOKEN (algo como 123456789:ABCdefGhIJK...).
  2. Escribele cualquier mensaje a TU bot (para que pueda hablarte).
  3. Abre en el navegador:
     https://api.telegram.org/bot<TU_TOKEN>/getUpdates
     y busca el campo "chat":{"id": ...}  -> ese numero es tu CHAT_ID.
  4. Guarda ambos como "Secrets" en tu repositorio de GitHub.
"""

import os
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

LIMITE_TELEGRAM = 4096
MARGEN_SEGURIDAD = 200  # deja margen por si acaso, no llenar el limite exacto
LIMITE_EFECTIVO = LIMITE_TELEGRAM - MARGEN_SEGURIDAD


def escapar_html(texto):
    """
    Convierte &, < y > a su forma segura para HTML. Telegram usa
    parse_mode=HTML, asi que cualquier texto dinamico que insertemos en
    un mensaje (nombres de equipo, ligas, etc.) DEBE pasar por aqui
    antes de meterlo al mensaje.

    IMPORTANTE: solo se aplica al texto dinamico (nombres, etc.), NUNCA
    a las etiquetas <b>...</b> que nosotros mismos escribimos a proposito.
    """
    if texto is None:
        return ""
    return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _dividir_mensaje(texto, limite=LIMITE_EFECTIVO):
    """
    Divide un texto largo en partes que quepan en un mensaje de
    Telegram, cortando siempre en un salto de linea (nunca a mitad de
    una linea) para no romper etiquetas HTML abiertas a medias.
    """
    if len(texto) <= limite:
        return [texto]

    partes = []
    lineas = texto.split("\n")
    actual = ""
    for linea in lineas:
        candidato = (actual + "\n" + linea) if actual else linea
        if len(candidato) > limite:
            if actual:
                partes.append(actual)
            # si una sola linea ya es mas larga que el limite (raro),
            # se corta a la fuerza para no quedar atascado
            if len(linea) > limite:
                for i in range(0, len(linea), limite):
                    partes.append(linea[i:i + limite])
                actual = ""
            else:
                actual = linea
        else:
            actual = candidato
    if actual:
        partes.append(actual)
    return partes


def _enviar_una_parte(texto):
    if not TOKEN or not CHAT_ID:
        print("[AVISO] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID. No se envio el mensaje:")
        print(texto)
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": texto, "parse_mode": "HTML"}, timeout=15)
        if not r.ok:
            print(f"[ERROR] Telegram respondio {r.status_code}: {r.text}")
        r.raise_for_status()
        return True
    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] No se pudo enviar el mensaje de Telegram: {e}")
        return False
    except Exception as e:
        print(f"[ERROR] No se pudo enviar el mensaje de Telegram: {e}")
        return False


NOMBRE_PROYECTO = "Flow Elo"


def enviar_mensaje_telegram(texto):
    """Envia 'texto' al chat configurado, con el nombre del proyecto
    como encabezado. Si el mensaje supera el limite de Telegram, lo
    divide y envia en varias partes en orden. Devuelve True solo si
    TODAS las partes se enviaron con exito."""
    texto_con_encabezado = f"⚙️ <b>{NOMBRE_PROYECTO}</b>\n{texto}"
    partes = _dividir_mensaje(texto_con_encabezado)
    if len(partes) > 1:
        print(f"[INFO] Mensaje de {len(texto_con_encabezado)} caracteres supera el limite de Telegram, "
              f"se divide en {len(partes)} partes.")

    exito_total = True
    for i, parte in enumerate(partes, start=1):
        prefijo = f"(parte {i}/{len(partes)})\n" if len(partes) > 1 else ""
        if not _enviar_una_parte(prefijo + parte):
            exito_total = False
    return exito_total
