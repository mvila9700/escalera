"""
Lector de la sección 64 del Daily Bulletin de CME («Metals Option Products»): opciones del ORO (GC).

Devuelve lo mismo que fuentes.cme_pdf.leer (tabla en el formato común + diccionario info), así
la escalera del oro sale con el mismo código que la del NQ y la del ES.

Qué trae el PDF (Fase ORO-0, boletín del 6-nov-2026):
- Mensuales «OG CALL» / «OG PUT», por mes de la OPCIÓN (DEC26 vence el 24-nov sobre GCZ6).
  Las de un mes activo del futuro (feb, abr, jun, ago, dic) son MENSUAL_ACTIVO (el equivalente a
  la trimestral del NQ); las de los demás meses, MENSUAL (van sobre el siguiente mes activo).
- Viernes «OG1»…«OG5» CALL/PUT: separadas (W1…W5).
- Lunes a jueves «GMW MON», «GWT TUE», «GWW WED», «GWR THU»: un solo bloque por mes con calls,
  puts y varias semanas mezcladas por strike, SIN marca. Se separan con `separar_revueltas`
  (delta del boletín, precio y futuro); la página dice cuánto open interest queda sin fecha segura.
- Micro oro (OMG, MMG, WMG, FMG): se descartan (son sobre el MGC; su OI es despreciable).
- Plata, cobre, platino, paladio, acero…: no son oro, no se leen.
- No trae la tabla del futuro GC: la liquidación de cada futuro sale de la PARIDAD CALL-PUT de
  la mensual que vence antes sobre él (C − P = e^(−rT)·(F − K), en los strikes más cerca del
  dinero). No hay máximo/mínimo del futuro (el «Estado hoy» del oro usa velas).
- Trae la delta de CME por strike (columna delta_cme): sirve para comprobar el Black-76.

Las opciones del oro son de estilo americano; el motor usa Black-76 (europeo), como en el NQ y el
ES. Con tipos de interés del 4 % y plazos de semanas la diferencia es pequeña.
"""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pdfplumber

from calculos import black76
from calculos import config as configuracion
from calculos.calendario import anterior_habil, es_habil, habiles_entre
from fuentes.modelo import ErrorDatos, validar

MESES = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7,
         "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
NOMBRE_MES = {v: k for k, v in MESES.items()}
ACTIVOS = (2, 4, 6, 8, 12)                    # meses activos del futuro GC
DIA_REVUELTA = {"MON": ("LUNES", 0), "TUE": ("MARTES", 1), "WED": ("MIERCOLES", 2), "THU": ("JUEVES", 3)}
RE_FECHA = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\w{3}) (\d{2}), (\d{4})")
RE_CABECERA = re.compile(r"^([A-Z0-9]{2,6}) (CALL|PUT|OPT|OOF|MON|TUE|WED|THU|FRI) (.+)$")
RE_MES = re.compile(r"^([A-Z]{3})(\d{2})$")
RE_FILA = re.compile(
    r"^(\d+(?:\.\d+)?) .*?(\d+\.\d+|----) (UNCH|NEW|[+-] ?\d+\.\d+) (\d?\.\d{4}|----) "
    r"(\S+) (\S+) (\S+) (\S+) ([\d,]+) (UNCH|NEW|[+-] ?[\d,]+)$")
RE_TOTAL = re.compile(r"^TOTAL (.*)$")


def _num(t):
    if t is None or t.startswith("--"):
        return None
    return float(t.replace(",", "").replace(" ", "").rstrip("ABNP"))


def _miles(x):
    return f"{x:,.0f}".replace(",", ".")


def _anio_mes(codigo):
    m = RE_MES.match(codigo)
    return 2000 + int(m.group(2)), MESES[m.group(1)]


def codigo_mes(anio, mes):
    return f"{NOMBRE_MES[mes]}{anio % 100:02d}"


def mes_activo(anio, mes):
    """Primer mes activo del futuro GC en o después de (anio, mes)."""
    while mes not in ACTIVOS:
        mes += 1
        if mes > 12:
            anio, mes = anio + 1, 1
    return anio, mes


def _ultimos_habiles(anio, mes):
    d = (date(anio + (mes == 12), mes % 12 + 1, 1)) - timedelta(days=1)
    while True:
        if es_habil(d):
            yield d
        d -= timedelta(days=1)


def vencimiento_mensual(anio, mes):
    """Regla de CME para la opción OG del mes (anio, mes): 4.º último día hábil del mes ANTERIOR;
    si cae en viernes o víspera de festivo, el día hábil anterior. (Comprobado con la tabla del
    boletín del 6-nov-2026: DEC26 → 24-nov, APR27 → 24-mar por el Viernes Santo.)"""
    a, m = (anio, mes - 1) if mes > 1 else (anio - 1, 12)
    gen = _ultimos_habiles(a, m)
    d = [next(gen) for _ in range(4)][-1]
    siguiente = d + timedelta(days=1)          # siguiente día laborable (lunes a viernes)
    while siguiente.weekday() >= 5:
        siguiente += timedelta(days=1)
    if d.weekday() == 4 or not es_habil(siguiente):
        d = anterior_habil(d - timedelta(days=1))
    return d


def primer_aviso(anio, mes):
    """Primer día de aviso de entrega del futuro GC del mes: último hábil del mes anterior."""
    a, m = (anio, mes - 1) if mes > 1 else (anio - 1, 12)
    return next(_ultimos_habiles(a, m))


def _viernes(anio, mes, n):
    d = date(anio, mes, 1)
    d += timedelta(days=(4 - d.weekday()) % 7 + 7 * (n - 1))
    return d if es_habil(d) else anterior_habil(d - timedelta(days=1))


# --- Lectura del PDF --------------------------------------------------------

def _tabla_vencimientos(pagina):
    """{(código, tipo): {mes: fecha}} de la tabla «LAST TRADE DATES» de la página 1 (cada fecha
    va a su columna por la posición horizontal)."""
    palabras = pagina.extract_words()
    cab = [w for w in palabras if w["text"] == "EXPIRATION:"]
    if not cab:
        return {}
    top = cab[0]["top"]
    cols = [(w["text"], (w["x0"] + w["x1"]) / 2) for w in palabras
            if abs(w["top"] - top) < 2 and RE_MES.match(w["text"])]
    filas = defaultdict(list)
    for w in palabras:
        if w["top"] > top + 2:
            filas[round(w["top"])].append(w)
    out = {}
    anio_boletin = None
    for t in sorted(filas):
        f = sorted(filas[t], key=lambda w: w["x0"])
        if len(f) < 3 or not re.fullmatch(r"\d\d/\d\d", f[2]["text"]):
            continue
        fechas = {}
        for w in f[2:]:
            if re.fullmatch(r"\d\d/\d\d", w["text"]):
                mes = min(cols, key=lambda c: abs(c[1] - (w["x0"] + w["x1"]) / 2))[0]
                fechas.setdefault(mes, []).append(w["text"])
        out[(f[0]["text"], f[1]["text"])] = fechas
    return out


def _fecha_mmdd(txt, anio_ref, mes_ref):
    """'11/24' → date, en el año que deja la fecha cerca del mes de referencia."""
    m, d = map(int, txt.split("/"))
    for a in (anio_ref - 1, anio_ref, anio_ref + 1):
        f = date(a, m, d)
        if abs((f - date(anio_ref, mes_ref, 15)).days) < 200:
            return f
    return date(anio_ref, m, d)


def leer_filas(ruta):
    """Filas de opciones del oro del PDF, sin interpretar: (fecha_datos, preliminar, filas,
    totales, tabla_vencimientos, descartados)."""
    filas, totales, descartados = [], [], defaultdict(float)
    fecha, preliminar, tabla = None, False, {}
    prod = mes = None
    suma = defaultdict(float)
    with pdfplumber.open(ruta) as pdf:
        for n, pagina in enumerate(pdf.pages):
            if n == 0:
                tabla = _tabla_vencimientos(pagina)
            for linea in (pagina.extract_text() or "").splitlines():
                linea = linea.strip()
                if fecha is None and RE_FECHA.search(linea):
                    g = RE_FECHA.search(linea).groups()
                    fecha = datetime.strptime(f"{g[1]} {g[2]} {g[3]}", "%b %d %Y").date()
                if linea == "PRELIMINARY":
                    preliminar = True
                if linea.endswith("OPTIONS ON FUTURES") or linea.startswith("EXERCISES"):
                    prod = None if not linea.startswith("GOLD") else prod
                m = RE_CABECERA.match(linea)
                if m and not re.search(r"\d\d/\d\d", m.group(3)):
                    prod = (m.group(1), m.group(2)) if "GOLD" in m.group(3) else None
                    continue
                if RE_MES.match(linea):
                    mes = linea
                    continue
                if prod is None or mes is None:
                    continue
                t = RE_TOTAL.match(linea)
                if t:
                    tok = t.group(1).replace(",", "")
                    s = re.search(r"(\d+)([+-]) ?\d+$", tok)
                    oi_total = float(s.group(1)) if s else float(tok.split()[-2]) if len(tok.split()) >= 2 else None
                    totales.append({"producto": prod[0], "tipo": prod[1], "mes": mes, "oi_boletin": oi_total,
                                    "oi_leido": suma[(prod, mes)]})
                    suma[(prod, mes)] = 0.0
                    continue
                f = RE_FILA.match(linea)
                if not f:
                    continue
                oi = float(f.group(9).replace(",", ""))
                suma[(prod, mes)] += oi
                vol = sum(_num(x) or 0 for x in (f.group(6), f.group(7), f.group(8)))
                cambio = f.group(10)
                filas.append({
                    "codigo": prod[0], "clase": prod[1], "mes": mes, "strike": float(f.group(1)),
                    "precio": _num(f.group(2)), "delta_cme": _num(f.group(4)), "open_interest": oi,
                    "volumen": vol,
                    "cambio_oi": 0.0 if cambio in ("UNCH", "NEW") else float(cambio.replace(" ", "").replace(",", "")),
                })
    if fecha is None:
        raise ErrorDatos(f"{ruta}: no encuentro la fecha del boletín (¿es la sección 64, Metals Option Products?).")
    return fecha, preliminar, filas, totales, tabla, descartados


# --- Vencimientos, futuros y separación de las semanales revueltas -----------

def _producto(codigo, clase):
    if codigo == "OG":
        return "OG"
    m = re.fullmatch(r"OG(\d)", codigo)
    if m:
        return f"W{m.group(1)}"
    if codigo in ("GMW", "GWT", "GWW", "GWR") and clase in DIA_REVUELTA:
        return DIA_REVUELTA[clase][0]
    return None


MICRO = {"OMG", "MMG", "WMG", "FMG"}


def _fechas_tabla(tabla, codigo, clase, mes, anio_ref, mes_ref):
    txt = tabla.get((codigo, clase), {}).get(mes, [])
    return [_fecha_mmdd(t, anio_ref, mes_ref) for t in txt]


def paridad(filas_mes, T, r, n=3):
    """Futuro implícito por paridad call-put (mediana de los n strikes con |C − P| más pequeño)."""
    c = {f["strike"]: f["precio"] for f in filas_mes if f["tipo"] == "call" and f["precio"]}
    p = {f["strike"]: f["precio"] for f in filas_mes if f["tipo"] == "put" and f["precio"]}
    comunes = sorted(set(c) & set(p), key=lambda k: abs(c[k] - p[k]))[:n]
    if not comunes:
        return None
    return float(np.median([k + (c[k] - p[k]) * np.exp(r * T) for k in comunes]))


def separar_revueltas(filas, candidatas, futuro_de, T_de, iv_ref, r, minimo_precio=0.25, margen=0.12):
    """Asigna (call/put, fecha) a las filas de un bloque revuelto (lunes...jueves de un mes).

    Para cada fila y cada candidata (tipo, fecha) con precio ≥ valor intrínseco:
      coste = |ln(IV implícita / IV de referencia en ese strike y fecha)| + 2·|delta − delta CME|.
    En cada strike, cada (tipo, fecha) sale como mucho una vez (asignación óptima: a igual strike
    y tipo, más plazo vale más). «Fecha segura» si la segunda mejor fecha del mismo tipo cuesta
    al menos `margen` más y el precio no es el mínimo de cotización (≥ minimo_precio).

    Devuelve una lista paralela a `filas`: (tipo, fecha, segura) o None si no encaja ninguna.
    """
    from scipy.optimize import linear_sum_assignment
    cands = [(tipo, d) for d in candidatas for tipo in ("call", "put")]
    out = [None] * len(filas)
    por_strike = defaultdict(list)
    for i, f in enumerate(filas):
        por_strike[f["strike"]].append(i)
    for K, idx in por_strike.items():
        coste = np.full((len(idx), len(cands)), 1e6)
        for a, i in enumerate(idx):
            f = filas[i]
            if not f["precio"]:
                continue
            for b, (tipo, d) in enumerate(cands):
                F, T = futuro_de(d), T_de(d)
                if F is None or T <= 0:
                    continue
                iv = black76.volatilidad_implicita(f["precio"], F, K, T, r, tipo)
                ref = iv_ref(d, K)
                if not np.isfinite(iv) or not ref:
                    continue
                coste[a, b] = abs(np.log(iv / ref))
                if f["delta_cme"] is not None:
                    coste[a, b] += 2 * abs(abs(float(black76.delta(F, K, T, r, iv, tipo))) - f["delta_cme"])
        filas_a, cols = linear_sum_assignment(coste)
        for a, b in zip(filas_a, cols):
            if coste[a, b] >= 1e6:
                continue
            tipo, d = cands[b]
            otras = [coste[a, j] for j, (t2, d2) in enumerate(cands) if t2 == tipo and d2 != d]
            segura = (min(otras, default=1e6) - coste[a, b] >= margen) and filas[idx[a]]["precio"] >= minimo_precio
            out[idx[a]] = (tipo, d, bool(segura))
    return out


def leer(rutas, config=None, indice="GC"):
    """(tabla_validada, info) del boletín de la sección 64."""
    if isinstance(rutas, (list, tuple)):
        if len(rutas) != 1:
            raise ErrorDatos("El oro lee un solo PDF (sección 64).")
        rutas = rutas[0]
    ruta = Path(rutas)
    config = config or configuracion.para_indice(configuracion.cargar(), indice)
    r = config["tipo_interes"]
    h = config["horas_et"]
    from calculos.tiempo import anios_habiles
    hora_liq = h.get("liquidacion", 16)
    T_de = lambda d: anios_habiles(fecha_datos, hora_liq, d, h["vencimiento"])

    fecha_datos, preliminar, crudas, totales, tabla, _ = leer_filas(ruta)
    anio_b, mes_b = fecha_datos.year, fecha_datos.month
    avisos, descartados = [], {}
    comprobaciones = {"avisos_totales": 0}
    for t in totales:
        if t["oi_boletin"] is not None and abs(t["oi_boletin"] - t["oi_leido"]) > 0.5:
            comprobaciones["avisos_totales"] += 1
            avisos.append(f"El total de {t['producto']} {t['tipo']} {t['mes']} no cuadra: boletín "
                          f"{_miles(t['oi_boletin'])}, leído {_miles(t['oi_leido'])}.")
    comprobaciones["bloques_con_total"] = len(totales)

    # Micro: fuera
    micro = defaultdict(float)
    filas = []
    for f in crudas:
        if f["codigo"] in MICRO:
            micro[f["codigo"]] += f["open_interest"]
            continue
        p = _producto(f["codigo"], f["clase"])
        if p is None:
            descartados[f"{f['codigo']} {f['clase']}"] = "serie del oro sin identificar"
            continue
        f["producto"] = p
        filas.append(f)
    for c, oi in micro.items():
        descartados[c] = f"micro oro (sobre el MGC; {_miles(oi)} contratos de OI): descartada"

    # 1) Mensuales: vencimiento (tabla del boletín o regla) y futuro por paridad
    venc_mensual = {}
    for f in filas:
        if f["producto"] != "OG" or f["mes"] in venc_mensual:
            continue
        a, m = _anio_mes(f["mes"])
        tab = _fechas_tabla(tabla, "OG", "CALL", f["mes"], a, m) or _fechas_tabla(tabla, "OG", "PUT", f["mes"], a, m)
        venc_mensual[f["mes"]] = tab[0] if tab else vencimiento_mensual(a, m)
    for f in filas:
        if f["producto"] == "OG":
            a, m = _anio_mes(f["mes"])
            f["producto"] = "MENSUAL_ACTIVO" if m in ACTIVOS else "MENSUAL"
            f["vencimiento"] = venc_mensual[f["mes"]]
            f["tipo"] = "call" if f["clase"] == "CALL" else "put"
            f["fut"] = codigo_mes(*mes_activo(a, m))
    vigentes = sorted((v, mes) for mes, v in venc_mensual.items() if v > fecha_datos)
    futuros, origen_fut = {}, {}
    for v, mes in vigentes:
        a, m = _anio_mes(mes)
        fut = codigo_mes(*mes_activo(a, m))
        if fut in futuros:
            continue
        F = paridad([f for f in filas if f.get("vencimiento") == v and f["mes"] == mes], T_de(v), r)
        if F:
            futuros[fut], origen_fut[fut] = round(F, 2), f"paridad call-put de OG {mes} (vence el {v})"
    if not futuros:
        raise ErrorDatos("No puedo calcular el futuro GC por paridad call-put (no hay mensuales con calls y puts).")
    fechas_mensual = [(v, codigo_mes(*mes_activo(*_anio_mes(mes)))) for v, mes in vigentes]

    def fut_de(d):
        """Futuro de una opción que vence el día d: el de la primera mensual que vence ese día o después."""
        for v, fut in fechas_mensual:
            if v >= d:
                return fut
        return None

    # 2) Viernes: separadas
    for f in filas:
        if f["producto"].startswith("W"):
            a, m = _anio_mes(f["mes"])
            tab = _fechas_tabla(tabla, f["codigo"], f["clase"], f["mes"], a, m)
            f["vencimiento"] = tab[0] if tab else _viernes(a, m, int(f["producto"][1:]))
            f["tipo"] = "call" if f["clase"] == "CALL" else "put"
            f["fut"] = fut_de(f["vencimiento"])

    # 3) IV de referencia (curvas de las series ya separadas, interpoladas en varianza total)
    separadas = defaultdict(list)
    for f in filas:
        if f.get("vencimiento") and f["vencimiento"] > fecha_datos and f.get("fut") in futuros and f["precio"]:
            F = futuros[f["fut"]]
            otm = (f["tipo"] == "call") == (f["strike"] >= F)
            if otm:
                iv = black76.volatilidad_implicita(f["precio"], F, f["strike"], T_de(f["vencimiento"]), r, f["tipo"])
                if np.isfinite(iv):
                    separadas[f["vencimiento"]].append((f["strike"], iv))
    curvas = sorted((T_de(v), np.array(sorted(set(p)))) for v, p in separadas.items() if len(p) >= 5)

    def iv_ref(d, K):
        T = T_de(d)
        val = [(Tc, float(np.interp(K, c[:, 0], c[:, 1]))) for Tc, c in curvas]
        if not val:
            return None
        if T <= val[0][0]:
            return val[0][1]
        for (T1, s1), (T2, s2) in zip(val, val[1:]):
            if T1 <= T <= T2:
                w = s1 ** 2 * T1 + (s2 ** 2 * T2 - s1 ** 2 * T1) * (T - T1) / (T2 - T1)
                return float(np.sqrt(w / T))
        return val[-1][1]

    # 4) Revueltas (lunes...jueves): candidatas = ese día de la semana del mes, hábil, posterior
    #    al boletín, hasta la última fecha que da la tabla, sin el día de una mensual
    dias_mensual = {v for v, _ in vigentes}
    resumen_rev = {"oi_total": 0.0, "oi_sin_fecha_segura": 0.0, "oi_sin_encaje": 0.0, "por_bloque": []}
    bloques = defaultdict(list)
    for f in filas:
        if f["producto"] in ("LUNES", "MARTES", "MIERCOLES", "JUEVES"):
            bloques[(f["codigo"], f["clase"], f["mes"])].append(f)
    for (codigo, clase, mes), grupo in bloques.items():
        a, m = _anio_mes(mes)
        dia = DIA_REVUELTA[clase][1]
        tab = _fechas_tabla(tabla, codigo, clase, mes, a, m)
        ultima = max(tab) if tab else date(a + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        d = date(a, m, 1)
        cand = []
        while d.month == m:
            if d.weekday() == dia and es_habil(d) and fecha_datos < d <= ultima and d not in dias_mensual:
                cand.append(d)
            d += timedelta(days=1)
        res = separar_revueltas(grupo, cand, lambda x: futuros.get(fut_de(x)), T_de, iv_ref, r)
        oi_b = sum(f["open_interest"] for f in grupo)
        sin = sum(f["open_interest"] for f, x in zip(grupo, res) if x and not x[2])
        nada = sum(f["open_interest"] for f, x in zip(grupo, res) if x is None)
        for f, x in zip(grupo, res):
            if x:
                f["tipo"], f["vencimiento"], f["fecha_segura"] = x
                f["fut"] = fut_de(x[1])
        resumen_rev["oi_total"] += oi_b
        resumen_rev["oi_sin_fecha_segura"] += sin
        resumen_rev["oi_sin_encaje"] += nada
        resumen_rev["por_bloque"].append({"bloque": f"{codigo} {clase} {mes}", "fechas": [str(c) for c in cand],
                                          "oi": oi_b, "oi_sin_fecha_segura": sin, "oi_sin_encaje": nada})
    if resumen_rev["oi_total"]:
        avisos.append(
            f"Semanales de lunes a jueves (vienen revueltas en el boletín: calls, puts y varias semanas en un "
            f"bloque): separadas por delta, precio y futuro. {_miles(resumen_rev['oi_sin_fecha_segura'])} de "
            f"{_miles(resumen_rev['oi_total'])} contratos quedan sin fecha segura (van a la fecha más probable)"
            + (f"; {_miles(resumen_rev['oi_sin_encaje'])} no encajan en ninguna y se descartan"
               if resumen_rev["oi_sin_encaje"] else "") + ".")

    # 5) Tabla común
    import pandas as pd
    ok = [f for f in filas if f.get("vencimiento") and f.get("tipo") and f.get("fut") in futuros
          and f["vencimiento"] > fecha_datos]
    sin_fut = sum(f["open_interest"] for f in filas if f.get("vencimiento") and f["vencimiento"] > fecha_datos
                  and f.get("tipo") and f.get("fut") not in futuros)
    if sin_fut:
        avisos.append(f"{_miles(sin_fut)} contratos de vencimientos lejanos sin futuro calculable "
                      "(no hay mensual con calls y puts sobre él): fuera.")
    df = pd.DataFrame([{
        "subyacente": config["indices"][indice].get("subyacente", indice),
        "vencimiento": f["vencimiento"], "tipo": f["tipo"], "strike": f["strike"],
        "open_interest": f["open_interest"], "volumen": f["volumen"], "precio": f["precio"],
        "producto": f["producto"], "delta_cme": f["delta_cme"], "futuro": futuros[f["fut"]],
        "cambio_oi": f["cambio_oi"], "contrato_futuro": f["fut"],
        "fecha_segura": f.get("fecha_segura", True),
    } for f in ok])
    # Varias filas iguales (mismo vencimiento, tipo y strike) tras separar: se suman
    claves = ["subyacente", "vencimiento", "tipo", "strike"]
    rep = df.duplicated(claves, keep=False)
    if rep.any():
        avisos.append(f"{int(rep.sum())} filas repetidas (mismo strike, tipo y vencimiento) tras separar las "
                      "semanales: se suman.")
        df = df.groupby(claves, as_index=False).agg({
            "open_interest": "sum", "volumen": "sum", "precio": "mean", "producto": "first",
            "delta_cme": "mean", "futuro": "first", "cambio_oi": "sum", "contrato_futuro": "first",
            "fecha_segura": "all"})

    # 6) Contrato activo (el del gráfico) y su precio: la mensual que toca
    sesion = _siguiente_sesion(fecha_datos)
    activo = contrato_activo(sesion)
    if activo not in futuros:
        activo = min(futuros, key=lambda k: _anio_mes(k))
        avisos.append(f"No hay futuro para el contrato activo: se usa {activo}.")
    info = {
        "indice": indice,
        "fuente": "CME Daily Bulletin, sección 64 (Metals Option Products, PDF descargado a mano)",
        "fecha_datos": fecha_datos,
        "preliminar": preliminar,
        "futuros": futuros,
        "origen_futuros": origen_fut,
        "contrato_ref": activo,
        "futuro_ref": futuros[activo],
        "avisos": avisos,
        "descartados": descartados,
        "productos_desconocidos": [],
        "comprobaciones": comprobaciones,
        "revueltas": resumen_rev,
    }
    return validar(df), info


def _siguiente_sesion(d):
    from calculos.calendario import siguiente_habil
    return siguiente_habil(d)


def fecha_roll(codigo):
    """Cambio de contrato del GC (el volumen pasa al siguiente mes activo): el día en que vencen las
    opciones OG de ese mes (4.º último hábil del mes anterior), unos días antes del primer aviso
    de entrega (último hábil del mes anterior). Regla PROVISIONAL (sin medir con volúmenes reales)."""
    return vencimiento_mensual(*_anio_mes(codigo))


def siguiente_activo(codigo):
    a, m = _anio_mes(codigo)
    m += 1
    if m > 12:
        a, m = a + 1, 1
    return codigo_mes(*mes_activo(a, m))


def contrato_activo(sesion):
    """Contrato GC del gráfico en esa sesión: el primer mes activo cuyo cambio de contrato aún no
    ha llegado (desde el día del roll, ya el siguiente)."""
    cod = codigo_mes(*mes_activo(sesion.year, sesion.month))
    while sesion >= fecha_roll(cod):
        cod = siguiente_activo(cod)
    return cod


def leer_futuros(ruta, config=None, indice="GC"):
    """Como cme_pdf.leer_futuros: (fecha_datos, preliminar, {}) — la sección 64 no trae la tabla del
    futuro (ni máximo ni mínimo)."""
    fecha, preliminar = None, False
    with pdfplumber.open(ruta) as pdf:
        for linea in (pdf.pages[0].extract_text() or "").splitlines():
            if fecha is None and RE_FECHA.search(linea):
                g = RE_FECHA.search(linea).groups()
                fecha = datetime.strptime(f"{g[1]} {g[2]} {g[3]}", "%b %d %Y").date()
            if linea.strip() == "PRELIMINARY":
                preliminar = True
    if fecha is None:
        raise ErrorDatos(f"{ruta}: no encuentro la fecha del boletín (¿es la sección 64?).")
    return fecha, preliminar, {}
