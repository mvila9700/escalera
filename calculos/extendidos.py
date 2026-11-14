"""
Escalera EXTENDIDA: niveles entre ±desviaciones_rango·σ (2σ, el 95 %) y ±desviaciones_extendido·σ
(4σ), por cada lado. No cambia la escalera de siempre: va aparte (res["zonas_extendidas"]).

Usa los MISMOS umbrales que la escalera de dentro del 95 % (no se recalculan con la tabla ampliada):
- OI relevante: el umbral de OI del día (percentil `niveles.oi_relevante_percentil` de la tabla ±2σ);
- categoría: los percentiles de GEX bruta estructural de los niveles de ±2σ (p_principal,
  p_secundario); muros e imán, siempre principales (como dentro);
- fusión y ancho de zona: los mismos.

Tipos, en cada franja (por debajo y por encima por separado):
- Muro de puts (franja de abajo) / Muro de calls (franja de arriba): el strike con más puts / calls
  de la franja, si ese tipo de OI SOLO ya pasa el umbral de OI relevante;
- Imán: la misma regla de siempre (más OI a `iman_distancia_puntos` del precio): con la distancia
  de config.yaml no llega a la franja, así que en la práctica no sale;
- Aceleración: hasta `num_aceleraciones` strikes con OI relevante y gamma negativa en el strike
  (calculada en el propio strike, no con el perfil, que solo llega a ±rango_pct);
- OI relevante: el resto de strikes con OI relevante.
Solo se quedan los niveles PRINCIPALES y SECUNDARIOS (los menores no, para no llenar el gráfico).
Las resistencias no se añaden.
"""
import numpy as np

from calculos import gex, jerarquia, niveles

TIPOS = ["Muro de calls", "Muro de puts", "Imán", "Aceleración", "OI relevante"]


def franjas(precio_ref, sigma, config):
    """[(lo, hi)] de las dos franjas: (P−4σ, P−2σ) y (P+2σ, P+4σ), extremos interiores excluidos."""
    dentro = float(config.get("desviaciones_rango", 2))
    fuera = float(config.get("desviaciones_extendido", 4))
    if fuera <= dentro:
        return []
    return [(precio_ref - fuera * sigma, precio_ref - dentro * sigma),
            (precio_ref + dentro * sigma, precio_ref + fuera * sigma)]


def marcar(tabla_total, df, precio_ref, sigma, umbral, config):
    """{strike: set(tipos)} en las dos franjas, con los umbrales de dentro."""
    c = config["niveles"]
    marcas = {}

    def poner(s, tipo):
        marcas.setdefault(float(s), set()).add(tipo)

    for i, (lo, hi) in enumerate(franjas(precio_ref, sigma, config)):
        # Por debajo: [lo, hi) · por encima: (lo, hi] (lo de ±2σ exacto es de la escalera normal)
        f = tabla_total[(tabla_total["strike"] >= lo) & (tabla_total["strike"] < hi)] if i == 0 else \
            tabla_total[(tabla_total["strike"] > lo) & (tabla_total["strike"] <= hi)]
        if f.empty:
            continue
        relevante = f[f["oi_total"] >= umbral]
        if relevante.empty:
            continue
        # Muro de su lado (puts por debajo, calls por encima), y que ese tipo solo ya pase el umbral:
        # el strike con más calls de la franja de abajo puede tener unos cientos (no es un muro)
        col, tipo = ("oi_puts", "Muro de puts") if i == 0 else ("oi_calls", "Muro de calls")
        if relevante[col].max() >= umbral:
            poner(relevante.loc[relevante[col].idxmax(), "strike"], tipo)
        cerca = f[(f["strike"] - precio_ref).abs() <= c["iman_distancia_puntos"]]
        if not cerca.empty:
            poner(cerca.loc[cerca["oi_total"].idxmax(), "strike"], "Imán")
        g = np.asarray(gex.gex_en_precios(df, precio_ref, config, relevante["strike"].to_numpy(float)))
        for s in relevante[g < 0].nlargest(c["num_aceleraciones"], "oi_total")["strike"]:
            poner(s, "Aceleración")
        for s in relevante["strike"]:
            if float(s) not in marcas:
                poner(s, "OI relevante")
    return marcas


def categoria(z, umbrales, cfg_j):
    """Categoría con los umbrales YA calculados dentro del 95 % (no se recalculan)."""
    if set(cfg_j["tipos_siempre_principales"]) & set(z["tipos"]) or z["gex_bruta_estructural"] >= umbrales["p_principal"]:
        return "principal"
    if z["gex_bruta_estructural"] >= umbrales["p_secundario"]:
        return "secundario"
    return "menor"


def calcular(df, tabla_total, precio_ref, sigma, umbral, umbrales, ancho, distancia_fusion, config, completar):
    """Zonas extendidas (principales y secundarias). `completar(z, tabla)` rellena cada zona igual
    que en la escalera normal (probabilidad, desglose, GEX bruta estructural...)."""
    marcas = marcar(tabla_total, df, precio_ref, sigma, umbral, config)
    if not marcas:
        return []
    tabla = tabla_total[tabla_total["strike"].isin(list(marcas))]
    zonas = niveles.agrupar(marcas, tabla, config["distancia_agrupar_puntos"])
    for z in zonas:
        completar(z, tabla)
    cfg_j = config["jerarquia"]
    zonas = jerarquia.fusionar(zonas, distancia_fusion, cfg_j["tipos_nunca_absorbidos"])
    out = []
    for z in zonas:
        z["categoria"] = categoria(z, umbrales, cfg_j)
        if z["categoria"] in ("principal", "secundario"):
            z["zona_desde"], z["zona_hasta"] = jerarquia.zona_de(z, ancho)
            z["extendido"] = True
            out.append(z)
    return out
