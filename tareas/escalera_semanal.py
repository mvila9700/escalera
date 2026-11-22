"""
Escalera de la SEMANA (NQ y ES), para el análisis de los domingos.

    python -m tareas.escalera_semanal                 # la semana que empieza el próximo lunes
    python -m tareas.escalera_semanal --lunes 2026-11-23
    python -m tareas.escalera_semanal --indice NQ

La lanza GitHub los domingos por la mañana (.github/workflows/semanal.yml). Deja:
  - SEMANA_NQ.txt / SEMANA_ES.txt          (texto para leer, también en docs/semana_nq.txt y docs/semana_es.txt)
  - datos/historico/SEMANA_<lunes>_<índice>.json   (los mismos datos, para guardarlos)

QUÉ DATOS USA (léelo):
- El domingo a mediodía aún NO está el open interest del viernes: CME lo publica el domingo hacia
  las 17:30 UTC y el histórico de Databento llega con horas de retraso. Lo último que hay es el
  open interest del cierre del JUEVES (el boletín con el que se hizo la escalera del viernes).
- De ese boletín se quitan las opciones que ya vencieron (las del viernes o antes): quedan solo las
  que siguen vivas la semana que viene.
- El precio de referencia es la liquidación del VIERNES del futuro (lo que cerró la semana), y la
  gamma se calcula en la apertura del lunes. La IV de cada opción es la de su liquidación del jueves.
- Dos vistas: «semana» (solo los vencimientos de lunes a viernes de esa semana: los que más
  empujan hasta que vencen) y «completa» (hasta el trimestral, como la escalera diaria).
- Rangos 68/95 % SEMANALES: precio × IV ATM del último vencimiento de la semana × raíz(días hábiles/252).
- La escalera diaria de cada mañana (HOY_*.txt) sigue siendo la buena para operar: trae el OI del día anterior.
"""
import argparse
import json
import logging
import shutil
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from calculos import config as configuracion
from calculos import gex, movimiento
from calculos.calendario import anterior_habil, es_habil, habiles_entre, siguiente_habil
from calculos.config import RAIZ
from calculos.escalera import vencimiento_limite
from calculos.griegas import anadir_gamma, anadir_iv
from fuentes.modelo import ErrorDatos

REGISTRO = RAIZ / "datos" / "registro.log"
DOCS = RAIZ / "docs"
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_ES = ["", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def f(x, dec=0):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fecha_txt(d):
    return f"{DIAS_ES[d.weekday()]} {d.day} {MESES_ES[d.month]}"


# --- Fechas -----------------------------------------------------------------------
def fechas_semana(lunes):
    """(primera sesión, último día hábil de la semana, viernes previo, boletín con OI)."""
    primera = lunes if es_habil(lunes) else siguiente_habil(lunes)
    viernes = lunes + timedelta(days=4)
    ultima = anterior_habil(viernes)
    previo = anterior_habil(primera - timedelta(days=1))      # última sesión de la semana anterior
    boletin = anterior_habil(previo - timedelta(days=1))       # su boletín (OI del día antes)
    return primera, ultima, previo, boletin


def proximo_lunes(hoy):
    return hoy + timedelta(days=(7 - hoy.weekday()) % 7 or 7)


# --- Datos ------------------------------------------------------------------------
def asegurar_datos(cliente, indice, boletin, previo, hoy):
    """Baja (si faltan) el boletín con OI y la liquidación del futuro del viernes. Coste en $."""
    from tareas import databento_oi as dbo
    from tareas.escalera_databento import _config, apuntar_gasto, descargar, puede_gastar
    cfg = _config()
    total = 0.0
    if dbo.pendientes([boletin], indice):
        coste, error = descargar(cliente, indice, boletin, cfg, hoy, esperar=False)
        if error:
            raise ErrorDatos(error)
        total += coste
    destino = dbo.archivo(previo, "futuros", indice)
    if not destino.exists():
        _, futuros = dbo.POR_INDICE[indice]
        q = ("statistics", futuros, f"{previo}T19:00", f"{previo + timedelta(days=1)}T00:00")
        coste = cliente.metadata.get_cost(**dbo._consulta(*q))
        ok, motivo = puede_gastar(coste, hoy, cfg)
        if not ok:
            raise ErrorDatos(f"No bajo la liquidación del futuro del {previo}: {motivo}")
        dbo.CARPETA.mkdir(parents=True, exist_ok=True)
        dbo.bajar(cliente, q, destino)
        apuntar_gasto(hoy, coste)
        total += coste
    return total


def liquidacion_futuros(indice, dia):
    """{símbolo: liquidación} de los futuros del índice el día `dia`."""
    from fuentes.databento_oi import LIQUIDACION, _del_dia, _df
    from tareas import databento_oi as dbo
    fut = _df(dbo.archivo(dia, "futuros", indice))
    fut = fut[fut["symbol"].str.fullmatch(indice + r"[FGHJKMNQUVXZ]\d")]
    liq = _del_dia(fut, LIQUIDACION, dia)
    if liq.empty:
        raise ErrorDatos(f"No hay liquidación del futuro {indice} del {dia} en Databento.")
    from fuentes.databento_oi import _nombre_futuro
    return {_nombre_futuro(k, dia): float(v) for k, v in zip(liq["symbol"], liq["price"])}


# --- Cálculo ----------------------------------------------------------------------
def preparar(df, boletin, primera, liq_viernes, jueves, config):
    """Opciones vivas la semana, con IV (del boletín), futuro movido al cierre del viernes y gamma
    en la apertura de la primera sesión. Devuelve (df, precio_ref, contrato_ref)."""
    df = df[df["vencimiento"] >= primera].copy()
    limite = vencimiento_limite(primera, config, df)
    df = df[df["vencimiento"] <= limite].copy()
    if df.empty:
        raise ErrorDatos("No hay opciones vivas para la semana.")
    df = anadir_iv(df, boletin, config)
    # Futuro de cada opción: la tabla trae su liquidación del jueves; info["futuros"] dice de qué
    # contrato es cada precio (DEC26: precio) y liq_viernes trae el cierre del viernes de cada uno.
    front_jueves = float(df.loc[df["vencimiento"].idxmin(), "futuro"])
    contrato = next((k for k, v in jueves.items() if v == front_jueves), None)
    if contrato not in liq_viernes:
        raise ErrorDatos(f"Falta la liquidación del viernes del futuro {contrato}.")
    ref = float(liq_viernes[contrato])
    de_precio = {v: liq_viernes.get(k) for k, v in jueves.items()}
    nuevo = df["futuro"].map(de_precio).astype(float)
    # (si un contrato no tiene cierre del viernes, se mueve el mismo % que el de referencia)
    df["futuro"] = nuevo.fillna(df["futuro"] * ref / front_jueves)
    df["strike_ref"] = df["strike"] + (ref - df["futuro"])
    df = anadir_gamma(df, primera, config)
    df = gex.anadir_gex(df, config)
    return df, ref, contrato


def _muro(tabla, lado, ref):
    if lado == "call":
        t = tabla[(tabla["strike"] > ref) & (tabla["gex_calls"] > 0)]
        return None if t.empty else t.loc[t["gex_calls"].idxmax()]
    t = tabla[(tabla["strike"] < ref) & (tabla["gex_puts"] < 0)]
    return None if t.empty else t.loc[t["gex_puts"].idxmin()]


def vista(df, ref, lo, hi, iv, t_semana, config, n=8):
    """Régimen, frontera, muros, OI máximo y niveles principales de un conjunto de opciones."""
    perfil = gex.perfil_gex(df, ref, config)
    frontera = gex.gamma_flip(perfil, ref)
    gex_ref = float(np.interp(ref, perfil["precio"], perfil["gex_total"]))
    tabla = gex.gex_por_strike(df)
    tabla = tabla[(tabla["strike"] >= lo) & (tabla["strike"] <= hi)].copy()
    toque = lambda k: float(movimiento.prob_toque(ref, k, iv, t_semana))

    def fila(r, tipo):
        if r is None:
            return None
        k = float(r["strike"])
        return {"strike": k, "tipo": tipo, "oi_calls": float(r["oi_calls"]), "oi_puts": float(r["oi_puts"]),
                "gex_neto": float(r["gex_neto"]), "distancia": k - ref, "prob_toque_semana": toque(k)}

    muro_c, muro_p = _muro(tabla, "call", ref), _muro(tabla, "put", ref)
    oi_max = tabla.loc[tabla["oi_total"].idxmax()] if not tabla.empty else None
    tabla["peso"] = tabla["gex_calls"].abs() + tabla["gex_puts"].abs()
    # Niveles distintos: el de más peso de cada zona (separados al menos `separacion` puntos)
    separacion = max(float(config.get("distancia_agrupar_puntos", 0)), 0.15 * (hi - lo) / 4)
    principales, elegidos = [], []
    for _, r in tabla.sort_values("peso", ascending=False).iterrows():
        if len(principales) >= n or r["peso"] <= 0:
            break
        if any(abs(r["strike"] - k) < separacion for k in elegidos):
            continue
        elegidos.append(r["strike"])
        tipo = "calls" if r["gex_calls"] >= abs(r["gex_puts"]) else "puts"
        principales.append(fila(r, tipo))
    return {
        "vencimientos": [str(v) for v in sorted(set(df["vencimiento"]))],
        "regimen": "positiva" if gex_ref >= 0 else "negativa",
        "gex_en_precio": gex_ref,
        "frontera": frontera,
        "muro_calls": fila(muro_c, "calls"),
        "muro_puts": fila(muro_p, "puts"),
        "oi_maximo": fila(oi_max, "calls" if oi_max is not None and oi_max["oi_calls"] >= oi_max["oi_puts"] else "puts"),
        "principales": sorted(principales, key=lambda x: -x["strike"]),
    }


def calcular_semana(df, info, liq_viernes, lunes, config):
    primera, ultima, previo, boletin = fechas_semana(lunes)
    df, ref, contrato = preparar(df, boletin, primera, liq_viernes, info.get("futuros", {}), config)
    semana = df[df["vencimiento"] <= ultima]
    if semana.empty:
        raise ErrorDatos(f"No hay vencimientos entre el {primera} y el {ultima}.")
    dias = habiles_entre(primera, ultima) + 1
    t_semana = dias / 252
    venc_iv = max(semana["vencimiento"])
    iv = movimiento.iv_atm(semana[semana["vencimiento"] == venc_iv], ref)
    sigma = ref * iv * np.sqrt(t_semana)
    lo95, hi95 = ref - 2 * sigma, ref + 2 * sigma
    return {
        "indice": info.get("indice"),
        "lunes": str(lunes), "primera_sesion": str(primera), "ultima_sesion": str(ultima),
        "dias_habiles": dias,
        "boletin_oi": str(boletin), "viernes_precio": str(previo),
        "contrato_ref": contrato, "precio_ref": ref,
        "iv_atm": iv, "vencimiento_iv": str(venc_iv), "sigma_semana": sigma,
        "rango_68": [ref - sigma, ref + sigma], "rango_95": [lo95, hi95],
        "semana": vista(semana, ref, lo95, hi95, iv, t_semana, config),
        "completa": vista(df, ref, lo95, hi95, iv, t_semana, config),
        "oi_semana_pct": float(semana["open_interest"].sum() / df["open_interest"].sum()),
        "generado": datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d %H:%M"),
    }


# --- Texto ------------------------------------------------------------------------
def _linea(n, ref):
    if n is None:
        return "no hay ninguno dentro del rango del 95 %"
    lado = "por encima" if n["distancia"] > 0 else "por debajo"
    return (f"{f(n['strike'])}  ({f(abs(n['distancia']))} puntos {lado}; OI calls {f(n['oi_calls'])} / "
            f"puts {f(n['oi_puts'])}; prob. de tocarlo en la semana ≈ {n['prob_toque_semana']:.0%})")


def texto(r):
    s, c = r["semana"], r["completa"]
    ref = r["precio_ref"]
    reg = lambda v: "POSITIVA (los muros frenan y el precio tiende a volver)" if v["regimen"] == "positiva" \
        else "NEGATIVA (más rango; los muros son objetivo más que freno)"
    fr = lambda v: (f"{f(v['frontera'])} ({'por encima' if v['frontera'] > ref else 'por debajo'} del precio, "
                    f"a {f(abs(v['frontera'] - ref))} puntos)") if v["frontera"] else "no encontrada en ±10 %"
    lunes = date.fromisoformat(r["lunes"])
    ultima = date.fromisoformat(r["ultima_sesion"])
    out = [
        f"ESCALERA DE LA SEMANA · {r['indice']} · del {fecha_txt(lunes)} al {fecha_txt(ultima)} "
        f"({r['dias_habiles']} sesiones)",
        f"Generada: {r['generado']} (Madrid)",
        "",
        f"Precio de referencia: {f(ref, 2)} = cierre (liquidación) del {r['indice']} {r['contrato_ref']} del "
        f"{fecha_txt(date.fromisoformat(r['viernes_precio']))}.",
        f"Open interest: el del cierre del {fecha_txt(date.fromisoformat(r['boletin_oi']))}, solo opciones que "
        f"siguen vivas (lo del viernes ya venció). El OI del viernes aún no está publicado el domingo.",
        f"Los vencimientos de esta semana son el {r['oi_semana_pct']:.0%} del open interest vivo hasta el trimestral.",
        "",
        "RANGO ESPERADO DE LA SEMANA",
        f"  IV ATM {f(r['iv_atm'] * 100, 1)} % (vencimiento {r['vencimiento_iv']}) → 1 desviación = {f(r['sigma_semana'])} puntos",
        f"  68 %: {f(r['rango_68'][0])} – {f(r['rango_68'][1])}",
        f"  95 %: {f(r['rango_95'][0])} – {f(r['rango_95'][1])}",
        "",
        "RÉGIMEN Y FRONTERA",
        f"  Con los vencimientos de la semana: gamma {reg(s)}",
        f"    Frontera semanal: {fr(s)}",
        f"  Con todo hasta el trimestral:      gamma {reg(c)}",
        f"    Frontera completa: {fr(c)}",
        "",
        "MUROS Y OI MÁXIMO (solo vencimientos de la semana)",
        f"  Muro de calls: {_linea(s['muro_calls'], ref)}",
        f"  Muro de puts:  {_linea(s['muro_puts'], ref)}",
        f"  OI máximo:     {_linea(s['oi_maximo'], ref)}",
        "",
        "MUROS Y OI MÁXIMO (todo hasta el trimestral)",
        f"  Muro de calls: {_linea(c['muro_calls'], ref)}",
        f"  Muro de puts:  {_linea(c['muro_puts'], ref)}",
        f"  OI máximo:     {_linea(c['oi_maximo'], ref)}",
        "",
        "NIVELES PRINCIPALES DE LA SEMANA (más gamma de los vencimientos de la semana, dentro del 95 %)",
    ]
    for n in s["principales"]:
        out.append(f"  {f(n['strike']):>8}  {n['tipo']:<5}  {f(n['distancia']):>6} pts  OI c/p {f(n['oi_calls'])}/"
                   f"{f(n['oi_puts'])}  toque ≈ {n['prob_toque_semana']:.0%}")
    out += ["",
            "Cómo leerla: es la foto de partida de la semana. Cada mañana la escalera diaria (HOY_*.txt) la "
            "actualiza con el OI nuevo. Por encima de la frontera los muros aguantan algo más; por debajo "
            "suelen ser objetivo. No dice la dirección: dice dónde están las zonas que importan."]
    return "\n".join(out) + "\n"


# --- Principal --------------------------------------------------------------------
def generar(indice, lunes, cliente, hoy):
    from fuentes import databento_oi
    from salida import historico
    config = configuracion.para_indice(configuracion.cargar(), indice)
    # En una semana el precio recorre más que en un día: la frontera se busca en ±10 % (no ±5 %)
    config["gamma_flip"] = dict(config["gamma_flip"], rango_pct=2 * config["gamma_flip"]["rango_pct"],
                                pasos=2 * config["gamma_flip"]["pasos"] - 1)
    primera, ultima, previo, boletin = fechas_semana(lunes)
    coste = asegurar_datos(cliente, indice, boletin, previo, hoy) if cliente else 0.0
    df, info = databento_oi.leer(boletin, indice=indice)
    liq = liquidacion_futuros(indice, previo)
    r = calcular_semana(df, info, liq, lunes, config)
    txt = texto(r)
    (RAIZ / f"SEMANA_{indice}.txt").write_text(txt, encoding="utf-8")
    DOCS.mkdir(exist_ok=True)
    shutil.copyfile(RAIZ / f"SEMANA_{indice}.txt", DOCS / f"semana_{indice.lower()}.txt")
    (historico.carpeta() / f"SEMANA_{lunes}_{indice}.json").write_text(
        json.dumps(r, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    logging.info("Escalera semanal %s %s: lista (OI del %s, precio del %s, %.4f $)", indice, lunes, boletin, previo, coste)
    return txt, coste


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--indice", choices=["NQ", "ES"])
    p.add_argument("--lunes", help="AAAA-MM-DD (por defecto, el próximo lunes)")
    a = p.parse_args(argv)
    logging.basicConfig(filename=REGISTRO, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    hoy = datetime.now(ZoneInfo("Europe/Madrid")).date()
    lunes = date.fromisoformat(a.lunes) if a.lunes else proximo_lunes(hoy)
    from tareas.databento_velas import _cliente
    cliente, error = _cliente()
    if not cliente:
        print(f"Databento no disponible ({error}): uso solo lo que ya esté descargado.")
    fallos = []
    for indice in ([a.indice] if a.indice else ["NQ", "ES"]):
        try:
            txt, coste = generar(indice, lunes, cliente, hoy)
            print(txt)
            print(f"({indice}: {coste:.3f} $)\n")
        except Exception as e:
            logging.exception("Escalera semanal %s %s", indice, lunes)
            fallos.append(f"{indice}: {e}")
    if fallos:
        print("\n".join(fallos), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
