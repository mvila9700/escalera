"""
Coste EXACTO (consultas gratuitas de Databento, no descarga nada) de las opciones de QQQ y NDX
(OPRA.PILLAR) que harían falta para la escalera del NQ, día a día.

    .venv/bin/python -m tareas.coste_opra --desde 2025-11-03 --hasta 2026-11-03

Por cada sesión S (boletín D = día hábil anterior), las mismas tres piezas que con CME:
  - definiciones: de 06:00 a 07:00 NY de S (OPRA las publica ≈ 06:00 NY; desde 2026 las repite cada hora);
  - open interest de OCC del cierre de D: estadísticas de 05:00 a 07:30 NY de S (llega ≈ 06:00 NY);
  - precios para la IV: cbbo-1m (mejor oferta y demanda consolidadas) de 15:55 a 16:00 NY de D.
Grupos: QQQ (ETF), NDX + NDXP (índice, mensuales AM y semanales/diarias PM) y XND (micro NDX).
Resultado: datos/opra/costes_opra.csv (una fila por sesión y grupo) y un resumen en pantalla.
"""
import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from calculos.calendario import anterior_habil, es_habil

NY, UTC = ZoneInfo("America/New_York"), ZoneInfo("UTC")
GRUPOS = {"QQQ": ["QQQ.OPT"], "NDX": ["NDX.OPT", "NDXP.OPT"], "XND": ["XND.OPT"]}
SALIDA = Path("datos/opra/costes_opra.csv")


def _utc(dia, hh, mm):
    return datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=NY).astimezone(UTC).strftime("%Y-%m-%dT%H:%M")


def tramos(sesion):
    d = anterior_habil(sesion - timedelta(days=1))
    return {"definiciones": ("definition", _utc(sesion, 6, 0), _utc(sesion, 7, 0)),
            "open_interest": ("statistics", _utc(sesion, 5, 0), _utc(sesion, 7, 30)),
            "precios": ("cbbo-1m", _utc(d, 15, 55), _utc(d, 16, 0))}


def _coste(c, esquema, simbolos, ini, fin, intentos=5):
    for n in range(intentos):
        try:
            return c.metadata.get_cost(dataset="OPRA.PILLAR", schema=esquema, symbols=simbolos,
                                       stype_in="parent", start=ini, end=fin)
        except Exception:
            if n == intentos - 1:
                raise
            time.sleep(5 * (n + 1))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", required=True)
    p.add_argument("--hasta", required=True)
    a = p.parse_args(argv)
    from tareas.databento_velas import _cliente
    c, error = _cliente()
    if not c:
        print(error)
        return 1
    hechas = pd.read_csv(SALIDA) if SALIDA.exists() else pd.DataFrame(columns=["sesion", "grupo"])
    vistas = set(zip(hechas["sesion"].astype(str), hechas["grupo"]))
    filas = hechas.to_dict("records")
    s, fin = date.fromisoformat(a.desde), date.fromisoformat(a.hasta)
    while s <= fin:
        if es_habil(s):
            for grupo, simbolos in GRUPOS.items():
                if (str(s), grupo) in vistas:
                    continue
                fila = {"sesion": str(s), "grupo": grupo}
                for nombre, (esquema, ini, f) in tramos(s).items():
                    fila[nombre] = _coste(c, esquema, simbolos, ini, f)
                filas.append(fila)
            SALIDA.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(filas).to_csv(SALIDA, index=False)
        s += timedelta(days=1)
    t = pd.DataFrame(filas)
    t["total"] = t[["definiciones", "open_interest", "precios"]].sum(axis=1)
    print(t.groupby("grupo")[["definiciones", "open_interest", "precios", "total"]].mean().round(4))
    return 0


if __name__ == "__main__":
    sys.exit(main())
