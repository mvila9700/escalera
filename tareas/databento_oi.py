"""
Open interest y liquidaciones de las opciones del ES en Databento (para reconstruir escaleras pasadas).

    # 1) Solo calcula el coste exacto (gratis, no descarga nada):
    .venv/bin/python -m tareas.databento_oi coste --desde 2025-10-31 --hasta 2026-10-29
    # 2) Descarga SOLO si el coste total no pasa del tope (y vuelve a calcularlo justo antes):
    .venv/bin/python -m tareas.databento_oi descargar --desde 2025-10-31 --hasta 2026-10-29 --tope 20
    # NQ: lo mismo con --indice NQ (archivos NQ_... en datos/oi, precios en costes_NQ.json)

Por cada día de datos D (la fecha del boletín) se compran solo los tramos que hacen falta
(horas UTC, medidas el 30-oct-2026 con el boletín del 23-oct):
- "liquidacion": estadísticas de las opciones de D de 19:00 a 24:00 (CME manda la liquidación
  a las 15:00 de Chicago);
- "oi": la PRIMERA publicación del open interest de D, la que ya existe antes de la sesión
  siguiente: la noche de D (≈ 01:30 UTC, tramo de 00:30 a 05:00) o, si D es viernes o víspera
  de festivo, la tarde anterior a la sesión siguiente (≈ 17:30 UTC del domingo, tramo de 16:30
  a 19:30). CME manda una corrección a las 14:00 UTC del día siguiente (ya con la sesión
  abierta): no se usa, para no mirar el futuro (con el 23-oct cambia 114 contratos de 4,8 M);
- "futuros": liquidación del futuro ES de D (casi gratis);
- "definiciones": strike, vencimiento y call/put de cada opción, una vez por semana (la foto
  de las 00:00 UTC del primer día hábil de la semana). CME lista las opciones con semanas de
  antelación y el número de cada opción no cambia, así que basta con una foto por semana.
Los archivos se guardan tal cual en datos/oi/ (DBN comprimido) y los lee fuentes.databento_oi.
Lo ya descargado no se vuelve a pagar.
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from calculos.calendario import es_habil, siguiente_habil
from tareas.databento_velas import _cliente

DATASET = "GLBX.MDP3"
CARPETA = Path("datos/oi")
# Raíces de las opciones del ES: trimestrales (ES), fin de mes (EW), viernes (EW1-EW4) y diarias
# de lunes a jueves (E1A-E5D: semana 1-5, A = lunes ... D = jueves).
RAICES = ["ES", "EW"] + [f"EW{i}" for i in range(1, 5)] + [f"E{i}{d}" for i in "12345" for d in "ABCD"]
OPCIONES = [f"{r}.OPT" for r in RAICES]
FUTUROS = ["ES.FUT"]
# NQ (comprobado con la resolución de símbolos de Databento, gratis, el 3-nov-2026): trimestrales
# (NQ), fin de mes (QNE), viernes (QN1-QN5; la mensual es la QN3) y diarias de lunes a jueves
# (Q1A-Q5D). Las micro (MNQ, MQ...) no entran, como en el lector del PDF.
RAICES_NQ = ["NQ", "QNE"] + [f"QN{i}" for i in range(1, 6)] + [f"Q{i}{d}" for i in "12345" for d in "ABCD"]
# Oro (Fase ORO-1, 2026-11-07): mensuales (OG), viernes (OG1-OG5) y diarias de lunes a jueves
# (G1M-G5R: semana 1-5, M = lunes, T = martes, W = miércoles, R = jueves). Las micro (OMG, MMG...)
# no entran, como en el lector de la sección 64. Solo las raíces que Databento conoce (las de la
# 5.ª semana no existen todos los meses: una raíz desconocida en el periodo da error 422).
RAICES_GC = ["OG"] + [f"OG{i}" for i in range(1, 6)] + [f"G{i}{d}" for i in "12345" for d in "MTWR"]
POR_INDICE = {"ES": (OPCIONES, FUTUROS),
              "NQ": ([f"{r}.OPT" for r in RAICES_NQ], ["NQ.FUT"]),
              "GC": ([f"{r}.OPT" for r in RAICES_GC], ["GC.FUT"])}


def lunes_de(d):
    """Primer día hábil de la semana de d (la foto de definiciones de esa semana)."""
    lunes = d - timedelta(days=d.weekday())
    return lunes if es_habil(lunes) else siguiente_habil(lunes)


def tramos(dia, indice="ES"):
    """[(esquema, símbolos, inicio, fin, archivo)] que hay que comprar para el boletín de `dia`."""
    OPCIONES, FUTUROS = POR_INDICE[indice]
    archivo_i = lambda d, n: archivo(d, n, indice)
    sesion = siguiente_habil(dia)
    manana = dia + timedelta(days=1)
    # El oro liquida a las 13:30 de Nueva York (18:30 UTC en invierno, 17:30 en verano): una hora antes
    ini_liq = "17:00" if indice == "GC" else "19:00"
    out = [
        ("statistics", OPCIONES, f"{dia}T{ini_liq}", f"{manana}T00:00", archivo_i(dia, "liquidacion")),
        ("statistics", OPCIONES, f"{manana}T00:30", f"{manana}T05:00", archivo_i(dia, "oi")),
        ("statistics", FUTUROS, f"{dia}T{ini_liq}", f"{manana}T00:00", archivo_i(dia, "futuros")),
    ]
    if sesion != manana:                     # viernes o víspera de festivo: OI la tarde anterior
        vispera = sesion - timedelta(days=1)
        out.append(("statistics", OPCIONES, f"{vispera}T16:30", f"{vispera}T19:30", archivo_i(dia, "oi_vispera")))
    foto = lunes_de(sesion)
    out.append(("definition", OPCIONES, f"{foto}T00:00", f"{foto}T01:00", archivo_definiciones(foto, indice)))
    return out


def archivo(dia, nombre, indice="ES"):
    return CARPETA / f"{indice}_{dia}_{nombre}.dbn.zst"


def archivo_definiciones(foto, indice="ES"):
    return CARPETA / f"{indice}_definiciones_{foto}.dbn.zst"


def dias_de_datos(desde, hasta):
    """Días hábiles de CME entre desde y hasta (incluidos): cada uno es un boletín."""
    d, out = desde, []
    while d <= hasta:
        if es_habil(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _consulta(esquema, simbolos, ini, fin):
    return dict(dataset=DATASET, schema=esquema, symbols=simbolos, stype_in="parent", start=ini, end=fin)


def pendientes(dias, indice="ES"):
    """Tramos aún no descargados (sin repetir la foto semanal de definiciones)."""
    vistos, out = set(), []
    for dia in dias:
        for esquema, simbolos, ini, fin, destino in tramos(dia, indice):
            if destino.exists() or destino in vistos:
                continue
            vistos.add(destino)
            out.append((destino, (esquema, simbolos, ini, fin)))
    return out


def _coste(cliente, q, intentos=5):
    """Consulta de coste (gratis) con reintentos: Databento a veces corta la conexión."""
    import time
    for n in range(intentos):
        try:
            return cliente.metadata.get_cost(**_consulta(*q))
        except Exception:
            if n == intentos - 1:
                raise
            time.sleep(5 * (n + 1))


def costes(cliente, lista, progreso=False, cache=None):
    """Coste de cada tramo (consultas gratuitas, una a una). Cada precio se apunta en `cache`
    (datos/oi/costes.json) para no repetir consultas si se corta la conexión a mitad."""
    import json
    cache = Path(cache) if cache else CARPETA / "costes.json"
    sabidos = json.loads(cache.read_text()) if cache.exists() else {}
    detalle, total = [], 0.0
    for i, (destino, q) in enumerate(lista, 1):
        clave = f"{q[0]}|{q[2]}|{q[3]}|{'fut' if q[1][0].endswith('.FUT') else 'opt'}"
        if clave not in sabidos:
            sabidos[clave] = _coste(cliente, q)
            if i % 10 == 0:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(sabidos))
        total += sabidos[clave]
        detalle.append((destino, q, sabidos[clave]))
        if progreso and i % 50 == 0:
            print(f"  … {i}/{len(lista)} tramos: {total:.2f} $", flush=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(sabidos))
    return total, detalle


def bajar(cliente, q, destino, intentos=5):
    """Descarga un tramo con reintentos (Databento a veces da 504). Escribe en un temporal y lo
    renombra al final: un corte nunca deja un archivo a medias (que luego se daría por bajado)."""
    import time
    tmp = destino.with_name(destino.name + ".parcial")
    for n in range(intentos):
        try:
            cliente.timeseries.get_range(**_consulta(*q), path=tmp)
            tmp.replace(destino)
            return
        except Exception:
            tmp.unlink(missing_ok=True)
            if n == intentos - 1:
                raise
            time.sleep(10 * (n + 1))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=["coste", "descargar"])
    p.add_argument("--desde", required=True, help="primer día de datos (fecha del boletín)")
    p.add_argument("--hasta", required=True, help="último día de datos (incluido)")
    p.add_argument("--tope", type=float, help="descargar: máximo de dólares en total (obligatorio)")
    p.add_argument("--indice", default="ES", choices=sorted(POR_INDICE), help="ES (por defecto) o NQ")
    a = p.parse_args(argv)
    if a.accion == "descargar" and a.tope is None:
        print("Para descargar hace falta --tope (máximo de dólares).")
        return 1
    cliente, error = _cliente()
    if not cliente:
        print(f"No disponible: {error}.")
        return 1
    dias = dias_de_datos(date.fromisoformat(a.desde), date.fromisoformat(a.hasta))
    lista = pendientes(dias, a.indice)
    # Cada índice con su caché de precios (las claves no llevan el índice)
    cache = CARPETA / ("costes.json" if a.indice == "ES" else f"costes_{a.indice}.json")
    total, detalle = costes(cliente, lista, progreso=True, cache=cache)
    por_tipo = {}
    for destino, q, k in detalle:
        nombre = "definiciones" if "definiciones" in destino.name else destino.name.split("_", 2)[2].split(".")[0]
        por_tipo[nombre] = por_tipo.get(nombre, 0.0) + k
    print(f"{a.indice}: {len(dias)} días de datos ({a.desde} → {a.hasta}), {len(lista)} tramos por comprar: "
          f"{total:.2f} $ en total")
    for nombre, k in sorted(por_tipo.items()):
        print(f"  {nombre:<14} {k:8.2f} $")
    if a.accion == "coste":
        return 0
    if total > a.tope:
        print(f"No descargo: el coste ({total:.2f} $) supera el tope (--tope {a.tope}).")
        return 1
    CARPETA.mkdir(parents=True, exist_ok=True)
    gastado = 0.0
    for destino, q, k in detalle:
        if gastado + k > a.tope:                    # por si el precio cambiara a mitad
            print(f"Paro: con este tramo pasaría del tope ({gastado:.2f} $ gastados).")
            return 1
        bajar(cliente, q, destino)
        gastado += k
    print(f"Descargados {len(detalle)} tramos en {CARPETA} ({gastado:.2f} $).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
