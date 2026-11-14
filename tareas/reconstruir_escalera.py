"""
Escaleras del ES reconstruidas con los datos de Databento (open interest y liquidaciones de CME).

    # Reconstruye las escaleras de los boletines descargados entre dos fechas (fecha del boletín):
    .venv/bin/python -m tareas.reconstruir_escalera reconstruir --desde 2025-10-31 --hasta 2026-10-29
    # Compara la reconstruida de un boletín con la real (la del PDF, en datos/historico):
    .venv/bin/python -m tareas.reconstruir_escalera comparar --dia 2026-10-23

Usa el mismo cálculo que la escalera diaria (calculos.escalera.calcular); solo cambia de dónde
salen los datos (fuentes.databento_oi en vez del PDF). Se guarda en datos/historico_reconstruido/
ES_<sesión>_esc2.txt, aparte del histórico real, para que el backtest sepa cuál es cuál.
No descarga nada: los datos se compran antes con tareas.databento_oi.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

from calculos import config as configuracion
from calculos.escalera import calcular
from fuentes import databento_oi
from fuentes.modelo import ErrorDatos
from tareas.databento_oi import dias_de_datos

CARPETA = Path("datos/historico_reconstruido")
CAMPOS = [("ref", "precio de referencia"), ("flip", "frontera (gamma flip)"), ("68", "rango del 68 %"),
          ("95", "rango del 95 %"), ("w", "medio ancho de zona"), ("fc", "muro de calls corto"),
          ("mc", "muro de calls"), ("mp", "muro de puts"), ("rc", "régimen corto")]


def reconstruir(dia, config=None, indice="ES"):
    """(texto ESC2, resultado) de la escalera reconstruida del boletín de `dia`."""
    from salida import historico
    config = config or configuracion.para_indice(configuracion.cargar(), indice)
    df, info = databento_oi.leer(dia, indice=indice)
    res = calcular(df, info, config)
    return historico.desde_resultado(res)["tradingview_esc2"], res


def partes(texto):
    """ESC2 → (cabecera {clave: valor}, niveles {precio: campos}, zonas grises [texto])."""
    cab, niveles, grises = {}, {}, []
    for tok in texto.strip().split("|")[1:]:
        clave, _, v = tok.partition("=")
        if clave == "L":
            c = v.split(":")
            niveles[c[1]] = {"letras": c[0], "cat": c[2], "oi": c[3], "venc": c[4],
                             "zona": c[6] if len(c) > 6 else "", "corto": c[7] if len(c) > 7 else ""}
        elif clave == "Z":
            grises.append(v)
        else:
            cab[clave] = v
    return cab, niveles, grises


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def comparar(real, reconstruida):
    """Diferencias entre dos ESC2 del mismo día. Devuelve un diccionario:
    cabecera [(campo, nombre, real, reconstruida, diferencia en puntos)], niveles
    [(precio, qué cambia, real, reconstruida)], iguales (n.º de niveles idénticos en tipo,
    categoría y zona), grises (real, reconstruida) y cuadra (True si niveles, categorías,
    zonas y zonas grises son iguales y la frontera se mueve ≤ 1 punto)."""
    ca, na, ga = partes(real)
    cb, nb, gb = partes(reconstruida)
    cabecera = []
    for k, nombre in CAMPOS:
        a, b = ca.get(k), cb.get(k)
        if a != b:
            pa = [_num(x) for x in (a or "").split("-")]
            pb = [_num(x) for x in (b or "").split("-")]
            dif = max((abs(x - y) for x, y in zip(pa, pb) if x is not None and y is not None), default=None)
            cabecera.append((k, nombre, a, b, dif))
    niveles, iguales = [], 0
    for precio in sorted(set(na) | set(nb), key=lambda p: -_num(p.split("-")[0])):
        a, b = na.get(precio), nb.get(precio)
        if a is None or b is None:
            niveles.append((precio, "solo en la " + ("reconstruida" if a is None else "real"),
                            a and f"{a['letras']} {a['cat']}", b and f"{b['letras']} {b['cat']}"))
            continue
        cambios = [(n, a[k], b[k]) for k, n in (("letras", "tipo"), ("cat", "categoría"),
                                                ("zona", "zona"), ("corto", "muro corto")) if a[k] != b[k]]
        if cambios:
            niveles += [(precio, n, x, y) for n, x, y in cambios]
        else:
            iguales += 1
        if a["oi"] != b["oi"] or a["venc"] != b["venc"]:
            niveles.append((precio, "open interest (calls/puts) y vencimientos", f"{a['oi']} · {a['venc']}",
                            f"{b['oi']} · {b['venc']}"))
    estructurales = [n for n in niveles if not n[1].startswith("open interest")]
    flip = next((c[4] for c in cabecera if c[0] == "flip"), 0.0) or 0.0
    otros = [c for c in cabecera if c[0] not in ("flip", "fc")]
    cuadra = not estructurales and ga == gb and flip <= 1.0 and not otros
    return {"cabecera": cabecera, "niveles": niveles, "iguales": iguales, "total": len(set(na) | set(nb)),
            "grises": (ga, gb), "cuadra": cuadra}


def texto_comparacion(c):
    lineas = [f"Niveles idénticos (tipo, categoría y zona): {c['iguales']} de {c['total']}",
              f"Zonas grises 0DTE: real {c['grises'][0] or '—'} · reconstruida {c['grises'][1] or '—'}"]
    for k, nombre, a, b, dif in c["cabecera"]:
        lineas.append(f"  {nombre}: real {a} · reconstruida {b}" + (f" (±{dif:.2f} puntos)" if dif is not None else ""))
    for precio, que, a, b in c["niveles"]:
        lineas.append(f"  {precio} — {que}: real {a} · reconstruida {b}")
    lineas.append("CUADRA" if c["cuadra"] else "NO CUADRA del todo (ver diferencias)")
    return "\n".join(lineas)


def _nombre_real(sesion, indice):
    return f"{sesion}_esc2.txt" if indice == "NQ" else f"{indice}_{sesion}_esc2.txt"


def _uno(args):
    """Reconstruye un boletín y guarda su ESC2 (y, con json, el JSON del histórico). Para los procesos."""
    dia, indice, con_json = args
    import json
    from salida import historico
    try:
        texto, res = reconstruir(dia, indice=indice)
    except (ErrorDatos, ValueError) as e:
        return dia, None, str(e)
    (CARPETA / f"{indice}_{res['fecha_sesion']}_esc2.txt").write_text(texto + "\n")
    if con_json:
        d = historico.desde_resultado(res)
        (CARPETA / f"{indice}_{res['fecha_sesion']}.json").write_text(json.dumps(d, ensure_ascii=False))
    return dia, res["fecha_sesion"], None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=["reconstruir", "comparar"])
    p.add_argument("--desde")
    p.add_argument("--hasta")
    p.add_argument("--dia", help="comparar: fecha del boletín (AAAA-MM-DD)")
    p.add_argument("--indice", default="ES", choices=["ES", "NQ"])
    p.add_argument("--json", action="store_true", help="guardar también el JSON completo de cada día")
    p.add_argument("--procesos", type=int, default=1)
    a = p.parse_args(argv)
    config = configuracion.para_indice(configuracion.cargar(), a.indice)
    if a.accion == "comparar":
        from calculos.calendario import siguiente_habil
        dia = date.fromisoformat(a.dia)
        real_ruta = Path("datos/historico") / _nombre_real(siguiente_habil(dia), a.indice)
        if not real_ruta.exists():
            print(f"No está la escalera real de la sesión {siguiente_habil(dia)} ({real_ruta}).")
            return 1
        texto, _ = reconstruir(dia, config, a.indice)
        print(texto_comparacion(comparar(real_ruta.read_text(), texto)))
        return 0
    if not (a.desde and a.hasta):
        print("reconstruir necesita --desde y --hasta.")
        return 1
    CARPETA.mkdir(parents=True, exist_ok=True)
    dias = dias_de_datos(date.fromisoformat(a.desde), date.fromisoformat(a.hasta))
    trabajos = [(d, a.indice, a.json) for d in dias]
    if a.procesos > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(a.procesos) as ex:
            resultados = list(ex.map(_uno, trabajos))
    else:
        resultados = [_uno(t) for t in trabajos]
    hechos = sum(1 for _, s, _ in resultados if s)
    print(f"Reconstruidas: {hechos} escaleras {a.indice} en {CARPETA}")
    for dia, s, e in resultados:
        if e:
            print(f"  {dia}: {e}")
    return 0 if hechos else 1


if __name__ == "__main__":
    sys.exit(main())
