"""
Validación del charm con las escaleras reconstruidas del ES y las velas de 1 minuto.

    # 1) Charm y vanna de cada sesión reconstruida (datos de Databento ya descargados; no descarga nada):
    .venv/bin/python -m tareas.validar_charm calcular --desde 2025-10-31 --hasta 2026-10-29
    # 2) Informe: ¿la última hora (15:00-16:00 NY) sube más con charm comprador fuerte y baja con vendedor?
    .venv/bin/python -m tareas.validar_charm informe

Paso 1: misma escalera que tareas.reconstruir_escalera (calculos.escalera.calcular sobre
fuentes.databento_oi), y guarda solo los totales de vanna y charm en
datos/historico_reconstruido/ES_vanna_charm.csv (los ESC2 reconstruidos no se tocan).

Paso 2: umbral de «fuerte» = percentil 67 de |charm| en el periodo de ajuste (nov-jul): el
tercio de días con más charm. Se fija SOLO con la distribución del charm (no se busca el que
mejor sale con los precios) y se comprueba sin tocarlo en ago-oct.
"""
import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

CARPETA = Path("datos/historico_reconstruido")
CSV = CARPETA / "ES_vanna_charm.csv"
VELAS = {"ES": Path("datos/velas/ES_v_0_2021-10-29_2026-10-29.csv"),
         "NQ": Path("datos/velas/MNQ_v_0_2021-10-29_2026-10-31.csv")}
AJUSTE = ("2025-11-01", "2026-07-31")
COMPROBACION = ("2026-08-01", "2026-10-31")
PERCENTIL_FUERTE = 67
INFORME = Path("documentacion/validacion_charm.md")


# --- Paso 1 -------------------------------------------------------------------
def _un_dia(dia):
    from calculos import config as configuracion
    from calculos.escalera import calcular
    from fuentes import databento_oi
    config = configuracion.para_indice(configuracion.cargar(), "ES")
    try:
        df, info = databento_oi.leer(dia)
        res = calcular(df, info, config)
    except Exception as e:      # día sin datos o incompleto: se apunta y se sigue
        return {"fecha_datos": str(dia), "error": str(e)[:200]}
    v = res["vanna_charm"]
    return {"fecha_datos": str(dia), "fecha_sesion": str(res["fecha_sesion"]), "precio_ref": res["precio_ref"],
            "cex_total": v["cex_total"], "cex_0dte": v["cex_0dte"], "cex_resto": v["cex_resto"],
            "vex_total": v["vex_total"], "vex_0dte": v["vex_0dte"], "gex_total": res["gex_total"],
            "hay_0dte": bool(res["mapa_0dte"] is not None), "error": ""}


def calcular(desde, hasta, procesos=6):
    from tareas.databento_oi import dias_de_datos
    dias = dias_de_datos(desde, hasta)
    with ProcessPoolExecutor(procesos) as ex:
        filas = list(ex.map(_un_dia, dias))
    t = pd.DataFrame(filas)
    CARPETA.mkdir(parents=True, exist_ok=True)
    t.to_csv(CSV, index=False)
    return t


# --- Paso 2 -------------------------------------------------------------------
def ultima_hora(ruta):
    """Por sesión (fecha NY): rentabilidad (%) de 15:00 a 16:00 y, como control, de 9:30 a 15:00.
    Se quitan los días de cambio de contrato y los de cierre anticipado (sin velas a las 15:59)."""
    v = pd.read_csv(ruta, usecols=["ts_event", "open", "close", "symbol", "instrument_id"])
    v.index = pd.to_datetime(v["ts_event"], utc=True).dt.tz_convert("America/New_York")
    v = v[(v.index >= "2025-10-30") & (v.index.time >= pd.Timestamp("09:30").time())
          & (v.index.time < pd.Timestamp("16:00").time())]
    v["dia"] = v.index.strftime("%Y-%m-%d")
    v["hhmm"] = v.index.strftime("%H:%M")
    filas = []
    for dia, h in v.groupby("dia"):
        if h["instrument_id"].nunique() > 1:
            continue                                   # cambio de contrato dentro de la sesión
        idx = h.set_index("hhmm")
        if not {"09:30", "15:00", "15:59"} <= set(idx.index):
            continue
        a930, a15, c16 = idx.loc["09:30", "open"], idx.loc["15:00", "open"], idx.loc["15:59", "close"]
        filas.append({"fecha_sesion": dia, "ret_ultima": (c16 / a15 - 1) * 100,
                      "ret_antes": (a15 / a930 - 1) * 100, "puntos_ultima": c16 - a15})
    return pd.DataFrame(filas)


def _grupo(x):
    return {"n": len(x), "media": x.mean() if len(x) else np.nan, "mediana": x.median() if len(x) else np.nan,
            "sube": (x > 0).mean() * 100 if len(x) else np.nan}


def _p(a, b):
    if len(a) < 3 or len(b) < 3:
        return np.nan
    return stats.ttest_ind(a, b, equal_var=False).pvalue


def tabla_periodo(t, umbral, columna="ret_ultima"):
    """Grupos: comprador fuerte, vendedor fuerte, resto (débiles)."""
    compra = t[(t["flujo"] > 0) & (t["flujo"].abs() >= umbral)][columna]
    venta = t[(t["flujo"] < 0) & (t["flujo"].abs() >= umbral)][columna]
    resto = t[t["flujo"].abs() < umbral][columna]
    rho, p_rho = stats.spearmanr(t["flujo"], t[columna]) if len(t) > 3 else (np.nan, np.nan)
    return {"compra": _grupo(compra), "venta": _grupo(venta), "resto": _grupo(resto),
            "p_compra": _p(compra, resto), "p_venta": _p(venta, resto),
            "rho": rho, "p_rho": p_rho, "n": len(t)}


def _fmt(x, d=3):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}".replace(".", ",")


def _md(nombre, r, puntos=None):
    filas = [f"**{nombre}** ({r['n']} sesiones)", "",
             "| Grupo | Días | Media % | Mediana % | Días que sube | p (vs resto) |",
             "|---|---:|---:|---:|---:|---:|"]
    for clave, etiqueta, p in (("compra", "Charm comprador fuerte", r["p_compra"]),
                               ("venta", "Charm vendedor fuerte", r["p_venta"]),
                               ("resto", "Resto (charm débil)", None)):
        g = r[clave]
        filas.append(f"| {etiqueta} | {g['n']} | {_fmt(g['media'])} | {_fmt(g['mediana'])} | "
                     f"{_fmt(g['sube'], 0)} % | {_fmt(p, 2) if p is not None else ''} |")
    filas += ["", f"Correlación de rangos (Spearman) entre el flujo del charm y la última hora: "
                  f"{_fmt(r['rho'], 2)} (p = {_fmt(r['p_rho'], 2)})", ""]
    return filas


def informe():
    t = pd.read_csv(CSV)
    errores = t[t["error"].fillna("") != ""]
    t = t[t["error"].fillna("") == ""].copy()
    t["flujo"] = -t["cex_total"]          # contratos que compran (+) o venden (−) los creadores
    t["flujo_0dte"] = -t["cex_0dte"]
    ajuste = t[(t["fecha_sesion"] >= AJUSTE[0]) & (t["fecha_sesion"] <= AJUSTE[1])]
    umbral = float(np.percentile(ajuste["flujo"].abs(), PERCENTIL_FUERTE))
    umbral0 = float(np.percentile(ajuste["flujo_0dte"].abs(), PERCENTIL_FUERTE))
    lineas = ["# Validación del charm (ES): ¿mueve la última hora?", "",
              f"Generado con `tareas/validar_charm.py` sobre {len(t)} escaleras reconstruidas del ES "
              f"(datos de Databento ya descargados) y las velas de 1 minuto de `datos/velas`. "
              f"No se ha descargado nada.", "",
              "## Qué se mide", "",
              "- **Charm del día**: lo que tendrían que comprar (+) o vender (−) los creadores de mercado por "
              "cada día que pasa, con precio e IV quietos, calculado en la apertura con la escalera de esa "
              "sesión (mismo supuesto de posición que la GEX: largos de calls, cortos de puts). En contratos ES.",
              "- **Última hora**: rentabilidad del futuro de 15:00 a 16:00 de Nueva York (apertura de la vela "
              "de 15:00 al cierre de la de 15:59). Se quitan los días de cambio de contrato y los de cierre "
              "anticipado.",
              f"- **Fuerte**: |charm| ≥ percentil {PERCENTIL_FUERTE} del periodo de ajuste (nov-jul) = "
              f"**{_miles(umbral)} contratos/día** (0DTE sola: {_miles(umbral0)}). Se fija solo con la "
              f"distribución del charm, sin mirar los precios, y se aplica igual en ago-oct.",
              "- **p**: prueba t de Welch del grupo frente al resto (p < 0,05 sería significativo).", "",
              "## Lo primero: el charm sale comprador todos los días", "",
              f"De {len(t)} sesiones, el charm total sale **comprador en {(t['flujo'] > 0).sum()}** y vendedor en "
              f"{(t['flujo'] < 0).sum()} (0DTE: comprador en {(t['flujo_0dte'] > 0).sum()}); la vanna, «si baja la IV, "
              f"comprador» en {(t['vex_total'] > 0).sum()}. Rango del charm: {_miles(t['flujo'].min())} a "
              f"{_miles(t['flujo'].max())} contratos/día (mediana {_miles(t['flujo'].median())}).", "",
              "No es un error: es lo que da el supuesto de posición de la GEX. Con los creadores largos de calls y "
              "cortos de puts, casi todo el open interest está fuera del dinero; las calls largas pierden delta con "
              "el tiempo y las puts cortas también (su delta va a 0), y en los dos casos deshacen coberturas "
              "**comprando**. Con este supuesto, la dirección del charm casi no cambia: lo que cambia es la "
              "**intensidad**. Por eso «charm vendedor fuerte» no sale ningún día y la segunda pregunta "
              "(¿baja con charm vendedor?) no se puede contestar con estos datos. Lo que sí se puede comprobar "
              "es si los días de charm comprador **fuerte** suben más en la última hora que el resto.", ""]
    resumen = {}
    for indice in ("ES", "NQ"):
        if not VELAS[indice].exists():
            continue
        r = ultima_hora(VELAS[indice])
        m = t.merge(r, on="fecha_sesion")
        titulo = "ES" if indice == "ES" else "NQ (velas del MNQ, con el charm del ES: no hay escaleras del NQ reconstruidas)"
        lineas += [f"## {titulo}", ""]
        for nombre, (a, b) in (("Ajuste nov-jul", AJUSTE), ("Comprobación ago-oct", COMPROBACION)):
            p = m[(m["fecha_sesion"] >= a) & (m["fecha_sesion"] <= b)]
            res = tabla_periodo(p, umbral)
            resumen[(indice, nombre)] = res
            lineas += _md(nombre, res)
            control = tabla_periodo(p, umbral, "ret_antes")
            lineas += [f"Control (9:30-15:00, la misma señal): comprador fuerte {_fmt(control['compra']['media'])} % · "
                       f"vendedor fuerte {_fmt(control['venta']['media'])} % · resto {_fmt(control['resto']['media'])} %", ""]
        lineas += ["Solo la parte 0DTE (fuerte = percentil 67 de la 0DTE en nov-jul):", ""]
        for nombre, (a, b) in (("Ajuste nov-jul", AJUSTE), ("Comprobación ago-oct", COMPROBACION)):
            p = m[(m["fecha_sesion"] >= a) & (m["fecha_sesion"] <= b)]
            r0 = tabla_periodo(p.assign(flujo=p["flujo_0dte"]), umbral0)
            resumen[(indice, nombre + " 0DTE")] = r0
            lineas.append(f"- {nombre}: 0DTE fuerte {_fmt(r0['compra']['media'])} % ({r0['compra']['n']} días, "
                          f"sube {_fmt(r0['compra']['sube'], 0)} %) · resto {_fmt(r0['resto']['media'])} % "
                          f"({r0['resto']['n']} días, sube {_fmt(r0['resto']['sube'], 0)} %) · p = {_fmt(r0['p_compra'], 2)}")
        lineas.append("")
        if indice == "ES":
            p = m[(m["fecha_sesion"] >= AJUSTE[0]) & (m["fecha_sesion"] <= COMPROBACION[1])]
            rho0, p0 = stats.spearmanr(p["flujo_0dte"], p["ret_ultima"])
            rho1, p1 = stats.spearmanr(p["flujo"] - p["flujo_0dte"], p["ret_ultima"])
            lineas += [f"Por partes (todo el año): correlación con la última hora del charm 0DTE "
                       f"{_fmt(rho0, 2)} (p = {_fmt(p0, 2)}) y del resto {_fmt(rho1, 2)} (p = {_fmt(p1, 2)}).", ""]
    lineas += ["## Conclusión", "", conclusion(resumen), ""]
    if len(errores):
        lineas += ["## Días sin escalera", ""] + [f"- {f}: {e}" for f, e in zip(errores["fecha_datos"], errores["error"])]
    INFORME.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))
    print(f"\nUmbral fuerte (ES): {umbral:.0f} contratos/día · informe en {INFORME}")
    return umbral, resumen


def _miles(x):
    return f"{x:,.0f}".replace(",", ".")


def conclusion(resumen):
    """Frase sencilla por índice y medida (total, 0DTE): «sentido esperado» = comprador fuerte
    sube más que el resto (y, si hay días de vendedor fuerte, estos menos que el resto);
    «se sostiene» = en el sentido esperado en los dos periodos y con p < 0,05 en los dos."""
    trozos = []
    for indice in ("ES", "NQ"):
        for medida, suf in (("charm total", ""), ("charm 0DTE", " 0DTE")):
            a, c = resumen.get((indice, "Ajuste nov-jul" + suf)), resumen.get((indice, "Comprobación ago-oct" + suf))
            if not a or not c:
                continue
            def ok(r):
                if not r["compra"]["n"] or r["compra"]["media"] <= r["resto"]["media"]:
                    return False
                return not r["venta"]["n"] or r["venta"]["media"] < r["resto"]["media"]
            sig = lambda r: r["p_compra"] == r["p_compra"] and r["p_compra"] < 0.05
            if ok(a) and ok(c) and sig(a) and sig(c):
                v = "se sostiene (sentido esperado y significativo en los dos periodos)"
            elif ok(a) and ok(c):
                v = (f"va en el sentido esperado en los dos periodos, pero la diferencia NO es significativa "
                     f"(p = {_fmt(a['p_compra'], 2)} y {_fmt(c['p_compra'], 2)}): no se distingue del azar")
            elif ok(a):
                v = "va en el sentido esperado en el ajuste, pero NO se repite en la comprobación"
            else:
                v = "no va en el sentido esperado en el ajuste"
            trozos.append(f"- **{indice}, {medida}**: {v}.")
    return "\n".join(trozos)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=["calcular", "informe"])
    p.add_argument("--desde", default="2025-10-31")
    p.add_argument("--hasta", default="2026-10-29")
    p.add_argument("--procesos", type=int, default=6)
    a = p.parse_args(argv)
    if a.accion == "calcular":
        t = calcular(date.fromisoformat(a.desde), date.fromisoformat(a.hasta), a.procesos)
        print(f"{(t['error'].fillna('') == '').sum()} sesiones en {CSV}; {(t['error'].fillna('') != '').sum()} sin datos")
        return 0
    informe()
    return 0


if __name__ == "__main__":
    sys.exit(main())
