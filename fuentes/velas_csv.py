"""
Lectura de un CSV de velas de 1 minuto exportado de tu gráfico (opcional).

Se usa para el "Estado hoy": hora del toque y si el nivel se respetó o se rompió.

Formatos aceptados:
- Columna de tiempo llamada time / datetime / date / timestamp / fecha
  (o dos columnas: date + time). Puede ser:
    * número = segundos Unix (o milisegundos), como exporta TradingView;
    * texto con zona horaria (2026-10-26T15:30:00+01:00);
    * texto sin zona horaria: se entiende en la zona de config.yaml
      (estado.zona_horaria, por defecto Europe/Madrid).
- Columnas open, high, low, close (en inglés o apertura/maximo/minimo/cierre).
"""
from pathlib import Path

import pandas as pd

ALIAS = {
    "open": "open", "apertura": "open", "o": "open",
    "high": "high", "maximo": "high", "máximo": "high", "h": "high",
    "low": "low", "minimo": "low", "mínimo": "low", "l": "low",
    "close": "close", "cierre": "close", "c": "close",
}
COLUMNAS_TIEMPO = ["time", "datetime", "date", "timestamp", "fecha", "hora", "fecha_hora"]


def es_csv_de_velas(ruta):
    """True si el CSV tiene columnas de máximo, mínimo y cierre."""
    try:
        cab = pd.read_csv(ruta, nrows=0).columns
    except Exception:
        return False
    nombres = {ALIAS.get(c.strip().lower()) for c in cab}
    return {"high", "low", "close"} <= nombres


def leer(ruta, zona_horaria="Europe/Madrid"):
    """Devuelve un DataFrame con índice de tiempo (UTC) y columnas open/high/low/close."""
    df = pd.read_csv(ruta)
    df.columns = [c.strip().lower() for c in df.columns]
    tiempo_cols = [c for c in COLUMNAS_TIEMPO if c in df.columns]
    if not tiempo_cols:
        raise ValueError(f"{Path(ruta).name}: no encuentro la columna de tiempo.")
    if "date" in df.columns and "time" in df.columns and not pd.api.types.is_numeric_dtype(df["time"]):
        bruto = df["date"].astype(str) + " " + df["time"].astype(str)
    else:
        bruto = df[tiempo_cols[0]]

    if pd.api.types.is_numeric_dtype(bruto):
        unidad = "ms" if bruto.iloc[0] > 1e12 else "s"
        tiempo = pd.to_datetime(bruto, unit=unidad, utc=True)
    else:
        tiempo = pd.to_datetime(bruto, utc=False)
        if tiempo.dt.tz is None:
            tiempo = tiempo.dt.tz_localize(zona_horaria)
        tiempo = tiempo.dt.tz_convert("UTC")

    velas = df.rename(columns={c: ALIAS[c] for c in df.columns if c in ALIAS})
    velas = velas[["open", "high", "low", "close"]].astype(float)
    velas.index = tiempo
    return velas.sort_index()
