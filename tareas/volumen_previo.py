"""
VAL, VAH y POC de las sesiones anteriores que el precio aún no ha vuelto a tocar (solo referencia
visual; cálculo en calculos/volumen_previo.py). Lo llama la generación diaria del NQ y del ES antes de
guardar la escalera: deja en el JSON "volumen_previo" y el ESC2 lleva un «VP=tipo:precio:AAAAMMDD»
por nivel (opcional: sin él, el indicador funciona igual).

    .venv/bin/python -m tareas.volumen_previo --indice NQ --sesion 2026-11-16   # solo enseñarlos

Velas: las de 1 minuto por sesión de Databento que ya usa «Estado hoy» (tareas.estado_hoy.velas_databento:
contrato de la escalera, ventana Globex, guardadas en datos/velas/databento/ para no pagarlas dos veces,
con los topes de databento_diario). Además, si Databento ya tiene algo de la noche de la sesión que se
genera, esas velas (sin guardar, céntimos) cuentan para la mitigación. Si algo falla, la escalera sale
igual, sin «VP=».
"""
import argparse
import copy
import logging
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from calculos import config as configuracion
from calculos import estado
from calculos import volumen_previo as vp
from calculos.calendario import anterior_habil

NUEVA_YORK = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def sesiones_previas(sesion, n):
    """Las n sesiones hábiles anteriores a `sesion`, de la más antigua a la más reciente."""
    res, d = [], sesion
    for _ in range(n):
        d = anterior_habil(d - timedelta(days=1))
        res.append(d)
    return res[::-1]


def _hora_ny(d, h, m):
    return datetime.combine(d, time(h, m), NUEVA_YORK).astimezone(UTC)


def velas_de_sesiones(indice, sesion, contrato_ref, config, cliente=None, hoy=None):
    """{fecha: velas UTC con volumen} de las sesiones anteriores y, si hay, la noche de `sesion`."""
    from tareas import estado_hoy
    cfg = copy.deepcopy(config)
    cfg.setdefault("estado", {})["ventana_velas"] = "globex"     # hace falta lo de después del cierre
    res, motivos = {}, []
    for d in sesiones_previas(sesion, int(config["volumen_previo"].get("sesiones", 5))):
        v, nombre = estado_hoy.velas_databento(d, indice, contrato_ref, cfg, cliente=cliente, hoy=hoy)
        if v is None:
            motivos.append(f"{d}: {nombre}")
        else:
            res[d] = v
    noche = velas_noche(indice, sesion, contrato_ref, cliente, hoy)
    if noche is not None:
        res[sesion] = noche
    return res, motivos


def velas_noche(indice, sesion, contrato_ref, cliente=None, hoy=None):
    """Velas de la sesión que se genera desde su apertura en Globex hasta lo que ya tenga Databento
    (sin guardar). None si aún no hay nada, si no cabe en los topes o si no hay Databento."""
    try:
        import databento as db
        from tareas import escalera_databento as ed
        from tareas.estado_hoy import simbolo_databento
        simbolo = simbolo_databento(indice, contrato_ref)
        inicio, fin = estado.ventana_sesion(sesion, "globex")
        if cliente is None:
            from tareas.databento_velas import _cliente
            cliente, error = _cliente()
            if not cliente:
                return None
        hasta = min(ed.disponible_hasta(cliente).replace(tzinfo=UTC), fin, datetime.now(UTC))
        if not simbolo or hasta <= inicio + timedelta(minutes=5):
            return None
        q = dict(dataset="GLBX.MDP3", schema="ohlcv-1m", symbols=[simbolo], stype_in="raw_symbol",
                 start=inicio.strftime("%Y-%m-%dT%H:%M"), end=hasta.strftime("%Y-%m-%dT%H:%M"))
        coste = float(cliente.metadata.get_cost(**q))
        hoy = hoy or date.today()
        ok, motivo = ed.puede_gastar(coste, hoy, ed._config())
        if not ok:
            logging.info("Volumen previo %s: sin las velas de la noche (%s)", indice, motivo)
            return None
        v = cliente.timeseries.get_range(**q).to_df(price_type="float")
        if coste > 0:
            ed.apuntar_gasto(hoy, coste)
        if v.empty:
            return None
        v = v[["open", "high", "low", "close", "volume"]].astype(float)
        v.index = v.index.tz_convert("UTC")
        return v
    except Exception as e:                       # la noche es un extra: sin ella, Pine mitiga en vivo
        logging.info("Volumen previo %s: sin las velas de la noche (%s)", indice, e)
        return None


def calcular(sesion, velas, config):
    """Niveles no mitigados (calculos.volumen_previo.niveles) con las velas de velas_de_sesiones()."""
    import pandas as pd
    cfg = config["volumen_previo"]
    todas = pd.concat([velas[d] for d in sorted(velas)]).sort_index() if velas else None
    sesiones = []
    for d in sorted(velas):
        if d >= sesion:
            continue
        v = velas[d]
        rth = v[(v.index >= _hora_ny(d, 9, 30)) & (v.index < _hora_ny(d, 16, 0))]
        despues = todas[todas.index >= _hora_ny(d, 16, 0)]
        sesiones.append({"fecha": d, "rth": rth, "despues": despues})
    hasta = todas.index.max().isoformat() if todas is not None and len(todas) else None
    return vp.niveles(sesiones, float(cfg.get("tramo_puntos", 5)), float(cfg.get("value_area", 0.70))), hasta


def anadir(datos, config=None, cliente=None, hoy=None):
    """Pone datos["volumen_previo"] (o nada si falla o está desactivado). Nunca lanza excepciones."""
    indice = datos.get("indice", "NQ")
    try:
        if indice not in ("NQ", "ES"):
            return None
        config = config or configuracion.para_indice(configuracion.cargar(), indice)
        cfg = config.get("volumen_previo") or {}
        if not cfg.get("activo", False):
            return None
        sesion = date.fromisoformat(str(datos["fecha_sesion"]))
        velas, motivos = velas_de_sesiones(indice, sesion, datos.get("contrato_ref"), config, cliente, hoy)
        lista, hasta = calcular(sesion, velas, config)
        datos["volumen_previo"] = {
            "nota": "Solo referencia visual: sin ventaja estable en el estudio del 15-nov-2026; no son niveles de la escalera.",
            "sesiones": [str(d) for d in sorted(velas) if d < sesion], "faltan": motivos,
            "mitigacion_hasta": hasta, "tramo_puntos": cfg.get("tramo_puntos"), "niveles": lista}
        logging.info("Volumen previo %s %s: %d sin mitigar (%s)%s", indice, sesion, len(lista),
                     ", ".join(f"{n['tipo']} {n['precio']:g} {n['dia']}" for n in lista) or "ninguno",
                     f"; faltan {motivos}" if motivos else "")
        return datos["volumen_previo"]
    except Exception:
        logging.exception("Volumen previo %s: no se pudo calcular (la escalera sale sin «VP=»)", indice)
        return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--indice", choices=["NQ", "ES"], required=True)
    p.add_argument("--sesion", required=True, help="sesión para la que valen (AAAA-MM-DD)")
    p.add_argument("--contrato", default="DEC26", help="contrato de la escalera (DEC26, MAR27...)")
    a = p.parse_args(argv)
    from tareas.escalera import _configurar_registro
    _configurar_registro()
    datos = {"indice": a.indice, "fecha_sesion": a.sesion, "contrato_ref": a.contrato}
    r = anadir(datos)
    if not r:
        print("No se pudo calcular (mira datos/registro.log).")
        return 1
    print(f"{a.indice} · sesión {a.sesion} · sesiones {', '.join(r['sesiones'])} · velas hasta {r['mitigacion_hasta']}")
    for f in r["faltan"]:
        print("  falta:", f)
    for n in r["niveles"]:
        print(f"  {n['tipo']} {n['precio']:g}  ({n['dia']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
