"""
Mantenimiento mensual del estudio de fiabilidad (Fase F-4): NQ y ES.

    .venv/bin/python -m tareas.fiabilidad_mensual            # lo que hace launchd el día 1 a las 22:00
    .venv/bin/python -m tareas.fiabilidad_mensual --sin-velas  # solo repetir el estudio y comparar

Para cada índice:
1. Velas de 1 minuto (MNQ.v.0 para el NQ, ES.v.0 para el ES) desde el final del último CSV hasta ayer.
   Coste exacto primero (consulta gratuita); solo descarga si no pasa de `tope_velas` ni del tope mensual
   de Databento (databento_diario.tope_mes). Lo gastado se apunta en datos/oi/gasto_databento.json.
2. Guarda el estudio anterior (salida/fiabilidad_historico/fiabilidad_<I>_<fecha>.json) y repite
   tareas.estudio_fiabilidad con todas las escaleras y velas.
3. Avisa si alguna casilla (rechazo a 0,2σ, con n ≥ n_minimo antes y ahora) cambia más de
   `umbral_cambio` puntos, o si la ventaja de los muros por encima de la frontera deja de ser positiva.
Cada paso queda en datos/registro.log.
"""
import argparse
import json
import logging
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from calculos import config as configuracion
from tareas import estudio_fiabilidad as ef

RAIZ = Path(__file__).resolve().parent.parent
SIMBOLO = {"NQ": "MNQ.v.0", "ES": "ES.v.0"}
X = "0.2"


def _cfg():
    c = configuracion.cargar()
    m = c.get("fiabilidad", {}).get("mensual", {})
    return {"indices": m.get("indices", ["NQ", "ES"]), "tope_velas": float(m.get("tope_velas", 0.5)),
            "umbral_cambio": float(m.get("umbral_cambio", 10)),
            "n_minimo": int(c.get("fiabilidad", {}).get("n_minimo", 20)),
            "tope_mes": float(c.get("databento_diario", {}).get("tope_mes", 12))}


def ultimo_dia_de_velas(indice):
    """Fecha final (no incluida) del CSV de velas más reciente del índice."""
    fechas = [re.findall(r"\d{4}-\d{2}-\d{2}", p.name)[-1] for p in ef.archivos_velas(indice)]
    return max(date.fromisoformat(f) for f in fechas) if fechas else None


def actualizar_velas(indice, hoy, cfg, cliente=None):
    """(descargado, coste, motivo)."""
    from tareas import databento_velas as dv
    from tareas.escalera_databento import apuntar_gasto, gastado
    desde = ultimo_dia_de_velas(indice)
    if desde is None:
        return False, 0.0, "no hay velas previas"
    if desde >= hoy:
        return False, 0.0, "las velas ya están al día"
    if cliente is None:
        cliente, error = dv._cliente()
        if not cliente:
            return False, 0.0, error
    a = argparse.Namespace(simbolo=SIMBOLO[indice], desde=str(desde), hasta=str(hoy))
    q = dv._consulta(a)
    coste = float(cliente.metadata.get_cost(**q))
    _, mes = gastado(hoy)
    if coste > cfg["tope_velas"]:
        return False, coste, f"costaría {coste:.2f} $ (tope {cfg['tope_velas']:.2f} $)"
    if mes + coste > cfg["tope_mes"] + 1e-9:
        return False, coste, f"costaría {coste:.2f} $ y este mes ya van {mes:.2f} $ (tope {cfg['tope_mes']:.2f} $)"
    df = cliente.timeseries.get_range(**q).to_df(price_type="float", pretty_ts=True, map_symbols=True)
    destino = dv.CARPETA / f"{a.simbolo.replace('.', '_')}_{a.desde}_{a.hasta}.csv"
    df.to_csv(destino)
    apuntar_gasto(hoy, coste)
    return True, coste, f"{len(df):,} velas en {destino.name}".replace(",", ".")


def comparar(antes, ahora, cfg):
    """Lista de avisos (textos) entre dos estudios."""
    avisos = []
    if antes:
        ca, cn = antes["casillas"].get(X, {}), ahora["casillas"].get(X, {})
        for k, a in ca.items():
            n = cn.get(k)
            if not n or a.get("rechazo") is None or n.get("rechazo") is None:
                continue
            if min(a["n"], n["n"]) < cfg["n_minimo"]:
                continue
            if abs(n["rechazo"] - a["rechazo"]) > cfg["umbral_cambio"]:
                avisos.append(f"{k}: {a['rechazo']:.0f} % → {n['rechazo']:.0f} % (n {a['n']} → {n['n']})")
    m = ahora["casillas"].get(X, {}).get("Muros|positiva") or {}
    if m.get("ventaja") is not None and m["ventaja"] <= 0:
        avisos.append(f"La ventaja de los muros por encima de la frontera ya no es positiva: {m['ventaja']:+.0f} "
                      f"puntos ({m['rechazo']:.0f} % frente a {m['control']:.0f} % del control, n={m['n']}).")
    return avisos


def ejecutar(indice, hoy, cfg, velas=True, avisar=None, cliente=None):
    """Hace los 3 pasos para un índice y devuelve el resumen (dict)."""
    salida = RAIZ / "salida"
    ruta = salida / f"fiabilidad_{indice}.json"
    antes = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None
    resumen = {"indice": indice}
    if velas:
        ok, coste, motivo = actualizar_velas(indice, hoy, cfg, cliente)
        resumen["velas"] = motivo
        logging.info("Fiabilidad mensual %s: velas %s (%s; %.2f $)", indice, "descargadas" if ok else "no", motivo, coste)
    if antes:
        hist = salida / "fiabilidad_historico"
        hist.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ruta, hist / f"fiabilidad_{indice}_{antes.get('generado', hoy)}.json")
    codigo = ef.main(["--indice", indice])
    if codigo != 0 or not ruta.exists():
        resumen["error"] = "el estudio no ha salido"
        return resumen
    ahora = json.loads(ruta.read_text(encoding="utf-8"))
    resumen["sesiones"] = ahora["sesiones"]
    resumen["avisos"] = comparar(antes, ahora, cfg)
    logging.info("Fiabilidad mensual %s: %s sesiones; avisos: %s", indice, ahora["sesiones"], resumen["avisos"] or "ninguno")
    if avisar:
        if resumen["avisos"]:
            avisar(f"Fiabilidad {indice}: revisa el estudio", " · ".join(resumen["avisos"])[:250])
        else:
            avisar(f"Fiabilidad {indice}", f"Estudio repetido ({ahora['sesiones']} sesiones): sin cambios de más de "
                                           f"{cfg['umbral_cambio']:.0f} puntos.")
    return resumen


def main(argv=None, hoy=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sin-velas", action="store_true", help="no descargar velas")
    p.add_argument("--indice", choices=["NQ", "ES"], help="solo este índice")
    a = p.parse_args(argv)
    from tareas.escalera import _configurar_registro
    from tareas.escalera_databento import avisar
    _configurar_registro()
    cfg = _cfg()
    hoy = hoy or date.today()
    for indice in ([a.indice] if a.indice else cfg["indices"]):
        try:
            r = ejecutar(indice, hoy, cfg, velas=not a.sin_velas, avisar=avisar)
            print(r)
        except Exception as e:                       # un índice que falla no para al otro
            logging.exception("Fiabilidad mensual %s: error", indice)
            avisar(f"Fiabilidad {indice}: error", str(e)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
