"""
Velas de 1 minuto del ES de Databento para el backtest (se agrupan a 2 minutos al cargar).

    # 1) Solo calcula el coste (no descarga nada, no gasta nada):
    .venv/bin/python -m tareas.databento_velas coste --desde 2021-11-01 --hasta 2026-10-29

    # 2) Descarga SOLO si el coste exacto no pasa del tope que pongas tú:
    .venv/bin/python -m tareas.databento_velas descargar --desde 2021-11-01 --hasta 2026-10-29 --tope 5

Necesita la clave en la variable DATABENTO_API_KEY (o en el archivo .env del proyecto) y el paquete `databento`
(.venv/bin/pip install databento). Sin ellos, «coste» da solo la estimación por tamaño.

Series:
- fase A (histórico largo): ES.v.0 = contrato del ES con más volumen cada día
  (continuo sin ajustar; las sesiones del cambio de contrato se excluyen del backtest).
- fase B (con escalera): ESZ6 = el contrato de los niveles (ESZ2026 en TradingView).
"""
import argparse
import os
import sys
from datetime import date
from pathlib import Path

DATASET = "GLBX.MDP3"
ESQUEMA = "ohlcv-1m"
BYTES_REGISTRO = 56          # tamaño de un registro OHLCV en DBN
VELAS_DIA = 23 * 60          # Globex: 23 h de negociación por sesión
CARPETA = Path("datos/velas")


def estimar(desde, hasta):
    dias_habiles = sum(1 for d in range((hasta - desde).days + 1)
                       if date.fromordinal(desde.toordinal() + d).weekday() < 5)
    registros = dias_habiles * VELAS_DIA
    return registros, registros * BYTES_REGISTRO


def _clave_env(ruta=Path(".env")):
    """DATABENTO_API_KEY del archivo .env del proyecto (no se muestra nunca)."""
    if not ruta.exists():
        return None
    for linea in ruta.read_text().splitlines():
        k, _, v = linea.partition("=")
        if k.strip() == "DATABENTO_API_KEY":
            return v.strip().strip('"').strip("'") or None
    return None


def _cliente():
    try:
        import databento as db
    except ImportError:
        return None, "falta el paquete databento (.venv/bin/pip install databento)"
    clave = os.environ.get("DATABENTO_API_KEY") or _clave_env()
    if not clave:
        return None, "falta la variable DATABENTO_API_KEY"
    return db.Historical(clave), None


def _consulta(a):
    stype = "continuous" if ".v." in a.simbolo or ".c." in a.simbolo else "raw_symbol"
    return dict(dataset=DATASET, schema=ESQUEMA, symbols=[a.simbolo], stype_in=stype,
                start=a.desde, end=a.hasta)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=["coste", "descargar"])
    p.add_argument("--desde", required=True)
    p.add_argument("--hasta", required=True, help="fecha final (no incluida)")
    p.add_argument("--simbolo", default="ES.v.0", help="ES.v.0 (fase A) o ESZ6 (fase B)")
    p.add_argument("--tope", type=float, help="descargar: máximo de dólares que aceptas gastar")
    a = p.parse_args(argv)

    registros, tam = estimar(date.fromisoformat(a.desde[:10]), date.fromisoformat(a.hasta[:10]))
    print(f"{a.simbolo} {ESQUEMA} {a.desde} → {a.hasta}: ~{registros:,} velas de 1 min, "
          f"~{tam / 1e6:.1f} MB ({tam / 1e9:.3f} GB) sin comprimir")
    cliente, error = _cliente()
    if not cliente:
        print(f"Coste exacto: no disponible ({error}).")
        return 1 if a.accion == "descargar" else 0
    q = _consulta(a)
    coste = cliente.metadata.get_cost(**q)            # consulta gratuita: no descarga
    tam_real = cliente.metadata.get_billable_size(**q)
    print(f"Coste exacto según Databento: {coste:.2f} $ ({tam_real / 1e6:.1f} MB facturables)")
    if a.accion == "coste":
        return 0
    if a.tope is None or coste > a.tope:
        print(f"No descargo: el coste ({coste:.2f} $) supera el tope (--tope {a.tope}).")
        return 1
    CARPETA.mkdir(parents=True, exist_ok=True)
    destino = CARPETA / f"{a.simbolo.replace('.', '_')}_{a.desde}_{a.hasta}.csv".replace(':', '')
    datos = cliente.timeseries.get_range(**q)
    df = datos.to_df(price_type="float", pretty_ts=True, map_symbols=True)
    df.to_csv(destino)
    print(f"Guardado: {destino} ({len(df):,} velas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
