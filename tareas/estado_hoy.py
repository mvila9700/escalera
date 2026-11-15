"""
Tarea "Estado hoy" (la de las 23:00; también se lanza al final de la de las 14:00).

Para cada índice (NQ y ES) y cada sesión reciente de su histórico busca:
1. El Daily Bulletin de ESE día (NQ: sección 40; ES: sección 47, que trae la
   tabla de futuros; en datos/entrada o en datos/entrada/archivo): de él saca
   el máximo y el mínimo del futuro (sesión completa) y marca cada nivel
   "Tocado" / "No alcanzado".
2. Opcional: un CSV de velas de 1 minuto en datos/entrada que cubra esa
   sesión. Con él añade la hora del toque y "respetado" / "roto". El índice
   del CSV se reconoce por el nombre (NQ/MNQ, ES/MES); un CSV sin índice en el
   nombre vale para el NQ, como siempre.
   Si no hay boletín pero sí velas, usa el máximo/mínimo de las velas.
3. Si no hay boletín ni CSV: velas de 1 minuto de Databento del contrato de la escalera
   (NQZ6, ESZ6...) en la misma ventana (config.yaml -> estado -> databento). Dan máximo,
   mínimo, hora del toque y respetado/roto. Cuestan ≈ 0,005 $ por sesión e índice, con los
   mismos topes que la escalera diaria (databento_diario), y se guardan en
   datos/velas/databento/ para no pagarlas dos veces. Si luego llega el boletín, manda él.

Normalmente el boletín del lunes es el PDF que descargas el martes para la
escalera del martes, así que la tarea de las 14:00 rellena el estado del día
anterior sin que tengas que hacer nada más.

Uso a mano:
    .venv/bin/python -m tareas.estado_hoy                 # NQ y ES
    .venv/bin/python -m tareas.estado_hoy --indice ES
    .venv/bin/python -m tareas.estado_hoy --sesion 2026-10-26
"""
import argparse
import logging
import re
import sys
from datetime import date, datetime

from calculos import estado
from calculos import config as configuracion
from calculos.config import RAIZ, cargar
from fuentes import cme_pdf, velas_csv
from salida import historico, pagina

ENTRADA = RAIZ / "datos" / "entrada"
ARCHIVO = ENTRADA / "archivo"


INDICES = ["NQ", "ES"]


def boletines(indice="NQ"):
    """{fecha_datos: (ruta, preliminar, futuros)} de todos los PDF disponibles
    con la tabla de futuros del índice (NQ: sección 40; ES: sección 47)."""
    prefijo = cargar()["indices"][indice]["cme_pdf"]["archivos"][0]
    encontrados = {}
    rutas = list(ENTRADA.glob(f"{prefijo}*.pdf")) + list(ARCHIVO.glob(f"{prefijo}*.pdf"))
    for ruta in sorted(rutas, key=lambda p: p.stat().st_mtime):
        try:
            fecha, preliminar, futuros = cme_pdf.leer_futuros(ruta, indice=indice)
        except Exception as e:
            logging.warning("No puedo leer %s: %s", ruta, e)
            continue
        anterior = encontrados.get(fecha)
        # Si hay varios del mismo día, mejor el definitivo que el preliminar
        if anterior is None or (anterior[1] and not preliminar) or anterior[1] == preliminar:
            encontrados[fecha] = (ruta, preliminar, futuros)
    return encontrados


def csv_es_del_indice(nombre, indice, config):
    """El CSV de velas es de este índice si su nombre lo dice (NQ/MNQ, ES/MES).
    Un CSV sin índice en el nombre vale solo para el NQ (como siempre)."""
    patrones = {k: v.get("velas_patron") for k, v in config["indices"].items() if v.get("velas_patron")}
    nombre = nombre.upper()
    if patrones.get(indice) and re.search(patrones[indice], nombre):
        return True
    if any(re.search(p, nombre) for p in patrones.values()):
        return False                      # es de otro índice
    return indice == "NQ"


def velas_de(fecha_sesion, config, indice="NQ"):
    """(velas dentro de la ventana, nombre del archivo) o (None, None)."""
    c = config["estado"]
    inicio, fin = estado.ventana_sesion(fecha_sesion, c["ventana_velas"])
    for ruta in sorted(ENTRADA.glob("*.csv"), key=lambda p: -p.stat().st_mtime):
        if not csv_es_del_indice(ruta.name, indice, config) or not velas_csv.es_csv_de_velas(ruta):
            continue
        try:
            v = velas_csv.leer(ruta, c["zona_horaria"])
        except Exception as e:
            logging.warning("No puedo leer las velas de %s: %s", ruta, e)
            continue
        v = v[(v.index >= inicio) & (v.index < fin)]
        if len(v) >= 30:   # al menos media hora de velas de esa sesión
            return v, ruta.name
    return None, None


CARPETA_VELAS_DB = RAIZ / "datos" / "velas" / "databento"
CODIGO_MES = {"JAN": "F", "FEB": "G", "MAR": "H", "APR": "J", "MAY": "K", "JUN": "M",
              "JUL": "N", "AUG": "Q", "SEP": "U", "OCT": "V", "NOV": "X", "DEC": "Z"}


def simbolo_databento(indice, contrato_ref):
    """"DEC26" → "NQZ6" (símbolo de CME en Databento)."""
    if not contrato_ref or contrato_ref[:3] not in CODIGO_MES:
        return None
    return f"{indice}{CODIGO_MES[contrato_ref[:3]]}{contrato_ref[-1]}"


def velas_databento(sesion, indice, contrato_ref, config, cliente=None, hoy=None):
    """(velas en UTC con open/high/low/close, nombre) de Databento para la ventana de la sesión,
    o (None, motivo). Descarga solo si Databento ya tiene la sesión entera y el coste cabe en los
    topes; lo descargado queda en datos/velas/databento/ (no se vuelve a pagar)."""
    import databento as db
    simbolo = simbolo_databento(indice, contrato_ref)
    if not simbolo:
        return None, f"no sé el símbolo de Databento del contrato {contrato_ref}"
    inicio, fin = estado.ventana_sesion(sesion, config["estado"]["ventana_velas"])
    ruta = CARPETA_VELAS_DB / f"{indice}_{sesion}_{simbolo}_{config['estado']['ventana_velas']}.dbn.zst"
    nombre = f"Databento {simbolo}"
    if not ruta.exists():
        from tareas import escalera_databento as ed
        if cliente is None:
            from tareas.databento_velas import _cliente
            cliente, error = _cliente()
            if not cliente:
                return None, f"Databento no disponible: {error}"
        if ed.disponible_hasta(cliente) < fin.replace(tzinfo=None):
            return None, "Databento aún no tiene la sesión completa"
        q = dict(dataset="GLBX.MDP3", schema="ohlcv-1m", symbols=[simbolo], stype_in="raw_symbol",
                 start=inicio.strftime("%Y-%m-%dT%H:%M"), end=fin.strftime("%Y-%m-%dT%H:%M"))
        coste = cliente.metadata.get_cost(**q)
        hoy = hoy or date.today()
        ok, motivo = ed.puede_gastar(coste, hoy, ed._config())
        if not ok:
            return None, f"no descargo las velas: {motivo}"
        CARPETA_VELAS_DB.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_name(ruta.name + ".parcial")
        cliente.timeseries.get_range(**q, path=tmp)
        tmp.replace(ruta)
        ed.apuntar_gasto(hoy, coste)
        logging.info("Velas de Databento %s %s: %.4f $", simbolo, sesion, coste)
    v = db.DBNStore.from_file(ruta).to_df(price_type="float")
    if v.empty:
        return None, f"Databento no trae velas de {simbolo} en la sesión del {sesion}"
    # con el volumen (lo usa el perfil de volumen de los días anteriores: tareas/volumen_previo.py)
    v = v[[c for c in ("open", "high", "low", "close", "volume") if c in v.columns]].astype(float)
    v.index = v.index.tz_convert("UTC")
    v = v[(v.index >= inicio) & (v.index < fin)]
    return (v, nombre) if len(v) >= 30 else (None, f"pocas velas de {simbolo} en Databento")


def _calidad(estado_dia):
    """Puntuación de lo completo que es un estado (para no empeorarlo)."""
    if not estado_dia:
        return -1
    base = {"boletin": 4, "boletin_preliminar": 2, "velas": 0}.get(estado_dia.get("origen"), 0)
    return base + (1 if estado_dia.get("velas") else 0)


def actualizar(fecha_sesion, config, lista_boletines=None, indice="NQ"):
    """Rellena el estado de una sesión. Devuelve True si ha cambiado algo."""
    try:
        datos = historico.cargar(fecha_sesion, indice)
    except FileNotFoundError:
        return False
    sesion = date.fromisoformat(datos["fecha_sesion"])
    lista_boletines = boletines(indice) if lista_boletines is None else lista_boletines
    c = config["estado"]
    velas, nombre_velas = velas_de(sesion, config, indice)
    avisos = []
    de_databento = False
    if sesion not in lista_boletines and velas is None and c.get("databento", True):
        try:
            velas, nombre_velas = velas_databento(sesion, indice, datos.get("contrato_ref"), config)
            de_databento = velas is not None
            if velas is None:
                logging.info("Estado %s %s sin Databento: %s", indice, sesion, nombre_velas)
                nombre_velas = None
        except Exception:
            logging.exception("No se pudieron traer las velas de Databento (%s %s)", indice, sesion)
            velas, nombre_velas = None, None

    if sesion in lista_boletines:
        ruta, preliminar, futuros = lista_boletines[sesion]
        contrato = datos.get("contrato_ref") or next(iter(futuros), None)
        fut = futuros.get(contrato)
        if not fut or fut["maximo"] is None or fut["minimo"] is None:
            logging.warning("El boletín del %s no trae máximo/mínimo de %s", sesion, contrato)
            return False
        maximo, minimo = fut["maximo"], fut["minimo"]
        origen = "boletin_preliminar" if preliminar else "boletin"
        fuente = (f"Daily Bulletin del {sesion.strftime('%d/%m/%Y')}"
                  + (" (preliminar)" if preliminar else "") + f", futuro {contrato}")
        alcance = config["indices"][indice]["cme_pdf"]["futuros"].get("alcance", cme_pdf.ALCANCE_MAX_MIN)
        if fut["max_min_sin_operacion"]:
            avisos.append("El máximo o el mínimo del boletín es una oferta/demanda (B/A), no una operación.")
        if velas is not None:
            dif = max(abs(velas["high"].max() - maximo), abs(velas["low"].min() - minimo))
            if dif > 2 * c["umbral_rotura_puntos"] and c["ventana_velas"] == "globex":
                avisos.append(f"El máximo/mínimo de las velas ({nombre_velas}) difiere {dif:.0f} puntos del "
                              "boletín: revisa la zona horaria o el contrato del CSV.")
    elif velas is not None:
        maximo, minimo = float(velas["high"].max()), float(velas["low"].min())
        origen, contrato = "velas", datos.get("contrato_ref")
        fuente = (f"Velas de 1 minuto de {nombre_velas} (sin PDF del Daily Bulletin)" if de_databento
                  else f"Velas de 1 minuto ({nombre_velas}); aún no hay Daily Bulletin de ese día")
        alcance = ("Sesión completa de Globex según las velas" if c["ventana_velas"] == "globex"
                   else "Solo horario regular (9:30-16:00 de Nueva York) según las velas")
    else:
        return False

    nuevo = {
        "origen": origen, "maximo": maximo, "minimo": minimo, "contrato": contrato,
        "fuente": fuente, "alcance": alcance, "velas": nombre_velas,
        "ventana_velas": c["ventana_velas"] if nombre_velas else None,
        "avisos": avisos, "actualizado": datetime.now().isoformat(timespec="seconds"),
    }
    if _calidad(nuevo) <= _calidad(datos.get("estado_dia")):
        return False

    for n in datos["niveles"]:
        if velas is not None:
            n["estado"], n["hora_toque"] = estado.con_velas(
                n["desde"], n["hasta"], velas, c["umbral_rotura_puntos"], c["zona_horaria"])
        else:
            n["estado"], n["hora_toque"] = estado.por_rango(n["desde"], n["hasta"], maximo, minimo), None
    datos["estado_dia"] = nuevo
    historico.guardar(datos, conservar_notas=False)
    pagina.escribir(datos, es_la_ultima=_es_la_ultima(datos["fecha_sesion"], indice), indice=indice)
    logging.info("Estado de %s %s actualizado (%s)", indice, sesion, fuente)
    return True


def _patron(indice):
    return "????-??-??.json" if indice == "NQ" else f"{indice}_????-??-??.json"


def _es_la_ultima(fecha_sesion, indice="NQ"):
    archivos = sorted(historico.carpeta().glob(_patron(indice)))
    return bool(archivos) and archivos[-1].stem[-10:] == fecha_sesion


def main(argv=None):
    p = argparse.ArgumentParser(description="Rellena el 'Estado hoy' de las sesiones recientes.")
    p.add_argument("--sesion", help="AAAA-MM-DD; por defecto, las 5 últimas sesiones")
    p.add_argument("--indice", choices=INDICES, help="solo este índice (por defecto, NQ y ES)")
    args = p.parse_args(argv)
    if not logging.getLogger().handlers:
        logging.basicConfig(filename=RAIZ / "datos" / "registro.log", level=logging.INFO,
                            encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    for indice in ([args.indice] if args.indice else INDICES):
        archivos = sorted(historico.carpeta().glob(_patron(indice)))
        if not archivos and indice != "NQ":
            continue                      # aún no hay escaleras de ese índice
        config = configuracion.para_indice(cargar(), indice)
        lista = boletines(indice)
        sesiones = [args.sesion] if args.sesion else [p.stem[-10:] for p in archivos[-5:]]
        cambios = [s for s in sesiones if actualizar(s, config, lista, indice)]
        etiqueta = "" if indice == "NQ" else f" ({indice})"
        print(f"Estado actualizado{etiqueta}: "
              + (", ".join(cambios) if cambios else "nada nuevo (falta el boletín o las velas de ese día)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
