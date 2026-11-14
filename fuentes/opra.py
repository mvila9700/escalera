"""
Opciones de QQQ y NDX de OPRA (Databento, descargadas con tareas.databento_opra) → la tabla que usa
calculos.confluencia.a_nq: raiz, tipo, strike, vencimiento, open_interest, precio.

De cada opción:
- raíz, strike, vencimiento y call/put: de su definición (raw_symbol "QQQ   261120C00720000");
- todo se une por símbolo: el instrument_id de OPRA cambia cada día;
- open interest: estadística 9 (OCC) publicada la mañana de la sesión, la última que llega;
- precio: punto medio de la mejor oferta y demanda consolidadas (cbbo-1m) de la última vela
  antes de las 16:00 NY del boletín. Sin oferta o sin demanda, no hay precio (se descarta).

Columnas comprobadas con el 4-nov-2026 (QQQ, NDX y NDXP). El open interest llega repetido por cada
una de las 20 bolsas con el mismo valor (se toma el último).
"""
from pathlib import Path

import pandas as pd

from fuentes.modelo import ErrorDatos

CARPETA = Path("datos/opra")
OPEN_INTEREST = 9


def tabla(defs, stats, precios):
    """DataFrames de Databento (definiciones, estadísticas, cbbo-1m) → tabla para a_nq.

    Se unen por SÍMBOLO (raw_symbol, "QQQ   261120C00720000"), no por instrument_id: en OPRA el
    número de cada opción cambia de un día a otro (comprobado el 4-nov-2026: los precios del día
    anterior solo casan por número en un 0,1 %). Si las tablas no traen "symbol" (pruebas), por número."""
    d = defs[defs["instrument_class"].isin(["C", "P"])]
    por_simbolo = "symbol" in stats and "symbol" in precios
    clave = "raw_symbol" if por_simbolo else "instrument_id"
    d = d.drop_duplicates(clave, keep="last").set_index(clave)
    k_stats = "symbol" if por_simbolo else "instrument_id"
    oi = (stats[stats["stat_type"] == OPEN_INTEREST].sort_values("ts_recv")
          .groupby(k_stats)["quantity"].last())
    q = precios.sort_values("ts_recv").groupby(k_stats)[["bid_px_00", "ask_px_00"]].last()
    medio = ((q["bid_px_00"] + q["ask_px_00"]) / 2).where((q["bid_px_00"] > 0) & (q["ask_px_00"] > 0))
    simbolos = d.index.to_series() if por_simbolo else d["raw_symbol"]
    t = pd.DataFrame({
        "raiz": simbolos.str[:6].str.strip(),
        "tipo": d["instrument_class"].map({"C": "call", "P": "put"}),
        "strike": d["strike_price"].astype(float),
        "vencimiento": pd.to_datetime(d["expiration"]).dt.tz_convert("America/New_York").dt.date,
    })
    t["open_interest"] = oi.reindex(t.index).fillna(0)
    t["precio"] = medio.reindex(t.index)
    t = t[(t["open_interest"] > 0) & t["precio"].notna()]
    return t.reset_index(drop=True)


def _df(ruta, precio=True):
    import databento as db
    if not ruta.exists():
        raise ErrorDatos(f"Falta {ruta} (descárgalo con tareas.databento_opra).")
    tienda = db.DBNStore.from_file(ruta)
    # map_symbols: el símbolo de cada opción (la tabla de símbolos viene en el propio archivo)
    return (tienda.to_df(price_type="float", map_symbols=True) if precio else tienda.to_df(map_symbols=True)).reset_index()


def leer(sesion, grupos=("QQQ", "NDX"), carpeta=CARPETA):
    """Tabla de QQQ y NDX de la sesión `sesion` (o ErrorDatos si falta algo)."""
    partes = []
    for g in grupos:
        base = lambda pieza: carpeta / f"{g}_{sesion}_{pieza}.dbn.zst"
        partes.append(tabla(_df(base("definiciones"), precio=True), _df(base("open_interest")), _df(base("precios"))))
    t = pd.concat(partes, ignore_index=True)
    if t.empty:
        raise ErrorDatos(f"Sin opciones de QQQ/NDX con open interest y precio para la sesión {sesion}.")
    return t
