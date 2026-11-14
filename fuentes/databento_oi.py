"""
Tabla de opciones del ES (o del NQ, con indice="NQ") a partir de los archivos de Databento (descargados con tareas.databento_oi).

Devuelve lo mismo que el lector del PDF de CME (fuentes.cme_pdf.leer): la tabla en el formato
común (fuentes.modelo) y el diccionario info. Así la escalera se calcula con el mismo código.

De cada opción:
- strike, vencimiento (fecha exacta) y call/put: de su definición;
- open interest y volumen compensado del día D: estadísticas 9 y 6 con fecha de referencia D;
- liquidación del día D: estadística 3 con fecha de referencia D (la última que llega);
- futuro: la liquidación del día D de su subyacente real (ESZ6, ESH7...).

Diferencias con el PDF (las dice el informe de la reconstrucción):
- el vencimiento es exacto también en las diarias (el PDF mezcla semanas en un bloque);
- no trae la delta de CME ni el cambio de open interest.
"""
from pathlib import Path

import pandas as pd

from calculos.calendario import siguiente_habil
from fuentes.modelo import ErrorDatos, validar

CARPETA = Path("datos/oi")
MESES_FUT = dict(zip("FGHJKMNQUVXZ", ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]))
LIQUIDACION, VOLUMEN, OPEN_INTEREST = 3, 6, 9
DIAS = {"A": "LUNES", "B": "MARTES", "C": "MIERCOLES", "D": "JUEVES"}


def producto_nq(raiz):
    """Raíz de Databento del NQ → producto: NQ trimestral, QNE fin de mes, QN1-QN5 viernes
    (W1-W5; la mensual es la QN3), Q1A-Q5D diarias de lunes a jueves."""
    if raiz == "NQ":
        return "TRIMESTRAL"
    if raiz == "QNE":
        return "FIN_DE_MES"
    if len(raiz) == 3 and raiz[:2] == "QN" and raiz[2].isdigit():
        return f"W{raiz[2]}"
    if len(raiz) == 3 and raiz[0] == "Q" and raiz[1].isdigit() and raiz[2] in DIAS:
        return DIAS[raiz[2]]
    return None


DIAS_GC = {"M": "LUNES", "T": "MARTES", "W": "MIERCOLES", "R": "JUEVES"}


def producto_gc(raiz, simbolo=""):
    """Raíz de Databento del oro → producto del lector de la sección 64: OG mensual (MENSUAL_ACTIVO
    si el mes de la opción es un mes activo del futuro: G, J, M, Q, Z; si no, MENSUAL), OG1-OG5
    viernes (W1-W5), G1M-G5R diarias de lunes a jueves."""
    if raiz == "OG":
        return "MENSUAL_ACTIVO" if simbolo[2:3] in "GJMQZ" else "MENSUAL"
    if len(raiz) == 3 and raiz[:2] == "OG" and raiz[2].isdigit():
        return f"W{raiz[2]}"
    if len(raiz) == 3 and raiz[0] == "G" and raiz[1].isdigit() and raiz[2] in DIAS_GC:
        return DIAS_GC[raiz[2]]
    return None


def producto(raiz, indice="ES", simbolo=""):
    """Raíz de Databento → nombre de producto del lector del PDF."""
    if indice == "GC":
        return producto_gc(raiz, simbolo)
    if indice == "NQ":
        return producto_nq(raiz)
    if raiz == "ES":
        return "TRIMESTRAL"
    if raiz == "EW":
        return "FIN_DE_MES"
    if raiz.startswith("EW") and raiz[2:].isdigit():
        return f"W{raiz[2:]}"
    if len(raiz) == 3 and raiz[0] == "E" and raiz[1].isdigit() and raiz[2] in DIAS:
        return DIAS[raiz[2]]
    return None


def _df(ruta, precio=True):
    import databento as db
    if not ruta.exists():
        raise ErrorDatos(f"Falta el archivo de Databento {ruta} (descárgalo con tareas.databento_oi).")
    tienda = db.DBNStore.from_file(ruta)
    df = tienda.to_df(price_type="float", map_symbols=True) if precio else tienda.to_df(map_symbols=True)
    return df.reset_index()


def _del_dia(stats, tipo, dia):
    s = stats[(stats["stat_type"] == tipo) & (stats["ts_ref"].dt.date == dia)]
    return s.sort_values("ts_recv").groupby("instrument_id").last()


def _nombre_futuro(simbolo, dia):
    """ESZ6 → DEC26 (la década, la más cercana desde el año del día)."""
    mes, cifra = simbolo[2], int(simbolo[3:])
    anio = dia.year - dia.year % 10 + cifra
    if anio < dia.year:
        anio += 10
    return f"{MESES_FUT[mes]}{anio % 100:02d}"


def leer(dia, carpeta=CARPETA, indice="ES"):
    """(tabla, info) del boletín del día `dia` (fecha de los datos, date)."""
    from tareas.databento_oi import archivo_definiciones, lunes_de
    base = lambda n: carpeta / f"{indice}_{dia}_{n}.dbn.zst"
    foto = carpeta / archivo_definiciones(lunes_de(siguiente_habil(dia)), indice).name
    defs = _df(foto, precio=False)
    defs = defs[defs["instrument_class"].isin(["C", "P"])].drop_duplicates("instrument_id", keep="last")
    defs = defs.set_index("instrument_id")
    con_oi = [base(n) for n in ("oi", "oi_vispera") if base(n).exists()]
    if not con_oi:
        raise ErrorDatos(f"Falta el archivo del open interest del {dia} en {carpeta}.")
    stats = pd.concat([_df(base("liquidacion"))] + [_df(r) for r in con_oi], ignore_index=True)
    liq, oi, vol = (_del_dia(stats, t, dia) for t in (LIQUIDACION, OPEN_INTEREST, VOLUMEN))
    if oi.empty:
        raise ErrorDatos(f"Los archivos de Databento no traen el open interest del {dia}.")
    fut = _df(base("futuros"))
    fut = fut[fut["symbol"].str.fullmatch(indice + r"[FGHJKMNQUVXZ]\d")]
    fut_liq = _del_dia(fut, LIQUIDACION, dia)
    futuros = dict(zip(fut_liq["symbol"], fut_liq["price"]))

    avisos, descartados = [], {}
    sin_def = oi.index.difference(defs.index)
    if len(sin_def):
        avisos.append(f"{len(sin_def)} opciones con open interest sin definición en la foto semanal "
                      f"({oi.loc[sin_def, 'quantity'].sum():,.0f} contratos): descartadas.".replace(",", "."))
    t = pd.DataFrame({
        "raiz": defs["asset"],
        "tipo": defs["instrument_class"].map({"C": "call", "P": "put"}),
        "strike": defs["strike_price"],
        "vencimiento": defs["expiration"].dt.tz_convert("America/New_York").dt.date,
        "subyacente_real": defs["underlying"],
        "simbolo": defs["raw_symbol"],
    })
    t["producto"] = [producto(r, indice, s) for r, s in zip(t["raiz"], t["simbolo"])]
    desconocidas = t[t["producto"].isna()]
    for raiz, g in desconocidas.groupby("raiz"):
        descartados[raiz] = f"raíz desconocida ({len(g)} opciones)"
    t = t[t["producto"].notna()]
    t["open_interest"] = oi["quantity"].reindex(t.index).fillna(0)
    t["volumen"] = vol["quantity"].reindex(t.index).fillna(0)
    t["precio"] = liq["price"].reindex(t.index)
    sin_liq = t["precio"].isna()
    if sin_liq.any():
        oi_perdido = t.loc[sin_liq, "open_interest"].sum()
        avisos.append(f"{int(sin_liq.sum())} opciones sin liquidación del {dia} "
                      f"({oi_perdido:,.0f} contratos de open interest): descartadas.".replace(",", "."))
        t = t[~sin_liq]
    t["futuro"] = t["subyacente_real"].map(futuros)
    sin_fut = t["futuro"].isna()
    if sin_fut.any():
        avisos.append(f"{int(sin_fut.sum())} opciones sin liquidación de su futuro: descartadas.")
        t = t[~sin_fut]
    t = t[t["vencimiento"] > dia]
    t["subyacente"] = indice
    info = {
        "indice": indice,
        "fuente": "Databento GLBX.MDP3 (estadísticas y definiciones de CME): escalera RECONSTRUIDA",
        "fecha_datos": dia,
        "preliminar": False,
        "futuros": {_nombre_futuro(k, dia): v for k, v in sorted(futuros.items(), key=lambda kv: kv[0])},
        "avisos": avisos,
        "descartados": descartados,
        "productos_desconocidos": [],
        "comprobaciones": None,
    }
    if indice == "GC":
        # Oro: precio del contrato del gráfico (el activo); cada opción sigue sobre su futuro
        from fuentes.cme_pdf_oro import contrato_activo
        activo = contrato_activo(siguiente_habil(dia))
        if activo in info["futuros"]:
            info["contrato_ref"], info["futuro_ref"] = activo, info["futuros"][activo]
        info["revueltas"] = {"oi_total": 0.0, "oi_sin_fecha_segura": 0.0, "oi_sin_encaje": 0.0, "por_bloque": []}
    t = t.drop(columns=["simbolo"])
    return validar(t.reset_index(drop=True)), info
