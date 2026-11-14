"""
Opciones de QQQ y NDX (OPRA.PILLAR) en Databento, para marcar la confluencia en la escalera del NQ.

    # Solo el coste exacto (gratis, no descarga nada):
    .venv/bin/python -m tareas.databento_opra coste --desde 2026-08-03 --hasta 2026-10-30
    # Descarga SOLO si el total no pasa del tope (y lo vuelve a calcular justo antes):
    .venv/bin/python -m tareas.databento_opra descargar --desde 2026-08-03 --hasta 2026-10-30 --tope 6

Fechas = SESIONES (las de la escalera). Por cada sesión S (boletín D = hábil anterior), lo mismo
que calcula tareas.coste_opra:
  - definiciones: 06:00-07:00 NY de S (strike, vencimiento, call/put);
  - open interest de OCC del cierre de D: estadísticas de 05:00 a 07:30 NY de S;
  - precios para la IV: cbbo-1m de 15:55 a 16:00 NY de D.
Grupos: QQQ (QQQ.OPT), NDX (NDX.OPT y NDXP.OPT). XND (micro, 1/100 del NDX) se deja fuera
para ahorrar: cada opción equivale a 0,05 opciones NQ (≈ 0,01 $/día; se puede añadir a GRUPOS).
Archivos en datos/opra/<grupo>_<S>_<pieza>.dbn.zst.
"""
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from calculos.calendario import es_habil
from tareas.coste_opra import tramos as ventanas

DATASET = "OPRA.PILLAR"
CARPETA = Path("datos/opra")
GRUPOS = {"QQQ": ["QQQ.OPT"], "NDX": ["NDX.OPT", "NDXP.OPT"]}


def archivo(grupo, sesion, pieza):
    return CARPETA / f"{grupo}_{sesion}_{pieza}.dbn.zst"


def pendientes(sesiones, grupos=GRUPOS):
    out = []
    for s in sesiones:
        for grupo, simbolos in grupos.items():
            for pieza, (esquema, ini, fin) in ventanas(s).items():
                destino = archivo(grupo, s, pieza)
                if not destino.exists():
                    out.append((destino, dict(dataset=DATASET, schema=esquema, symbols=simbolos,
                                              stype_in="parent", start=ini, end=fin)))
    return out


def _reintentar(f, intentos=5):
    for n in range(intentos):
        try:
            return f()
        except Exception:
            if n == intentos - 1:
                raise
            time.sleep(10 * (n + 1))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=["coste", "descargar"])
    p.add_argument("--desde", required=True, help="primera sesión")
    p.add_argument("--hasta", required=True, help="última sesión (incluida)")
    p.add_argument("--tope", type=float, help="descargar: máximo de dólares en total (obligatorio)")
    a = p.parse_args(argv)
    if a.accion == "descargar" and a.tope is None:
        print("Para descargar hace falta --tope (máximo de dólares).")
        return 1
    from tareas.databento_velas import _cliente
    cliente, error = _cliente()
    if not cliente:
        print(f"No disponible: {error}.")
        return 1
    s, fin, sesiones = date.fromisoformat(a.desde), date.fromisoformat(a.hasta), []
    while s <= fin:
        if es_habil(s):
            sesiones.append(s)
        s += timedelta(days=1)
    lista = pendientes(sesiones)
    detalle = [(d, q, _reintentar(lambda q=q: cliente.metadata.get_cost(**q))) for d, q in lista]
    total = sum(k for _, _, k in detalle)
    print(f"{len(sesiones)} sesiones, {len(lista)} tramos por comprar: {total:.2f} $")
    if a.accion == "coste":
        return 0
    if total > a.tope:
        print(f"No descargo: el coste ({total:.2f} $) supera el tope (--tope {a.tope}).")
        return 1
    CARPETA.mkdir(parents=True, exist_ok=True)
    gastado = 0.0
    for destino, q, k in detalle:
        if gastado + k > a.tope:
            print(f"Paro: con este tramo pasaría del tope ({gastado:.2f} $ gastados).")
            return 1
        tmp = destino.with_name(destino.name + ".parcial")
        _reintentar(lambda: cliente.timeseries.get_range(**q, path=tmp))
        tmp.replace(destino)
        gastado += k
    print(f"Descargados {len(detalle)} tramos en {CARPETA} ({gastado:.2f} $).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
