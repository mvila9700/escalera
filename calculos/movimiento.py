"""
Movimiento esperado del día y probabilidad de tocar un nivel.

movimiento (1 desviación) = precio × IV at the money × raíz(1/252)
   rango 68 % = precio ± 1 desviación
   rango 95 % = precio ± 2 desviaciones

Probabilidad de tocar un nivel durante el día (aproximación lognormal):
   prob_toque ≈ 2 × probabilidad de CERRAR más allá del nivel
Es la regla del "principio de reflexión"; vale como orientación, no es exacta
(supone volatilidad constante y no tiene en cuenta saltos ni noticias).
"""
import numpy as np
from scipy.stats import norm

DIA = 1 / 252


def iv_atm(df_vencimiento, futuro):
    """IV en el dinero de un vencimiento: interpolada en el precio del futuro."""
    d = df_vencimiento.dropna(subset=["iv"]).drop_duplicates("strike").sort_values("strike")
    if d.empty:
        return np.nan
    return float(np.interp(futuro, d["strike"], d["iv"]))


def straddle_atm(df_vencimiento, futuro):
    """Precio del straddle (call + put) en el strike más cercano al futuro."""
    d = df_vencimiento.dropna(subset=["precio"])
    strikes = d.groupby("strike")["tipo"].nunique()
    strikes = strikes[strikes == 2].index  # que tenga call y put
    if len(strikes) == 0:
        return np.nan, np.nan
    k = min(strikes, key=lambda s: abs(s - futuro))
    total = d[d["strike"] == k]["precio"].sum()
    return float(total), float(k)


def movimiento_esperado(precio, iv):
    una = precio * iv * np.sqrt(DIA)
    return {
        "desviacion": una,
        "rango_68": (precio - una, precio + una),
        "rango_95": (precio - 2 * una, precio + 2 * una),
    }


def prob_toque(precio, nivel, iv, t=DIA):
    """Probabilidad aproximada de que el precio toque `nivel` en el periodo t."""
    # Sin deriva: probabilidad de cerrar más allá = N(-|ln(nivel/precio)| / s)
    nivel = np.asarray(nivel, dtype=float)
    s = iv * np.sqrt(t)
    cerrar_mas_alla = norm.cdf(-np.abs(np.log(nivel / precio)) / s)
    return np.minimum(1.0, 2 * cerrar_mas_alla)
