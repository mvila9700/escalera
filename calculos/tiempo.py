"""
Tiempo hasta el vencimiento.

Lo medimos en DÍAS HÁBILES / 252 (no en días naturales / 365):
- Del cierre del viernes al cierre del lunes = 1 día (no 3). Así el fin de
  semana no "diluye" la volatilidad de las opciones de muy corto plazo.
- Así la volatilidad implícita encaja con la fórmula del movimiento
  esperado diario: precio × IV × raíz(1/252).

Las horas son de Nueva York (ET). El "cierre" de referencia son las 16:00 ET
(15:00 de Chicago), que es cuando CME fija la liquidación y cuando vencen
las semanales/diarias. Las trimestrales vencen a la apertura (9:30 ET).
"""
from calculos.calendario import habiles_entre

HORA_CIERRE = 16.0


def anios_habiles(desde_fecha, desde_hora, hasta_fecha, hasta_hora, minimo_horas=1.0):
    """Años (base 252) entre dos momentos. Nunca menos de `minimo_horas`."""
    dias = (habiles_entre(desde_fecha, hasta_fecha)
            + (hasta_hora - HORA_CIERRE) / 24
            - (desde_hora - HORA_CIERRE) / 24)
    return max(dias, minimo_horas / 24) / 252
