"""
Vigila el precio y, si sale del rango del 68 % de la escalera del día, genera sola la escalera
RECENTRADA (mismo open interest, centrada en el precio actual) y avisa.

    .venv/bin/python -m tareas.vigilar_rango            # una comprobación (NQ y ES)
    .venv/bin/python -m tareas.vigilar_rango --indice NQ
    .venv/bin/python -m tareas.vigilar_rango --precio 27900 --indice NQ   # con un precio a mano

Lo lanza launchd (tareas/auto/com.marc.vigilar-rango.plist) cada 5 minutos; fuera del horario
`vigilar_rango.desde`–`hasta` (hora de Nueva York, días hábiles) no hace nada.

Precio: Databento no vale para esto (su histórico de GLBX llega con horas de retraso y el
directo es otra suscripción), así que se lee el gráfico público de Yahoo (NQ=F, ES=F: el
contrato principal, ≈ 10 min de retraso, gratis). Si ese precio está a más de
`max_sigmas` desviaciones del precio de la escalera (otro contrato, dato raro), no hace nada
y lo apunta en el registro.

Cuándo recentra: si el precio no está dentro del rango del 68 % de NINGUNA de las escaleras del
día (la original y las recentradas ya hechas). Así, después de recentrar en 27.900, solo vuelve a
avisar si el precio sale también del rango de esa; si vuelve al rango original, no hace nada.
La recentrada se guarda como <sesión>_recentrada_<precio>_esc2.txt (el histórico del día no se
toca), se avisa en pantalla y se abre la página (con el botón para copiar el ESC2).
"""
import argparse
import json
import logging
import subprocess
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from calculos import config as configuracion
from calculos.calendario import es_habil
from salida import historico
from tareas.escalera import fuera_del_rango, recentrar
from tareas.escalera_databento import REGISTRO, avisar

NY = ZoneInfo("America/New_York")


def _config():
    c = configuracion.cargar().get("vigilar_rango") or {}
    return {"indices": c.get("indices", ["NQ", "ES"]), "desde": c.get("desde", "08:00"),
            "hasta": c.get("hasta", "16:00"), "simbolos": c.get("simbolos", {"NQ": "NQ=F", "ES": "ES=F"}),
            "max_sigmas": float(c.get("max_sigmas", 4)), "abrir": bool(c.get("abrir", True)),
            "redondeo": float(c.get("redondeo", 1))}


def precio_yahoo(simbolo, abrir_url=urllib.request.urlopen):
    """(último precio, hora UTC) del gráfico de 1 minuto de Yahoo."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}?interval=1m&range=1d"
    peticion = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with abrir_url(peticion, timeout=20) as r:
        meta = json.load(r)["chart"]["result"][0]["meta"]
    return float(meta["regularMarketPrice"]), datetime.utcfromtimestamp(meta["regularMarketTime"])


def rangos_del_dia(sesion, indice):
    """[(nombre, rango_68)] de la escalera del día y de sus recentradas, o [] si no hay escalera."""
    try:
        original = historico.cargar(sesion, indice)
    except FileNotFoundError:
        return [], None
    out = [("original", original["movimiento"]["rango_68"])]
    out += [(f"recentrada {r['precio']:g}", r["rango_68"]) for r in historico.recentradas(sesion, indice)
            if r.get("rango_68")]
    return out, original


def decidir(precio, rangos, original, max_sigmas):
    """None si no hay que hacer nada (con el motivo), o el precio en el que recentrar."""
    if not rangos:
        return None, "no hay escalera del día"
    sigma = (original.get("movimiento") or {}).get("desviacion") or 0
    if sigma and abs(precio - original["precio_ref"]) > max_sigmas * sigma:
        return None, (f"precio {precio:g} a más de {max_sigmas:g}σ de la escalera ({original['precio_ref']:g}): "
                      "¿otro contrato? no recentro")
    dentro = [n for n, r in rangos if fuera_del_rango(precio, r) == 0]
    if dentro:
        return None, f"precio {precio:g} dentro del rango del 68 % ({dentro[0]})"
    return precio, None


def comprobar(indice, sesion, precio, cfg, abrir=True):
    rangos, original = rangos_del_dia(sesion, indice)
    objetivo, motivo = decidir(precio, rangos, original, cfg["max_sigmas"])
    if objetivo is None:
        logging.info("Vigilar rango %s %s: %s", indice, sesion, motivo)
        return None
    paso = cfg["redondeo"] or 0.25
    objetivo = round(objetivo / paso) * paso
    lo, hi = rangos[0][1]
    lado = "por debajo" if objetivo < lo else "por encima"
    _, ruta = recentrar(indice, objetivo, str(sesion), forzar=True, abrir=False)
    texto = (f"{indice} en {precio:g}, {lado} del rango del 68 % ({lo:g}–{hi:g}). "
             f"Escalera recentrada en {objetivo:g}: pega el ESC2 nuevo en TradingView.")
    logging.info("Vigilar rango %s %s: %s", indice, sesion, texto)
    avisar(f"{indice}: escalera recentrada", texto)
    if abrir and ruta:
        subprocess.run(["open", str(ruta)], check=False)
    return objetivo


def main(argv=None, ahora=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--indice", choices=["NQ", "ES"])
    p.add_argument("--precio", type=float, help="precio a mano (no consulta Yahoo; ignora el horario)")
    p.add_argument("--no-abrir", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(filename=REGISTRO, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = _config()
    ahora = ahora or datetime.now(NY)
    sesion = ahora.date()
    if a.precio is None and (not es_habil(sesion) or not cfg["desde"] <= ahora.strftime("%H:%M") < cfg["hasta"]):
        return 0
    codigo = 0
    for indice in ([a.indice] if a.indice else cfg["indices"]):
        try:
            if a.precio is not None:
                precio = a.precio
            else:
                precio, hora = precio_yahoo(cfg["simbolos"][indice])
                if (datetime.utcnow() - hora).total_seconds() > 3600:
                    logging.info("Vigilar rango %s: el precio de Yahoo es de las %s UTC (viejo), no hago nada",
                                 indice, hora)
                    continue
            hecho = comprobar(indice, sesion, precio, cfg, abrir=cfg["abrir"] and not a.no_abrir)
            if hecho is not None:
                print(f"{indice}: recentrada en {hecho:g}")
        except Exception as e:
            logging.exception("Vigilar rango %s", indice)
            print(f"{indice}: {e}", file=sys.stderr)
            codigo = 1
    return codigo


if __name__ == "__main__":
    sys.exit(main())
