"""
"Estado hoy" de cada nivel.

Con el máximo y el mínimo del día (del Daily Bulletin):
    "Tocado"        el nivel está dentro del rango [mínimo, máximo]
    "No alcanzado"  fuera del rango

Si además hay velas de 1 minuto de ese día:
    hora del primer toque, y para los tocados:
    "Tocado y roto"       después del toque, algún cierre de 1 minuto queda
                          más allá del nivel (por el lado contrario al que llegó
                          el precio) al menos `umbral_rotura_puntos`.
    "Tocado y respetado"  en caso contrario.
    Si el nivel ya estaba dentro de la primera vela (el precio "empezó" en él),
    queda como "Tocado" sin más.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")
NUEVA_YORK = ZoneInfo("America/New_York")


def ventana_sesion(fecha_sesion, tipo="globex"):
    """(inicio, fin) de la sesión en UTC.

    globex: de las 17:00 del día natural anterior a las 16:00 de Chicago
            (igual que el máximo/mínimo «GLOBEX HIGH/LOW» del boletín).
    rth:    de 9:30 a 16:00 de Nueva York.
    """
    if tipo == "rth":
        inicio = datetime.combine(fecha_sesion, time(9, 30), NUEVA_YORK)
        fin = datetime.combine(fecha_sesion, time(16, 0), NUEVA_YORK)
    else:
        inicio = datetime.combine(fecha_sesion - timedelta(days=1), time(17, 0), CHICAGO)
        fin = datetime.combine(fecha_sesion, time(16, 0), CHICAGO)
    return inicio.astimezone(ZoneInfo("UTC")), fin.astimezone(ZoneInfo("UTC"))


def por_rango(desde, hasta, maximo, minimo):
    """Estado de un nivel [desde, hasta] solo con el máximo y el mínimo del día."""
    return "No alcanzado" if hasta < minimo or desde > maximo else "Tocado"


def con_velas(desde, hasta, velas, umbral, zona_horaria="Europe/Madrid"):
    """Estado y hora del primer toque usando velas de 1 minuto.

    Devuelve (estado, hora_toque) con la hora en texto ("15:42") en zona_horaria.
    """
    if velas.empty:
        return None, None
    toca = (velas["low"] <= hasta) & (velas["high"] >= desde)
    if not toca.any():
        return "No alcanzado", None
    t0 = toca.idxmax()
    hora = t0.tz_convert(zona_horaria).strftime("%H:%M")
    # ¿Desde dónde llegó el precio? Del cierre de la vela anterior al toque.
    anteriores = velas[velas.index < t0]
    if anteriores.empty:
        return "Tocado", hora   # ya estaba en el nivel al empezar
    previo = anteriores["close"].iloc[-1]
    despues = velas[velas.index >= t0]["close"]
    if previo < desde:      # llegó desde abajo: roto si cierra por encima
        roto = (despues >= hasta + umbral).any()
    elif previo > hasta:    # llegó desde arriba: roto si cierra por debajo
        roto = (despues <= desde - umbral).any()
    else:
        return "Tocado", hora
    return ("Tocado y roto" if roto else "Tocado y respetado"), hora
