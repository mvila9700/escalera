"""
Modelo Black-76: el adecuado para opciones sobre futuros (como las del NQ).

Notación:
    F     precio del futuro
    K     strike
    T     tiempo hasta el vencimiento, en años
    r     tipo de interés libre de riesgo anual (0.04 = 4 %)
    sigma volatilidad implícita anual (0.20 = 20 %)

Todas las funciones aceptan números sueltos o arrays de numpy.
"""
import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm


def _d1_d2(F, K, T, sigma):
    raiz_t = np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * raiz_t)
    return d1, d1 - sigma * raiz_t


def precio(F, K, T, r, sigma, tipo):
    """Precio teórico de una call o una put ('call'/'put')."""
    d1, d2 = _d1_d2(F, K, T, sigma)
    descuento = np.exp(-r * T)
    if tipo == "call":
        return descuento * (F * norm.cdf(d1) - K * norm.cdf(d2))
    return descuento * (K * norm.cdf(-d2) - F * norm.cdf(-d1))


def delta(F, K, T, r, sigma, tipo):
    d1, _ = _d1_d2(F, K, T, sigma)
    descuento = np.exp(-r * T)
    return descuento * norm.cdf(d1) if tipo == "call" else -descuento * norm.cdf(-d1)


def gamma(F, K, T, r, sigma):
    """Gamma: cuánto cambia la delta por cada punto que se mueve el futuro.
    Es igual para calls y puts."""
    d1, _ = _d1_d2(F, K, T, sigma)
    return np.exp(-r * T) * norm.pdf(d1) / (F * sigma * np.sqrt(T))


def vanna(F, K, T, r, sigma):
    """Vanna: cuánto cambia la delta cuando la volatilidad sube 1 (=100 puntos de IV).
    Es ∂delta/∂sigma = ∂vega/∂F = −e^(−rT)·φ(d1)·d2/σ, igual para calls y puts
    (la delta de la put es la de la call menos e^(−rT), que no depende de σ).
    Para 1 punto de IV (0,20 → 0,21) hay que multiplicar por 0,01."""
    d1, d2 = _d1_d2(F, K, T, sigma)
    return -np.exp(-r * T) * norm.pdf(d1) * d2 / sigma


def charm(F, K, T, r, sigma, tipo):
    """Charm: cuánto cambia la delta al pasar el tiempo (precio e IV quietos),
    por AÑO (con T en años de 252 días hábiles: /252 = por día hábil).

    Es −∂delta/∂T (el tiempo que queda baja). Con ∂d1/∂T = −d2/(2T):
        call: r·e^(−rT)·N(d1)  + e^(−rT)·φ(d1)·d2/(2T)
        put: −r·e^(−rT)·N(−d1) + e^(−rT)·φ(d1)·d2/(2T)
    Una call fuera del dinero (d2 < 0) pierde delta con el tiempo; una put
    fuera del dinero (d2 > 0) también se acerca a 0 (su delta negativa sube)."""
    d1, d2 = _d1_d2(F, K, T, sigma)
    descuento = np.exp(-r * T)
    comun = descuento * norm.pdf(d1) * d2 / (2 * T)
    if tipo == "call":
        return r * descuento * norm.cdf(d1) + comun
    return -r * descuento * norm.cdf(-d1) + comun


def volatilidad_implicita(precio_mercado, F, K, T, r, tipo):
    """Volatilidad que hace que el precio teórico coincida con el de mercado.

    Devuelve NaN si no tiene solución (precio por debajo del valor
    intrínseco, precio vacío, etc.) en vez de inventarse un número.
    """
    if not (precio_mercado and precio_mercado > 0 and T > 0):
        return np.nan
    descuento = np.exp(-r * T)
    intrinseco = descuento * max(F - K, 0) if tipo == "call" else descuento * max(K - F, 0)
    if precio_mercado <= intrinseco + 1e-9:
        return np.nan
    funcion = lambda s: precio(F, K, T, r, s, tipo) - precio_mercado
    try:
        return brentq(funcion, 1e-4, 5.0, xtol=1e-7)
    except ValueError:
        return np.nan
