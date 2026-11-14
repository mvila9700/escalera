"""
Vista corta de la escalera: lo mismo, pero solo con los vencimientos de los
próximos N días naturales (config.yaml -> vista_corta -> dias; 7 por defecto,
de la sesión a la sesión + 6, ambos incluidos). No cambia la escalera: se
calcula aparte y se guarda en el JSON en "vista_corta".

- Frontera corta: gamma flip con esos vencimientos.
- Muro de calls corto: strike con más GEX de calls ESTRICTAMENTE POR ENCIMA del
  precio de referencia; muro de puts corto: strike con más GEX de puts (más
  negativa) ESTRICTAMENTE POR DEBAJO. Los dos dentro del rango del 95 %, con su
  OI, su GEX y su distancia al precio (puntos y σ). Si en un lado no hay
  ninguno, el muro queda vacío y se dice (no se elige otro).
- Régimen corto: signo de la GEX corta en el precio de referencia.
- Régimen combinado: «Positivo (corto y completo)», «Negativo (corto y
  completo)» o «Transición» (si no coinciden), con la franja entre las dos fronteras.
"""
from datetime import timedelta

import numpy as np

from calculos import gex


def opciones_cortas(opciones, fecha_sesion, dias):
    """Opciones que vencen en los próximos `dias` días naturales y la fecha límite."""
    limite = fecha_sesion + timedelta(days=dias - 1)
    return opciones[opciones["vencimiento"] <= limite], limite


def resumen(opciones, precio_ref, config, n=5):
    """Frontera, GEX en el precio y mayores bloques (a ±3 % del precio) de un
    conjunto de opciones. Lo usa también tareas/diagnostico_frontera.py."""
    perfil = gex.perfil_gex(opciones, precio_ref, config)
    tabla = gex.gex_por_strike(opciones)
    tabla = tabla[(tabla["strike"] >= precio_ref * 0.97) & (tabla["strike"] <= precio_ref * 1.03)]
    return {
        "vencimientos": sorted(set(opciones["vencimiento"])),
        "frontera": gex.gamma_flip(perfil, precio_ref),
        "gex_en_precio": float(np.interp(precio_ref, perfil["precio"], perfil["gex_total"])),
        "calls_oi": [(s, o) for s, o in tabla.nlargest(n, "oi_calls")[["strike", "oi_calls"]].values],
        "puts_oi": [(s, o) for s, o in tabla.nlargest(n, "oi_puts")[["strike", "oi_puts"]].values],
        "gex_calls": [(s, g) for s, g in tabla.nlargest(3, "gex_calls")[["strike", "gex_calls"]].values],
        "gex_puts": [(s, g) for s, g in tabla.nsmallest(3, "gex_puts")[["strike", "gex_puts"]].values],
    }


def regimen_combinado(regimen_completo, regimen_corto, frontera, frontera_corta):
    """Régimen para enseñar arriba: igual en las dos vistas, o «Transición»
    con la franja entre las dos fronteras."""
    if regimen_completo == regimen_corto:
        signo = "positivo" if regimen_completo == "positiva" else "negativo"
        return {"tipo": signo, "texto": f"{signo.capitalize()} (corto y completo)", "franja": None}
    fronteras = sorted(f for f in (frontera, frontera_corta) if f is not None)
    return {"tipo": "transicion", "texto": "Transición",
            "franja": [fronteras[0], fronteras[-1]] if fronteras else None}


SIN_MURO = {"call": "no hay ningún strike con GEX de calls por encima del precio en el rango del 95 %",
            "put": "no hay ningún strike con GEX de puts por debajo del precio en el rango del 95 %"}


def muro(tabla, tipo, precio_ref, sigma):
    """Muro corto de un lado: calls estrictamente por encima del precio (más GEX
    de calls) o puts estrictamente por debajo (GEX de puts más negativa).
    None si en ese lado no hay ninguno."""
    if tipo == "call":
        lado = tabla[(tabla["strike"] > precio_ref) & (tabla["gex_calls"] > 0)]
        fila = lado.loc[lado["gex_calls"].idxmax()] if not lado.empty else None
    else:
        lado = tabla[(tabla["strike"] < precio_ref) & (tabla["gex_puts"] < 0)]
        fila = lado.loc[lado["gex_puts"].idxmin()] if not lado.empty else None
    if fila is None:
        return None
    distancia = float(fila["strike"]) - precio_ref
    return {"strike": float(fila["strike"]),
            "open_interest": float(fila["oi_calls"] if tipo == "call" else fila["oi_puts"]),
            "gex": float(fila["gex_calls"] if tipo == "call" else fila["gex_puts"]),
            "distancia_puntos": distancia,
            "distancia_sigma": distancia / sigma if sigma else None}


def calcular(res, config):
    """Vista corta a partir del resultado de calculos.escalera.calcular."""
    dias = config["vista_corta"]["dias"]
    corto, limite = opciones_cortas(res["opciones"], res["fecha_sesion"], dias)
    base = {"dias": dias, "hasta": limite, "vencimientos": sorted(set(corto["vencimiento"]))}
    if corto.empty:
        return dict(base, frontera=None, regimen=None, gex_en_precio=None, muro_calls=None, muro_puts=None,
                    sin_muro={"calls": SIN_MURO["call"], "puts": SIN_MURO["put"]}, combinado=None)
    ref = res["precio_ref"]
    perfil = gex.perfil_gex(corto, ref, config)
    frontera = gex.gamma_flip(perfil, ref)
    gex_ref = float(np.interp(ref, perfil["precio"], perfil["gex_total"]))
    regimen = "positiva" if gex_ref >= 0 else "negativa"
    lo, hi = res["movimiento"]["rango_95"]
    tabla = gex.gex_por_strike(corto)
    tabla = tabla[(tabla["strike"] >= lo) & (tabla["strike"] <= hi)]
    sigma = res["movimiento"]["desviacion"]
    muro_calls, muro_puts = muro(tabla, "call", ref, sigma), muro(tabla, "put", ref, sigma)
    return dict(base, frontera=frontera, regimen=regimen, gex_en_precio=gex_ref,
                muro_calls=muro_calls, muro_puts=muro_puts,
                sin_muro={k: SIN_MURO[t] for k, t, m in (("calls", "call", muro_calls), ("puts", "put", muro_puts))
                          if m is None},
                combinado=regimen_combinado(res["regimen"], regimen, res["gamma_flip"], frontera))


def marcar_niveles(niveles, vista):
    """Pone en cada nivel "muro_corto": ["calls"] / ["puts"] / ["calls", "puts"] / []
    si el muro corto cae dentro de su zona (la frontera no se marca)."""
    for n in niveles:
        n["muro_corto"] = []
        if n.get("tipos") == ["Frontera"] or n.get("zona_desde") is None or not vista:
            continue
        for lado, muro in (("calls", vista.get("muro_calls")), ("puts", vista.get("muro_puts"))):
            if muro and n["zona_desde"] <= muro["strike"] <= n["zona_hasta"]:
                n["muro_corto"].append(lado)
    return niveles
