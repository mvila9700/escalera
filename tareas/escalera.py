"""
Tarea de la escalera (la de las 14:00).

Uso a mano, desde la carpeta del proyecto:
    .venv/bin/python -m tareas.escalera                 # NQ: PDF más reciente de datos/entrada
    .venv/bin/python -m tareas.escalera --indice ES     # ES: secciones 47 y 48 más recientes
    .venv/bin/python -m tareas.escalera --precio 29250  # con precio de referencia a mano
    .venv/bin/python -m tareas.escalera --pdf ruta.pdf  # (ES: --pdf Section47_....pdf Section48_....pdf)
    .venv/bin/python -m tareas.escalera --csv datos/entrada/manual.csv --fecha-datos 2026-10-23 --futuro 29117.75
    .venv/bin/python -m tareas.escalera --no-abrir      # no abre el navegador
    .venv/bin/python -m tareas.escalera --indice ES --comprobar   # ¿están el 47 y el 48 del mismo boletín?
    .venv/bin/python -m tareas.escalera --indice ES --recentrar --precio 7740   # precio fuera del 68 %

Recentrar: si el precio actual sale del rango del 68 % de la escalera del día, la recalcula con el
MISMO open interest (el boletín archivado de ese día) centrada en ese precio y guarda un ESC2 nuevo
como ES_<sesión>_recentrada_<precio> (el histórico del día no se toca). La página del día avisa y
enseña el botón para copiarlo. Dentro del rango no hace nada (salvo con --forzar).

Si algo falla (no hay PDF, formato cambiado, datos que no son del día
anterior...), genera una página de ERROR en vez de mostrar datos viejos.
"""
import argparse
import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

import re

from calculos import config as configuracion
from calculos.calendario import es_habil, siguiente_habil
from calculos.config import RAIZ, cargar
from calculos.escalera import calcular
from fuentes.modelo import ErrorDatos
from salida import historico, pagina

REGISTRO = RAIZ / "datos" / "registro.log"


def _configurar_registro():
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=REGISTRO, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")


def sesion_esperada(hoy=None):
    """La sesión que toca preparar: hoy si es hábil, si no, la siguiente."""
    hoy = hoy or date.today()
    return hoy if es_habil(hoy) else siguiente_habil(hoy)


def pdf_mas_reciente(prefijo="Section40"):
    pdfs = sorted((RAIZ / "datos" / "entrada").glob(f"{prefijo}*.pdf"), key=lambda p: p.stat().st_mtime)
    if not pdfs:
        raise FileNotFoundError(f"No hay ningún PDF '{prefijo}...' en datos/entrada.")
    return pdfs[-1]


def pdfs_del_boletin(indice="NQ"):
    """PDF más reciente de cada sección del índice (NQ: 40; ES: 47 y 48).

    Si el índice tiene varias secciones, tienen que ser del mismo boletín (la
    fecha se lee dentro del PDF); si falta una o son de días distintos, da un
    error que dice qué falta.
    """
    from fuentes import cme_pdf
    cfg = configuracion.cargar()["indices"][indice]["cme_pdf"]
    nombres = dict(zip(cfg["archivos"], cfg["secciones"]))
    faltan = []
    rutas = []
    for prefijo, seccion in nombres.items():
        try:
            rutas.append(pdf_mas_reciente(prefijo))
        except FileNotFoundError:
            faltan.append(seccion)
    if faltan:
        raise ErrorDatos(f"Falta la sección {' y '.join(map(str, faltan))} del {indice} en datos/entrada "
                         f"(hacen falta las {' y '.join(map(str, cfg['secciones']))} del mismo boletín).")
    if len(rutas) > 1:
        fechas = {r: cme_pdf.leer_futuros(r, indice=indice)[0] for r in rutas}
        if len(set(fechas.values())) > 1:
            ultima = max(fechas.values())
            viejas = [_seccion(r) for r, f in fechas.items() if f < ultima]
            raise ErrorDatos(
                f"Las secciones del {indice} son de boletines distintos ("
                + ", ".join(f"{_seccion(r)}: {f}" for r, f in fechas.items())
                + f"): falta la sección {' y '.join(map(str, viejas))} del boletín del {ultima}.")
    return rutas


def _seccion(ruta):
    m = re.match(r"Section(\d+)", Path(ruta).name)
    return int(m.group(1)) if m else None


def leer_datos(args, config, indice):
    if args.csv:
        from fuentes import csv_manual
        if not (args.fecha_datos and args.futuro):
            raise ValueError("Con --csv hay que indicar también --fecha-datos y --futuro.")
        df = csv_manual.cargar(args.csv)
        df["futuro"] = args.futuro
        df["producto"] = "MANUAL"
        info = {"fecha_datos": date.fromisoformat(args.fecha_datos), "preliminar": False,
                "fuente": f"CSV manual ({args.csv})", "avisos": ["Datos cargados a mano desde un CSV."]}
        return df, info
    from fuentes import cme_pdf
    rutas = args.pdf or pdfs_del_boletin(indice)
    logging.info("Leyendo %s", ", ".join(map(str, rutas)))
    df, info = cme_pdf.leer(rutas, config, indice)
    for ruta in rutas:
        archivar(ruta, info["fecha_datos"], info["preliminar"])
    return df, info


def archivar(ruta, fecha_datos, preliminar):
    """Copia el PDF a datos/entrada/archivo/SectionNN_AAAA-MM-DD.pdf (NN = su
    sección: 40, 47 o 48).

    Así el boletín de cada día sigue disponible para el "Estado hoy" aunque el
    navegador guarde el siguiente con el mismo nombre.
    """
    import shutil
    carpeta = RAIZ / "datos" / "entrada" / "archivo"
    carpeta.mkdir(parents=True, exist_ok=True)
    seccion = _seccion(ruta) or 40
    destino = carpeta / f"Section{seccion}_{fecha_datos}{'_preliminar' if preliminar else ''}.pdf"
    if not destino.exists():
        shutil.copy2(ruta, destino)


def pdfs_archivados(indice, fecha_datos, preliminar):
    """Los PDF del boletín de ese día en datos/entrada/archivo (el mismo open interest)."""
    cfg = configuracion.cargar()["indices"][indice]["cme_pdf"]
    carpeta = RAIZ / "datos" / "entrada" / "archivo"
    rutas = [carpeta / f"Section{s}_{fecha_datos}{'_preliminar' if preliminar else ''}.pdf" for s in cfg["secciones"]]
    faltan = [r.name for r in rutas if not r.exists()]
    if faltan:
        raise ErrorDatos(f"Para recentrar hace falta el boletín archivado del {fecha_datos}: falta {', '.join(faltan)} "
                         "en datos/entrada/archivo.")
    return rutas


def fuera_del_rango(precio, rango):
    """Puntos que el precio está fuera del rango (0 si está dentro)."""
    lo, hi = rango
    return max(0.0, lo - precio, precio - hi)


def recentrar(indice, precio, sesion=None, forzar=False, abrir=True):
    """Escalera recentrada en `precio` con el mismo open interest. Devuelve (código, ruta de la página)."""
    from fuentes import cme_pdf
    if sesion is None:
        ultimo = _ultimo_historico(indice)
        if not ultimo:
            raise ErrorDatos(f"No hay ninguna escalera del {indice} en el histórico para recentrar.")
        sesion = ultimo["fecha_sesion"]
    original = historico.cargar(sesion, indice)
    rango = original["movimiento"]["rango_68"]
    fuera = fuera_del_rango(precio, rango)
    if fuera == 0 and not forzar:
        print(f"El precio {precio:g} está dentro del rango del 68 % ({rango[0]:g} – {rango[1]:g}) de la escalera "
              f"del {sesion}: no hace falta recentrar (usa --forzar si aun así la quieres).")
        return 0, None
    fuente = str(original.get("fuente", ""))
    config = configuracion.para_indice(cargar(), indice)
    fecha_datos = date.fromisoformat(original["fecha_datos"])
    if fuente.startswith("CME Daily Bulletin"):
        df, info = cme_pdf.leer(pdfs_archivados(indice, fecha_datos, original.get("preliminar")), config, indice)
    elif fuente.startswith("Databento"):
        # Mismo open interest ya descargado (datos/oi): no se compra nada
        from fuentes import databento_oi
        df, info = databento_oi.leer(fecha_datos, indice=indice)
        info["fuente"] = fuente
    else:
        raise ErrorDatos(f"No sé recentrar una escalera de esta fuente: {fuente or '¿?'}.")
    res = calcular(df, info, config, precio)
    if str(res["fecha_sesion"]) != str(sesion):
        raise ErrorDatos(f"El boletín archivado da la sesión del {res['fecha_sesion']}, no la del {sesion}.")
    datos = historico.desde_resultado(res)
    lado = "por encima" if precio > rango[1] else "por debajo" if precio < rango[0] else "dentro"
    datos["origen_precio"] = (f"RECENTRADA en el precio actual {precio:g} (escrito a mano), mismo open interest "
                              f"del boletín del {fecha_datos}")
    datos["recentrada"] = {"precio": precio, "rango_68_original": rango, "precio_original": original["precio_ref"],
                           "fuera_puntos": fuera, "lado": lado}
    datos["avisos"].insert(0, f"ESCALERA RECENTRADA: el precio ({precio:g}) estaba {lado} del rango del 68 % "
                              f"({rango[0]:g} – {rango[1]:g}) de la escalera del día, calculada en "
                              f"{original['precio_ref']:g}. Mismo open interest; cambian la gamma, la frontera, "
                              "los rangos y los niveles que dependen del precio.")
    ruta_json = historico.guardar_recentrada(datos, precio)
    ultimo = _ultimo_historico(indice)
    ruta = pagina.escribir(original, es_la_ultima=bool(ultimo) and ultimo["fecha_sesion"] == original["fecha_sesion"],
                           indice=indice)
    logging.info("Escalera recentrada: %s", ruta_json)
    print(f"Escalera recentrada en {precio:g} ({lado} del rango del 68 %, a {fuera:g} puntos): {ruta_json}\n"
          f"ESC2 recentrado:\n{datos['tradingview_esc2']}\nPágina: {ruta}")
    if abrir:
        subprocess.run(["open", str(ruta)], check=False)
    return 0, ruta


def main(argv=None):
    p = argparse.ArgumentParser(description="Genera la escalera de niveles del NQ o del ES.")
    p.add_argument("--indice", default="NQ", help="NQ (por defecto) o ES")
    p.add_argument("--pdf", nargs="+", help="PDF concretos (ES: el de la sección 47 y el de la 48)")
    p.add_argument("--comprobar", action="store_true",
                   help="solo dice si están los PDF del boletín (para el vigilante): 0 = sí, 3 = falta algo")
    p.add_argument("--csv")
    p.add_argument("--fecha-datos", help="AAAA-MM-DD (solo con --csv)")
    p.add_argument("--futuro", type=float, help="liquidación del futuro (solo con --csv)")
    p.add_argument("--precio", type=float, help="precio de referencia escrito a mano")
    p.add_argument("--no-abrir", action="store_true")
    p.add_argument("--permitir-viejos", action="store_true",
                   help="genera la página aunque los datos no sean del día anterior (para pruebas)")
    p.add_argument("--recentrar", action="store_true",
                   help="con --precio: si sale del rango del 68 %%, recalcula con el mismo OI centrada en ese precio")
    p.add_argument("--sesion", help="recentrar: sesión AAAA-MM-DD (por defecto, la última del histórico)")
    p.add_argument("--forzar", action="store_true", help="recentrar aunque el precio esté dentro del rango")
    args = p.parse_args(argv)
    _configurar_registro()
    indice = args.indice

    if args.recentrar:
        if not args.precio:
            print("Para recentrar hace falta el precio actual: --precio 7740", file=sys.stderr)
            return 2
        try:
            codigo, _ = recentrar(indice, args.precio, args.sesion, args.forzar, abrir=not args.no_abrir)
            return codigo
        except (ErrorDatos, FileNotFoundError, ValueError) as e:
            logging.exception("Fallo al recentrar")
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

    if args.comprobar:
        try:
            rutas = pdfs_del_boletin(indice)
            print("Completo: " + ", ".join(Path(r).name for r in rutas))
            return 0
        except (ErrorDatos, FileNotFoundError) as e:
            print(str(e))
            return 3

    try:
        config = configuracion.para_indice(cargar(), indice)
        df, info = leer_datos(args, config, indice)
        res = calcular(df, info, config, args.precio)
        datos = historico.desde_resultado(res)
        esperada = sesion_esperada()
        if res["fecha_sesion"] != esperada:
            mensaje = (f"Los datos son para la sesión del {res['fecha_sesion']} y hoy toca la del {esperada}. "
                       "Probablemente CME aún no ha publicado el boletín o no se ha descargado el PDF nuevo.")
            if not args.permitir_viejos:
                raise ValueError(mensaje)
            datos["avisos"].insert(0, "DATOS ANTIGUOS (generado con --permitir-viejos): " + mensaje)
        ruta_json = historico.guardar(datos)
        ruta = pagina.escribir(datos, indice=indice)
        logging.info("Escalera generada: %s / %s", ruta_json, ruta)
        _comparar_con_databento(datos, indice)
        print(f"Escalera generada: {ruta}\nHistórico: {ruta_json}")
        codigo = 0
        # El PDF de hoy es el boletín de la sesión anterior: rellena su estado
        try:
            from tareas import estado_hoy
            estado_hoy.main(["--indice", indice])
        except Exception:
            logging.exception("Fallo al actualizar el estado de sesiones anteriores")
    except Exception as e:  # cualquier fallo -> página de error clara
        logging.exception("Fallo generando la escalera")
        ultimo = _ultimo_historico(indice)
        ruta = pagina.escribir(ultimo, error=str(e), indice=indice)
        print(f"ERROR: {e}\nPágina de error: {ruta}", file=sys.stderr)
        codigo = 1

    if not args.no_abrir:
        subprocess.run(["open", str(ruta)], check=False)
    return codigo


def _comparar_con_databento(datos, indice):
    """Si la tarea de Databento ya hizo la escalera de esta sesión, apunta las diferencias con la
    del PDF en el registro. Nunca hace fallar la escalera del PDF."""
    try:
        from tareas.reconstruir_escalera import comparar, texto_comparacion
        otra = historico.carpeta() / f"{historico.nombre(datos['fecha_sesion'], indice)}_databento_esc2.txt"
        if otra.exists() and datos.get("tradingview_esc2"):
            c = comparar(datos["tradingview_esc2"], otra.read_text(encoding="utf-8"))
            logging.info("Comparación %s %s (PDF frente a Databento):\n%s", indice, datos["fecha_sesion"],
                         texto_comparacion(c).replace("real", "PDF").replace("reconstruida", "Databento"))
    except Exception:
        logging.exception("No se pudo comparar con la escalera de Databento")


def _ultimo_historico(indice="NQ"):
    patron = "????-??-??.json" if indice == "NQ" else f"{indice}_????-??-??.json"
    archivos = sorted(historico.carpeta().glob(patron))
    if not archivos:
        return None
    return historico.cargar(archivos[-1].stem[-10:], indice)


if __name__ == "__main__":
    sys.exit(main())
