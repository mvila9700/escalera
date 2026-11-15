"""
Perfil de volumen de las sesiones anteriores: POC, VAH y VAL (value area) y si el precio ya ha vuelto
a tocarlos (mitigados). SOLO REFERENCIA VISUAL: el estudio del 15-nov-2026
(documentacion/estudio_volumen_gamma_negativa_2026-11-15.md) no les encuentra ventaja estable, así que
no son niveles de la escalera ni entran en la fiabilidad.

Método (el clásico de la value area; no había otro en el proyecto):
- Perfil: el volumen de cada vela de 1 minuto se reparte a partes iguales entre los tramos de
  `tramo` puntos que toca, de su mínimo a su máximo. Cada tramo se nombra por su precio central,
  múltiplo de `tramo` (27.700, 27.705... en el NQ con tramo 5).
- POC: el tramo con más volumen (si empatan, el más cercano al centro del perfil).
- Value area: desde el POC se va añadiendo, de uno en uno, el tramo vecino (el de encima o el de
  debajo) con más volumen; si empatan, los dos. Para al llegar al 70 % del volumen.
  VAH = tramo más alto de la value area · VAL = tramo más bajo.
- Mitigado: alguna vela posterior al cierre de esa sesión (16:00 de Nueva York; incluida la noche)
  tiene el precio entre su mínimo y su máximo.
"""
import numpy as np
import pandas as pd

TIPOS = ("VAL", "POC", "VAH")


def perfil(velas, tramo):
    """Serie volumen por precio (índice: precio central de cada tramo, de menor a mayor, sin huecos)."""
    if velas is None or len(velas) == 0:
        return pd.Series(dtype=float)
    lo = np.round(velas["low"].to_numpy(float) / tramo).astype(int)
    hi = np.round(velas["high"].to_numpy(float) / tramo).astype(int)
    vol = velas["volume"].to_numpy(float)
    base = lo.min()
    acum = np.zeros(hi.max() - base + 1)
    for a, b, v in zip(lo - base, hi - base, vol):
        if v > 0:
            acum[a:b + 1] += v / (b - a + 1)
    return pd.Series(acum, index=[(base + i) * tramo for i in range(len(acum))])


def value_area(p, parte=0.70):
    """(poc, val, vah) del perfil `p` (serie de perfil()). None si no hay volumen."""
    v = p.to_numpy(float)
    total = v.sum()
    if len(v) == 0 or total <= 0:
        return None
    maximos = np.flatnonzero(v == v.max())
    centro = (len(v) - 1) / 2
    poc = int(maximos[np.argmin(np.abs(maximos - centro))])
    arriba, abajo, acum = poc + 1, poc - 1, v[poc]
    while acum < parte * total - 1e-9:
        va = v[arriba] if arriba < len(v) else -1.0
        vb = v[abajo] if abajo >= 0 else -1.0
        if va < 0 and vb < 0:
            break
        if va >= vb and va >= 0:
            acum += va
            arriba += 1
        if vb >= va and vb >= 0:
            acum += vb
            abajo -= 1
    precios = p.index
    return float(precios[poc]), float(precios[abajo + 1]), float(precios[arriba - 1])


def tocado(precio, velas):
    """¿Alguna vela tiene `precio` entre su mínimo y su máximo?"""
    if velas is None or len(velas) == 0:
        return False
    return bool(((velas["low"] <= precio) & (velas["high"] >= precio)).any())


def niveles(sesiones, tramo, parte=0.70):
    """sesiones: lista de {"fecha": date, "rth": velas 9:30-16:00, "despues": velas desde el cierre}.
    Devuelve los no mitigados: [{"tipo": "VAL"|"POC"|"VAH", "precio", "dia": "AAAA-MM-DD"}], por día y tipo."""
    res = []
    for s in sesiones:
        va = value_area(perfil(s["rth"], tramo), parte)
        if not va:
            continue
        poc, val, vah = va
        for tipo, precio in zip(TIPOS, (val, poc, vah)):
            if not tocado(precio, s.get("despues")):
                res.append({"tipo": tipo, "precio": precio, "dia": str(s["fecha"])})
    return res
