"""
Lector del Daily Bulletin de CME para un índice: sección 40 (NQ: Nasdaq 100,
E-mini Nasdaq 100 y Russell, calls y puts en un PDF) o secciones 47 y 48
(ES: E-mini S&P 500, calls en una y puts en otra). Lo propio de cada índice
(secciones, etiquetas, columnas de futuros...) está en config.yaml
(indices -> NQ / ES -> cme_pdf); el código es el mismo para los dos.

IMPORTANTE: este módulo solo LEE los PDF que tú has descargado a mano.
No descarga nada de internet (las condiciones de CME lo prohíben).

Cómo está organizado el boletín (lo que hemos visto en los boletines reales):
- Cabecera (página 1 en el NQ; páginas 1-3 en el ES): fechas de último día
  de negociación por producto y mes, rangos de strikes por producto y mes, y
  la tabla de futuros (apertura, máximo, mínimo y, en el ES, liquidación).
- Las opciones van por secciones: "WEEKLY-1 CALLS", "ADDITIONAL NASDAQ
  CALLS", "WK EW-W1 E-MINI S&P PUTS"... con bloques por producto y mes:
    "NOV26"                      (mes sin etiqueta)
    "OCT26 QTW TUE"              (mes + producto)
    "20261000 MN1 MON"           (mes en formato AAAAMM00 + producto)
- Cada fila de opción tiene las columnas en posiciones fijas (letra de ancho
  fijo), así que cada dato se lee por su posición horizontal. Las
  posiciones son las mismas en las secciones 40, 47 y 48 (medidas).

Bloques sin etiqueta: en el NQ, las puts trimestrales (y las del Russell)
salen sin nombre de producto; en el ES, las calls de fin de mes. Se
identifican por la secuencia: los dos lados (calls y puts) siguen el mismo
orden de productos, y cada producto va con sus meses en orden creciente;
cuando el mes vuelve atrás empieza el siguiente producto. Se comprueba con
el propio boletín: los rangos de strikes de la cabecera ("QZ 2500-44000",
"EW 100-10800"...) y la sección "OPTIONS EOO'S, BLOCKS and EXERCISES"
(comparada por producto).

Fechas de vencimiento: salen de la cabecera del boletín cuando están (p. ej.
trimestral de junio de 2027 = 17-jun, por festivo). Si no están, se calculan
según el producto (ver REGLAS_PRODUCTO).
"""
import calendar
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pdfplumber
from calculos import config as configuracion
from calculos.calendario import anterior_habil
from fuentes.modelo import ErrorDatos, validar

MESES = {m.upper(): i for i, m in enumerate(calendar.month_abbr) if m}

# --- Posiciones horizontales de las columnas (en puntos del PDF) -----------
# Medidas sobre el boletín real. Si CME cambia el diseño, el lector
# detecta que no encuentra filas y da un error claro.
X_STRIKE_MAX = 50          # el strike empieza a la izquierda de x=50
X_SETT = (290, 342)        # precio de liquidación (termina en ~335)
X_DELTA = (370, 400)
X_VOLUMEN = (428, 456)     # número alineado a la derecha en ~451
X_OI = (462, 491)          # open interest alineado a la derecha en ~489
X_SIGNO_CAMBIO = (491, 500)  # "+" / "-" del cambio de open interest
X_CAMBIO = (500, 524)        # cambio de OI respecto al día anterior ("UNCH" = 0)


def _cambio_oi(linea):
    """Cambio de open interest respecto al día anterior (None si no se lee)."""
    signo = next((w["text"] for w in linea
                  if X_SIGNO_CAMBIO[0] <= w["x0"] <= X_SIGNO_CAMBIO[1] and w["text"] in "+-"), None)
    numero = _en_columna(linea, X_CAMBIO)
    if numero == "UNCH":
        return 0.0
    valor = _numero(numero) if numero else None
    if valor is None or signo is None:
        return None
    return valor if signo == "+" else -valor

# --- Productos -------------------------------------------------------------
# tipo de regla de vencimiento y si lo incluimos en la versión 1.
#   ("viernes_n", n)   -> n-ésimo viernes del mes
#   ("tercer_viernes",)-> mensual / trimestral
#   ("dia_semana", d)  -> siguiente lunes(0)...jueves(3) del mes tras la fecha de datos
#   ("fin_de_mes",)    -> último día hábil del mes
REGLAS_PRODUCTO = {
    "W1": ("viernes_n", 1),
    "W2": ("viernes_n", 2),
    "W3": ("viernes_n", 3),
    "W4": ("viernes_n", 4),
    "W5": ("viernes_n", 5),
    "TRIMESTRAL": ("tercer_viernes",),
    "MENSUAL": ("tercer_viernes",),
    "LUNES": ("dia_semana", 0),
    "MARTES": ("dia_semana", 1),
    "MIERCOLES": ("dia_semana", 2),
    "JUEVES": ("dia_semana", 3),
    "FIN_DE_MES": ("fin_de_mes",),
}


def config_indice(config=None, indice=None):
    """Parte de config.yaml con lo propio del índice (por defecto, el activo)."""
    config = config or configuracion.cargar()
    indice = indice or config.get("indice", "NQ")
    datos = config["indices"][indice]
    return dict(datos["cme_pdf"], indice=indice, subyacente=datos.get("subyacente", indice))


def clave_de_etiqueta(etiqueta, cfg):
    """Nombre común de producto para calls y puts.

    'EMINI NASD CALL' y 'EMINI NASD PUT' -> 'EMINI NASD';
    'MINI NSDQ EOM C' -> 'MINI NSDQ EOM'; 'QN4 PUT' -> 'W4' (igual que la
    sección "WEEKLY-4 CALLS"); 'EOW1 MINI S&P P' -> 'W1'.
    """
    e = re.sub(r"\s+", " ", etiqueta.upper()).strip()
    m = re.match(cfg["semanal"] + r"\b", e)
    if m:
        return f"W{m.group(1)}"
    return re.sub(r"\s+(CALL|PUT|C|P)$", "", e)


def producto_de_clave(clave, cfg):
    """Nuestro nombre de producto. Devuelve (producto, motivo_si_se_descarta)."""
    if clave is None:
        return None, "no se ha podido identificar"
    if re.fullmatch(r"W\d", clave):
        return clave, None
    for patron, motivo in cfg["descartar"].items():
        if re.match(patron, clave):
            return None, motivo
    for patron, producto in cfg["productos"].items():
        if re.match(patron, clave):
            return producto, None
    return None, f"producto desconocido '{clave}'"


def producto_de_linea(texto, cfg):
    """Producto de una línea de producto de la sección EOO, p. ej.
    "MDW MID E-MINI S&P 500 WEDNESDAY WEEKLY OPT" -> MIERCOLES.

    Devuelve (reconocida, producto): reconocida=False si la línea no es de
    ningún producto conocido; producto=None si es de uno que se descarta.
    """
    e = re.sub(r"\s+", " ", texto.upper()).strip()
    m = re.match(cfg["semanal"] + r"\b", e)
    if m:
        return True, f"W{m.group(1)}"
    for patron in cfg["descartar"]:
        if re.match(patron, e):
            return True, None
    for patron, producto in cfg["productos"].items():
        if re.match(patron, e):
            return True, producto
    return False, None


NOMBRE_LADO = {"call": "Calls", "put": "Puts"}


def _resolver_lado(bloques, referencia, objetivo):
    """Pone nombre a los bloques sin etiqueta de `objetivo` ("call"/"put")
    siguiendo la secuencia de productos de `referencia`."""
    # Secuencia de productos del lado de referencia, con sus meses: [(clave, [meses])]
    secuencia = []
    for b in bloques:
        if b["tipo"] == referencia and b["clave"]:
            if not secuencia or secuencia[-1][0] != b["clave"]:
                secuencia.append((b["clave"], []))
            secuencia[-1][1].append(b["anio_mes"])
    posicion = {}
    for i, (clave, _) in enumerate(secuencia):
        posicion.setdefault(clave, i)

    avisos, i_actual, anterior = [], None, None
    for b in bloques:
        if b["tipo"] != objetivo:
            continue
        if not b["sin_etiqueta"]:
            i_actual = posicion.get(b["clave"], i_actual)
        else:
            # Mismo producto mientras el mes siga creciendo; si vuelve
            # atrás (o venimos de un bloque con etiqueta), el siguiente.
            sigue = (anterior is not None and anterior["sin_etiqueta"]
                     and b["anio_mes"] > anterior["anio_mes"])
            if not sigue:
                i_actual = 0 if i_actual is None else i_actual + 1
            mes = _mes_texto(b["anio_mes"])
            if i_actual >= len(secuencia):
                b["clave"] = None
                avisos.append(f"{NOMBRE_LADO[objetivo]} sin etiqueta {mes}: no queda ningún producto de las "
                              f"{referencia}s con el que emparejarlas. Descartadas.")
            else:
                clave, meses = secuencia[i_actual]
                b["clave"] = clave
                if b["anio_mes"] not in meses:
                    avisos.append(f"{NOMBRE_LADO[objetivo]} sin etiqueta {mes}: identificadas como «{clave}» "
                                  f"por la secuencia, pero en las {referencia}s de «{clave}» no hay ese mes. "
                                  "Revísalo.")
        anterior = b
    return avisos


def resolver_sin_etiqueta(bloques):
    """Pone nombre a los bloques sin etiqueta (de puts o de calls) por la
    secuencia de productos del otro lado. Cambia b["clave"] de esos bloques y
    devuelve los avisos.

    Cada bloque es un diccionario con: tipo ("call"/"put"), clave (None si
    no tiene etiqueta), anio_mes (anio, mes) y sin_etiqueta (True/False),
    en el orden en que aparecen en el PDF (o PDFs).
    """
    return _resolver_lado(bloques, "call", "put") + _resolver_lado(bloques, "put", "call")


def _mes_texto(anio_mes):
    """(2026, 12) -> 'DEC26' (como en el boletín)"""
    return f"{calendar.month_abbr[anio_mes[1]].upper()}{anio_mes[0] % 100:02d}"


def comprobar_rangos(bloques, rangos):
    """Cabecera: cada strike de un bloque debe estar en el rango que el
    boletín da para su producto y mes.

    rangos: {(producto, (anio, mes)): (desde, hasta, código)}.
    Devuelve (bloques comprobados, avisos).
    """
    comprobados, avisos = 0, []
    for b in bloques:
        r = rangos.get((b.get("producto"), b["anio_mes"]))
        if r is None:
            continue
        comprobados += 1
        lo, hi, codigo = r
        fuera = sorted(s for s in b["strikes"] if not lo <= s <= hi)
        if fuera:
            avisos.append(f"Comprobación de rangos: en el bloque {b['clave']} {b['tipo']} "
                          f"{_mes_texto(b['anio_mes'])} hay strikes fuera del rango {codigo} "
                          f"{lo:.0f}-{hi:.0f}: {', '.join(f'{x:.0f}' for x in fuera)}.")
    return comprobados, avisos


def comprobar_eoo(bloques, eoo):
    """Sección EOO/BLOCKS: cada strike que aparece ahí debe existir en algún
    bloque del mismo producto, tipo y mes. Devuelve (comprobados, avisos)."""
    por_producto = {}
    for b in bloques:
        if b.get("producto"):
            por_producto.setdefault((b["producto"], b["tipo"], b["anio_mes"]), set()).update(b["strikes"])
    avisos = []
    for e in eoo:
        strikes = por_producto.get((e["producto"], e["tipo"], e["anio_mes"]))
        donde = f"«{e['etiqueta']} · {_mes_texto(e['anio_mes'])} {e['tipo'].upper()}»"
        if strikes is None:
            avisos.append(f"Comprobación EOO: el bloque {donde} (strike {e['strike']:.0f}) "
                          "no coincide con ningún bloque leído.")
        elif e["strike"] not in strikes:
            avisos.append(f"Comprobación EOO: el strike {e['strike']:.0f} de {donde} no está en los "
                          f"bloques {e['producto']} {e['tipo']} leídos.")
    return len(eoo), avisos


def _fechas_de_serie(bloque, fechas_diarias, fecha_datos):
    """Fechas reales de las series de un bloque de diarias (lunes...jueves) del
    mes, según la cabecera del boletín. [] si no las da (el NQ no las tiene) o
    si el producto no es una diaria."""
    regla = REGLAS_PRODUCTO.get(bloque.get("producto") or "", ("",))
    if regla[0] != "dia_semana":
        return []
    anio, mes = bloque["anio_mes"]
    return sorted(d for d in fechas_diarias.get(regla[1], []) if (d.year, d.month) == (anio, mes) and d > fecha_datos)


def separar_por_precio(bloque, fechas, aplicar=True):
    """Diarias con varias semanas en el mismo bloque (mismo strike una vez por semana).

    Los strikes que aparecen exactamente una vez por cada fecha, con precios
    distintos, se fechan por precio (a igual strike y subyacente, más plazo
    vale más): el más barato, la primera fecha. El resto queda como siempre:
    los repetidos se descartan y los que salen una sola vez van a la primera fecha.

    Devuelve ({índice de fila en "todas": fecha}, resumen de open interest).
    Con aplicar=False no fecha nada (opción "a"), pero da el resumen.
    """
    por_strike = {}
    for i, fila in enumerate(bloque["todas"]):
        por_strike.setdefault(fila["strike"], []).append(i)
    oi = {f["_n"]: f["open_interest"] for f in bloque["filas"]}
    fijas, resumen = {}, {"fechados_por_precio": 0.0, "primera_fecha": 0.0, "descartados": 0.0}
    for strike, indices in por_strike.items():
        precios = [bloque["todas"][i]["precio"] for i in indices]
        completo = (len(indices) == len(fechas) and None not in precios and len(set(precios)) == len(precios))
        oi_strike = sum(oi.get(i, 0) for i in indices)
        if completo and aplicar:
            for fecha, i in zip(fechas, sorted(indices, key=lambda i: bloque["todas"][i]["precio"])):
                if i in oi:
                    fijas[i] = fecha
            resumen["fechados_por_precio"] += oi_strike
        elif sum(1 for i in indices if i in oi) <= 1:   # una sola fila con OI: se queda en la primera fecha
            resumen["primera_fecha"] += oi_strike
        else:
            resumen["descartados"] += oi_strike
    return fijas, resumen


# --- Fechas de vencimiento -------------------------------------------------
def _viernes_n(anio, mes, n):
    dias = [d for d in calendar.Calendar().itermonthdates(anio, mes)
            if d.month == mes and d.weekday() == 4]
    return dias[n - 1] if n <= len(dias) else None


def _fin_de_mes_habil(anio, mes):
    return anterior_habil(date(anio, mes, calendar.monthrange(anio, mes)[1]))


def calcular_vencimiento(producto, anio, mes, fecha_datos):
    """Fecha de vencimiento deducida a partir del producto y el mes."""
    regla = REGLAS_PRODUCTO[producto]
    if regla[0] == "viernes_n":
        return _viernes_n(anio, mes, regla[1])
    if regla[0] == "tercer_viernes":
        return _viernes_n(anio, mes, 3)
    if regla[0] == "fin_de_mes":
        return _fin_de_mes_habil(anio, mes)
    if regla[0] == "dia_semana":
        d = max(date(anio, mes, 1), fecha_datos + timedelta(days=1))
        while d.month == mes:
            if d.weekday() == regla[1]:
                return d
            d += timedelta(days=1)
        return None
    raise ValueError(regla)


# --- Lectura de números del PDF ----------------------------------------------
def _numero(texto):
    """'1,030.75-' -> 1030.75 ; '----', 'CAB', 'NEW' -> None"""
    t = texto.replace(",", "").rstrip("+-AB")
    try:
        return float(t)
    except ValueError:
        return None


def _en_columna(palabras, rango, por="x1"):
    for w in palabras:
        if rango[0] <= w[por] <= rango[1]:
            return w["text"]
    return None


def _agrupar_lineas(palabras, tolerancia=2.5):
    """Agrupa las palabras de una página en líneas según su altura."""
    palabras = sorted(palabras, key=lambda w: (w["top"], w["x0"]))
    lineas, actual, top0 = [], [], None
    for w in palabras:
        if top0 is None or w["top"] - top0 <= tolerancia:
            actual.append(w)
            top0 = top0 if top0 is not None else w["top"]
        else:
            lineas.append(sorted(actual, key=lambda w: w["x0"]))
            actual, top0 = [w], w["top"]
    if actual:
        lineas.append(sorted(actual, key=lambda w: w["x0"]))
    return lineas


RE_SECCION = re.compile(r"(WEEKLY-(\d) )?(CALLS|PUTS)$")
RE_MES = re.compile(r"^(?:([A-Z]{3})(\d{2})|(\d{4})(\d{2})00)(?:\s+(.*))?$")
RE_FECHA = re.compile(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) PG\d+")
RE_RANGO = re.compile(r"^(\d+)-(\d+)$")                              # "2500-44000"
RE_DIA_MES = re.compile(r"^(\d\d)/(\d\d)$")                           # "12/18"
RE_EOO_MES = re.compile(r"^([A-Z]{3})(\d{2}) (CALL|PUT)$")             # "DEC26 PUT"
RE_EOO_FILA = re.compile(r"^[A-Z0-9]{1,4} (\d+) ")                     # "QZ 26500 ---- 193 ..."
TOLERANCIA_X = 6     # puntos: un dato de la cabecera va con el mes que tiene encima


def _es_cabecera_de_pagina(linea):
    """La cabecera "40 NASDAQ 100, E-MINI ..." empieza por un número como
    las filas de opciones: se distingue por llevar palabras."""
    return any(re.fullmatch(r"[A-Z]{4,},?", w["text"]) and w["text"] != "UNCH" for w in linea)


# Tabla de futuros del NQ (página 1). En el boletín se llaman "GLOBEX OPEN /
# GLOBEX HIGH / GLOBEX LOW": sesión electrónica completa (17:00 a 16:00 de
# Chicago), no solo horario regular. Las posiciones de cada índice están en
# config.yaml (cme_pdf -> futuros -> columnas_x0); el alcance, en "alcance".
ALCANCE_MAX_MIN = ("Sesión electrónica completa de Globex (de las 17:00 del día anterior a las "
                   "16:00 de Chicago); columnas «GLOBEX HIGH/LOW» del Daily Bulletin.")


def _anio_mes(codigo):
    """'DEC26' -> (2026, 12), o None si no es un mes."""
    if re.fullmatch(r"[A-Z]{3}\d{2}", codigo) and codigo[:3] in MESES:
        return 2000 + int(codigo[3:]), MESES[codigo[:3]]
    return None


def _fila_futuro(linea, cfg):
    """Datos de una fila de la tabla de futuros ("DEC26 7706.25 ..."), por posición x."""
    def columna(rango):
        return next((w["text"] for w in linea if rango[0] <= w["x0"] < rango[1]), None)
    cols = cfg["futuros"]["columnas_x0"]
    maximo, minimo = columna(cols["maximo"]), columna(cols["minimo"])
    fila = {
        "apertura": _numero(columna(cols["apertura"]) or ""),
        "maximo": _numero(maximo or ""),
        "minimo": _numero(minimo or ""),
        "max_min_sin_operacion": bool(maximo and maximo[-1] in "AB") or bool(minimo and minimo[-1] in "AB"),
    }
    if "liquidacion" in cols:
        fila["liquidacion"] = _numero(columna(cols["liquidacion"]) or "")
    return fila


class _Cabecera:
    """Lee la cabecera del boletín línea a línea: fechas de vencimiento,
    rangos de strikes y tabla de futuros."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.meses = []          # [(x0, (anio, mes))] de la última fila de meses
        self.fechas = {}         # {(producto, anio, mes): fecha}
        self.filas_sin_etiqueta = [[]]  # tramos seguidos de filas de fechas sin etiqueta (página 1 del ES)
        self.codigos_diarias = []       # códigos de las diarias en la tabla de rangos (E1A, E1B... en el ES)
        self.rangos = {}         # {(producto, (anio, mes)): (desde, hasta, código)}
        self.futuros = {}        # {"DEC26": {apertura, maximo, minimo, ...}}
        self.en_futuros = False

    def _mes_en_x(self, x0):
        cerca = [(abs(x - x0), am) for x, am in self.meses if abs(x - x0) <= TOLERANCIA_X]
        return min(cerca)[1] if cerca else None

    def fechas_diarias(self):
        """{día de la semana (0 = lunes): [fechas]} de las diarias, emparejando los
        códigos de la tabla de rangos (E1A, E1B...: letra A-D = lunes-jueves) con el
        tramo de filas de fechas sin etiqueta que tiene el mismo número de filas.
        Solo si cada fecha cae en el día de su letra; si no, {} (no se adivina)."""
        patron = self.cfg.get("diarias_codigos")
        if not patron or not self.codigos_diarias:
            return {}
        for tramo in self.filas_sin_etiqueta:
            if len(tramo) != len(self.codigos_diarias):
                continue
            resultado = {}
            for codigo, fechas in zip(self.codigos_diarias, tramo):
                dia = "ABCD".index(re.fullmatch(patron, codigo).group(2))
                if any(f.weekday() != dia for f in fechas):
                    return {}
                resultado.setdefault(dia, []).extend(fechas)
            return resultado
        return {}

    def linea(self, linea, texto):
        palabras = [w for w in linea if w["text"] != "EXPIRATION:"]
        # Fila de meses ("OCT26 NOV26 DEC26 ..."): da el mes de cada columna
        if palabras and all(_anio_mes(w["text"]) for w in palabras):
            self.meses = [(w["x0"], _anio_mes(w["text"])) for w in palabras]
            return
        # Tabla de futuros
        # (en el ES, la fila de fechas "EMINI S&P FUT 12/18 ..." empieza igual: lleva fechas)
        if texto.startswith(self.cfg["futuros"]["etiqueta"]) and not any(RE_DIA_MES.match(w["text"]) for w in linea):
            self.en_futuros = True
            return
        if texto.startswith("TOTAL"):
            self.en_futuros = False
        if self.en_futuros and _anio_mes(linea[0]["text"]):
            self.futuros[linea[0]["text"]] = _fila_futuro(linea, self.cfg)
            return
        # Rangos de strikes ("QZ 2500-44000 5000-44500 ...")
        codigo = linea[0]["text"]
        patron = self.cfg.get("diarias_codigos")
        if patron and re.fullmatch(patron, codigo) and all(RE_RANGO.match(w["text"]) for w in linea[1:]):
            self.codigos_diarias.append(codigo)
        if codigo in self.cfg["rangos"] and len(linea) > 1 and all(RE_RANGO.match(w["text"]) for w in linea[1:]):
            for w in linea[1:]:
                am = self._mes_en_x(w["x0"])
                if am:
                    m = RE_RANGO.match(w["text"])
                    self.rangos[(self.cfg["rangos"][codigo], am)] = (float(m.group(1)), float(m.group(2)), codigo)
            return
        # Fechas de vencimiento ("EMINI S&P OPT 12/18 03/19 ...")
        n = next((k for k, w in enumerate(linea) if RE_DIA_MES.match(w["text"])), None)
        if n == 0:
            # Fila de fechas sin etiqueta (en el ES, las diarias E1A...E5D, en el mismo
            # orden que sus códigos en la tabla de rangos): se emparejan después
            fechas = []
            for w in linea:
                am, dm = self._mes_en_x(w["x0"]), RE_DIA_MES.match(w["text"])
                if am and dm and int(dm.group(1)) == am[1]:
                    fechas.append(date(am[0], am[1], int(dm.group(2))))
            self.filas_sin_etiqueta[-1].append(fechas)
            return
        if self.filas_sin_etiqueta[-1]:
            self.filas_sin_etiqueta.append([])
        if n:
            etiqueta = " ".join(w["text"] for w in linea[:n])
            for patron, valor in self.cfg.get("fechas", {}).items():
                m = re.fullmatch(patron, etiqueta)
                if not m:
                    continue
                producto = m.expand(valor)
                for w in linea[n:]:
                    am, dm = self._mes_en_x(w["x0"]), RE_DIA_MES.match(w["text"])
                    if am and dm and int(dm.group(1)) == am[1]:
                        self.fechas[(producto, am[0], am[1])] = date(am[0], am[1], int(dm.group(2)))
                break


def leer_futuros(ruta, config=None, indice=None):
    """Lee SOLO la cabecera: fecha del boletín y apertura/máximo/mínimo de
    cada futuro del índice (y la liquidación si la tabla la trae). Es rápido
    (no lee las opciones).

    Devuelve (fecha_datos, preliminar, {"DEC26": {...}, ...}). Si el máximo o
    el mínimo llevan B/A (bid/ask, sin operación a ese precio) se indica.
    """
    cfg = config_indice(config, indice)
    if cfg.get("lector") == "oro":
        from fuentes import cme_pdf_oro
        return cme_pdf_oro.leer_futuros(ruta)
    cab = _Cabecera(cfg)
    fecha, preliminar = None, False
    with pdfplumber.open(ruta) as pdf:
        for pagina in pdf.pages[:cfg["paginas_cabecera"]]:
            for linea in _agrupar_lineas(pagina.extract_words()):
                texto = " ".join(w["text"] for w in linea).strip()
                if fecha is None and RE_FECHA.search(texto):
                    fecha = datetime.strptime(" ".join(RE_FECHA.search(texto).groups()), "%b %d %Y").date()
                if texto == "PRELIMINARY":
                    preliminar = True
                cab.linea(linea, texto)
    if fecha is None:
        raise ErrorDatos(f"{ruta}: no encuentro la fecha del boletín (¿es la sección "
                         f"{'/'.join(map(str, cfg['secciones']))}?).")
    return fecha, preliminar, cab.futuros


def _texto_secciones(cfg):
    s = cfg["secciones"]
    return f"sección {s[0]}" if len(s) == 1 else f"secciones {', '.join(map(str, s[:-1]))} y {s[-1]}"


def leer(rutas, config=None, indice=None):
    """Lee el PDF (o los PDF: en el ES, calls y puts por separado) del mismo
    boletín y devuelve (tabla_validada, info).

    info es un diccionario con: indice, fecha_datos, preliminar, futuros
    (liquidación de cada futuro trimestral), avisos, descartados,
    productos_desconocidos y comprobaciones.
    """
    if isinstance(rutas, (str, Path)):
        rutas = [rutas]
    cfg = config_indice(config, indice)
    if cfg.get("lector") == "oro":              # sección 64 (oro): formato propio
        from fuentes import cme_pdf_oro
        completa = config if (config and config.get("indice") == cfg["indice"]) else \
            configuracion.para_indice(config or configuracion.cargar(), cfg["indice"])
        return cme_pdf_oro.leer(rutas, completa, cfg["indice"])
    avisos, descartados, eoo = [], {}, []
    futuros, preliminar = {}, False
    cab = _Cabecera(cfg)
    bloques = []         # bloques (producto + tipo + mes) en el orden de los PDF
    fechas_pdf = []
    re_fut = None
    if cfg["futuros"]["liquidacion"] == "opciones":
        re_fut = re.compile(rf"^([A-Z]{{3}}\d{{2}}) {re.escape(cfg['futuros']['etiqueta_liquidacion'])} "
                            r"\(FUTURES SETT\. ([\d.]+)")

    for ruta in rutas:
        fecha_pdf = None
        tipo, clave_seccion, etiquetada = None, None, False
        dentro, en_eoo, actual = False, False, None
        eoo_producto, eoo_etiqueta, eoo_mes = None, None, None
        with pdfplumber.open(ruta) as pdf:
            for n_pagina, pagina in enumerate(pdf.pages):
                for linea in _agrupar_lineas(pagina.extract_words()):
                    texto = " ".join(w["text"] for w in linea).strip()

                    if fecha_pdf is None:
                        m = RE_FECHA.search(texto)
                        if m:
                            fecha_pdf = datetime.strptime(" ".join(m.groups()), "%b %d %Y").date()
                    if texto == "PRELIMINARY":
                        preliminar = True
                    # Cabecera: fechas, rangos de strikes y tabla de futuros
                    if n_pagina < cfg["paginas_cabecera"] and not dentro:
                        cab.linea(linea, texto)

                    if texto.startswith("OPTIONS EOO'S"):
                        dentro, en_eoo, actual = False, True, None
                        continue

                    # Sección EOO/BLOCKS (solo para comprobar), por producto
                    if en_eoo and not RE_SECCION.search(texto):
                        m = RE_EOO_MES.match(texto)
                        if m and m.group(1) in MESES:
                            eoo_mes = (2000 + int(m.group(2)), MESES[m.group(1)], m.group(3).lower())
                            continue
                        m = RE_EOO_FILA.match(texto)
                        if m and eoo_etiqueta and eoo_mes:
                            if eoo_producto:      # los de productos descartados no se comprueban
                                eoo.append({"etiqueta": eoo_etiqueta, "producto": eoo_producto,
                                            "tipo": eoo_mes[2], "anio_mes": eoo_mes[:2],
                                            "strike": float(m.group(1))})
                            continue
                        reconocida, producto = producto_de_linea(texto, cfg)
                        if reconocida:
                            eoo_producto, eoo_mes = producto, None
                            eoo_etiqueta = re.split(r"\s+E-MINI\b", texto)[0]
                        continue

                    # Cabeceras de sección de opciones
                    m = RE_SECCION.search(texto)
                    if m and (cfg["palabra_seccion"] in texto):
                        dentro, en_eoo, actual = True, False, None
                        tipo = "call" if m.group(3) == "CALLS" else "put"
                        clave_seccion = f"W{m.group(2)}" if m.group(2) else None
                        etiquetada = False
                        continue
                    if not dentro:
                        continue
                    if texto.startswith(("STRIKE", "SETT.", "OPEN", "VOLUME", "INTEREST",
                                         "HIGH LOW", "TRADES", "DELTA", "EXER", "CISES",
                                         "CLEARED", "--CONTRACT")):
                        continue

                    # Liquidación de los futuros trimestrales (NQ: en las opciones)
                    m = re_fut.match(texto) if re_fut else None
                    if m and float(m.group(2)) > 0:  # ".00" = sin liquidación publicada
                        futuros[m.group(1)] = float(m.group(2))

                    # Línea de mes (con o sin etiqueta de producto): empieza un bloque
                    m = RE_MES.match(texto)
                    if m and (m.group(1) in MESES or m.group(3)):
                        if m.group(1):
                            anio, mes = 2000 + int(m.group(2)), MESES[m.group(1)]
                        else:
                            anio, mes = int(m.group(3)), int(m.group(4))
                        etiqueta = re.sub(r"\(FUTURES.*", "", m.group(5) or "").strip()
                        if etiqueta:
                            etiquetada = True
                            clave = clave_de_etiqueta(etiqueta, cfg)
                        elif etiquetada:
                            clave = None        # sin etiqueta: se identifica por la secuencia
                        else:
                            clave = clave_seccion  # p. ej. meses de "WEEKLY-1 CALLS"
                        actual = None
                        if clave or etiquetada:
                            actual = {"clave": clave, "etiqueta": etiqueta, "sin_etiqueta": clave is None,
                                      "tipo": tipo, "anio_mes": (anio, mes), "strikes": set(), "filas": [],
                                      "todas": []}
                            bloques.append(actual)
                        continue

                    # Fila de opción
                    primera = linea[0]
                    if not (primera["x0"] < X_STRIKE_MAX and primera["text"].isdigit()):
                        continue
                    if actual is None or _es_cabecera_de_pagina(linea):
                        continue
                    strike = float(primera["text"])
                    actual["strikes"].add(strike)      # todos, para las comprobaciones
                    sett_txt = _en_columna(linea, X_SETT)
                    actual["todas"].append({"strike": strike, "precio": _numero(sett_txt) if sett_txt else None})
                    oi_txt = _en_columna(linea, X_OI)
                    oi = _numero(oi_txt) if oi_txt else None
                    if not oi:
                        continue  # sin open interest (o vencida hoy): no aporta
                    vol = _numero(_en_columna(linea, X_VOLUMEN) or "") or 0
                    delta_txt = _en_columna(linea, X_DELTA)
                    actual["filas"].append({
                        "tipo": tipo,
                        "anio_mes": actual["anio_mes"],
                        "strike": strike,
                        "open_interest": oi,
                        "volumen": vol,
                        "precio": _numero(sett_txt) if sett_txt else None,
                        "delta_cme": _numero(delta_txt) if delta_txt else None,
                        "cambio_oi": _cambio_oi(linea),
                        "vencimiento": None,
                        "_n": len(actual["todas"]) - 1,   # fila en "todas"
                    })
        fechas_pdf.append((Path(ruta).name, fecha_pdf))

    if any(f is None for _, f in fechas_pdf):
        raise ErrorDatos(f"No encuentro la fecha del boletín: ¿es el PDF de la {_texto_secciones(cfg)}?")
    if len({f for _, f in fechas_pdf}) > 1:
        raise ErrorDatos("Los PDF son de boletines de días distintos: "
                         + ", ".join(f"{n} ({f})" for n, f in fechas_pdf))
    fecha_datos = fechas_pdf[0][1]
    if cfg["futuros"]["liquidacion"] == "tabla":
        futuros = {k: v["liquidacion"] for k, v in cab.futuros.items() if v.get("liquidacion")}

    # Bloques sin etiqueta -> producto, por la secuencia del otro lado
    avisos += resolver_sin_etiqueta(bloques)

    fechas_diarias = cab.fechas_diarias()
    filas, desconocidos = [], {}
    diarias = {"fechados_por_precio": 0.0, "primera_fecha": 0.0, "descartados": 0.0}
    for b in bloques:
        b["producto"], motivo = producto_de_clave(b["clave"], cfg)
        if motivo:
            nombre = b["etiqueta"] or f"{b['clave'] or '?'} ({b['tipo']}s sin etiqueta)"
            descartados[nombre] = motivo
            if motivo.startswith("producto desconocido"):
                desconocidos.setdefault(nombre, []).extend(
                    {"strike": f["strike"], "mes": _mes_texto(b["anio_mes"]), "tipo": b["tipo"],
                     "open_interest": f["open_interest"]} for f in b["filas"])
            continue
        # Diarias con varias semanas en un mismo bloque (ES): si el boletín da sus
        # fechas, los strikes que tienen todas las semanas se fechan por precio.
        fijas = {}
        fechas_serie = _fechas_de_serie(b, fechas_diarias, fecha_datos)
        if len(fechas_serie) >= 2:
            fijas, resumen_b = separar_por_precio(b, fechas_serie, cfg.get("diarias_por_precio", False))
            for k in diarias:
                diarias[k] += resumen_b[k]
        # Strikes repetidos dentro de un bloque = series que el PDF no permite
        # distinguir. Quitamos solo esos strikes y avisamos.
        cuenta = pd.Series([f["strike"] for f in b["filas"] if f["_n"] not in fijas], dtype=float).value_counts()
        repetidos = set(cuenta[cuenta > 1].index)
        if repetidos and len(fechas_serie) < 2:   # las diarias con varias semanas van en su propio resumen
            lista = sorted(repetidos)
            detalle = (str(lista) if len(lista) <= 10 else
                       f"{len(lista)} strikes, de {lista[0]:.0f} a {lista[-1]:.0f} (varias series del mes en un "
                       "mismo bloque; se queda la primera fecha para los no repetidos)")
            avisos.append(f"Bloque {b['producto']} {b['tipo']} {_mes_texto(b['anio_mes'])}: "
                          f"strikes repetidos descartados {detalle}.")
        filas += [dict(f, producto=b["producto"], vencimiento=fijas.get(f["_n"])) for f in b["filas"]
                  if f["_n"] in fijas or f["strike"] not in repetidos]

    def vencimiento_de(producto, anio, mes):
        """La fecha del boletín si la da; si no, la calculada."""
        return cab.fechas.get((producto, anio, mes)) or calcular_vencimiento(producto, anio, mes, fecha_datos)

    # Comprobaciones cruzadas con el propio boletín
    n_rangos, avisos_rangos = comprobar_rangos(bloques, cab.rangos)
    # En la sección EOO salen también los ejercicios de las series que vencen
    # el mismo día del boletín, que ya no aparecen enteras en las tablas:
    # esas no sirven para comprobar.
    def vencida(e):
        # Sin fecha posterior al boletín (p. ej. la diaria del lunes de un mes
        # cuyo último lunes es el propio día del boletín) = ya vencida.
        v = vencimiento_de(e["producto"], *e["anio_mes"])
        return v is None or v <= fecha_datos
    eoo_vigentes = [e for e in eoo if not vencida(e)]
    n_eoo, avisos_eoo = comprobar_eoo(bloques, eoo_vigentes)
    if not cab.rangos:
        avisos_rangos.append(f"No encuentro los rangos de strikes ({', '.join(cfg['rangos'])}) de la "
                             "cabecera: comprobación no hecha.")
    avisos += avisos_rangos + avisos_eoo
    comprobaciones = {
        "puts_sin_etiqueta": [f"{_mes_texto(b['anio_mes'])} → {b['clave']}" for b in bloques
                              if b["sin_etiqueta"] and b["tipo"] == "put"],
        "rangos_pagina1": {"bloques": n_rangos, "avisos": len(avisos_rangos)},
        "eoo": {"strikes": n_eoo, "avisos": len(avisos_eoo),
                "vencidas_no_comprobadas": len(eoo) - len(eoo_vigentes)},
        "fechas_boletin": len(cab.fechas),
    }
    if any(diarias.values()):
        comprobaciones["diarias_varias_semanas"] = diarias
        sin = diarias["primera_fecha"] + diarias["descartados"]
        miles = lambda x: f"{x:,.0f}".replace(",", ".")
        avisos.append(
            "Diarias con varias semanas en un mismo bloque: "
            + (f"{miles(diarias['fechados_por_precio'])} contratos fechados por precio (strikes con todas las "
               "semanas); " if diarias["fechados_por_precio"] else "")
            + f"sin fecha segura {miles(sin)} contratos ({miles(diarias['primera_fecha'])} puestos en la primera "
              f"fecha del mes y {miles(diarias['descartados'])} descartados).")
    calls_sin = [f"{_mes_texto(b['anio_mes'])} → {b['clave']}" for b in bloques
                 if b["sin_etiqueta"] and b["tipo"] == "call"]
    if calls_sin:
        comprobaciones["calls_sin_etiqueta"] = calls_sin

    if not filas:
        raise ErrorDatos("No he encontrado filas de opciones: puede que CME haya cambiado el formato.")

    df = pd.DataFrame(filas)

    # Vencimientos: los del boletín y, si no están, calculados
    df["vencimiento"] = [v if v is not None else vencimiento_de(p, am[0], am[1])
                         for v, p, am in zip(df["vencimiento"], df["producto"], df["anio_mes"])]
    sin_fecha = df["vencimiento"].isna()
    if sin_fecha.any():
        avisos.append(f"{sin_fecha.sum()} filas sin fecha de vencimiento calculable: descartadas.")
    df = df[~sin_fecha]
    df = df[df["vencimiento"] > fecha_datos]

    # Futuro subyacente: el trimestral que vence en o después de la opción. El día que vence el
    # futuro (tercer viernes, a la apertura) solo la trimestral es sobre él; las demás series de
    # ese día (W3 del ES, mensual del NQ) liquidan por la tarde, con el futuro ya vencido, y son
    # sobre el trimestral SIGUIENTE (lo dicen las definiciones de CME y la paridad put-call del
    # propio boletín: W3 del 18-dic-2026 → ESH7, no ESZ6).
    # Vencimiento de cada futuro: el de su trimestral en el boletín (p. ej. jun-2027 el jueves 17
    # por festivo) o, si no está, el tercer viernes.
    fecha_trim = {(v.year, v.month): v for v, p in zip(df["vencimiento"], df["producto"]) if p == "TRIMESTRAL"}
    vtos_fut = sorted(
        (fecha_trim.get((2000 + int(k[3:]), MESES[k[:3]]), _viernes_n(2000 + int(k[3:]), MESES[k[:3]], 3)), v)
        for k, v in futuros.items()
    )
    def futuro_de(venc, producto):
        for vf, precio in vtos_fut:
            if vf > venc or (vf == venc and producto == "TRIMESTRAL"):
                return precio
        return None
    df["futuro"] = [futuro_de(v, p) for v, p in zip(df["vencimiento"], df["producto"])]

    df["subyacente"] = cfg["subyacente"]
    df = df.drop(columns=["anio_mes", "_n"])
    varios = len(rutas) > 1
    info = {
        "indice": cfg["indice"],
        "fuente": f"CME Daily Bulletin, {_texto_secciones(cfg)} "
                  f"({'PDFs descargados' if varios else 'PDF descargado'} a mano)",
        "fecha_datos": fecha_datos,
        "preliminar": preliminar,
        "futuros": futuros,
        "avisos": avisos,
        "descartados": descartados,
        "productos_desconocidos": [
            {"etiqueta": nombre, "oi_total": float(sum(f["open_interest"] for f in lista)),
             "strikes_mas_oi": sorted(lista, key=lambda f: -f["open_interest"])[:3]}
            for nombre, lista in desconocidos.items()],
        "comprobaciones": comprobaciones,
    }
    return validar(df), info


def resumen(df, info):
    """Texto con un resumen por vencimiento, para comprobar a ojo."""
    lineas = [
        f"Fecha de los datos: {info['fecha_datos']}"
        + ("  (boletín PRELIMINAR)" if info["preliminar"] else ""),
        f"Futuros: {info['futuros']}",
        "",
        f"{'vencimiento':<12} {'producto':<11} {'OI calls':>9} {'OI puts':>9} {'strikes':>8}",
    ]
    g = df.groupby(["vencimiento", "producto", "tipo"])["open_interest"].sum().unstack(fill_value=0)
    n = df.groupby(["vencimiento", "producto"])["strike"].nunique()
    for (venc, prod), fila in g.iterrows():
        lineas.append(f"{str(venc):<12} {prod:<11} {fila.get('call', 0):>9.0f} "
                      f"{fila.get('put', 0):>9.0f} {n[(venc, prod)]:>8}")
    lineas.append(f"\nTOTAL filas: {len(df)}")
    if info["descartados"]:
        lineas.append("\nProductos descartados:")
        lineas += [f"  - {k}: {v}" for k, v in sorted(info["descartados"].items())]
    if info["avisos"]:
        lineas.append("\nAvisos:")
        lineas += [f"  - {a}" for a in info["avisos"]]
    return "\n".join(lineas)


if __name__ == "__main__":
    # Uso: python -m fuentes.cme_pdf datos/entrada/Section40_....pdf
    #      python -m fuentes.cme_pdf --indice ES Section47_....pdf Section48_....pdf
    argumentos = sys.argv[1:]
    indice = None
    if argumentos[:1] == ["--indice"]:
        indice, argumentos = argumentos[1], argumentos[2:]
    tabla, datos = leer(argumentos, indice=indice)
    print(resumen(tabla, datos))
