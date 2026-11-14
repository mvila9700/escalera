"""
Bloque OPCIONAL del ESC2 para recalcular la gamma en vivo en TradingView (clave "gv").

Con r pequeño, la gamma de Black-76 de un strike solo depende del precio F, del strike K y de
s = σ·√T (la volatilidad que queda hasta el vencimiento):
    gamma = φ(d1) / (F·s),   d1 = (ln(F/K) + s²/2) / s
(el descuento e^(−rT) se deja fuera: en un día cambia la gamma menos de un 0,02 %).
Por eso, por strike y por grupo de vencimientos basta con calls, puts y un s.

Grupos de vencimientos (días naturales desde la sesión): 0 = 0DTE (vence hoy a las 16:00 NY),
1 = ≤ 2 días, 2 = ≤ 7, 3 = ≤ 21, 4 = ≤ 60, 5 = el resto. En cada strike y grupo, el s y las
calls/puts son los que mejor reproducen (mínimos cuadrados) la gamma real de sus opciones en
±3σ alrededor del precio: son contratos «efectivos», no el OI tal cual. Con el tiempo, el s de
cada grupo baja como √(T que queda / T en la apertura): la 0DTE es la que más cambia.

Formato (una sola clave del ESC2, sin "|" ni "="):
    gv=1;F;base;paso;T0;T1;...;T5;mult;st;st;...
      1      versión
      F      precio de referencia (el de la escalera)
      base, paso   strike = base + k·paso
      T0..T5 tiempo en años (base 252) de cada grupo EN LA APERTURA (0 si el grupo está vacío)
      mult   multiplicador (20 NQ, 50 ES), para la GEX en dólares
    cada strike: k,c0,p0,s0,c1,p1,s1,...,c5,p5,s5 separados por "," (los vacíos al final se quitan)
      c/p: calls/puts efectivas (enteros; vacío = 0); s: σ√T × 10000 (entero)
    Dentro de ±3σ va cada strike; fuera, las opciones se suman en tramos de 0,25σ (un "strike"
    por tramo, en su centro): sin ellas la frontera se desvía decenas de puntos (gamma ancha de
    los vencimientos largos). Si no cabe en `max_caracteres`, entran primero los de más gamma bruta.

Algoritmo de la frontera (el MISMO que el indicador): GEX total en una malla de 61 precios en
±3σ alrededor del precio, cruce de signo más cercano al precio y bisección (25 pasos).
Comprobado con los boletines del 23 al 30-oct-2026: a menos de 2 puntos de la frontera de
calculos.gex (NQ) y de 0,5 puntos (ES).
"""
import math

import numpy as np

LIMITES_DIAS = [0, 2, 7, 21, 60]   # grupos de vencimientos (el último, el resto)
GRUPOS = len(LIMITES_DIAS) + 1
VERSION = "1"
ANCHO_LEJOS = 0.25                 # tramos fuera de ±3σ, en σ


def _ajustar(y, K, F, sigma, puntos=25, candidatos=80):
    """(calls, puts, s) de un strike y grupo: el s (de una rejilla logarítmica) y los contratos
    efectivos que mejor reproducen, por mínimos cuadrados, la gamma real de sus opciones en
    `puntos` precios de ±3σ alrededor de F. Calls y puts comparten s (misma IV y plazo por strike)."""
    Fj = np.linspace(F - 3 * sigma, F + 3 * sigma, puntos)[:, None]
    Ki = y["strike_ref"].to_numpy(float)[None, :]
    si = y["s"].to_numpy(float)[None, :]
    oi = y["open_interest"].to_numpy(float)
    es_call = (y["tipo"] == "call").to_numpy()
    gi = _gamma(Fj, Ki, si)                                     # puntos × opciones
    real_c, real_p = gi[:, es_call] @ oi[es_call], gi[:, ~es_call] @ oi[~es_call]
    real = real_c + real_p
    lo, hi = max(si.min() * 0.7, 1e-4), si.max() * 1.3
    mejor = None
    for s_c in np.round(np.geomspace(lo, hi, candidatos) * 1e4) / 1e4:
        g = _gamma(Fj[:, 0], K, s_c)
        gg = float(g @ g)
        if gg <= 0:
            continue
        a = float(g @ real) / gg                                # escala óptima (mínimos cuadrados)
        err = float(((a * g - real) ** 2).sum())
        if mejor is None or err < mejor[0]:
            mejor = (err, s_c, g, gg)
    if mejor is None:
        return 0, 0, 0.0
    _, s_c, g, gg = mejor
    return int(round(float(g @ real_c) / gg)), int(round(float(g @ real_p) / gg)), float(s_c)


def _gamma(F, K, s):
    """Gamma de Black-76 sin el descuento: φ(d1)/(F·s)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(F / K) + s * s / 2) / s
        return np.where(s > 0, np.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (F * s), 0.0)


def _grupo(venc, fecha_sesion):
    dias = (venc - fecha_sesion).days
    return next((i for i, lim in enumerate(LIMITES_DIAS) if dias <= lim), len(LIMITES_DIAS))


def bloque(res, config, max_caracteres=24000):
    """Texto "gv=..." a partir del resultado de calculos.escalera.calcular (o None)."""
    df = res["opciones"]
    F = res["precio_ref"]
    sigma = res["movimiento"]["desviacion"]
    d = df[df["open_interest"] > 0].copy()
    # Strike equivalente en el futuro de referencia con el mismo F/K (lo que cuenta en Black-76);
    # para las opciones sobre otro trimestral, mejor que desplazarlo en puntos (strike_ref).
    d["strike_ref"] = d["strike"] * F / d["futuro"]
    # Strike a strike hasta ±max(3σ, desviaciones_extendido·σ) (4σ: lo que cubre la escalera
    # extendida); si no cabe en max_caracteres, se quedan fuera los de menos gamma, como siempre.
    k = max(3.0, float(config.get("desviaciones_extendido", 3)))
    cerca = (d["strike_ref"] >= F - k * sigma) & (d["strike_ref"] <= F + k * sigma)
    if not cerca.any():
        return None
    d["g"] = [_grupo(v, res["fecha_sesion"]) for v in d["vencimiento"]]
    d["s"] = d["iv"] * np.sqrt(d["T_sesion"])
    d["peso"] = d["open_interest"] * d["gamma"]          # para el T medio de cada grupo
    pasos_strike = np.diff(np.sort(d.loc[cerca, "strike_ref"].unique()))
    paso = float(np.min(pasos_strike[pasos_strike > 0.01])) if len(pasos_strike) else 1.0
    paso = _paso_redondo(paso)
    base = round(F / paso) * paso
    d["k"] = ((d["strike_ref"] - base) / paso).round().astype(int)
    # Fuera de esa franja: tramos de 0,25σ (strike = el centro del tramo). Su gamma cerca del precio es
    # suave, pero las opciones lejanas de plazo largo pesan: sin ellas la frontera se va decenas de puntos.
    ancho = ANCHO_LEJOS * sigma
    lejos = ~cerca
    centro = F + (np.floor((d.loc[lejos, "strike_ref"] - F) / ancho) + 0.5) * ancho
    d.loc[lejos, "k"] = ((centro - base) / paso).round().astype(int)
    T = [0.0] * GRUPOS
    for g, x in d.groupby("g"):
        T[g] = float(np.average(x["T_sesion"], weights=x["peso"])) if x["peso"].sum() > 0 else float(x["T_sesion"].mean())
    mult = config["multiplicadores"][config["indices"][config.get("indice", "NQ")]["subyacente"]]
    d["gc"] = _gamma(F, d["strike_ref"].to_numpy(float), d["s"].to_numpy(float))   # gamma de cada opción
    filas = []
    for k, x in d.groupby("k"):
        campos, bruta = [str(int(k))], 0.0
        K = base + k * paso
        for g in range(GRUPOS):
            y = x[x["g"] == g]
            if y.empty:
                campos += ["", "", ""]
                continue
            c, p_, s_ag = _ajustar(y, K, F, sigma)
            campos += [str(c) if c else "", str(p_) if p_ else "", str(int(round(s_ag * 1e4))) if (c or p_) else ""]
            bruta += float((y["open_interest"] * y["gc"]).sum())
        while campos and campos[-1] == "":
            campos.pop()
        if len(campos) > 1:
            filas.append((bruta, int(k), ",".join(campos)))
    cab = ["gv=" + VERSION, _num(F), _num(base), _num(paso)] + [f"{t:.6g}" for t in T] + [str(mult)]
    texto_cab = ";".join(cab)
    restante = max_caracteres - len(texto_cab)
    elegidas = []
    for bruta, k, f in sorted(filas, key=lambda x: -x[0]):
        if len(f) + 1 > restante:
            continue
        elegidas.append((k, f))
        restante -= len(f) + 1
    return texto_cab + "".join(";" + f for _, f in sorted(elegidas))


def _paso_redondo(p):
    for c in (0.25, 0.5, 1, 2.5, 5, 10, 25, 50, 100):
        if p <= c + 1e-9:
            return c
    return p


def _num(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


def leer(valor):
    """"1;F;base;paso;T0;...;T5;mult;..." (lo que va tras "gv=") → dict con arrays."""
    t = valor.split(";")
    if not t or t[0] != VERSION:
        return None
    F, base, paso = float(t[1]), float(t[2]), float(t[3])
    T = [float(x) for x in t[4:4 + GRUPOS]]
    mult = float(t[4 + GRUPOS])
    K, C, P, S = [], [], [], []
    for st in t[5 + GRUPOS:]:
        c = st.split(",") + [""] * (1 + 3 * GRUPOS)
        K.append(base + int(c[0]) * paso)
        fila_c, fila_p, fila_s = [], [], []
        for g in range(GRUPOS):
            v = c[1 + 3 * g: 4 + 3 * g]
            fila_c.append(float(v[0] or 0)); fila_p.append(float(v[1] or 0)); fila_s.append(float(v[2] or 0) / 1e4)
        C.append(fila_c); P.append(fila_p); S.append(fila_s)
    return {"F": F, "T": T, "mult": mult, "K": np.array(K), "C": np.array(C), "P": np.array(P), "S": np.array(S)}


def s_en_vivo(b, horas_pasadas, minimo_horas=1.0):
    """σ√T de cada strike y grupo cuando han pasado `horas_pasadas` desde la apertura (9:30).
    Cada grupo pierde ese tiempo (en años de 252 días de 24 h); nunca menos de `minimo_horas`."""
    dt = horas_pasadas / 24 / 252
    minimo = minimo_horas / 24 / 252
    S = b["S"].copy()
    for g in range(GRUPOS):
        T0 = b["T"][g]
        if T0 > 0:
            S[:, g] *= math.sqrt(max(T0 - dt, minimo) / T0)
    return S


def gex_total(b, F, S=None):
    """GEX (dólares por 1 %) al precio F: Σ (calls − puts) · gamma · mult · F² · 0,01."""
    S = b["S"] if S is None else S
    K = b["K"][:, None]
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(F / K) + S * S / 2) / S
        gam = np.where(S > 0, np.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi) / (F * S), 0.0)
    neto = b["C"] - b["P"]
    return float(np.nansum(neto * gam) * b["mult"] * F * F * 0.01)


def frontera(b, precio, sigma, S=None, puntos=61, bisecciones=25):
    """Gamma flip con el mismo algoritmo que el indicador (None si no hay cruce en ±3σ)."""
    malla = np.linspace(precio - 3 * sigma, precio + 3 * sigma, puntos)
    g = [gex_total(b, p, S) for p in malla]
    mejor = None
    for i in range(puntos - 1):
        if (g[i] < 0) != (g[i + 1] < 0):
            a, z, ga = malla[i], malla[i + 1], g[i]
            for _ in range(bisecciones):
                m = (a + z) / 2
                gm = gex_total(b, m, S)
                if (gm < 0) == (ga < 0):
                    a, ga = m, gm
                else:
                    z = m
            x = (a + z) / 2
            if mejor is None or abs(x - precio) < abs(mejor - precio):
                mejor = x
    return mejor


def fuerza(b, lo, hi, F, S=None):
    """Gamma bruta (calls + puts) de los strikes de la zona lo-hi al precio F, en dólares por 1 %
    (como la GEX). La fuerza «en vivo» de un nivel es esta al precio actual / esta en la apertura."""
    S = b["S"] if S is None else S
    dentro = (b["K"] >= lo) & (b["K"] <= hi)
    if not dentro.any():
        return 0.0
    K = b["K"][dentro][:, None]
    g = _gamma(F, K, S[dentro])
    return float(np.nansum((b["C"][dentro] + b["P"][dentro]) * g) * b["mult"] * F * F * 0.01)
