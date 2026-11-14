"""
Respeto de niveles: el método de salida/respeto_niveles_nq.csv (reproducido fila a fila con los
niveles del NQ: pruebas/test_respeto.py).

Para cada zona, en la sesión regular (velas de 1 minuto de 9:30 a 16:00 NY):
- primer toque: primera vela cuyo rango entra en la zona [zlo, zhi];
- lado de llegada: el cierre de la vela anterior (en la primera vela, su apertura); si está dentro
  de la zona, no cuenta (el precio «empezó» en ella);
- rechazo: el precio se aleja X·σ más allá del borde por el que llegó, antes de que pase X·σ más
  allá del borde contrario (rotura). Si las dos cosas pasan en la misma vela, o ninguna antes del
  cierre: «nada». X = 0,1 y 0,2 σ (σ = movimiento esperado de 1 día de la escalera).
- distancia: |centro de la zona − apertura de las 9:30| / σ.
Sesiones fuera: hueco de apertura > 1,5σ respecto al precio de referencia, y del jueves del cambio
de contrato (8 días antes del tercer viernes de mar/jun/sep/dic) a una semana después de ese viernes.
"""
from datetime import date, timedelta

import numpy as np

UMBRALES = (0.1, 0.2)
HUECO_MAX = 1.5


def tercer_viernes(anio, mes):
    d = date(anio, mes, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def en_cambio_de_contrato(s):
    return any(tercer_viernes(a, m) - timedelta(days=8) < s <= tercer_viernes(a, m) + timedelta(days=7)
               for a in (s.year - 1, s.year) for m in (3, 6, 9, 12))


def sesion_valida(sesion, apertura, precio_ref, sigma):
    return not en_cambio_de_contrato(sesion) and abs(apertura - precio_ref) / sigma <= HUECO_MAX


def resultado(velas, zlo, zhi, X, sigma):
    """"rechazo", "rotura", "nada", "dentro" (empezó en la zona) o None (no se tocó)."""
    hi, lo, cl, op = (velas[c].to_numpy(float) for c in ("high", "low", "close", "open"))
    toca = np.where((lo <= zhi) & (hi >= zlo))[0]
    if not len(toca):
        return None
    i = toca[0]
    previo = cl[i - 1] if i > 0 else op[0]
    if previo > zhi:
        arriba = True
    elif previo < zlo:
        arriba = False
    else:
        return "dentro"
    llegada, contrario = (zhi, zlo) if arriba else (zlo, zhi)
    for j in range(i, len(hi)):
        rech = hi[j] >= llegada + X * sigma if arriba else lo[j] <= llegada - X * sigma
        rot = lo[j] <= contrario - X * sigma if arriba else hi[j] >= contrario + X * sigma
        if rech and rot:
            return "nada"
        if rech:
            return "rechazo"
        if rot:
            return "rotura"
    return "nada"


def filas_zona(velas, zlo, zhi, sigma, apertura, **extra):
    """Una fila por umbral si la zona se tocó con dirección: {X, res, dist, ...extra}."""
    out = []
    for X in UMBRALES:
        r = resultado(velas, zlo, zhi, X, sigma)
        if r in (None, "dentro"):
            continue
        out.append({"X": X, "res": r, "dist": abs((zlo + zhi) / 2 - apertura) / sigma, **extra})
    return out
