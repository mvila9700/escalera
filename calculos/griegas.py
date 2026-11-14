"""
Añade a la tabla de opciones la volatilidad implícita (IV) y la gamma.

Cómo se elige la IV de cada strike:
1. Se calcula con Black-76 a partir del precio de liquidación, usando la
   opción FUERA del dinero (call si strike >= futuro, put si strike < futuro).
   Las opciones muy dentro del dinero casi no tienen valor temporal y su IV
   sale poco fiable.
2. La call y la put del mismo strike y vencimiento usan esa misma IV
   (en teoría es la misma por la paridad put-call).
3. Si un strike no tiene IV calculable, se interpola entre los strikes
   vecinos del mismo vencimiento. Esas filas quedan marcadas
   (iv_interpolada = True) para poder avisarlo.
"""
import numpy as np

from calculos import black76
from calculos.tiempo import HORA_CIERRE, anios_habiles


def _hora_vencimiento(producto, config):
    h = config["horas_et"]
    return h["vencimiento_trimestral"] if producto == "TRIMESTRAL" else h["vencimiento"]


def anadir_iv(df, fecha_datos, config):
    """Columnas nuevas: T_liq (tiempo desde la liquidación), iv, iv_interpolada."""
    r = config["tipo_interes"]
    df = df.copy()
    df["T_liq"] = [
        anios_habiles(fecha_datos, config["horas_et"].get("liquidacion", HORA_CIERRE), v,
                      _hora_vencimiento(p, config))
        for v, p in zip(df["vencimiento"], df["producto"])
    ]
    iv_propia = [
        black76.volatilidad_implicita(p, F, K, T, r, tipo)
        for p, F, K, T, tipo in zip(df["precio"], df["futuro"], df["strike"], df["T_liq"], df["tipo"])
    ]
    df["iv_propia"] = iv_propia

    fuera_dinero = np.where(df["strike"] >= df["futuro"], df["tipo"] == "call", df["tipo"] == "put")
    iv_otm = (df[fuera_dinero].groupby(["vencimiento", "strike"])["iv_propia"].first()
              .rename("iv_otm"))
    df = df.join(iv_otm, on=["vencimiento", "strike"])
    df["iv"] = df["iv_otm"].fillna(df["iv_propia"])
    df["iv_interpolada"] = df["iv"].isna()

    # Interpolación por strike dentro de cada vencimiento
    for _, grupo in df.groupby("vencimiento"):
        validos = grupo.dropna(subset=["iv"]).drop_duplicates("strike").sort_values("strike")
        faltan = grupo.index[grupo["iv"].isna()]
        if len(faltan) and len(validos):
            df.loc[faltan, "iv"] = np.interp(df.loc[faltan, "strike"], validos["strike"], validos["iv"])
    return df.drop(columns=["iv_otm"])


def anadir_gamma(df, fecha_sesion, config, precio_futuro=None):
    """Gamma de cada opción en la APERTURA de la sesión que vas a operar.

    precio_futuro: si se da, sustituye al futuro de cada fila (se usa para
    recalcular la gamma con precios hipotéticos).
    """
    h = config["horas_et"]
    df = df.copy()
    df["T_sesion"] = [
        anios_habiles(fecha_sesion, h["apertura"], v, _hora_vencimiento(p, config),
                      minimo_horas=h["tiempo_minimo"])
        for v, p in zip(df["vencimiento"], df["producto"])
    ]
    F = df["futuro"] if precio_futuro is None else precio_futuro
    df["gamma"] = black76.gamma(F, df["strike"], df["T_sesion"], config["tipo_interes"], df["iv"])
    return df
