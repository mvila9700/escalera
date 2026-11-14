"""
Gamma exposure (GEX), gamma flip y mapas por tramos.

GEX de una opción = gamma × open interest × multiplicador × precio² × 0,01
Es el número de dólares que los creadores de mercado tendrían que comprar o
vender (para cubrirse) si el precio se mueve un 1 %.

Signo (configurable en config.yaml, signo_gex): calls en positivo, puts en
negativo. Es el supuesto estándar (creadores de mercado largos de calls y
cortos de puts), NO un dato real de posicionamiento.
"""
import numpy as np
import pandas as pd

from calculos import black76


def _signo(df, config):
    return df["tipo"].map(config["signo_gex"]).astype(float)


def anadir_gex(df, config):
    """Columna gex (en dólares por 1 % de movimiento) para cada opción."""
    df = df.copy()
    mult = df["subyacente"].map(config["multiplicadores"]).astype(float)
    df["gex"] = _signo(df, config) * df["gamma"] * df["open_interest"] * mult * df["futuro"] ** 2 * 0.01
    return df


def gex_por_strike(df):
    """Resumen por strike: OI y GEX de calls, puts y neto (todos los vencimientos)."""
    t = df.pivot_table(index="strike_ref", columns="tipo", values=["open_interest", "gex"],
                       aggfunc="sum", fill_value=0)
    t.columns = [f"{a}_{b}" for a, b in t.columns]
    for c in ["open_interest_call", "open_interest_put", "gex_call", "gex_put"]:
        if c not in t:
            t[c] = 0.0
    t = t.rename(columns={"open_interest_call": "oi_calls", "open_interest_put": "oi_puts",
                          "gex_call": "gex_calls", "gex_put": "gex_puts"})
    t["oi_total"] = t["oi_calls"] + t["oi_puts"]
    t["gex_neto"] = t["gex_calls"] + t["gex_puts"]
    return t.reset_index().rename(columns={"strike_ref": "strike"})


def perfil_gex(df, precio_ref, config):
    """GEX total para precios hipotéticos alrededor del precio actual.

    Cada opción conserva su IV; al mover el precio, todos los futuros se
    mueven en el mismo porcentaje. Devuelve un DataFrame (precio, gex_total).
    """
    g = config["gamma_flip"]
    precios = np.linspace(precio_ref * (1 - g["rango_pct"] / 100),
                          precio_ref * (1 + g["rango_pct"] / 100), g["pasos"])
    return pd.DataFrame({"precio": precios, "gex_total": gex_en_precios(df, precio_ref, config, precios)})


def gex_en_precios(df, precio_ref, config, precios):
    """GEX total (lista) si el futuro estuviera en cada uno de `precios` (como perfil_gex)."""
    mult = df["subyacente"].map(config["multiplicadores"]).to_numpy(float)
    signo = _signo(df, config).to_numpy()
    F0 = df["futuro"].to_numpy(float)
    K = df["strike"].to_numpy(float)
    T = df["T_sesion"].to_numpy(float)
    iv = df["iv"].to_numpy(float)
    oi = df["open_interest"].to_numpy(float)
    r = config["tipo_interes"]
    totales = []
    for p in precios:
        F = F0 * p / precio_ref
        gam = black76.gamma(F, K, T, r, iv)
        totales.append(np.nansum(signo * gam * oi * mult * F ** 2 * 0.01))
    return totales


def gamma_flip(perfil, precio_ref):
    """Precio donde la GEX total pasa de negativa a positiva (la "frontera").

    Si hay varios cruces, devuelve el más cercano al precio actual.
    Devuelve None si no hay cruce dentro del rango estudiado.
    """
    p, g = perfil["precio"].to_numpy(), perfil["gex_total"].to_numpy()
    cruces = []
    for i in range(len(p) - 1):
        if g[i] < 0 <= g[i + 1] or g[i] >= 0 > g[i + 1]:
            # interpolación lineal entre los dos puntos
            cruces.append(p[i] + (p[i + 1] - p[i]) * (-g[i]) / (g[i + 1] - g[i]))
    if not cruces:
        return None
    return float(min(cruces, key=lambda x: abs(x - precio_ref)))


def por_tramos(df, columna, tramo, desde=None, hasta=None):
    """Suma `columna` por tramos de `tramo` puntos de strike (calls y puts por separado)."""
    d = df.copy()
    if desde is not None:
        d = d[(d["strike_ref"] >= desde) & (d["strike_ref"] <= hasta)]
    d["tramo"] = (d["strike_ref"] // tramo) * tramo
    t = d.pivot_table(index="tramo", columns="tipo", values=columna, aggfunc="sum", fill_value=0)
    for c in ("call", "put"):
        if c not in t:
            t[c] = 0.0
    t["neto"] = t["call"] + t["put"]
    return t.reset_index()


# --- Vanna (VEX) y charm (CEX) -----------------------------------------------
# Misma convención de posicionamiento que la GEX: los creadores de mercado
# tienen signo_gex × OI de cada opción (largos de calls, cortos de puts). Las
# dos exposiciones son el CAMBIO DE LA DELTA de esa cartera, en contratos del
# futuro del índice (y en dólares: × multiplicador × precio):
#   VEX = cambio de delta si la IV sube 1 punto (0,20 → 0,21), precio quieto.
#   CEX = cambio de delta por cada día hábil que pasa (1/252 de año), precio e
#         IV quietos, calculado en la apertura de la sesión.
# Para seguir cubiertos hacen lo CONTRARIO del cambio de su delta:
#   CEX > 0 → su delta sube con el tiempo → VENDEN hacia el cierre; CEX < 0 → compran.
#   VEX > 0 → si BAJA la IV su delta baja → COMPRAN; VEX < 0 → venden.

def _contratos_por_opcion(df, config):
    """Futuros equivalentes por opción: multiplicador de la opción / el del futuro del índice."""
    indice = config.get("indice", "NQ")
    mult_fut = config["multiplicadores"][config["indices"][indice]["subyacente"]]
    return df["subyacente"].map(config["multiplicadores"]).astype(float) / mult_fut


def anadir_vex_cex(df, config):
    """Columnas vanna, charm (por año), vex, cex (contratos) y vex_usd, cex_usd para cada opción.
    Usa las mismas columnas que la gamma: futuro, strike, T_sesion, iv, tipo, open_interest."""
    df = df.copy()
    r = config["tipo_interes"]
    F, K = df["futuro"].to_numpy(float), df["strike"].to_numpy(float)
    T, iv = df["T_sesion"].to_numpy(float), df["iv"].to_numpy(float)
    es_call = (df["tipo"] == "call").to_numpy()
    df["vanna"] = black76.vanna(F, K, T, r, iv)
    df["charm"] = np.where(es_call, black76.charm(F, K, T, r, iv, "call"), black76.charm(F, K, T, r, iv, "put"))
    base = _signo(df, config) * df["open_interest"] * _contratos_por_opcion(df, config)
    dias = config.get("dias_trading_ano", 252)
    df["vex"] = base * df["vanna"] * 0.01
    df["cex"] = base * df["charm"] / dias
    mult_fut = config["multiplicadores"][config["indices"][config.get("indice", "NQ")]["subyacente"]]
    df["vex_usd"] = df["vex"] * mult_fut * df["futuro"]
    df["cex_usd"] = df["cex"] * mult_fut * df["futuro"]
    return df


def vex_cex_por_strike(df, fecha_sesion):
    """Por strike (strike_ref): VEX y CEX totales y su parte 0DTE (vence en la sesión)."""
    d = df.assign(es_0dte=df["vencimiento"] == fecha_sesion)
    t = d.groupby("strike_ref")[["vex", "cex", "vex_usd", "cex_usd"]].sum()
    cero = d[d["es_0dte"]].groupby("strike_ref")[["vex", "cex"]].sum()
    t["vex_0dte"] = cero["vex"].reindex(t.index).fillna(0.0)
    t["cex_0dte"] = cero["cex"].reindex(t.index).fillna(0.0)
    return t.reset_index().rename(columns={"strike_ref": "strike"})


def _direccion(valor_flujo):
    return "compra" if valor_flujo > 0 else "venta" if valor_flujo < 0 else "neutro"


def resumen_vex_cex(df, fecha_sesion, config):
    """Totales del día y su lectura. Los flujos (lo que harían los creadores de mercado)
    van con el signo contrario al cambio de su delta: flujo > 0 = compran."""
    c = config.get("vanna_charm", {})
    umbral = c.get("charm_fuerte_contratos", 0)
    cero = df["vencimiento"] == fecha_sesion
    cex, cex0 = float(df["cex"].sum()), float(df.loc[cero, "cex"].sum())
    vex, vex0 = float(df["vex"].sum()), float(df.loc[cero, "vex"].sum())
    flujo_charm = -cex
    flujo_vanna = vex       # si la IV BAJA 1 punto: −(−VEX) = +VEX
    return {
        "cex_total": cex, "cex_0dte": cex0, "cex_resto": cex - cex0,
        "cex_usd": float(df["cex_usd"].sum()),
        "vex_total": vex, "vex_0dte": vex0, "vex_resto": vex - vex0,
        "vex_usd": float(df["vex_usd"].sum()),
        "charm": {"flujo": flujo_charm, "direccion": _direccion(flujo_charm),
                  "fuerte": bool(umbral and abs(cex) >= umbral), "umbral": umbral},
        "vanna": {"flujo_si_baja_iv": flujo_vanna, "direccion": _direccion(flujo_vanna)},
    }


def efecto_charm(desde, hasta, tipos, precio_ref, charm):
    """Si el flujo del charm refuerza o debilita un muro o el imán.

    Muro (calls o puts): lo refuerza el flujo que aleja el precio de él (venta si
    está encima, compra si está debajo) y lo debilita el que empuja hacia él.
    Imán: al revés, lo refuerza el flujo que lleva el precio hacia él.
    Devuelve "refuerza", "debilita" o None (no es muro ni imán, el precio está
    dentro de la zona o el charm es neutro)."""
    if not charm or charm.get("direccion") not in ("compra", "venta"):
        return None
    es_muro = "Muro de calls" in tipos or "Muro de puts" in tipos
    if not (es_muro or "Imán" in tipos):
        return None
    if desde > precio_ref:
        hacia = charm["direccion"] == "compra"
    elif hasta < precio_ref:
        hacia = charm["direccion"] == "venta"
    else:
        return None
    if es_muro:
        return "debilita" if hacia else "refuerza"
    return "refuerza" if hacia else "debilita"
