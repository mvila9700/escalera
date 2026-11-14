"""
Clasificación de niveles y agrupación en zonas.

Tipos (todos los umbrales en config.yaml, apartado "niveles"):
- Resistencia:   strikes por encima del precio con la mayor GEX positiva
                 (y open interest relevante).
- Frontera:      el gamma flip.
- Aceleración:   strikes con open interest relevante en zona de gamma negativa.
- Imán:          strike con más open interest total cerca del precio.
- Muro de calls: strike con más CALLS abiertas (open interest, no GEX).
- Muro de puts:  strike con más PUTS abiertas (open interest, no GEX).
- OI relevante:  strikes que entran siempre en la escalera aunque no tengan
                 otro tipo: los del 25 % con más OI dentro del rango del 68 %
                 y los números redondos (cada 500) con OI relevante dentro
                 del rango del 95 %.

La GEX se usa para el régimen, la frontera, las resistencias y las zonas de
aceleración; los muros y el "OI relevante" van por open interest.

Los textos de "qué suele hacer el precio" describen el comportamiento
HABITUAL que se atribuye a cada nivel; no es una garantía.
"""
import numpy as np

QUE_SUELE_HACER = {
    # Textos de la página (Fase F-2, 2026-11-12): lo que dicen los datos del estudio de fiabilidad
    # (tareas/estudio_fiabilidad.py), no lo que supone la teoría. Los números de cada índice, en la
    # columna «Fiabilidad histórica».
    "Resistencia": "Gamma positiva (las coberturas venden en las subidas). Contexto: al primer toque no frena más que un precio cualquiera.",
    "Muro de calls": "Mayor bloque de calls abiertas. Por encima de la frontera aguanta algo más que un precio cualquiera; por debajo, no.",
    "Muro de puts": "Mayor bloque de puts abiertas. Por encima de la frontera aguanta algo más que un precio cualquiera; por debajo, no.",
    "Imán": "OI máximo: el strike con más open interest cerca del precio. Los datos no muestran que atraiga ni que repela: el precio llega como a cualquier otro precio a la misma distancia.",
    "Aceleración": "Gamma negativa (las coberturas empujan a favor del movimiento). Contexto: al primer toque no tiene ventaja sobre un precio cualquiera.",
    "OI relevante": "Mucho open interest. Contexto: al primer toque no tiene ventaja sobre un precio cualquiera.",
    "Frontera": "Gamma flip: por encima el mercado tiende a calmarse; por debajo, a moverse más y todo rompe más.",
    "Precio ref.": "Precio de referencia del futuro.",
}

ORDEN_TIPOS = ["Muro de calls", "Resistencia", "Imán", "Aceleración", "Muro de puts", "OI relevante"]

# Nota en gris (no es un tipo de nivel): mucha gamma de la 0DTE, poco OI.
NOTA_0DTE = "gamma 0DTE"


def umbral_oi(tabla, config):
    """Open interest mínimo para considerarlo "relevante".

    Percentil configurable (75 = el 25 % con más OI), calculado solo con los
    strikes de la rejilla estándar (p. ej. múltiplos de 25): los strikes
    intermedios de las diarias son muchos y con poco OI, y bajarían el umbral.
    """
    c = config["niveles"]
    rejilla = c.get("rejilla_umbral_oi", 0)
    base = tabla[tabla["strike"] % rejilla == 0] if rejilla else tabla
    if base.empty:
        base = tabla
    return float(np.percentile(base["oi_total"], c["oi_relevante_percentil"]))


def clasificar(tabla, precio_ref, perfil, config, rango_68=None):
    """Devuelve un dict {strike: set(tipos)} con los strikes destacados.

    tabla:    resultado de gex.gex_por_strike, ya limitado al rango del 95 %.
    perfil:   resultado de gex.perfil_gex (para saber dónde la gamma es negativa).
    rango_68: (mínimo, máximo) del rango del 68 %.
    """
    c = config["niveles"]
    marcas = {}

    def marcar(strike, tipo):
        marcas.setdefault(float(strike), set()).add(tipo)

    if tabla.empty:
        return marcas

    umbral = umbral_oi(tabla, config)
    relevante = tabla[tabla["oi_total"] >= umbral]

    encima = relevante[(relevante["strike"] > precio_ref) & (relevante["gex_neto"] > 0)]
    for s in encima.nlargest(c["num_resistencias"], "gex_neto")["strike"]:
        marcar(s, "Resistencia")

    gex_en_strike = np.interp(relevante["strike"], perfil["precio"], perfil["gex_total"])
    negativa = relevante[gex_en_strike < 0]
    for s in negativa.nlargest(c["num_aceleraciones"], "oi_total")["strike"]:
        marcar(s, "Aceleración")

    cerca = tabla[(tabla["strike"] - precio_ref).abs() <= c["iman_distancia_puntos"]]
    if not cerca.empty:
        marcar(cerca.loc[cerca["oi_total"].idxmax(), "strike"], "Imán")

    # Muros: por número de contratos abiertos
    if (tabla["oi_calls"] > 0).any():
        marcar(tabla.loc[tabla["oi_calls"].idxmax(), "strike"], "Muro de calls")
    if (tabla["oi_puts"] > 0).any():
        marcar(tabla.loc[tabla["oi_puts"].idxmax(), "strike"], "Muro de puts")

    # Strikes que entran siempre: OI relevante en el 68 % y redondos en el 95 %
    con_tipo = {s for s, t in marcas.items() if t & set(ORDEN_TIPOS)}
    incluir = set()
    if rango_68 is not None:
        en_68 = relevante[relevante["strike"].between(*rango_68)]
        incluir |= set(en_68["strike"].astype(float))
    cada = c.get("numero_redondo_cada", 0)
    if cada:
        incluir |= set(relevante[relevante["strike"] % cada == 0]["strike"].astype(float))
    for s in incluir - con_tipo:
        marcar(s, "OI relevante")

    return marcas


def zonas_0dte(tabla, cfg, distancia):
    """Zonas grises de gamma 0DTE (no son niveles).

    Un strike entra si la 0DTE domina su gamma (|GEX 0DTE| > `dominio` ×
    (|GEX calls| + |GEX puts|)) y su |GEX 0DTE| está en el percentil
    `percentil` o más de los strikes de la tabla (rango del 95 %). Los
    strikes a menos de `distancia` puntos se unen en una zona.
    """
    if tabla.empty or "gex_0dte" not in tabla:
        return []
    g0 = tabla["gex_0dte"].abs()
    bruta = tabla["gex_calls"].abs() + tabla["gex_puts"].abs()
    umbral = float(np.percentile(g0, cfg["percentil"]))
    elegidos = tabla[(g0 > cfg["dominio"] * bruta) & (g0 >= umbral) & (g0 > 0)].sort_values("strike")
    zonas = []
    for _, f in elegidos.iterrows():
        if zonas and f["strike"] - zonas[-1]["hasta"] < distancia:
            zonas[-1]["hasta"] = float(f["strike"])
        else:
            zonas.append({"desde": float(f["strike"]), "hasta": float(f["strike"])})
    for z in zonas:
        dentro = tabla[tabla["strike"].between(z["desde"], z["hasta"])]
        z["gex_0dte"] = float(dentro["gex_0dte"].sum())
        z["strikes"] = [float(x) for x in elegidos[elegidos["strike"].between(z["desde"], z["hasta"])]["strike"]]
        z["zona_desde"], z["zona_hasta"] = z["desde"] - cfg["ancho_puntos"], z["hasta"] + cfg["ancho_puntos"]
        z["texto"] = texto_zona(z)
    return zonas


def agrupar(marcas, tabla, distancia):
    """Une strikes destacados a menos de `distancia` puntos en una sola zona.

    Cada zona suma el OI y la GEX de todos los strikes que contiene.
    """
    zonas = []
    for s in sorted(marcas):
        if zonas and s - zonas[-1]["hasta"] < distancia:
            zonas[-1]["hasta"] = s
            zonas[-1]["tipos"] |= marcas[s]
        else:
            zonas.append({"desde": s, "hasta": s, "tipos": set(marcas[s])})
    for z in zonas:
        dentro = tabla[(tabla["strike"] >= z["desde"]) & (tabla["strike"] <= z["hasta"])]
        z["oi_calls"] = float(dentro["oi_calls"].sum())
        z["oi_puts"] = float(dentro["oi_puts"].sum())
        z["gex_calls"] = float(dentro["gex_calls"].sum())
        z["gex_puts"] = float(dentro["gex_puts"].sum())
        z["gex_neto"] = float(dentro["gex_neto"].sum())
        z["gex_abs"] = abs(z["gex_neto"])   # para la jerarquía visual (Fase 2a)
        z["notas"] = [NOTA_0DTE] if NOTA_0DTE in z["tipos"] else []
        tipos = [t for t in ORDEN_TIPOS if t in z["tipos"]]
        # "OI relevante" sobra si la zona ya tiene un tipo más concreto
        if len(tipos) > 1 and "OI relevante" in tipos:
            tipos.remove("OI relevante")
        z["tipos"] = tipos
    return zonas


def texto_zona(z):
    """'29.400-29.433' o '29.400' con punto de miles."""
    f = lambda x: f"{x:,.0f}".replace(",", ".")
    return f(z["desde"]) if z["desde"] == z["hasta"] else f"{f(z['desde'])}-{f(z['hasta'])}"
