"""
Formato común de los datos de opciones.

Todas las fuentes (CSV manual, PDF de CME, Databento, Interactive Brokers...)
deben devolver una tabla (DataFrame de pandas) con estas mismas columnas.
Así los cálculos no necesitan saber de dónde vienen los datos.
"""
import pandas as pd

# Columnas obligatorias y qué significa cada una
COLUMNAS = {
    "subyacente": "NQ, NDX, ES o GC",
    "vencimiento": "fecha de vencimiento de la opción (fecha)",
    "tipo": "call o put",
    "strike": "precio de ejercicio",
    "open_interest": "contratos abiertos al cierre",
    "volumen": "contratos negociados en el día (puede faltar: 0)",
    "precio": "precio de liquidación (settlement) de la opción",
}

# Columnas opcionales:
#   iv        volatilidad implícita, si la fuente la trae
#   producto  tipo de serie (W1, LUNES, TRIMESTRAL...)
#   delta_cme delta publicada por CME (sirve para comprobar nuestros cálculos)
#   futuro    precio de liquidación del futuro subyacente de esa opción
#   cambio_oi cambio de open interest respecto al día anterior. OJO: las
#             cadenas que muestran el OI "del día" (p. ej. TradingView el
#             viernes) suelen enseñar el del día anterior = open_interest - cambio_oi
COLUMNAS_OPCIONALES = ["iv", "producto", "delta_cme", "futuro", "cambio_oi"]


class ErrorDatos(Exception):
    """Error claro cuando los datos no tienen el formato esperado."""


def validar(df: pd.DataFrame) -> pd.DataFrame:
    """Comprueba que la tabla tiene el formato común y la limpia.

    Lanza ErrorDatos con un mensaje en español si algo no cuadra,
    en vez de seguir con datos malos.
    """
    faltan = [c for c in COLUMNAS if c not in df.columns]
    if faltan:
        raise ErrorDatos(f"Faltan columnas: {', '.join(faltan)}")

    df = df.copy()
    df["subyacente"] = df["subyacente"].astype(str).str.upper().str.strip()
    df["tipo"] = df["tipo"].astype(str).str.lower().str.strip()
    # Aceptamos también "C"/"P"
    df["tipo"] = df["tipo"].replace({"c": "call", "p": "put"})
    df["vencimiento"] = pd.to_datetime(df["vencimiento"]).dt.date
    for col in ["strike", "open_interest", "volumen", "precio"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volumen"] = df["volumen"].fillna(0)

    malos_tipo = set(df["tipo"]) - {"call", "put"}
    if malos_tipo:
        raise ErrorDatos(f"Tipos no reconocidos (deben ser call/put): {malos_tipo}")
    malos_sub = set(df["subyacente"]) - {"NQ", "NDX", "ES", "GC"}
    if malos_sub:
        raise ErrorDatos(f"Subyacentes no reconocidos (deben ser NQ, NDX, ES o GC): {malos_sub}")
    if df[["strike", "open_interest"]].isna().any().any():
        raise ErrorDatos("Hay filas sin strike u open interest")
    if df.empty:
        raise ErrorDatos("La tabla está vacía")

    columnas = list(COLUMNAS) + [c for c in COLUMNAS_OPCIONALES if c in df.columns]
    return df[columnas].sort_values(["subyacente", "vencimiento", "strike", "tipo"]).reset_index(drop=True)
