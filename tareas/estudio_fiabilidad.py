"""
Estudio de fiabilidad de los niveles (documentacion/estudio_fiabilidad_niveles_2026-11-12.md), repetible.

    .venv/bin/python -m tareas.estudio_fiabilidad                 # NQ
    .venv/bin/python -m tareas.estudio_fiabilidad --indice ES
    .venv/bin/python -m tareas.estudio_fiabilidad --hasta 2026-10-29   # solo hasta esa sesión

Salidas: salida/fiabilidad_<INDICE>.json (lo leen la página y el ESC2) y salida/fiabilidad_<INDICE>.html.

Método (el de calculos/respeto.py, sesión regular 9:30-16:00 NY con velas de 1 minuto):
- primer toque de cada zona; «rechazo» si el precio se aleja X·σ por el lado por el que llegó antes de
  pasar X·σ por el otro; «rotura» al revés (X = 0,1 y 0,2σ; la tabla principal es 0,2σ);
- régimen LOCAL del nivel: por encima de la frontera de ese día = «positiva», por debajo = «negativa»;
- papel: «techo» si el precio llega desde abajo, «suelo» si llega desde arriba;
- control: por cada nivel, 20 zonas del mismo ancho a la MISMA distancia de la apertura y en lado al azar
  (heredan el tipo y el régimen del nivel; su papel y su hora son los suyos; cada una pesa 1/20);
- ventaja = % de rechazo del nivel − % del control de su misma casilla;
- un nivel con varios tipos (p. ej. «Aceleración, Muro de puts») cuenta en cada uno;
- llegada al imán: si el precio toca la zona del imán en la sesión, por tramo de distancia a la
  apertura (en σ), frente a 20 zonas de control a la misma distancia.
Sesiones fuera: las de calculos.respeto.sesion_valida (hueco de apertura > 1,5σ y semanas del cambio de
contrato). Escaleras: datos/historico_reconstruido/<INDICE>_<sesión>.json y, después de la última, las
diarias de datos/historico (mismo código).
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from calculos import respeto

RAIZ = Path(__file__).resolve().parent.parent
TIPOS = ["Muro de calls", "Muro de puts", "Imán", "Resistencia", "Aceleración", "OI relevante"]
MUROS = {"Muro de calls", "Muro de puts"}
TRAMOS_IMAN = [(0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.5)]
VELAS_PREFIJO = {"NQ": "MNQ", "ES": "ES", "GC": "GC"}
HORA_TARDE = "10:30"
X_PRINCIPAL = 0.2
N_MINIMO = 20


# --- Datos --------------------------------------------------------------------

def archivos_velas(indice):
    """CSV de velas de 1 minuto del índice (serie continua de Databento: <prefijo>_v_0_<desde>_<hasta>.csv),
    del más antiguo al más reciente; los posteriores completan a los anteriores."""
    pre = VELAS_PREFIJO[indice]
    return sorted((RAIZ / "datos" / "velas").glob(f"{pre}_v_0_*.csv"), key=lambda p: p.name)


def cargar_velas(indice, desde="2025-10-30"):
    partes = []
    for ruta in archivos_velas(indice):
        v = pd.read_csv(ruta, usecols=["ts_event", "open", "high", "low", "close", "volume"])
        v.index = pd.to_datetime(v["ts_event"], utc=True).dt.tz_convert("America/New_York")
        partes.append(v[v.index >= desde])
    if not partes:
        raise FileNotFoundError(f"No hay velas de 1 minuto del {indice} en datos/velas ({VELAS_PREFIJO[indice]}_v_0_….csv).")
    v = pd.concat(partes)
    v = v[~v.index.duplicated(keep="last")].sort_index()
    v["dia"] = v.index.strftime("%Y-%m-%d")
    v["hhmm"] = v.index.strftime("%H:%M")
    return v


LETRAS = {"C": "Muro de calls", "P": "Muro de puts", "R": "Resistencia", "I": "Imán", "A": "Aceleración",
          "O": "OI relevante"}
CATEGORIAS = {"P": "principal", "S": "secundario", "M": "menor"}
CHARM = {"F": "refuerza", "D": "debilita", "f": "refuerza", "d": "debilita"}


def desde_esc2(texto):
    """Lo que el estudio necesita de una escalera, sacado de su texto ESC2 (las reconstruidas del ES
    solo se guardaron así): σ = mitad del rango del 68 %, régimen del día = precio de referencia
    frente a la frontera."""
    campos = dict(c.split("=", 1) for c in texto.strip().split("|")[1:] if "=" in c and not c.startswith("L"))
    lo68, hi68 = map(float, campos["68"].split("-"))
    ref, flip = float(campos["ref"]), float(campos["flip"]) if campos.get("flip") else None
    niveles = []
    for c in texto.strip().split("|"):
        if not c.startswith("L="):
            continue
        f = c[2:].split(":")
        zlo, zhi = map(float, f[6].split("-")) if len(f) > 6 and f[6] else (None, None)
        niveles.append({"tipos": [LETRAS[x] for x in f[0] if x in LETRAS], "zona_desde": zlo, "zona_hasta": zhi,
                        "categoria": CATEGORIAS.get(f[2]), "efecto_charm": CHARM.get(f[8]) if len(f) > 8 else None})
    return {"movimiento": {"desviacion": (hi68 - lo68) / 2}, "precio_ref": ref, "gamma_flip": flip,
            "regimen": None if flip is None else ("positiva" if ref >= flip else "negativa"),
            "niveles": [n for n in niveles if n["zona_desde"] is not None]}


def leer_escalera(ruta):
    texto = ruta.read_text(encoding="utf-8")
    return desde_esc2(texto) if ruta.name.endswith("_esc2.txt") else json.loads(texto)


def escaleras(indice, hasta=None):
    """{sesión: ruta}: las reconstruidas (JSON o, si no hay, su ESC2) y, después de la última, las diarias."""
    out = {}
    carpeta = RAIZ / "datos" / "historico_reconstruido"
    for r in sorted(carpeta.glob(f"{indice}_????-??-??_esc2.txt")):
        out[re.search(r"\d{4}-\d{2}-\d{2}", r.name).group()] = r
    for r in sorted(carpeta.glob(f"{indice}_????-??-??.json")):
        out[re.search(r"\d{4}-\d{2}-\d{2}", r.name).group()] = r
    ultima = max(out) if out else ""
    patron = "????-??-??.json" if indice == "NQ" else f"{indice}_????-??-??.json"
    for r in sorted((RAIZ / "datos" / "historico").glob(patron)):
        s = re.search(r"\d{4}-\d{2}-\d{2}", r.name).group()
        if s > ultima:
            out[s] = r
    return {s: r for s, r in sorted(out.items()) if not hasta or s <= hasta}


# --- Medición -------------------------------------------------------------------

def toque(velas, zlo, zhi, X, sigma):
    """Como calculos.respeto.resultado, pero devuelve también el papel y la hora del toque:
    (resultado, papel, hhmm) o None si no se tocó / empezó dentro."""
    hi, lo, cl, op = (velas[c].to_numpy(float) for c in ("high", "low", "close", "open"))
    toca = np.where((lo <= zhi) & (hi >= zlo))[0]
    if not len(toca):
        return None
    i = toca[0]
    previo = cl[i - 1] if i > 0 else op[0]
    if previo > zhi:
        arriba = True
    elif previo < zlo:
        arriba = False
    else:
        return None
    res = respeto.resultado(velas, zlo, zhi, X, sigma)
    return res, ("suelo" if arriba else "techo"), velas["hhmm"].iloc[i]


def tocada(velas, zlo, zhi):
    return bool(((velas["low"] <= zhi) & (velas["high"] >= zlo)).any())


def medir(indice, hasta=None, controles=20, semilla=7):
    """(filas de toques, filas de llegada al imán, notas)."""
    rng = np.random.default_rng(semilla)
    velas = cargar_velas(indice)
    por_dia = {k: g for k, g in velas.groupby("dia")}
    filas, iman, notas, sesiones = [], [], [], []
    for s, ruta in escaleras(indice, hasta).items():
        g = por_dia.get(s)
        if g is None:
            notas.append(f"{s}: sin velas")
            continue
        h = g[(g["hhmm"] >= "09:30") & (g["hhmm"] < "16:00")]
        if h.empty:
            notas.append(f"{s}: sin velas de la sesión regular")
            continue
        d = leer_escalera(ruta)
        sigma, ap, F, flip = d["movimiento"]["desviacion"], h["open"].iloc[0], d["precio_ref"], d.get("gamma_flip")
        if not respeto.sesion_valida(date.fromisoformat(s), ap, F, sigma):
            notas.append(f"{s}: fuera (hueco de apertura > 1,5σ o cambio de contrato)")
            continue
        if flip is None:
            notas.append(f"{s}: sin frontera")
            continue
        sesiones.append(s)
        dia = d["regimen"]
        for n in d["niveles"]:
            tipos = [t for t in n["tipos"] if t in TIPOS]
            if not tipos:
                continue
            zlo, zhi = n["zona_desde"], n["zona_hasta"]
            centro = (zlo + zhi) / 2
            local = "positiva" if centro > flip else "negativa"
            base = {"s": s, "tipos": "|".join(tipos), "local": local, "dia": dia,
                    "categoria": n.get("categoria"), "charm": n.get("efecto_charm") or "-"}
            ancho, dist = zhi - zlo, abs(centro - ap)
            centros_control = [ap + dist * rng.choice([-1, 1]) for _ in range(controles)]
            for X in respeto.UMBRALES:
                t = toque(h, zlo, zhi, X, sigma)
                if t and t[0] in ("rechazo", "rotura", "nada"):
                    filas.append({**base, "X": X, "res": t[0], "papel": t[1], "hora": t[2],
                                  "dist": dist / sigma, "control": False, "peso": 1.0})
                for c in centros_control:
                    tc = toque(h, c - ancho / 2, c + ancho / 2, X, sigma)
                    if tc and tc[0] in ("rechazo", "rotura", "nada"):
                        filas.append({**base, "X": X, "res": tc[0], "papel": tc[1], "hora": tc[2],
                                      "dist": dist / sigma, "control": True, "peso": 1.0 / controles})
            if "Imán" in tipos:
                iman.append({"s": s, "dist": dist / sigma, "llega": tocada(h, zlo, zhi), "control": False, "peso": 1.0})
                for c in centros_control:
                    iman.append({"s": s, "dist": dist / sigma, "llega": tocada(h, c - ancho / 2, c + ancho / 2),
                                 "control": True, "peso": 1.0 / controles})
    t = pd.DataFrame(filas)
    if not t.empty:
        t = t.assign(tipo=t["tipos"].str.split("|")).explode("tipo")
    return t, pd.DataFrame(iman), notas, sesiones


# --- Tablas ---------------------------------------------------------------------

def tasa(t):
    """(% de rechazo entre rechazos y roturas, n) con pesos."""
    con = t[t["res"].isin(["rechazo", "rotura"])]
    n = con["peso"].sum()
    return (float(con.loc[con["res"] == "rechazo", "peso"].sum() / n * 100) if n else None), float(n)


def casilla(t, **filtro):
    """{rechazo, n, control, ventaja} para un filtro (columnas de t)."""
    for k, v in filtro.items():
        if k == "despues_de":
            t = t[t["hora"] >= v]
        elif k == "antes_de":
            t = t[t["hora"] < v]
        elif isinstance(v, (set, list, tuple)):
            t = t[t[k].isin(v)]
        else:
            t = t[t[k] == v]
    r, n = tasa(t[~t["control"]])
    rc, _ = tasa(t[t["control"]])
    return {"rechazo": r, "n": int(round(n)), "control": rc,
            "ventaja": (r - rc) if (r is not None and rc is not None) else None}


def clave(tipo, local, papel=None):
    return f"{tipo}|{local}" + (f"|{papel}" if papel else "")


def resumir(t, iman, sesiones, notas, indice):
    out = {"indice": indice, "generado": date.today().isoformat(), "sesiones": len(sesiones),
           "desde": sesiones[0] if sesiones else None, "hasta": sesiones[-1] if sesiones else None,
           "metodo": "calculos/respeto.py · primer toque · control de 20 zonas a la misma distancia de la apertura",
           "n_minimo": N_MINIMO, "x_principal": X_PRINCIPAL, "casillas": {}, "casos": {}, "iman": {}, "notas": notas}
    for X in respeto.UMBRALES:
        tx = t[t["X"] == X]
        cas = {}
        for tipo in TIPOS:
            for local in ("positiva", "negativa"):
                cas[clave(tipo, local)] = casilla(tx, tipo=tipo, local=local)
                for papel in ("techo", "suelo"):
                    cas[clave(tipo, local, papel)] = casilla(tx, tipo=tipo, local=local, papel=papel)
            cas[clave(tipo, "todas")] = casilla(tx, tipo=tipo)
        for local in ("positiva", "negativa"):
            cas[clave("Muros", local)] = casilla(tx, tipo=MUROS, local=local)
        out["casillas"][str(X)] = cas
        out["casos"][str(X)] = {
            "Muro de puts como suelo con gamma negativa": casilla(tx, tipo="Muro de puts", local="negativa", papel="suelo"),
            "Muros con gamma negativa tocados después de las 10:30 NY": casilla(tx, tipo=MUROS, local="negativa", despues_de=HORA_TARDE),
            "Muros con gamma positiva tocados después de las 10:30 NY": casilla(tx, tipo=MUROS, local="positiva", despues_de=HORA_TARDE),
            "Muros con día positivo y nivel por encima de la frontera": casilla(tx, tipo=MUROS, local="positiva", dia="positiva"),
            "Muros con día negativo y nivel por encima de la frontera": casilla(tx, tipo=MUROS, local="positiva", dia="negativa"),
            "Aceleración como suelo con gamma negativa": casilla(tx, tipo="Aceleración", local="negativa", papel="suelo"),
            "Muros que el charm refuerza": casilla(tx, tipo=MUROS, charm="refuerza"),
            "Muros que el charm debilita": casilla(tx, tipo=MUROS, charm="debilita"),
        }
    tramos = {}
    for lo, hi in TRAMOS_IMAN:
        f = iman[(iman["dist"] >= lo) & (iman["dist"] < hi)] if not iman.empty else iman
        tramos[f"{lo}-{hi}"] = _llegada(f)
    tramos["total"] = _llegada(iman)
    out["iman"] = tramos
    return out


def _llegada(f):
    if f.empty:
        return {"iman": None, "n": 0, "control": None}
    a, c = f[~f["control"]], f[f["control"]]
    pct = lambda x: float((x["llega"] * x["peso"]).sum() / x["peso"].sum() * 100) if len(x) else None
    return {"iman": pct(a), "n": int(len(a)), "control": pct(c)}


def tramo_iman(dist_sigma):
    for lo, hi in TRAMOS_IMAN:
        if lo <= dist_sigma < hi:
            return f"{lo}-{hi}"
    return None


# --- Página ----------------------------------------------------------------------

def _p(x):
    return "—" if x is None else f"{x:.0f} %"


def _v(x):
    return "—" if x is None else f"{x:+.0f}"


def pagina(res):
    X = str(X_PRINCIPAL)
    cas, casos = res["casillas"][X], res["casos"][X]
    fila = lambda nombre, c: (f"<tr><td>{nombre}</td><td>{_p(c['rechazo'])}</td><td>{c['n']}</td>"
                              f"<td>{_p(c['control'])}</td><td class='{'b' if (c['ventaja'] or 0) > 0 else 'm'}'>"
                              f"{_v(c['ventaja'])}{' · pocos datos' if c['n'] < N_MINIMO else ''}</td></tr>")
    filas = []
    for tipo in TIPOS:
        for local in ("positiva", "negativa"):
            c = cas[clave(tipo, local)]
            if c["n"]:
                filas.append(fila(f"{tipo} · {local}", c))
            for papel in ("techo", "suelo"):
                c = cas[clave(tipo, local, papel)]
                if c["n"]:
                    filas.append(fila(f"&nbsp;&nbsp;↳ {papel}", c))
    otros = "".join(fila(k, v) for k, v in casos.items())
    iman = "".join(f"<tr><td>{k.replace('-', '–')}σ</td><td>{_p(v['iman'])}</td><td>{v['n']}</td><td>{_p(v['control'])}</td></tr>"
                   for k, v in res["iman"].items())
    cab = "<tr><th>Casilla</th><th>Rechazo</th><th>n</th><th>Control</th><th>Ventaja</th></tr>"
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>Fiabilidad de los niveles · {res['indice']}</title>
<style>body{{font-family:-apple-system,Helvetica,sans-serif;max-width:900px;margin:2em auto;color:#222}}
table{{border-collapse:collapse;margin:1em 0}}td,th{{border-bottom:1px solid #ddd;padding:4px 10px;text-align:right}}
td:first-child,th:first-child{{text-align:left}}.b{{color:#1a7f37;font-weight:600}}.m{{color:#b42318}}
.n{{color:#666;font-size:90%}}</style></head><body>
<h1>Fiabilidad de los niveles · {res['indice']}</h1>
<p class="n">{res['sesiones']} sesiones ({res['desde']} a {res['hasta']}). {res['metodo']}. Rechazo a {X_PRINCIPAL}σ al primer
toque. Régimen local: nivel por encima (positiva) o por debajo (negativa) de la frontera. Ventaja = rechazo − control.
Con n &lt; {N_MINIMO}, pocos datos. Generado el {res['generado']}.</p>
<h2>Rechazo al primer toque</h2><table>{cab}{''.join(filas)}</table>
<h2>Casos concretos</h2><table>{cab}{otros}</table>
<h2>¿Llega el precio al imán (OI máximo)?</h2>
<table><tr><th>Distancia a la apertura</th><th>Imán</th><th>n</th><th>Control</th></tr>{iman}</table>
<p class="n">No demuestra que los niveles funcionen en el futuro: es lo que pasó en este periodo. Se repite cada mes.</p>
</body></html>"""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--indice", default="NQ", choices=["NQ", "ES", "GC"])
    p.add_argument("--hasta", help="última sesión (AAAA-MM-DD)")
    p.add_argument("--semilla", type=int, default=7)
    p.add_argument("--salida", help="carpeta de salida (por defecto, salida/)")
    a = p.parse_args(argv)
    t, iman, notas, sesiones = medir(a.indice, a.hasta, semilla=a.semilla)
    if t.empty:
        print(f"{a.indice}: sin toques que medir (¿faltan escaleras o velas?).")
        return 1
    res = resumir(t, iman, sesiones, notas, a.indice)
    carpeta = Path(a.salida) if a.salida else RAIZ / "salida"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"fiabilidad_{a.indice}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (carpeta / f"fiabilidad_{a.indice}.html").write_text(pagina(res), encoding="utf-8")
    X = str(X_PRINCIPAL)
    print(f"{a.indice}: {res['sesiones']} sesiones ({res['desde']} → {res['hasta']}); {carpeta}/fiabilidad_{a.indice}.json y .html")
    for k, c in res["casillas"][X].items():
        if k.count("|") == 1 and c["n"]:
            print(f"  {k:<28} {_p(c['rechazo']):>6} n={c['n']:<4} control {_p(c['control']):>6} ventaja {_v(c['ventaja'])}")
    for k, c in res["casos"][X].items():
        print(f"  {k:<58} {_p(c['rechazo']):>6} n={c['n']:<4} control {_p(c['control']):>6}")
    for k, v in res["iman"].items():
        print(f"  imán {k:<10} {_p(v['iman']):>6} n={v['n']:<4} control {_p(v['control'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
