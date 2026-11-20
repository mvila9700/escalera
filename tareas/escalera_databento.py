"""
Escalera diaria automática con Databento (sin PDF), para el NQ y el ES.

    .venv/bin/python -m tareas.escalera_databento              # la sesión de hoy (NQ y ES)
    .venv/bin/python -m tareas.escalera_databento --indice NQ  # solo uno
    .venv/bin/python -m tareas.escalera_databento --coste      # solo dice lo que costaría (gratis)
    .venv/bin/python -m tareas.escalera_databento --no-esperar # no espera a que Databento tenga los datos

Lo lanza launchd (tareas/auto/com.marc.escalera-databento.plist) los días laborables a las 08:00
(hora de Madrid) y al iniciar sesión en el Mac (RunAtLoad), dentro de /usr/bin/caffeinate -i -s
para que el Mac no se duerma mientras espera (además, pmset lo despierta a las 07:55 de lunes a
viernes). Si la escalera de la sesión ya
existe (del PDF o de Databento), no hace nada (--forzar para rehacerla). Para la sesión de hoy
usa el boletín del día hábil anterior D:

1. Tramos de D que faltan en datos/oi (los mismos que tareas.databento_oi: open interest,
   liquidaciones, definiciones de la semana y futuro).
2. ESPERA (gratis) a que Databento tenga completo el último tramo. Medido el 3-nov-2026: CME
   publica el open interest entre las 01:15 y las 05:00 UTC (casi siempre ≈ 01:30-02:00) y el
   histórico de Databento llega con un retraso variable (de 15 minutos a 8 horas): con 8 horas,
   está entre las 09:15 y las 13:00 UTC. Desde las 08:00 de Madrid la tarea espera lo que haga
   falta, como mucho hasta `databento_diario.esperar_hasta` (15:15 de Nueva York,
   `esperar_zona`). Si llega el límite sin datos, avisa: «Databento no ha llegado: descarga el
   PDF». Cada intento queda en datos/registro.log con su hora y si el Mac estuvo dormido.
3. Calcula el coste exacto (gratis) y descarga SOLO si no pasa de los topes del día y del mes
   (config.yaml -> databento_diario). Lo gastado se apunta en datos/oi/gasto_databento.json.
4. Escalera con el mismo cálculo (fuentes.databento_oi + calculos.escalera.calcular), guardada
   en datos/historico y la página, como el vigilante con el PDF. Si para esa sesión ya hay una
   escalera hecha con el PDF, no la pisa: guarda la de Databento aparte
   (<sesión>_databento_esc2.txt) y apunta las diferencias en datos/registro.log.
5. Si hay PDF de ese boletín (datos/entrada o su archivo), compara las dos escaleras y apunta las
   diferencias en el registro. El vigilante del PDF sigue funcionando igual (respaldo).
"""
import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from calculos import config as configuracion
from calculos.calendario import anterior_habil, es_habil
from calculos.config import RAIZ
from fuentes.modelo import ErrorDatos

REGISTRO = RAIZ / "datos" / "registro.log"
GASTO = RAIZ / "datos" / "oi" / "gasto_databento.json"
ARCHIVO_PDF = RAIZ / "datos" / "entrada" / "archivo"


def _config():
    c = configuracion.cargar().get("databento_diario") or {}
    return {"tope_dia": float(c.get("tope_dia", 0.5)), "tope_mes": float(c.get("tope_mes", 8.0)),
            "indices": c.get("indices", ["NQ", "ES"]), "esperar_hasta": c.get("esperar_hasta", "15:15"),
            "esperar_zona": c.get("esperar_zona", "America/New_York"),
            "cada_minutos": int(c.get("cada_minutos", 5))}


NO_LLEGA = "Databento no ha llegado: descarga el PDF"


def _ahora_zona(zona):
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(zona))


def avisar(titulo, mensaje):
    """Aviso en pantalla (macOS)."""
    m = mensaje.replace('"', "'")
    print(f"{titulo}: {mensaje}")
    if sys.platform != "darwin":       # en GitHub (Linux) no hay avisos en pantalla
        return
    subprocess.run(["osascript", "-e", f'display notification "{m}" with title "{titulo}"'], check=False)


# --- Gasto ----------------------------------------------------------------------
def gasto(hoy=None):
    """{"AAAA-MM-DD": dólares} de lo descargado por esta tarea."""
    return json.loads(GASTO.read_text()) if GASTO.exists() else {}


def gastado(hoy, registro=None):
    """(gastado hoy, gastado este mes)."""
    g = gasto() if registro is None else registro
    dia = g.get(str(hoy), 0.0)
    mes = sum(v for k, v in g.items() if k[:7] == str(hoy)[:7])
    return dia, mes


def puede_gastar(coste, hoy, topes, registro=None):
    """(True, "") o (False, motivo) si con este coste se pasaría del tope del día o del mes."""
    dia, mes = gastado(hoy, registro)
    if dia + coste > topes["tope_dia"] + 1e-9:
        return False, (f"costaría {coste:.2f} $ y hoy ya van {dia:.2f} $: pasaría del tope diario "
                       f"({topes['tope_dia']:.2f} $)")
    if mes + coste > topes["tope_mes"] + 1e-9:
        return False, (f"costaría {coste:.2f} $ y este mes ya van {mes:.2f} $: pasaría del tope mensual "
                       f"({topes['tope_mes']:.2f} $)")
    return True, ""


def apuntar_gasto(hoy, coste):
    g = gasto()
    g[str(hoy)] = round(g.get(str(hoy), 0.0) + coste, 6)
    GASTO.parent.mkdir(parents=True, exist_ok=True)
    GASTO.write_text(json.dumps(g, indent=1, sort_keys=True))


# --- Datos de Databento -----------------------------------------------------------
def boletin_de(sesion):
    """Día de los datos (boletín) de la sesión: el hábil ESTRICTAMENTE anterior
    (calendario.anterior_habil devuelve el mismo día si es hábil)."""
    return anterior_habil(sesion - timedelta(days=1))


def disponible_hasta(cliente):
    fin = cliente.metadata.get_dataset_range(dataset="GLBX.MDP3")["schema"]["statistics"]["end"]
    return datetime.fromisoformat(fin[:19])


def esperar_datos(cliente, lista, cfg, esperar=True, dormir=time.sleep, ahora=None, monotonico=time.monotonic,
                  adelantar=None):
    """Espera (consultas gratis) a que Databento tenga el último tramo completo. True si ya está.

    El límite es `esperar_hasta` en la zona `esperar_zona` (15:15 de Nueva York). Cada intento
    queda en el registro con su hora y si el Mac ha estado dormido desde el anterior: el reloj
    monotónico de macOS no avanza durante el reposo, así que si el reloj de pared avanza mucho
    más que él, el Mac durmió esa diferencia.

    `adelantar(lista, hay)`, si se da, se llama en cada intento y puede quitar de `lista` (en el
    sitio) los tramos que ya ha conseguido antes de tiempo (ver adelantar_oi)."""
    if not lista:
        return True
    zona = cfg.get("esperar_zona", "America/New_York")
    ahora = ahora or (lambda: _ahora_zona(zona))
    hh, mm = int(cfg["esperar_hasta"][:2]), int(cfg["esperar_hasta"][3:5])
    limite = ahora().replace(hour=hh, minute=mm, second=0, microsecond=0)
    intento, previo = 0, None
    while True:
        intento += 1
        t, m = ahora(), monotonico()
        dormido = ""
        if previo is not None:
            pared = (t - previo[0]).total_seconds()
            despierto = m - previo[1]
            if pared - despierto > 120:
                dormido = (f"; el Mac estuvo DORMIDO ≈ {(pared - despierto) / 60:.0f} min desde el intento anterior "
                           f"({previo[0]:%H:%M} → {t:%H:%M} {zona})")
        previo = (t, m)
        hay = disponible_hasta(cliente)
        if adelantar is not None:
            lista[:] = adelantar(lista, hay)
            if not lista:
                return True
        necesario = max(datetime.fromisoformat(q[3]) for _, q in lista)
        madrid = t.astimezone(_ahora_zona("Europe/Madrid").tzinfo) if t.tzinfo else t
        logging.info("Intento %d a las %s Madrid (%s %s): Databento hasta %s UTC, hace falta hasta %s UTC%s",
                     intento, f"{madrid:%H:%M}", f"{t:%H:%M}", zona, hay, necesario,
                     dormido or "; el Mac no ha dormido" if intento > 1 else "")
        if hay >= necesario:
            return True
        if not esperar or t >= limite:
            logging.info("Sin datos de Databento a las %s %s (límite %s): %s", f"{t:%H:%M}", zona,
                         cfg["esperar_hasta"], NO_LLEGA)
            return False
        dormir(cfg["cada_minutos"] * 60)


# El tramo del OI va de las 00:30 a las 05:00 UTC, pero CME manda el open interest casi siempre
# entre las 02:18 y las 02:48 UTC, todo de golpe en un minuto (medido con las 6 sesiones del 12 al
# 20-nov-2026), y el histórico de Databento (sin licencia Live de CME) va 8 horas por detrás
# de la hora real (medido el 20-nov-2026: a las 14:08 UTC llegaba hasta las 06:08). Esperar a tener
# hasta las 05:00 retrasaba la escalera hasta las 13:00 UTC (14:00 de Madrid). Con esto, en cuanto
# el histórico cubre la publicación del OI (y media hora más, para que la ráfaga haya terminado),
# baja el tramo SOLO hasta donde llega y lo da por bueno: la escalera sale hacia las 10:30-11:00 UTC
# (11:30-12:00 de Madrid en invierno) en vez de a partir de las 13:00 UTC.
OI_TIPO = 9
OI_DESDE = timedelta(minutes=105)      # no lo intenta hasta tener el tramo hasta las 02:15 UTC
OI_CADA = timedelta(minutes=15)        # entre intento e intento (cada uno cuesta ≈ 0,005 $)
OI_MARGEN = timedelta(minutes=10)      # el último OI recibido, al menos 10 min antes del final
OI_MINIMO = 0.8                        # y al menos el 80 % de las opciones con OI del boletín anterior


def _opciones_con_oi(ruta, dia=None):
    import databento as db
    df = db.DBNStore.from_file(ruta).to_df().reset_index()
    df = df[df["stat_type"] == OI_TIPO]
    if dia is not None:
        df = df[df["ts_ref"].dt.date == dia]
    return df


def _oi_anterior(destino):
    """Opciones con OI en el último boletín ya bajado del mismo índice (None si no hay)."""
    indice = destino.name.split("_")[0]
    previos = sorted(p for p in destino.parent.glob(f"{indice}_*_oi.dbn.zst") if p != destino)
    if not previos:
        return None
    try:
        return _opciones_con_oi(previos[-1])["instrument_id"].nunique()
    except Exception:
        return None


def adelantar_oi(cliente, cfg, hoy, dia, intentado=None):
    """Devuelve adelantar(lista, hay) para esperar_datos (ver el comentario de arriba)."""
    from tareas import databento_oi as dbo
    intentado = {} if intentado is None else intentado

    def adelantar(lista, hay):
        resto = []
        for destino, q in lista:
            ini, fin = datetime.fromisoformat(q[2]), datetime.fromisoformat(q[3])
            ultimo = intentado.get(destino)
            if (not destino.name.endswith("_oi.dbn.zst") or fin <= hay or hay < ini + OI_DESDE
                    or (ultimo is not None and hay < ultimo + OI_CADA)):
                resto.append((destino, q))
                continue
            intentado[destino] = hay
            corte = hay.replace(second=0, microsecond=0)
            q2 = (q[0], q[1], q[2], corte.strftime("%Y-%m-%dT%H:%M"))
            tmp = destino.with_name(destino.name.replace(".dbn.zst", "_adelanto.dbn.zst"))
            try:
                coste = cliente.metadata.get_cost(**dbo._consulta(*q2))
                ok, motivo = puede_gastar(coste, hoy, cfg)
                if not ok:
                    logging.info("OI adelantado %s: no lo bajo, %s", destino.name, motivo)
                    resto.append((destino, q))
                    continue
                dbo.bajar(cliente, q2, tmp)
                apuntar_gasto(hoy, coste)
                oi = _opciones_con_oi(tmp, dia)
                n, antes = oi["instrument_id"].nunique(), _oi_anterior(destino)
                ultimo_oi = oi["ts_recv"].max().to_pydatetime().replace(tzinfo=None) if n else None
                if n and ultimo_oi + OI_MARGEN <= corte and (antes is None or n >= OI_MINIMO * antes):
                    tmp.replace(destino)
                    logging.info("OI adelantado %s: %d opciones con OI (último a las %s UTC; el boletín "
                                 "anterior, %s), tramo hasta %s UTC, %.4f $", destino.name, n,
                                 f"{ultimo_oi:%H:%M}", antes, f"{corte:%H:%M}", coste)
                    print(f"Open interest de {dia} ya disponible ({destino.name}, {n} opciones).", flush=True)
                    continue
                logging.info("OI adelantado %s: aún no (hasta %s UTC: %d opciones con OI%s; el boletín "
                             "anterior, %s)", destino.name, f"{corte:%H:%M}", n,
                             f", último a las {ultimo_oi:%H:%M}" if n else "", antes)
            except Exception as e:
                logging.info("OI adelantado %s: error %s", destino.name, e)
            finally:
                tmp.unlink(missing_ok=True)
            resto.append((destino, q))
        return resto
    return adelantar


def descargar(cliente, indice, dia, cfg, hoy, esperar=True, solo_coste=False):
    """Descarga lo que falte del boletín `dia`. Devuelve (coste, mensaje de error o None)."""
    from tareas import databento_oi as dbo
    lista = dbo.pendientes([dia], indice)
    if not lista:
        return 0.0, None
    if not solo_coste and not esperar_datos(cliente, lista, cfg, esperar,
                                            adelantar=adelantar_oi(cliente, cfg, hoy, dia)):
        return 0.0, NO_LLEGA
    cache = dbo.CARPETA / ("costes.json" if indice == "ES" else f"costes_{indice}.json")
    total, detalle = dbo.costes(cliente, lista, cache=cache)
    if solo_coste:
        return total, None
    ok, motivo = puede_gastar(total, hoy, cfg)
    if not ok:
        return total, f"No descargo el {indice}: {motivo}."
    dbo.CARPETA.mkdir(parents=True, exist_ok=True)
    for destino, q, k in detalle:
        dbo.bajar(cliente, q, destino)
        apuntar_gasto(hoy, k)          # tramo a tramo: si falla a mitad, lo bajado también cuenta
    logging.info("Databento %s %s: %d tramos, %.4f $", indice, dia, len(detalle), total)
    return total, None


# --- Escalera ---------------------------------------------------------------------
def _pdfs_del_boletin(indice, dia):
    """PDF del boletín `dia` (archivados o en datos/entrada), si están todas las secciones."""
    from fuentes import cme_pdf
    cfg = configuracion.cargar()["indices"][indice]["cme_pdf"]
    rutas = []
    for prefijo, seccion in zip(cfg["archivos"], cfg["secciones"]):
        candidatos = [ARCHIVO_PDF / f"Section{seccion}_{dia}.pdf", ARCHIVO_PDF / f"Section{seccion}_{dia}_preliminar.pdf"]
        encontrado = next((r for r in candidatos if r.exists()), None)
        if encontrado is None:
            for r in sorted((RAIZ / "datos" / "entrada").glob(f"{prefijo}*.pdf")):
                try:
                    if cme_pdf.leer_futuros(r, indice=indice)[0] == dia:
                        encontrado = r
                except Exception:
                    continue
        if encontrado is None:
            return None
        rutas.append(encontrado)
    return rutas


def comparar_con_pdf(indice, dia, texto_databento):
    """Texto de la comparación con la escalera del PDF del mismo boletín, o None si no hay PDF."""
    from calculos.escalera import calcular
    from fuentes import cme_pdf
    from salida import historico
    from tareas.reconstruir_escalera import comparar, texto_comparacion
    rutas = _pdfs_del_boletin(indice, dia)
    if not rutas:
        return None
    config = configuracion.para_indice(configuracion.cargar(), indice)
    df, info = cme_pdf.leer(rutas, config, indice)
    texto_pdf = historico.desde_resultado(calcular(df, info, config))["tradingview_esc2"]
    c = comparar(texto_pdf, texto_databento)
    return ("PDF " + ", ".join(Path(r).name for r in rutas) + (" (preliminar)" if info.get("preliminar") else "")
            + "\n" + texto_comparacion(c).replace("real", "PDF").replace("reconstruida", "Databento"))


def escalera(indice, dia, sesion):
    """Escalera de Databento de la sesión. Devuelve (ruta de la página o None, mensaje)."""
    from calculos.escalera import calcular
    from fuentes import databento_oi
    from salida import historico, pagina
    config = configuracion.para_indice(configuracion.cargar(), indice)
    df, info = databento_oi.leer(dia, indice=indice)
    info["fuente"] = "Databento GLBX.MDP3 (open interest y liquidaciones de CME, sin PDF)"
    res = calcular(df, info, config)
    if res["fecha_sesion"] != sesion:
        raise ErrorDatos(f"Los datos de Databento dan la sesión del {res['fecha_sesion']}, no la del {sesion}.")
    datos = historico.desde_resultado(res)
    base = historico.nombre(sesion, indice)
    (historico.carpeta() / f"{base}_databento_esc2.txt").write_text(datos["tradingview_esc2"] + "\n", encoding="utf-8")
    previa = historico.carpeta() / f"{base}.json"
    hecha_con_pdf = previa.exists() and str(json.loads(previa.read_text(encoding="utf-8")).get("fuente", "")
                                             ).startswith("CME Daily Bulletin")
    try:
        comp = comparar_con_pdf(indice, dia, datos["tradingview_esc2"])
        if comp:
            logging.info("Comparación %s %s (PDF frente a Databento):\n%s", indice, sesion, comp)
    except Exception:
        logging.exception("No se pudo comparar con el PDF")
    if hecha_con_pdf:
        logging.info("%s %s: ya hay escalera del PDF; la de Databento queda en %s_databento_esc2.txt",
                     indice, sesion, base)
        return None, "ya había escalera del PDF (la de Databento queda aparte)"
    from tareas import volumen_previo              # VAL/VAH/POC no mitigados (solo referencia; nunca falla)
    volumen_previo.anadir(datos, config)
    ruta_json = historico.guardar(datos)
    ruta = pagina.escribir(datos, indice=indice)
    logging.info("Escalera %s (Databento) generada: %s / %s", indice, ruta_json, ruta)
    return ruta, "lista"


def ya_existe(sesion, indice):
    """¿Hay ya escalera de esa sesión en el histórico (del PDF o de Databento)?"""
    from salida import historico
    return (historico.carpeta() / f"{historico.nombre(sesion, indice)}.json").exists()


def main(argv=None, hoy=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--indice", choices=["NQ", "ES"], help="solo este índice (por defecto, los de config.yaml)")
    p.add_argument("--coste", action="store_true", help="solo calcula el coste (gratis), no descarga")
    p.add_argument("--no-esperar", action="store_true")
    p.add_argument("--sesion", help="AAAA-MM-DD (por defecto, hoy)")
    p.add_argument("--no-abrir", action="store_true")
    p.add_argument("--forzar", action="store_true", help="aunque ya exista la escalera de la sesión")
    a = p.parse_args(argv)
    logging.basicConfig(filename=REGISTRO, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = _config()
    hoy = hoy or date.today()
    sesion = date.fromisoformat(a.sesion) if a.sesion else hoy
    if not es_habil(sesion):
        print(f"{sesion} no es día hábil: no hay escalera.")
        return 0
    dia = boletin_de(sesion)
    indices = [a.indice] if a.indice else cfg["indices"]
    if not indices:
        print("databento_diario.indices está vacío: la escalera diaria la hace GitHub.")
        return 0
    if not (a.forzar or a.coste):
        # Al iniciar sesión (RunAtLoad) y a las 08:00: si la escalera ya está, no hace nada
        hechas = [i for i in indices if ya_existe(sesion, i)]
        if hechas:
            logging.info("Escalera Databento %s: ya existe la de %s, no hago nada con ellos", sesion, ", ".join(hechas))
        indices = [i for i in indices if i not in hechas]
        if not indices:
            print(f"Ya existen las escaleras de la sesión del {sesion}: no hago nada.")
            return 0
    from tareas.databento_velas import _cliente
    cliente, error = _cliente()
    if not cliente:
        avisar("Escalera Databento", f"Databento no disponible: {error}")
        return 1
    fallos, hechos = [], []
    for indice in indices:
        try:
            coste, error = descargar(cliente, indice, dia, cfg, hoy, esperar=not a.no_esperar, solo_coste=a.coste)
            if a.coste:
                print(f"{indice} boletín {dia}: {coste:.4f} $")
                continue
            if error:
                raise ErrorDatos(error)
            ruta, estado = escalera(indice, dia, sesion)
            hechos.append((indice, ruta, estado, coste))
        except Exception as e:
            logging.exception("Escalera Databento %s %s", indice, sesion)
            fallos.append(f"{indice}: {e}")
    if a.coste:
        if fallos:
            print("\n".join(fallos), file=sys.stderr)
        return 1 if fallos else 0
    dia_g, mes_g = gastado(hoy)
    for indice, ruta, estado, coste in hechos:
        print(f"{indice}: {estado} ({coste:.3f} $){' · ' + str(ruta) if ruta else ''}")
        if ruta and not a.no_abrir:
            subprocess.run(["open", str(ruta)], check=False)
    if fallos and all(f.endswith(NO_LLEGA) for f in fallos):
        avisar("Escalera Databento", NO_LLEGA)            # límite sin datos: el respaldo es el PDF
        print("\n".join(fallos), file=sys.stderr)
        return 1
    if fallos:
        avisar("Escalera Databento: ERROR", " · ".join(fallos)[:200])
        print("\n".join(fallos), file=sys.stderr)
        return 1
    avisar("Escalera Databento", f"{' y '.join(i for i, *_ in hechos)} listas para el {sesion} · "
                                 f"hoy {dia_g:.2f} $ · mes {mes_g:.2f} $")
    return 0


if __name__ == "__main__":
    sys.exit(main())
