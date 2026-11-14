"""
Validación: ¿los niveles del NQ con ★ (confluencia con QQQ/NDX) se respetan más que los sin ★?

    .venv/bin/python -m tareas.validar_confluencia            # ★ frente a sin ★ (necesita mucho histórico)
    .venv/bin/python -m tareas.validar_confluencia --azar     # ¿salen más ★ que por azar?
    .venv/bin/python -m tareas.validar_confluencia --respeto  # PRUEBA PRINCIPAL: (a) NQ · (b) QQQ/NDX · (c) control

Necesita las escaleras del NQ reconstruidas (datos/historico_reconstruido/NQ_<S>.json), las
opciones de QQQ/NDX de esas sesiones (datos/opra, de tareas.databento_opra) y las velas de 1
minuto del MNQ (datos/velas). No descarga nada.

Por sesión: niveles externos (calculos.confluencia, en precio NQ), marcas ★ con la distancia por
defecto (mitad del ancho de zona + error del ratio) y, con las velas de 9:30 a 16:00 NY, el
estado de cada nivel con la misma regla que el «Estado hoy» (calculos.estado.con_velas): roto si,
después del toque, algún cierre de 1 minuto queda al otro lado a ≥ umbral_rotura_puntos.
Se cuentan solo los tocados con dirección (respetado o roto). ★ frente a sin ★, en total y solo
principales; ajuste nov-jul y comprobación ago-oct por separado (prueba de Fisher).
Informe: documentacion/validacion_confluencia.md.
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from calculos import config as configuracion
from calculos import confluencia as cf
from calculos import estado

CARPETA = Path("datos/historico_reconstruido")
INFORME = Path("documentacion/validacion_confluencia.md")
AJUSTE = ("2025-11-01", "2026-07-31")
COMPROBACION = ("2026-08-01", "2026-10-31")


def filas_de_sesion(d, tabla_ext, velas, config):
    """Una fila por nivel tocado (respetado o roto) con su marca ★."""
    F = d["precio_ref"]
    fecha_datos = date.fromisoformat(d["fecha_datos"])
    tabla_nq = cf.a_nq(tabla_ext, F, fecha_datos, config["tipo_interes"])
    if tabla_nq.empty:
        return []
    c = config.get("confluencia", {})
    externos = cf.principales(cf.niveles_externos(tabla_nq, {"fecha_datos": fecha_datos}, config, F),
                              c.get("tipos_externos", cf.TIPOS_PRINCIPALES))
    distancia = c.get("distancia_puntos") or cf.distancia_por_defecto(d["jerarquia"]["ancho_zona"], F,
                                                                       c.get("error_ratio_pct", 0.03))
    niveles = [dict(n) for n in d["niveles"] if n["tipos"] != ["Frontera"]]
    cf.marcar(niveles, externos, distancia)
    umbral = config["estado"]["umbral_rotura_puntos"]
    out = []
    for n in niveles:
        est, _ = estado.con_velas(n["desde"], n["hasta"], velas, umbral)
        if est in ("Tocado y respetado", "Tocado y roto"):
            out.append({"fecha_sesion": d["fecha_sesion"], "nivel": n["texto"], "categoria": n.get("categoria"),
                        "estrella": bool(n.get("confluencia")), "respetado": est == "Tocado y respetado",
                        "fuentes": "+".join((n.get("confluencia") or {}).get("fuentes", []))})
    return out


def comparar(t):
    a, b = t[t["estrella"]], t[~t["estrella"]]
    if a.empty or b.empty:
        return {"n_si": len(a), "n_no": len(b), "si": np.nan, "no": np.nan, "p": np.nan}
    tabla = [[a["respetado"].sum(), (~a["respetado"]).sum()], [b["respetado"].sum(), (~b["respetado"]).sum()]]
    return {"n_si": len(a), "n_no": len(b), "si": a["respetado"].mean() * 100, "no": b["respetado"].mean() * 100,
            "p": stats.fisher_exact(tabla)[1]}


def _f(x, d=0):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}".replace(".", ",")


def contra_azar(sorteos=5000, semilla=1):
    """¿Salen más ★ que por azar? Con cada sesión que tenga datos de QQQ/NDX: ★ reales (niveles del
    NQ con un muro o imán de QQQ/NDX a ≤ distancia) frente a dos controles con los MISMOS niveles
    externos: (a) al azar a ±2σ del precio; (b) todo el conjunto desplazado 30-300 puntos (conserva
    sus separaciones). p = parte de los sorteos con tantas o más ★ que las reales."""
    from fuentes import opra
    from fuentes.modelo import ErrorDatos
    config = configuracion.para_indice(configuracion.cargar(), "NQ")
    c = config.get("confluencia", {})
    rng = np.random.default_rng(semilla)
    filas = []
    import re
    sesiones = sorted({re.search(r"\d{4}-\d{2}-\d{2}", r.name).group()
                       for r in Path("datos/opra").glob("QQQ_????-??-??_open_interest.dbn.zst")})
    for s in sesiones:
        ruta = next((r for r in (Path("datos/historico") / f"{s}.json", CARPETA / f"NQ_{s}.json") if r.exists()), None)
        if ruta is None:
            continue
        d = json.loads(ruta.read_text(encoding="utf-8"))
        try:
            tabla_ext = opra.leer(s)
        except ErrorDatos:
            continue
        F, sigma = d["precio_ref"], d["movimiento"]["desviacion"]
        fecha_datos = date.fromisoformat(d["fecha_datos"])
        tabla_nq = cf.a_nq(tabla_ext, F, fecha_datos, config["tipo_interes"])
        externos = cf.principales(cf.niveles_externos(tabla_nq, {"fecha_datos": fecha_datos}, config, F),
                                  c.get("tipos_externos", cf.TIPOS_PRINCIPALES))
        dist = c.get("distancia_puntos") or 12
        centros_nq = np.array([(n["desde"] + n["hasta"]) / 2 for n in d["niveles"] if n["tipos"] != ["Frontera"]])
        ext = np.array([e["centro"] for e in externos])

        def estrellas(x):
            return int((np.abs(centros_nq[:, None] - x[None, :]) <= dist).any(axis=1).sum()) if len(x) else 0
        real = estrellas(ext)
        azar = np.array([estrellas(F + rng.uniform(-2 * sigma, 2 * sigma, len(ext))) for _ in range(sorteos)])
        mov = rng.uniform(30, 300, sorteos) * rng.choice([-1, 1], sorteos)
        desplazado = np.array([estrellas(ext + m) for m in mov])
        filas.append({"sesion": s, "fuente": ruta.parent.name, "niveles_nq": len(centros_nq),
                      "externos": len(ext), "detalle": [(e["fuente"], "/".join(e["tipos"]), round(e["centro"])) for e in externos],
                      "estrellas": real, "azar_media": azar.mean(), "p_azar": (azar >= real).mean(),
                      "desplazado_media": desplazado.mean(), "p_desplazado": (desplazado >= real).mean(),
                      "_azar": azar, "_desp": desplazado})
    return filas


MUROS_IMAN = {"Muro de calls", "Muro de puts", "Imán"}
INFORME_RESPETO = Path("documentacion/validacion_qqq_ndx.md")


def respeto_por_grupos(placebos=20, semilla=7):
    """Método de salida/respeto_niveles_nq.csv (calculos.respeto) en las sesiones con datos de QQQ/NDX:
    (a) muros e imán del NQ · (b) muros e imán de QQQ/NDX en su precio trasladado a NQ ·
    (c) control: por cada zona de (a) y (b), `placebos` zonas del mismo ancho a la MISMA distancia de
    la apertura, lado al azar (cada una pesa 1/placebos)."""
    import re
    from calculos import respeto
    from fuentes import opra
    from fuentes.modelo import ErrorDatos
    from tareas.estudio_tierra_nadie import cargar_velas
    config = configuracion.para_indice(configuracion.cargar(), "NQ")
    rng = np.random.default_rng(semilla)
    todas = cargar_velas()
    por_dia = {k: g for k, g in todas.groupby("dia")}
    sesiones = sorted({re.search(r"\d{4}-\d{2}-\d{2}", r.name).group()
                       for r in Path("datos/opra").glob("QQQ_????-??-??_open_interest.dbn.zst")})
    filas, notas = [], []
    for s in sesiones:
        ruta = CARPETA / f"NQ_{s}.json"
        g = por_dia.get(s)
        if not ruta.exists() or g is None:
            notas.append(f"{s}: sin escalera reconstruida o sin velas")
            continue
        d = json.loads(ruta.read_text(encoding="utf-8"))
        h = g[(g["hhmm"] >= "09:30") & (g["hhmm"] < "16:00")]
        sigma, ap, F = d["movimiento"]["desviacion"], h["open"].iloc[0], d["precio_ref"]
        if not respeto.sesion_valida(date.fromisoformat(s), ap, F, sigma):
            notas.append(f"{s}: fuera (hueco de apertura > 1,5σ o cambio de contrato)")
            continue
        try:
            tabla_ext = opra.leer(s)
        except ErrorDatos as e:
            notas.append(f"{s}: {e}")
            continue
        fecha_datos = date.fromisoformat(d["fecha_datos"])
        externos = cf.principales(cf.niveles_externos(cf.a_nq(tabla_ext, F, fecha_datos, config["tipo_interes"]),
                                                      {"fecha_datos": fecha_datos}, config, F))
        zonas = [("a", "NQ", n["zona_desde"], n["zona_hasta"]) for n in d["niveles"] if set(n["tipos"]) & MUROS_IMAN]
        zonas += [("b", e["fuente"], e["zona_desde"], e["zona_hasta"]) for e in externos if e.get("zona_desde") is not None]
        for grupo, fuente, zlo, zhi in zonas:
            filas += respeto.filas_zona(h, zlo, zhi, sigma, ap, s=s, grupo=grupo, fuente=fuente, peso=1.0)
            ancho, dist = zhi - zlo, abs((zlo + zhi) / 2 - ap)
            for _ in range(placebos):
                c = ap + dist * rng.choice([-1, 1])
                filas += respeto.filas_zona(h, c - ancho / 2, c + ancho / 2, sigma, ap, s=s, grupo="c",
                                            fuente="control", peso=1.0 / placebos)
    return pd.DataFrame(filas), notas


def _tasa(t):
    """% de rechazos entre rechazos y roturas (pesado) y n efectivo."""
    con = t[t["res"].isin(["rechazo", "rotura"])]
    n = con["peso"].sum()
    return (con.loc[con["res"] == "rechazo", "peso"].sum() / n * 100 if n else np.nan), n, t["peso"].sum()


def informe_respeto(t, notas):
    L = ["# ¿Se respetan más los muros e imán de QQQ/NDX? (método de salida/respeto_niveles_nq.csv)", "",
         f"Sesiones: {t['s'].nunique() if len(t) else 0} con datos de QQQ/NDX, del {t['s'].min() if len(t) else '—'} "
         f"al {t['s'].max() if len(t) else '—'}. Primer toque de cada zona en la sesión regular; rechazo frente a "
         "rotura a 0,1σ y 0,2σ (calculos/respeto.py, que reproduce el CSV fila a fila). «% rechazo» = rechazos / "
         "(rechazos + roturas); las «nada» aparte.", "",
         "| Umbral | Grupo | Toques | % rechazo | Rechazos / roturas | Nada |", "|---|---|---:|---:|---:|---:|"]
    nombres = {"a": "(a) muros e imán del NQ", "b": "(b) muros e imán de QQQ/NDX (trasladados)",
               "c": "(c) control al azar, misma distancia"}
    resumen = {}
    for X in respeto_umbrales():
        for gr in ("a", "b", "c"):
            q = t[(t["X"] == X) & (t["grupo"] == gr)]
            tasa, n, total = _tasa(q)
            rech = q.loc[q["res"] == "rechazo", "peso"].sum()
            nada = q.loc[q["res"] == "nada", "peso"].sum()
            resumen[(X, gr)] = (tasa, n, rech)
            L.append(f"| {_f(X, 1)}σ | {nombres[gr]} | {_f(total, 0)} | {_f(tasa)} % | {_f(rech, 0)} / {_f(n - rech, 0)} | {_f(nada, 0)} |")
    L += ["", "Pruebas (una cola: ¿(b) se respeta MÁS?):", ""]
    for X in respeto_umbrales():
        tb, nb, rb = resumen[(X, "b")]
        ta, na, ra = resumen[(X, "a")]
        tc = resumen[(X, "c")][0]
        p_bc = stats.binomtest(int(round(rb)), int(round(nb)), tc / 100, alternative="greater").pvalue if nb and tc == tc else np.nan
        p_ba = (stats.fisher_exact([[rb, nb - rb], [ra, na - ra]], alternative="greater")[1] if nb and na else np.nan)
        L.append(f"- {_f(X, 1)}σ: (b) {_f(tb)} % frente a control {_f(tc)} % → p = {_f(p_bc, 2)} · "
                 f"(b) frente a (a) {_f(ta)} % → p = {_f(p_ba, 2)}")
        # Diferencia mínima detectable con estos toques (80 % de potencia, una cola, 5 %)
        if nb:
            mde = (1.645 + 0.84) * np.sqrt((tc / 100) * (1 - tc / 100) / nb) * 100
            L.append(f"  (con {_f(nb, 0)} toques de (b), solo se vería una diferencia de ≈ {_f(mde, 0)} puntos o más)")
    if notas:
        L += ["", "Sesiones no usadas:", ""] + [f"- {x}" for x in notas]
    INFORME_RESPETO.write_text("\n".join(L) + "\n", encoding="utf-8")
    return L


def respeto_umbrales():
    from calculos.respeto import UMBRALES
    return UMBRALES


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if "--respeto" in argv:
        t, notas = respeto_por_grupos()
        print("\n".join(informe_respeto(t, notas)))
        return 0
    if "--azar" in argv:
        filas = contra_azar()
        for f in filas:
            print(f"{f['sesion']} ({f['fuente']}): niveles NQ {f['niveles_nq']} · muros/imán QQQ+NDX {f['externos']} · "
                  f"★ reales {f['estrellas']} · al azar {f['azar_media']:.2f} (p = {f['p_azar']:.2f}) · "
                  f"desplazados {f['desplazado_media']:.2f} (p = {f['p_desplazado']:.2f})")
            print("   externos:", f["detalle"])
        if len(filas) > 1:
            real = sum(f["estrellas"] for f in filas)
            azar = sum(f["_azar"] for f in filas)
            desp = sum(f["_desp"] for f in filas)
            print(f"TOTAL {len(filas)} sesiones: ★ reales {real} · al azar {azar.mean():.1f} (p = {(azar >= real).mean():.3f})"
                  f" · desplazados {desp.mean():.1f} (p = {(desp >= real).mean():.3f})")
        return 0
    from fuentes import opra
    from fuentes.modelo import ErrorDatos
    from tareas.estudio_tierra_nadie import _otro_contrato, cargar_velas
    config = configuracion.para_indice(configuracion.cargar(), "NQ")
    todas = cargar_velas()
    por_dia = {k: g for k, g in todas.groupby("dia")}
    filas, sin_datos = [], []
    for ruta in sorted(CARPETA.glob("NQ_????-??-??.json")):
        d = json.loads(ruta.read_text(encoding="utf-8"))
        s = d["fecha_sesion"]
        try:
            tabla_ext = opra.leer(s)
        except ErrorDatos:
            sin_datos.append(s)
            continue
        g = por_dia.get(s)
        if g is None or _otro_contrato(por_dia, d) is not False:
            continue
        velas = g[(g["hhmm"] >= "09:30") & (g["hhmm"] < "16:00")]
        filas += filas_de_sesion(d, tabla_ext, velas, config)
    t = pd.DataFrame(filas)
    L = ["# Validación: ¿se respetan más los niveles del NQ con ★ (confluencia con QQQ/NDX)?", "",
         f"Sesiones con datos de QQQ/NDX: {t['fecha_sesion'].nunique() if len(t) else 0} · sin datos: {len(sin_datos)}.",
         "Solo niveles tocados en el horario regular (9:30-16:00 NY) con dirección: respetado o roto "
         f"(roto = algún cierre de 1 minuto a ≥ {config['estado']['umbral_rotura_puntos']} puntos al otro lado).", "",
         "| Periodo | Grupo | ★: tocados | ★: respetados | sin ★: tocados | sin ★: respetados | p (Fisher) |",
         "|---|---|---:|---:|---:|---:|---:|"]
    if len(t):
        for nombre, (a, b) in (("Ajuste nov-jul", AJUSTE), ("Comprobación ago-oct", COMPROBACION)):
            p = t[(t["fecha_sesion"] >= a) & (t["fecha_sesion"] <= b)]
            for grupo, q in (("todos", p), ("solo principales", p[p["categoria"] == "principal"])):
                r = comparar(q)
                L.append(f"| {nombre} | {grupo} | {r['n_si']} | {_f(r['si'])} % | {r['n_no']} | {_f(r['no'])} % | "
                         f"{_f(r['p'], 2)} |")
    L += ["", "Se da por buena la ★ solo si los niveles con ★ se respetan más en el ajuste Y en la comprobación, "
              "con p < 0,05 en los dos."]
    INFORME.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
