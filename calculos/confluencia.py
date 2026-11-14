"""
Confluencia de la escalera del NQ con los niveles de las opciones de QQQ (ETF) y NDX (índice).

NO cambia la escalera del NQ: los niveles de QQQ/NDX se calculan APARTE (con el mismo
calculos.escalera.calcular, sobre sus opciones trasladadas a precio NQ) y solo sirven para
MARCAR (★) los niveles del NQ que tienen uno de ellos a menos de `distancia` puntos.

Traslado a precio NQ, con un ratio por día y raíz R = F_NQ / subyacente (el cierre del QQQ o
del NDX; si no viene, el forward del vencimiento más cercano: ratio NQ/QQQ y base NQ-NDX):
    strike_NQ = strike · R          precio_NQ = precio · R
    futuro_NQ = forward del vencimiento · R   (paridad put-call: dividendos del QQQ incluidos)
    OI_equivalente = OI · mult_externo / (20 · R)
Cada opción conserva su F/K y su IV (Black-76) y queda en el precio NQ en que su subyacente
llegaría a ese strike. Así la GEX en
dólares de cada opción es exactamente la misma que con sus datos originales: 1 opción de QQQ
≈ 0,12 opciones NQ; 1 de NDX ≈ 5; 1 de XND ≈ 0,05.
"""
import math

import numpy as np
import pandas as pd

MULT_NQ = 20
MULTIPLICADOR = {"QQQ": 100, "NDX": 100, "NDXP": 100, "XND": 1}   # XND: 1/100 del NDX × 100
FUENTE = {"QQQ": "QQQ", "NDX": "NDX", "NDXP": "NDX", "XND": "NDX"}
# NDX mensual: liquidación por la mañana (como las trimestrales del NQ, 9:30); el resto, a las 16:00
PRODUCTO = {"NDX": "TRIMESTRAL", "NDXP": "W", "QQQ": "W", "XND": "W"}


def forward_implicito(g, r, T):
    """Forward de un vencimiento por paridad put-call (F = K + e^(rT)·(C − P)) con los 3 strikes
    donde call y put valen más parecido (los más cercanos al dinero). None si no hay pares."""
    # Un precio por strike (si hay series repetidas, p. ej. ajustes, la media)
    c = g[g["tipo"] == "call"].groupby("strike")["precio"].mean()
    p = g[g["tipo"] == "put"].groupby("strike")["precio"].mean()
    comunes = c.index.intersection(p.index)
    comunes = [k for k in comunes if c[k] > 0 and p[k] > 0]
    if not comunes:
        return None
    dif = pd.Series({k: c[k] - p[k] for k in comunes})
    cerca = dif.abs().nsmallest(3).index
    return float(np.median([k + math.exp(r * T) * dif[k] for k in cerca]))


def a_nq(tabla, F_nq, fecha_datos, r=0.04):
    """Tabla de opciones de QQQ/NDX (columnas raiz, tipo, strike, vencimiento, open_interest,
    precio y, opcional, subyacente_precio = cierre del subyacente) → formato común, como opciones
    NQ equivalentes.

    Un solo ratio por día y raíz para situar los strikes en precio NQ: R = F_NQ / subyacente
    (el cierre si viene; si no, el forward del vencimiento más cercano, ≈ contado). Cada opción
    conserva su F/K con futuro = forward de su vencimiento × R (como las opciones del NQ sobre
    el trimestral siguiente, que la escalera sitúa con strike_ref)."""
    forwards = {}
    for (raiz, venc), g in tabla.groupby(["raiz", "vencimiento"]):
        T = max((venc - fecha_datos).days, 1) / 365
        forwards[(raiz, venc)] = forward_implicito(g, r, T)
    filas = []
    for raiz, g in tabla.groupby("raiz"):
        if "subyacente_precio" in g and g["subyacente_precio"].notna().any():
            contado = float(g["subyacente_precio"].dropna().iloc[0])
        else:
            validos = sorted((v, f) for (rz, v), f in forwards.items() if rz == raiz and f)
            contado = validos[0][1] if validos else None
        if not contado or contado <= 0:
            continue
        R = F_nq / contado
        x = g.copy()
        x["futuro"] = [(forwards.get((raiz, v)) or contado) * R for v in x["vencimiento"]]
        x["strike"] = x["strike"] * R
        x["precio"] = x["precio"] * R
        x["open_interest"] = x["open_interest"] * MULTIPLICADOR[raiz] / (MULT_NQ * R)
        x["producto"] = PRODUCTO[raiz]
        x["fuente"] = FUENTE[raiz]
        x["ratio"] = R
        filas.append(x)
    if not filas:
        return pd.DataFrame()
    t = pd.concat(filas, ignore_index=True)
    t["subyacente"] = "NQ"
    t["volumen"] = 0
    return t


def niveles_externos(tabla_nq, info_nq, config, F_nq):
    """Escalera aparte de cada fuente (QQQ, NDX) en precio NQ: [{fuente, desde, hasta, centro, tipos}]."""
    from calculos.escalera import calcular
    from fuentes.modelo import validar
    out = []
    for fuente, g in tabla_nq.groupby("fuente"):
        info = {"fecha_datos": info_nq["fecha_datos"], "indice": "NQ", "fuente": f"{fuente} trasladado a NQ",
                "futuros": {}, "avisos": []}
        try:
            res = calcular(validar(g), info, config, precio_manual=F_nq)
        except ValueError:
            continue
        for z in res["zonas"]:
            out.append({"fuente": fuente, "desde": z["desde"], "hasta": z["hasta"],
                        "centro": (z["desde"] + z["hasta"]) / 2, "tipos": z["tipos"],
                        "categoria": z.get("categoria"),
                        "zona_desde": z.get("zona_desde"), "zona_hasta": z.get("zona_hasta")})
    return out


TIPOS_PRINCIPALES = ("Muro de calls", "Muro de puts", "Imán")


def principales(externos, tipos=TIPOS_PRINCIPALES):
    """Solo los niveles externos que son muro o imán (no cualquier strike con OI o gamma)."""
    return [e for e in externos if set(e["tipos"]) & set(tipos)]


def distancia_por_defecto(ancho_zona, precio, error_ratio_pct):
    """Mitad del ancho de zona + error típico del traslado (en puntos NQ)."""
    return ancho_zona / 2 + precio * error_ratio_pct / 100


def error_ratio_pct(ratios):
    """Error típico (%) del ratio NQ/QQQ: desviación de su cambio de un día al siguiente."""
    r = pd.Series(ratios, dtype=float).dropna()
    if len(r) < 3:
        return None
    return float(r.pct_change().dropna().std() * 100)


def marcar(niveles, externos, distancia):
    """Pone en cada nivel del NQ "confluencia": {fuentes, distancia, con} si hay un nivel externo a
    ≤ `distancia` puntos de su centro (si no, None). No toca nada más. Devuelve los niveles externos
    que no coinciden con ninguno del NQ («solo QQQ/NDX»)."""
    usados = set()
    for n in niveles:
        n["confluencia"] = None
        if n.get("tipos") == ["Frontera"]:
            continue
        centro = (n["desde"] + n["hasta"]) / 2
        cerca = [(abs(e["centro"] - centro), i, e) for i, e in enumerate(externos)
                 if abs(e["centro"] - centro) <= distancia]
        if cerca:
            usados.update(i for _, i, _ in cerca)
            fuentes = sorted({e["fuente"] for _, _, e in cerca})
            d, _, e = min(cerca, key=lambda x: x[0])
            n["confluencia"] = {"fuentes": fuentes, "distancia": d, "con": e["centro"]}
    return [e for i, e in enumerate(externos) if i not in usados]


def letras(confluencia):
    """Campo del ESC2: "Q" (QQQ), "N" (NDX), "QN" (los dos) o "-"."""
    if not confluencia:
        return "-"
    return "".join(l for f, l in (("QQQ", "Q"), ("NDX", "N")) if f in confluencia["fuentes"]) or "-"


def aplicar(res, tabla_externa, config):
    """Marca las zonas de la escalera del NQ (`res`, de calculos.escalera.calcular) con la confluencia
    de QQQ/NDX. Sin tabla externa no hace nada. Devuelve res["confluencia"] (o None)."""
    if tabla_externa is None or len(tabla_externa) == 0:
        res["confluencia"] = None
        return None
    c = config.get("confluencia", {})
    F = res["precio_ref"]
    tabla_nq = a_nq(tabla_externa, F, res["fecha_datos"], config["tipo_interes"])
    if tabla_nq.empty:
        res["confluencia"] = None
        return None
    externos = principales(niveles_externos(tabla_nq, {"fecha_datos": res["fecha_datos"]}, config, F),
                           c.get("tipos_externos", TIPOS_PRINCIPALES))
    distancia = c.get("distancia_puntos")
    if distancia is None:
        distancia = distancia_por_defecto(res["jerarquia"]["ancho_zona"], F, c.get("error_ratio_pct", 0.03))
    solo = marcar(res["zonas"], externos, distancia)
    res["confluencia"] = {"distancia": distancia, "fuentes": sorted(set(tabla_nq["fuente"])),
                          "ratios": {f: float(g["ratio"].median()) for f, g in tabla_nq.groupby("fuente")},
                          "externos": externos, "solo": solo}
    return res["confluencia"]
