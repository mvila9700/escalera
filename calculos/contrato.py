"""
Contrato de futuros al que se refieren los niveles y aviso de cambio de contrato.

- Los niveles están en precios del futuro sobre el que son las opciones del
  vencimiento más próximo (p. ej. DEC26 = NQZ2026 en TradingView).
- Los futuros trimestrales del NQ y del ES vencen el tercer viernes del mes. El
  "roll" (el volumen pasa al siguiente contrato) es 8 días antes, en jueves.
- Entre el roll y el vencimiento, las opciones de esa semana siguen siendo
  sobre el contrato viejo, pero la mayoría opera ya el nuevo: en el gráfico
  del contrato nuevo los niveles aparecerían desplazados por la base
  (diferencia de precio entre los dos contratos).
"""
from datetime import timedelta

from calculos.calendario import habiles_entre

LETRA_MES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M", 7: "N", 8: "Q",
             9: "U", 10: "V", 11: "X", 12: "Z"}
MESES = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7,
         "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
NOMBRE_MES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
NOMBRE_DIA = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _anio_mes(codigo):
    """'DEC26' -> (2026, 12)"""
    return 2000 + int(codigo[3:]), MESES[codigo[:3]]


def ticker_tradingview(codigo, raiz="NQ"):
    """'DEC26' -> 'NQZ2026'"""
    anio, mes = _anio_mes(codigo)
    return f"{raiz}{LETRA_MES[mes]}{anio}"


def vencimiento(codigo):
    from fuentes.cme_pdf import _viernes_n
    anio, mes = _anio_mes(codigo)
    return _viernes_n(anio, mes, 3)


def fecha_roll(codigo):
    """Jueves, 8 días antes del vencimiento (fecha de roll de CME)."""
    return vencimiento(codigo) - timedelta(days=8)


def siguiente(codigo):
    """'DEC26' -> 'MAR27'"""
    anio, mes = _anio_mes(codigo)
    mes += 3
    if mes > 12:
        mes, anio = mes - 12, anio + 1
    inverso = {v: k for k, v in MESES.items()}
    return f"{inverso[mes]}{anio % 100:02d}"


def _corta(d):
    return f"{NOMBRE_DIA[d.weekday()]} {d.day} {NOMBRE_MES[d.month - 1]}"


def info(fecha_sesion, codigo, futuros, dias_aviso=5, raiz="NQ"):
    """Diccionario con el contrato, el roll y, si toca, el aviso para la página.

    raiz: raíz del ticker de TradingView ("NQ", "ES")."""
    if not codigo:
        return None
    if raiz == "GC":
        return info_oro(fecha_sesion, codigo, futuros, dias_aviso)
    roll, venc, sig = fecha_roll(codigo), vencimiento(codigo), siguiente(codigo)
    actual_tv, siguiente_tv = ticker_tradingview(codigo, raiz), ticker_tradingview(sig, raiz)
    base = (futuros[sig] - futuros[codigo]) if (sig in futuros and codigo in futuros) else None
    txt_base = f"{base:+,.0f}".replace(",", ".") + " puntos" if base is not None else "desconocida"

    aviso = None
    if roll <= fecha_sesion <= venc:
        aviso = (f"Ya pasó el cambio de contrato (roll, {_corta(roll)}): el volumen está en {siguiente_tv}, "
                 f"pero estos niveles siguen en precios de {actual_tv} (las opciones de esta semana son sobre "
                 f"ese contrato) hasta su vencimiento el {_corta(venc)}. En el gráfico de {siguiente_tv} "
                 f"aparecerían desplazados por la base ({txt_base}): usa el gráfico de {actual_tv}.")
    elif fecha_sesion < roll and habiles_entre(fecha_sesion, roll) <= dias_aviso:
        aviso = (f"Se acerca el cambio de contrato (roll): {_corta(roll)}, dentro de "
                 f"{habiles_entre(fecha_sesion, roll)} días hábiles. Desde ese día el volumen pasa a "
                 f"{siguiente_tv}, pero los niveles seguirán en precios de {actual_tv} hasta el "
                 f"{_corta(venc)}. Base actual {siguiente_tv} − {actual_tv}: {txt_base}.")
    return {
        "codigo": codigo, "tradingview": actual_tv, "siguiente": siguiente_tv,
        "roll": roll, "vencimiento": venc, "base": base, "aviso": aviso,
    }


def info_oro(fecha_sesion, codigo, futuros, dias_aviso=5):
    """Contrato del oro (GC). Meses activos: feb, abr, jun, ago y dic. Cada opción va sobre SU
    futuro (GCZ6, GCG7...), y la escalera traslada todos los strikes al contrato del gráfico con
    la base entre futuros (como el SPX con el ES). Cambio de contrato (roll): el día en que vencen
    las opciones OG del contrato (fuentes.cme_pdf_oro.fecha_roll), antes del primer aviso de
    entrega; desde ese día la escalera sale ya en precios del siguiente."""
    from fuentes import cme_pdf_oro as oro
    roll, sig = oro.fecha_roll(codigo), oro.siguiente_activo(codigo)
    aviso_entrega = oro.primer_aviso(*_anio_mes(codigo))
    actual_tv, siguiente_tv = ticker_tradingview(codigo, "GC"), ticker_tradingview(sig, "GC")
    base = (futuros[sig] - futuros[codigo]) if (sig in futuros and codigo in futuros) else None
    txt_base = (f"{base:+,.1f}".replace(",", "X").replace(".", ",").replace("X", ".") + " $") if base is not None else "desconocida"
    aviso = None
    if fecha_sesion < roll and habiles_entre(fecha_sesion, roll) <= dias_aviso:
        aviso = (f"Se acerca el cambio de contrato del oro: {_corta(roll)} (vencen las opciones de "
                 f"{actual_tv}; primer aviso de entrega el {_corta(aviso_entrega)}), dentro de "
                 f"{habiles_entre(fecha_sesion, roll)} días hábiles. Desde ese día la escalera sale en precios "
                 f"de {siguiente_tv} (pásate a ese gráfico). Base actual {siguiente_tv} − {actual_tv}: {txt_base}.")
    bases = {k: round(v - futuros[codigo], 2) for k, v in futuros.items() if codigo in futuros}
    return {
        "codigo": codigo, "tradingview": actual_tv, "siguiente": siguiente_tv,
        "roll": roll, "vencimiento": aviso_entrega, "base": base, "aviso": aviso,
        "bases": bases,
    }
