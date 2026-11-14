"""
Importación manual de emergencia desde un CSV.

Formato esperado (una fila por strike y tipo; la cabecera es obligatoria):

    subyacente,vencimiento,tipo,strike,open_interest,volumen,precio
    NQ,2026-10-26,call,25000,1520,340,85.25
    NQ,2026-10-26,put,24800,2100,512,40.50

- subyacente: NQ o NDX (si falta la columna, se asume NQ)
- tipo: call/put (o C/P)
- vencimiento: AAAA-MM-DD
- volumen: opcional
"""
import pandas as pd

from fuentes.modelo import validar


def cargar(ruta) -> pd.DataFrame:
    """Lee el CSV y lo devuelve en el formato común ya validado."""
    df = pd.read_csv(ruta)
    df.columns = [c.strip().lower() for c in df.columns]
    if "subyacente" not in df.columns:
        df["subyacente"] = "NQ"
    if "volumen" not in df.columns:
        df["volumen"] = 0
    return validar(df)
