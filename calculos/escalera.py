"""
Cálculo completo de la escalera de niveles a partir de la tabla de opciones.

Uso desde la Terminal (para comprobar los números sin la página web):
    .venv/bin/python -m calculos.escalera datos/entrada/Section40_....pdf
    .venv/bin/python -m calculos.escalera datos/entrada/Section40_....pdf 29250
    .venv/bin/python -m calculos.escalera --indice ES Section47_....pdf Section48_....pdf
(el número, opcional, es un precio de referencia escrito a mano)
"""
import sys

import numpy as np

from calculos import config as configuracion
from calculos import extendidos, gex, jerarquia, movimiento, niveles, vista_corta
from calculos.calendario import habiles_entre, siguiente_habil
from calculos.griegas import anadir_gamma, anadir_iv


def trimestrales(desde, n=3):
    """Los n próximos vencimientos trimestrales (tercer viernes de mar/jun/sep/dic)
    en o después de `desde`."""
    from fuentes.cme_pdf import _viernes_n
    fechas, anio = [], desde.year
    while len(fechas) < n:
        for mes in (3, 6, 9, 12):
            d = _viernes_n(anio, mes, 3)
            if d >= desde and len(fechas) < n:
                fechas.append(d)
        anio += 1
    return fechas


def vencimiento_limite(fecha_sesion, config, df=None):
    """Último vencimiento que entra en la escalera.

    Modo "trimestral" (por defecto): hasta el próximo trimestral inclusive; si
    faltan menos de `dias_habiles_minimos` días hábiles para él, hasta el
    siguiente. Modo "dias": un número fijo de días naturales. Modo "producto"
    (oro): lo mismo que "trimestral" con los vencimientos del producto
    `ventana_vencimientos.producto` de la tabla (MENSUAL_ACTIVO: las OG de un mes
    activo del futuro GC).
    """
    v = config["ventana_vencimientos"]
    if v["modo"] == "dias":
        from datetime import timedelta
        return fecha_sesion + timedelta(days=v["max_dias"])
    if v["modo"] == "producto":
        fechas = sorted(d for d in df.loc[df["producto"] == v["producto"], "vencimiento"].unique() if d >= fecha_sesion)
        if not fechas:
            raise ValueError(f"No hay vencimientos {v['producto']} desde el {fecha_sesion}.")
        if len(fechas) > 1 and habiles_entre(fecha_sesion, fechas[0]) < v["dias_habiles_minimos"]:
            return fechas[1]
        return fechas[0]
    proximo, siguiente = trimestrales(fecha_sesion, 2)
    return siguiente if habiles_entre(fecha_sesion, proximo) < v["dias_habiles_minimos"] else proximo


def desglose_por_vencimiento(filas):
    """GEX y open interest de un nivel separados por vencimiento.

    filas: opciones del nivel (una por vencimiento, tipo y strike, con la
    columna "gex"). La suma de "gex" de la lista es la GEX neta del nivel.
    """
    lista = []
    for venc, g in filas.groupby("vencimiento"):
        lista.append({
            "vencimiento": venc,
            "productos": sorted(g["producto"].dropna().unique()) if "producto" in g else [],
            "gex": float(g["gex"].sum()),
            "oi_calls": float(g.loc[g["tipo"] == "call", "open_interest"].sum()),
            "oi_puts": float(g.loc[g["tipo"] == "put", "open_interest"].sum()),
        })
    return lista


def calcular(df, info, config, precio_manual=None):
    """Devuelve un diccionario con todo lo necesario para la página."""
    avisos = list(info.get("avisos", []))
    fecha_datos = info["fecha_datos"]
    fecha_sesion = siguiente_habil(fecha_datos)

    # 1. Vencimientos incluidos (desde la 0DTE de la sesión hasta el límite)
    limite = vencimiento_limite(fecha_sesion, config, df)
    df = df[(df["vencimiento"] >= fecha_sesion) & (df["vencimiento"] <= limite)].copy()
    if df.empty:
        raise ValueError("No hay opciones con vencimiento cercano en los datos.")

    # 2. Precio de referencia: futuro del vencimiento más próximo, o el manual. Si la fuente dice
    #    el contrato del gráfico (oro: el contrato activo, con su precio por paridad), ese.
    if info.get("futuro_ref"):
        futuro_front, contrato_ref = float(info["futuro_ref"]), info.get("contrato_ref")
    else:
        futuro_front = float(df.loc[df["vencimiento"].idxmin(), "futuro"])
        contrato_ref = next((k for k, v in info.get("futuros", {}).items() if v == futuro_front), None)
    precio_ref = float(precio_manual) if precio_manual else futuro_front
    origen_precio = "escrito a mano" if precio_manual else "liquidación del futuro (cierre anterior)"
    # Strikes expresados en precio del futuro de referencia (por si hay
    # opciones sobre otro trimestral con distinta base)
    df["strike_ref"] = df["strike"] + (futuro_front - df["futuro"])

    # 3. IV (con la liquidación) y gamma (en la apertura, al precio de referencia)
    df = anadir_iv(df, fecha_datos, config)
    df["futuro"] = df["futuro"] * precio_ref / futuro_front
    df = anadir_gamma(df, fecha_sesion, config)
    df = gex.anadir_gex(df, config)
    df = gex.anadir_vex_cex(df, config)   # vanna y charm (aparte: no cambian los niveles)
    n_interp = int(df.drop_duplicates(["vencimiento", "strike"])["iv_interpolada"].sum())
    if n_interp:
        avisos.append(f"{n_interp} strikes sin IV calculable: IV interpolada entre strikes vecinos.")

    # 4. Movimiento esperado: IV ATM de la 0DTE (o del vencimiento más próximo)
    venc_cercano = df["vencimiento"].min()
    hay_0dte = venc_cercano == fecha_sesion
    if not hay_0dte:
        avisos.append(f"No hay opciones que venzan el {fecha_sesion}: se usa el vencimiento {venc_cercano}.")
    d0 = df[df["vencimiento"] == venc_cercano]
    iv = movimiento.iv_atm(d0, precio_ref)
    mov = movimiento.movimiento_esperado(precio_ref, iv)
    straddle, k_straddle = movimiento.straddle_atm(d0, futuro_front)

    # 5. Perfil de GEX y gamma flip
    perfil = gex.perfil_gex(df, precio_ref, config)
    flip = gex.gamma_flip(perfil, precio_ref)
    gex_actual = float(np.interp(precio_ref, perfil["precio"], perfil["gex_total"]))
    regimen = "positiva" if gex_actual >= 0 else "negativa"
    if flip is None:
        avisos.append("No se encuentra gamma flip dentro del rango estudiado.")

    # 6. Tabla por strike dentro de ±desviaciones_rango·σ (2σ = el rango del 95 %) y clasificación
    k_rango = float(config.get("desviaciones_rango", 2))
    lo, hi = precio_ref - k_rango * mov["desviacion"], precio_ref + k_rango * mov["desviacion"]
    if k_rango == 2:
        lo, hi = mov["rango_95"]          # exactamente los mismos límites de siempre
    tabla_total = gex.gex_por_strike(df)
    gex_0dte = df[df["vencimiento"] == fecha_sesion].groupby("strike_ref")["gex"].sum()
    tabla_total["gex_0dte"] = tabla_total["strike"].map(gex_0dte).fillna(0.0)
    tabla = tabla_total[(tabla_total["strike"] >= lo) & (tabla_total["strike"] <= hi)]
    marcas = niveles.clasificar(tabla, precio_ref, perfil, config, mov["rango_68"])
    zonas = niveles.agrupar(marcas, tabla, config["distancia_agrupar_puntos"])

    def completar(z, tabla):
        borde = z["desde"] if z["desde"] > precio_ref else (z["hasta"] if z["hasta"] < precio_ref else precio_ref)
        z["prob_toque"] = float(movimiento.prob_toque(precio_ref, borde, iv))
        z["texto"] = niveles.texto_zona(z)
        dentro = df[(df["strike_ref"] >= z["desde"]) & (df["strike_ref"] <= z["hasta"])]
        por_venc = dentro.groupby("vencimiento")["open_interest"].sum()
        z["venc_principal"] = por_venc.idxmax() if len(por_venc) else None
        z["gex_0dte"] = float(tabla[(tabla["strike"] >= z["desde"]) & (tabla["strike"] <= z["hasta"])]["gex_0dte"].sum())
        z["por_vencimiento"] = desglose_por_vencimiento(dentro)
        z["gex_bruta_estructural"] = jerarquia.gex_bruta_estructural(dentro, fecha_sesion)

    for z in zonas:
        completar(z, tabla)

    # 6b. Limpieza visual: fusión de niveles cercanos, categorías y ancho de zona
    cfg_j = config["jerarquia"]
    antes_fusion = len(zonas)
    ancho = jerarquia.ancho_zona(mov["desviacion"], cfg_j)
    distancia_fusion = cfg_j["fusion_factor_ancho"] * ancho
    zonas = jerarquia.fusionar(zonas, distancia_fusion, cfg_j["tipos_nunca_absorbidos"])
    umbrales = jerarquia.categorizar(zonas, cfg_j)
    for z in zonas:
        z["zona_desde"], z["zona_hasta"] = jerarquia.zona_de(z, ancho)
    # Zonas grises de gamma 0DTE: aparte de los niveles
    grises = niveles.zonas_0dte(tabla, config["gamma_0dte"], config["distancia_agrupar_puntos"]) if hay_0dte else []

    # 6c. Vanna y charm: totales del día (0DTE aparte), por strike y efecto del charm en muros e imán
    vanna_charm = gex.resumen_vex_cex(df, fecha_sesion, config)
    por_strike = gex.vex_cex_por_strike(df, fecha_sesion)
    vanna_charm["por_strike"] = por_strike[(por_strike["strike"] >= lo) & (por_strike["strike"] <= hi)]
    # 6d. Escalera extendida (de 2σ a 4σ por lado), con los MISMOS umbrales de OI y categoría
    zonas_ext = extendidos.calcular(df, tabla_total, precio_ref, mov["desviacion"],
                                    niveles.umbral_oi(tabla, config) if not tabla.empty else float("inf"),
                                    umbrales, ancho, distancia_fusion, config, completar)
    for z in zonas + zonas_ext:
        dentro = df[(df["strike_ref"] >= z["desde"]) & (df["strike_ref"] <= z["hasta"])]
        z["cex"] = float(dentro["cex"].sum())
        z["vex"] = float(dentro["vex"].sum())
        z["efecto_charm"] = gex.efecto_charm(z["desde"], z["hasta"], z["tipos"], precio_ref, vanna_charm["charm"])

    # 7. Mapas para los gráficos
    tramo = config["tramo_puntos"]
    mapa_0dte = gex.por_tramos(df[df["vencimiento"] == fecha_sesion], "gex", tramo) if hay_0dte else None
    oi_tramos = gex.por_tramos(df, "open_interest", tramo, lo, hi)

    res = {
        "indice": info.get("indice", "NQ"),
        "fecha_datos": fecha_datos,
        "fecha_sesion": fecha_sesion,
        "preliminar": info.get("preliminar", False),
        "fuente": info.get("fuente", ""),
        "precio_ref": precio_ref,
        "origen_precio": origen_precio,
        "futuro_liquidacion": futuro_front,
        "contrato_ref": contrato_ref,
        "futuros": info.get("futuros", {}),
        "vencimiento_limite": limite,
        "regimen": regimen,
        "gex_total": gex_actual,
        "gamma_flip": flip,
        "iv_atm": iv,
        "vencimiento_iv": venc_cercano,
        "movimiento": mov,
        "straddle": straddle,
        "strike_straddle": k_straddle,
        "zonas": zonas,
        "zonas_extendidas": zonas_ext,   # de 2σ a 4σ (aparte: no cambia la escalera)
        "zonas_0dte": grises,
        "tabla_strikes": tabla,
        "perfil": perfil,
        "mapa_0dte": mapa_0dte,
        "oi_tramos": oi_tramos,
        "opciones": df,
        "avisos": avisos,
        "vanna_charm": vanna_charm,
        "comprobaciones": info.get("comprobaciones"),
        "productos_desconocidos": info.get("productos_desconocidos") or [],
        "vencimientos_incluidos": sorted(df["vencimiento"].unique()),
        "jerarquia": {"umbrales_gex_bruta": umbrales, "niveles_antes_de_fusionar": antes_fusion,
                      "ancho_zona": ancho, "distancia_fusion": distancia_fusion},
    }
    # Vista corta (solo los vencimientos de los próximos N días): aparte, no cambia la escalera
    res["vista_corta"] = vista_corta.calcular(res, config)
    vista_corta.marcar_niveles(zonas, res["vista_corta"])
    return res


def imprimir(res):
    f = lambda x: f"{x:,.0f}".replace(",", ".")
    m = res["movimiento"]
    print(f"Sesión: {res['fecha_sesion']}  |  datos del {res['fecha_datos']}"
          + ("  (PRELIMINARES)" if res["preliminar"] else ""))
    print(f"Precio ref.: {f(res['precio_ref'])} ({res['origen_precio']})  |  gamma {res['regimen'].upper()}"
          f"  |  GEX total {res['gex_total'] / 1e9:+.2f} mil M$ por 1 %")
    print(f"IV ATM ({res['vencimiento_iv']}): {res['iv_atm']:.1%}  |  1 desviación = {m['desviacion']:.0f} puntos")
    print(f"Rango 68 %: {f(m['rango_68'][0])} – {f(m['rango_68'][1])}  |  "
          f"Rango 95 %: {f(m['rango_95'][0])} – {f(m['rango_95'][1])}")
    print(f"Straddle ATM {f(res['strike_straddle'])}: {res['straddle']:.1f} puntos")
    flip = f(res["gamma_flip"]) if res["gamma_flip"] else "no encontrado"
    print(f"Gamma flip: {flip}\n")
    filas = [(z["desde"], z["texto"], ", ".join(z["tipos"] + [f"({n})" for n in z["notas"]]),
              z["oi_calls"], z["oi_puts"],
              z["gex_neto"], z["prob_toque"]) for z in res["zonas"]]
    filas.append((res["precio_ref"], f(res["precio_ref"]), "Precio ref.", None, None, None, None))
    if res["gamma_flip"]:
        filas.append((res["gamma_flip"], f(res["gamma_flip"]), "Frontera", None, None, None,
                      float(movimiento.prob_toque(res["precio_ref"], res["gamma_flip"], res["iv_atm"]))))
    print(f"{'Nivel':<14} {'Tipo':<30} {'OI calls':>9} {'OI puts':>9} {'GEX M$':>8} {'P toque':>8}")
    for _, txt, tipos, oc, op, g, p in sorted(filas, key=lambda x: -x[0]):
        num = lambda v, fmt: format(v, fmt) if v is not None else ""
        print(f"{txt:<14} {tipos:<30} {num(oc, '9.0f'):>9} {num(op, '9.0f'):>9} "
              f"{num(g / 1e6 if g is not None else None, '8.0f'):>8} {num(p, '8.0%'):>8}")
    if res["avisos"]:
        print("\nAvisos:")
        for a in res["avisos"]:
            print(f"  - {a}")


if __name__ == "__main__":
    from fuentes import cme_pdf
    argumentos = sys.argv[1:]
    indice = "NQ"
    if argumentos[:1] == ["--indice"]:
        indice, argumentos = argumentos[1], argumentos[2:]
    pdfs = [a for a in argumentos if a.lower().endswith(".pdf")]
    manual = next((float(a) for a in argumentos if not a.lower().endswith(".pdf")), None)
    config = configuracion.para_indice(configuracion.cargar(), indice)
    tabla_opciones, datos = cme_pdf.leer(pdfs, config, indice)
    imprimir(calcular(tabla_opciones, datos, config, manual))
