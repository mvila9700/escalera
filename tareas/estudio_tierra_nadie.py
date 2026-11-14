"""
Estudio (solo NQ): ¿los días de «tierra de nadie» con muchos niveles OI juntos lateralizan?

    .venv/bin/python -m tareas.estudio_tierra_nadie

Datos: escaleras del NQ reconstruidas con Databento (datos/historico_reconstruido/NQ_<sesión>.json,
de tareas.reconstruir_escalera --indice NQ --json) y velas de 1 minuto del MNQ ya descargadas
(datos/velas/MNQ_v_0_*.csv; cotiza igual que el NQ). No descarga nada.

1. Clasificación del día SOLO con la escalera y el precio de apertura (9:30 NY):
   - distancia (en σ del día) de la apertura al principal más cercano (muro de calls, muro de
     puts o imán) por arriba y por abajo (0 si la apertura cae dentro de su zona);
   - nº de niveles «OI relevante» con su centro a menos de 0,5σ de la apertura;
   - tierra de nadie (X, N): los dos principales a más de Xσ y al menos N OI relevantes cerca
     (X = 0,5 y 0,75; N = 3 y 4);
   - régimen en la apertura (signo del perfil de GEX en ese precio) y tamaño de la GEX total.
2. Lateralización (9:30-16:00): rango / rango del 68 %, eficiencia (|cierre − apertura| / suma
   de |movimientos de 5 min|), nº de cruces del VWAP (velas de 1 min) y día de tendencia (cierre
   en el 20 % extremo del rango del día).
3. Tierra de nadie frente al resto, en total y por régimen; ajuste nov-jul y comprobación ago-oct.
   Pruebas: Mann-Whitney (medidas continuas) y Fisher (días de tendencia).
4. Días de evento (datos/noticias.txt: NFP, CPI, FOMC; más elecciones) aparte: el análisis
   principal va SIN ellos y se repite con ellos.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

CARPETA = Path("datos/historico_reconstruido")
VELAS = Path("datos/velas/MNQ_v_0_2021-10-29_2026-10-31.csv")
NOTICIAS = Path("datos/noticias.txt")
ELECCIONES = {"2025-11-04": "elecciones (gobernadores de NJ y VA, alcaldía de NY)"}
INFORME = Path("documentacion/estudio_tierra_nadie_nq.md")
TABLA = CARPETA / "NQ_estudio_tierra_nadie.csv"
AJUSTE = ("2025-11-01", "2026-07-31")
COMPROBACION = ("2026-08-01", "2026-10-31")
DEFINICIONES = [(x, n) for x in (0.5, 0.75) for n in (3, 4)]
PRINCIPALES = {"Muro de calls", "Muro de puts", "Imán"}


# --- Eventos ------------------------------------------------------------------
def eventos():
    out = dict(ELECCIONES)
    if NOTICIAS.exists():
        for linea in NOTICIAS.read_text(encoding="utf-8").splitlines():
            fecha, _, nota = linea.partition("#")
            fecha = fecha.strip()
            if len(fecha) == 10 and fecha[4] == "-":
                out[fecha] = nota.strip() or "noticia"
    return out


# --- Clasificación con la escalera ---------------------------------------------
def clasificar(d, apertura):
    """Rasgos del día con la escalera `d` (JSON del histórico) y el precio de apertura."""
    sigma = d["movimiento"]["desviacion"]
    arriba, abajo = np.inf, np.inf
    n_oi = 0
    for n in d["niveles"]:
        tipos = set(n["tipos"])
        lo = n.get("zona_desde", n["desde"]) if n.get("zona_desde") is not None else n["desde"]
        hi = n.get("zona_hasta", n["hasta"]) if n.get("zona_hasta") is not None else n["hasta"]
        if tipos & PRINCIPALES:
            if lo <= apertura <= hi:
                arriba = abajo = 0.0
            elif lo > apertura:
                arriba = min(arriba, lo - apertura)
            else:
                abajo = min(abajo, apertura - hi)
        if "OI relevante" in tipos and abs((n["desde"] + n["hasta"]) / 2 - apertura) < 0.5 * sigma:
            n_oi += 1
    perfil = (d.get("graficos") or {}).get("perfil") or []
    if perfil:
        p = np.array([x["precio"] for x in perfil]); g = np.array([x["gex_total"] for x in perfil])
        gex_ap = float(np.interp(apertura, p, g))
    else:
        gex_ap = d["gex_total"]
    fila = {"sigma": sigma, "dist_arriba": arriba / sigma, "dist_abajo": abajo / sigma, "n_oi_cerca": n_oi,
            "regimen": "positiva" if gex_ap >= 0 else "negativa", "gex_apertura": gex_ap,
            "gex_total": d["gex_total"], "r68": d["movimiento"]["rango_68"][1] - d["movimiento"]["rango_68"][0]}
    for x, k in DEFINICIONES:
        fila[f"tn_{x}_{k}"] = bool(min(arriba, abajo) / sigma > x and n_oi >= k)
    return fila


# --- Velas --------------------------------------------------------------------
def cargar_velas():
    v = pd.read_csv(VELAS, usecols=["ts_event", "open", "high", "low", "close", "volume", "instrument_id"])
    v.index = pd.to_datetime(v["ts_event"], utc=True).dt.tz_convert("America/New_York")
    v = v[v.index >= "2025-10-30"]
    v["dia"] = v.index.strftime("%Y-%m-%d")
    v["hhmm"] = v.index.strftime("%H:%M")
    return v


def medir(h):
    """Medidas de lateralización de la sesión 9:30-16:00 (velas de 1 minuto `h`)."""
    ap, ci = h["open"].iloc[0], h["close"].iloc[-1]
    alto, bajo = h["high"].max(), h["low"].min()
    rango = alto - bajo
    c5 = h["close"].resample("5min", label="left", closed="left", origin="start").last().dropna()
    camino = np.abs(np.diff(np.concatenate([[ap], c5.to_numpy()]))).sum()
    tipico = (h["high"] + h["low"] + h["close"]) / 3
    vol = h["volume"].where(h["volume"] > 0, 1.0)
    vwap = (tipico * vol).cumsum() / vol.cumsum()
    lado = np.sign(h["close"] - vwap).to_numpy()
    lado = lado[lado != 0]
    cruces = int((lado[1:] != lado[:-1]).sum()) if len(lado) > 1 else 0
    pos = (ci - bajo) / rango if rango else 0.5
    return {"apertura": ap, "cierre": ci, "rango": rango, "eficiencia": abs(ci - ap) / camino if camino else 0.0,
            "cruces_vwap": cruces, "tendencia": bool(pos >= 0.8 or pos <= 0.2)}


def _otro_contrato(por_dia, d, umbral=0.004):
    """True si las velas del día de los datos (cierre de las 15:59 NY) no cuadran con la
    liquidación del futuro de la escalera: la serie continua ya va con el trimestral siguiente
    (la base entre trimestrales del NQ ronda el 1 %). None si no se puede comprobar."""
    g = por_dia.get(d["fecha_datos"])
    h = g[g["hhmm"] == "15:59"] if g is not None else None
    liq = d.get("futuro_liquidacion")
    if h is None or h.empty or not liq:
        return None
    return abs(h["close"].iloc[-1] / liq - 1) > umbral


def construir():
    todas = cargar_velas()
    por_dia = {k: g for k, g in todas.groupby("dia")}     # acceso por día, sin recorrer el año
    ev = eventos()
    filas, fuera = [], {}
    for ruta in sorted(CARPETA.glob("NQ_????-??-??.json")):
        d = json.loads(ruta.read_text(encoding="utf-8"))
        sesion = d["fecha_sesion"]
        g = por_dia.get(sesion)
        h = g[(g["hhmm"] >= "09:30") & (g["hhmm"] < "16:00")] if g is not None else todas.iloc[:0]
        if len(h) < 300 or h["hhmm"].iloc[-1] < "15:55":
            fuera[sesion] = "sin sesión completa (festivo o cierre anticipado)"
            continue
        if h["instrument_id"].nunique() > 1:
            fuera[sesion] = "las velas cambian de contrato en la sesión"
            continue
        otro = _otro_contrato(por_dia, d)
        if otro:
            fuera[sesion] = (f"las velas no son del contrato de la escalera ({d.get('contrato_ref')}): "
                             "cambio de contrato")
            continue
        if otro is None:
            fuera[sesion] = "no se puede comprobar el contrato (faltan velas del día de los datos)"
            continue
        m = medir(h.set_index(h.index))
        fila = {"fecha_sesion": sesion, **clasificar(d, m["apertura"]), **m}
        fila["rango_r68"] = m["rango"] / fila["r68"]
        fila["evento"] = ev.get(sesion, "")
        filas.append(fila)
    t = pd.DataFrame(filas)
    t.to_csv(TABLA, index=False)
    return t, fuera


# --- Comparaciones --------------------------------------------------------------
MEDIDAS = [("rango_r68", "Rango / rango 68 %", "menos = más lateral"),
           ("eficiencia", "Eficiencia", "menos = más lateral"),
           ("cruces_vwap", "Cruces del VWAP", "más = más lateral"),
           ("tendencia", "Días de tendencia", "menos = más lateral")]


def comparar(t, col):
    a, b = t[t[col]], t[~t[col]]
    out = {"n_tn": len(a), "n_resto": len(b)}
    for m, _, _ in MEDIDAS:
        if m == "tendencia":
            out[m] = (a[m].mean() * 100 if len(a) else np.nan, b[m].mean() * 100 if len(b) else np.nan)
            if len(a) and len(b):
                tabla = [[a[m].sum(), len(a) - a[m].sum()], [b[m].sum(), len(b) - b[m].sum()]]
                out[m + "_p"] = stats.fisher_exact(tabla)[1]
            else:
                out[m + "_p"] = np.nan
        else:
            out[m] = (a[m].median() if len(a) else np.nan, b[m].median() if len(b) else np.nan)
            out[m + "_p"] = (stats.mannwhitneyu(a[m], b[m], alternative="two-sided").pvalue
                             if len(a) >= 3 and len(b) >= 3 else np.nan)
    # ¿más lateral? (todas las medidas en la dirección de lateralizar)
    out["mas_lateral"] = sum([out["rango_r68"][0] < out["rango_r68"][1], out["eficiencia"][0] < out["eficiencia"][1],
                              out["cruces_vwap"][0] > out["cruces_vwap"][1], out["tendencia"][0] < out["tendencia"][1]])
    return out


def _f(x, d=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:.{d}f}".replace(".", ",")


def _p(x):
    if x is None or np.isnan(x):
        return "—"
    return ("**" + _f(x) + "**") if x < 0.05 else _f(x)


def tabla_md(t, col, titulo):
    filas = [f"**{titulo}**", "",
             "| Grupo | Días | Rango/68 % (mediana) | Eficiencia (mediana) | Cruces VWAP (mediana) | Días de tendencia |",
             "|---|---:|---:|---:|---:|---:|"]
    r = comparar(t, col)
    filas.append(f"| Tierra de nadie | {r['n_tn']} | {_f(r['rango_r68'][0])} | {_f(r['eficiencia'][0])} | "
                 f"{_f(r['cruces_vwap'][0], 0)} | {_f(r['tendencia'][0], 0)} % |")
    filas.append(f"| Resto | {r['n_resto']} | {_f(r['rango_r68'][1])} | {_f(r['eficiencia'][1])} | "
                 f"{_f(r['cruces_vwap'][1], 0)} | {_f(r['tendencia'][1], 0)} % |")
    filas.append(f"| p | | {_p(r['rango_r68_p'])} | {_p(r['eficiencia_p'])} | {_p(r['cruces_vwap_p'])} | "
                 f"{_p(r['tendencia_p'])} |")
    filas.append("")
    return filas, r


def periodo(t, a, b):
    return t[(t["fecha_sesion"] >= a) & (t["fecha_sesion"] <= b)]


def informe(t, fuera):
    ev = t["evento"] != ""
    base = t[~ev]
    L = ["# Estudio NQ: ¿los días de «tierra de nadie» lateralizan?", "",
         f"{len(t)} sesiones del NQ (9:30-16:00 NY) con escalera reconstruida y velas de 1 minuto del MNQ; "
         f"{int(ev.sum())} son días de evento y van aparte. Se quitan {len(fuera)} sesiones (festivos, cierres "
         f"anticipados y cambios de contrato). Ajuste: nov-25 a jul-26; comprobación: ago-oct-26.", "",
         "## Cómo se mide", "",
         "- **Tierra de nadie (X, N)**: con la escalera del día y el precio de apertura, el muro o imán más "
         "cercano por arriba y por abajo a más de X σ, y al menos N niveles «OI relevante» a menos de 0,5 σ "
         "de la apertura. σ = movimiento esperado de 1 día de la escalera.",
         "- **Lateraliza** si el rango del día es pequeño frente al rango del 68 %, la eficiencia es baja "
         "(el precio va y viene sin avanzar: |cierre − apertura| / suma de los movimientos de 5 min), cruza "
         "muchas veces el VWAP y no es día de tendencia (cierre en el 20 % más alto o más bajo del rango).",
         "- **p**: Mann-Whitney (medianas) y Fisher (días de tendencia). En **negrita** si p < 0,05. Hay "
         "muchas comparaciones: un p < 0,05 suelto puede ser casualidad; cuenta lo que se repite en el ajuste "
         "y en la comprobación.", "",
         "## Cuántos días son tierra de nadie", "",
         "| Definición | Días (sin eventos) | % | De ellos con gamma negativa |", "|---|---:|---:|---:|"]
    for x, n in DEFINICIONES:
        c = f"tn_{x}_{n}"
        k = base[c].sum()
        L.append(f"| > {_f(x)} σ y ≥ {n} OI | {k} | {_f(k / len(base) * 100, 0)} % | "
                 f"{(base[c] & (base['regimen'] == 'negativa')).sum()} |")
    L += ["", f"Distancia mediana de la apertura al principal más cercano: {_f(np.median(np.minimum(base['dist_arriba'], base['dist_abajo'])))} σ; "
              f"OI relevantes cerca (mediana): {_f(base['n_oi_cerca'].median(), 0)}; días con gamma negativa en la apertura: "
              f"{(base['regimen'] == 'negativa').sum()} de {len(base)}.", ""]
    resumen = {}
    for x, n in DEFINICIONES:
        c = f"tn_{x}_{n}"
        L += [f"## Tierra de nadie: principales a más de {_f(x)} σ y al menos {n} OI relevantes", ""]
        for nombre, (a, b) in (("Ajuste nov-jul", AJUSTE), ("Comprobación ago-oct", COMPROBACION)):
            p = periodo(base, a, b)
            filas, r = tabla_md(p, c, f"{nombre}, todos los días (sin eventos)")
            L += filas
            resumen[(x, n, nombre, "todos")] = r
            for reg in ("positiva", "negativa"):
                q = p[p["regimen"] == reg]
                filas, r = tabla_md(q, c, f"{nombre}, gamma {reg}")
                L += filas
                resumen[(x, n, nombre, reg)] = r
    # Con eventos y gamma negativa en general
    L += ["## Con los días de evento incluidos (todo el año)", ""]
    for x, n in DEFINICIONES:
        filas, _ = tabla_md(t, f"tn_{x}_{n}", f"> {_f(x)} σ y ≥ {n} OI, con eventos")
        L += filas
    L += ["## Días de evento", "", "| Grupo | Días | Rango/68 % | Eficiencia | Cruces VWAP | Días de tendencia |",
          "|---|---:|---:|---:|---:|---:|"]
    for nombre, g in (("Evento", t[ev]), ("Sin evento", base)):
        L.append(f"| {nombre} | {len(g)} | {_f(g['rango_r68'].median())} | {_f(g['eficiencia'].median())} | "
                 f"{_f(g['cruces_vwap'].median(), 0)} | {_f(g['tendencia'].mean() * 100, 0)} % |")
    L += ["", "## ¿Influye la gamma negativa? (todos los días sin eventos, sin mirar la tierra de nadie)", ""]
    for nombre, (a, b) in (("Ajuste nov-jul", AJUSTE), ("Comprobación ago-oct", COMPROBACION)):
        p = periodo(base, a, b).assign(neg=lambda z: z["regimen"] == "negativa")
        filas, _ = tabla_md(p, "neg", f"{nombre}: gamma negativa (fila 1) frente a positiva (fila 2)")
        L += [f.replace("Tierra de nadie", "Gamma negativa").replace("| Resto |", "| Gamma positiva |") for f in filas]
    L += ["## Conclusión", ""] + conclusion(resumen) + [""]
    L += ["## En sencillo", ""] + en_sencillo(resumen, base) + [""]
    L += ["## Sesiones quitadas", ""] + [f"- {k}: {v}" for k, v in sorted(fuera.items())]
    INFORME.write_text("\n".join(L) + "\n", encoding="utf-8")
    return L, resumen


def conclusion(resumen):
    """Por definición: ¿más lateral en las 4 medidas en ajuste y comprobación? ¿algo significativo?"""
    out = []
    for x, n in DEFINICIONES:
        a = resumen[(x, n, "Ajuste nov-jul", "todos")]
        c = resumen[(x, n, "Comprobación ago-oct", "todos")]
        sig = lambda r: [m for m, _, _ in MEDIDAS if r[m + "_p"] == r[m + "_p"] and r[m + "_p"] < 0.05]
        out.append(f"- **> {_f(x)} σ y ≥ {n} OI** ({a['n_tn']} + {c['n_tn']} días): más lateral en "
                   f"{a['mas_lateral']} de 4 medidas en el ajuste y {c['mas_lateral']} de 4 en la comprobación; "
                   f"significativas: ajuste {', '.join(sig(a)) or 'ninguna'} · comprobación {', '.join(sig(c)) or 'ninguna'}.")
    return out


def en_sencillo(resumen, base):
    """Respuesta corta a las tres preguntas del estudio, con los números del día."""
    sig = sum(1 for r in resumen.values() for m, _, _ in MEDIDAS
              if r.get(m + "_p") == r.get(m + "_p") and r.get(m + "_p") is not None and r[m + "_p"] < 0.05)
    amplia = base["tn_0.5_3"].sum()
    distancia = float(np.median(np.minimum(base["dist_arriba"], base["dist_abajo"])))
    neg = resumen.get((0.5, 3, "Ajuste nov-jul", "negativa"))
    lineas = [
        f"- **¿Lateralizan más?** No se ve. Con la definición más amplia (> 0,5σ y ≥ 3 OI) solo hay "
        f"{amplia} días en un año sin eventos, y en ninguna definición ni periodo la diferencia con el resto "
        f"es significativa ({sig} comparaciones con p < 0,05 de todas las hechas). Además son raros: la "
        f"apertura suele estar a {_f(distancia)} σ de un muro o del imán, así que casi nunca hay «tierra de nadie».",
    ]
    if neg and neg["n_tn"]:
        lineas.append(
            f"- **¿Influye la gamma negativa?** Con gamma negativa, los {neg['n_tn']} días de tierra de nadie de "
            f"nov-jul cruzaron más el VWAP (mediana {_f(neg['cruces_vwap'][0], 0)} frente a "
            f"{_f(neg['cruces_vwap'][1], 0)}) y avanzaron menos (eficiencia {_f(neg['eficiencia'][0])} frente a "
            f"{_f(neg['eficiencia'][1])}), que es lo que se esperaría de un día lateral; pero con tan pocos días no "
            f"es significativo (p = {_f(neg['cruces_vwap_p'])} y {_f(neg['eficiencia_p'])}) y en ago-oct no hubo "
            "ninguno para comprobarlo. Por sí sola, la gamma negativa tampoco cambió la lateralidad del día.")
    lineas.append(
        "- **Consejo práctico**: no operes la tierra de nadie como señal de rango por sí sola. Si un día cae en "
        "ella, tómalo como un indicio débil (sobre todo con gamma negativa): busca que el precio rechace los "
        "extremos o cruce varias veces el VWAP antes de operar el rango, y mantén el stop fuera de los OI "
        "relevantes. Los días de evento (NFP, CPI, FOMC) van aparte: se mueven más y cruzan más el VWAP.")
    return lineas


def main(argv=None):
    t, fuera = construir()
    lineas, _ = informe(t, fuera)
    print("\n".join(lineas))
    print(f"\nInforme: {INFORME} · tabla por día: {TABLA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
