"""
Calendario de días hábiles del mercado de EE. UU. (CME / NYSE).

OJO: no es el calendario federal. El mercado abre en Columbus Day y en
Veterans Day, y cierra en Viernes Santo. Los cierres anticipados (p. ej.
el día después de Acción de Gracias) no se tienen en cuenta.
"""
from datetime import date, timedelta

import numpy as np
from pandas.tseries.holiday import (
    AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay, USMartinLutherKingJr,
    USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday,
)


class CalendarioMercadoEEUU(AbstractHolidayCalendar):
    rules = [
        Holiday("Año Nuevo", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("Independencia", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Navidad", month=12, day=25, observance=nearest_workday),
    ]


_CAL = CalendarioMercadoEEUU()


def festivos(desde=date(2020, 1, 1), hasta=date(2035, 12, 31)):
    return set(_CAL.holidays(desde, hasta).date)


_FESTIVOS = festivos()
_FESTIVOS_NP = np.array(sorted(_FESTIVOS), dtype="datetime64[D]")


def es_habil(d):
    return d.weekday() < 5 and d not in _FESTIVOS


def siguiente_habil(d):
    """Primer día hábil estrictamente posterior a d."""
    d += timedelta(days=1)
    while not es_habil(d):
        d += timedelta(days=1)
    return d


def anterior_habil(d):
    """Último día hábil en o antes de d."""
    while not es_habil(d):
        d -= timedelta(days=1)
    return d


def habiles_entre(desde, hasta):
    """Número de días hábiles d con desde < d <= hasta."""
    return int(np.busday_count(
        np.datetime64(desde + timedelta(days=1)), np.datetime64(hasta + timedelta(days=1)),
        holidays=_FESTIVOS_NP,
    ))
