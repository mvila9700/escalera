"""
Medición (GRATIS, solo metadatos) de a qué hora publica Databento los datos de OPRA, para saber si
el open interest de OCC (llega a OPRA a las 06:30 NY) estaría a tiempo para la escalera diaria.

    .venv/bin/python -m tareas.medir_opra            # una medición (lo lanza launchd cada 10 min)
    .venv/bin/python -m tareas.medir_opra --resumen  # a qué hora (NY) quedó disponible cada día

Cada medición añade una línea a datos/opra/disponibilidad.csv: hora (UTC y NY), hasta dónde tiene
datos OPRA.PILLAR y GLBX.MDP3 (CME), y los minutos que el Mac durmió desde la medición anterior.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CSV = Path("datos/opra/disponibilidad.csv")
NY = ZoneInfo("America/New_York")


def medir():
    from tareas.databento_velas import _cliente
    c, error = _cliente()
    ahora = datetime.now(timezone.utc)
    fila = {"ahora_utc": ahora.strftime("%Y-%m-%d %H:%M"), "ahora_ny": ahora.astimezone(NY).strftime("%Y-%m-%d %H:%M"),
            "opra_hasta_utc": "", "cme_hasta_utc": "", "error": error or ""}
    if c:
        for clave, ds in (("opra_hasta_utc", "OPRA.PILLAR"), ("cme_hasta_utc", "GLBX.MDP3")):
            try:
                fila[clave] = c.metadata.get_dataset_range(dataset=ds)["end"][:16].replace("T", " ")
            except Exception as e:
                fila["error"] = str(e)[:80]
    # ¿Durmió el Mac? launchd lo lanza cada 10 min: un hueco de más de 15 min es reposo
    # (el reloj monotónico de Python cuenta desde que arranca cada proceso: aquí no sirve)
    previo = pd.read_csv(CSV).iloc[-1] if CSV.exists() else None
    fila["dormido_min"] = ""
    if previo is not None:
        hueco = (ahora - datetime.strptime(previo["ahora_utc"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                 ).total_seconds() / 60
        if hueco > 15:
            fila["dormido_min"] = round(hueco - 10)
    CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([fila]).to_csv(CSV, mode="a", header=not CSV.exists(), index=False)
    return fila


def resumen():
    """Por día (NY): primera medición en la que OPRA ya tenía datos hasta las 07:30 NY de ese día
    (el open interest llega a las 06:30 NY) y la hora NY de esa medición."""
    t = pd.read_csv(CSV)
    t["ahora"] = pd.to_datetime(t["ahora_utc"], utc=True)
    t["opra"] = pd.to_datetime(t["opra_hasta_utc"], utc=True, errors="coerce")
    t["dia"] = t["ahora"].dt.tz_convert(NY).dt.date
    for dia, g in t.groupby("dia"):
        objetivo = pd.Timestamp(f"{dia} 07:30", tz=NY).tz_convert("UTC")
        listo = g[g["opra"] >= objetivo]
        hora = listo["ahora"].min().tz_convert(NY).strftime("%H:%M") if len(listo) else "aún no"
        dormido = pd.to_numeric(g["dormido_min"], errors="coerce").sum()
        print(f"{dia}: OPRA con el open interest de las 06:30 NY disponible a las {hora} NY · "
              f"{len(g)} mediciones · Mac dormido {dormido:.0f} min")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--resumen", action="store_true")
    a = p.parse_args(argv)
    if a.resumen:
        resumen()
    else:
        print(medir())
    return 0


if __name__ == "__main__":
    sys.exit(main())
