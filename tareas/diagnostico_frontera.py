"""
Diagnóstico (no cambia nada): frontera (gamma flip) y mayores bloques de calls y
puts usando SOLO los vencimientos de los próximos N días naturales, al lado de
los de la ventana normal de la escalera. Sirve para comparar con otras fuentes
que calculan la frontera con los vencimientos más cercanos.

Uso:
    .venv/bin/python -m tareas.diagnostico_frontera --indice ES Section47_....pdf Section48_....pdf
    .venv/bin/python -m tareas.diagnostico_frontera --indice NQ --dias 7 Section40_....pdf

Por defecto, los días de config.yaml (vista_corta -> dias, 7): de la sesión a la
sesión + 6 días, ambos incluidos. Es el mismo cálculo que la «vista corta» de la
página (calculos/vista_corta.py).
Los mayores bloques se buscan a ±3 % del precio de referencia.
"""
import argparse

from calculos import config as configuracion
from calculos import escalera
from calculos.vista_corta import opciones_cortas, resumen
from fuentes import cme_pdf


def _n(x, decimales=0):
    """Número en formato español: 7.760,00"""
    return f"{x:,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def imprimir(nombre, r):
    v = r["vencimientos"]
    print(f"\n{nombre}: {len(v)} vencimientos ({v[0]} … {v[-1]})")
    flip = _n(r["frontera"], 2) if r["frontera"] else "sin cruce en el rango"
    print(f"  Frontera: {flip} · GEX en el precio {_n(r['gex_en_precio'] / 1e6)} M$")
    print("  Mayores calls (OI):", ", ".join(f"{_n(s)} ({_n(o)})" for s, o in r["calls_oi"]))
    print("  Mayores puts (OI): ", ", ".join(f"{_n(s)} ({_n(o)})" for s, o in r["puts_oi"]))
    print("  Mayor GEX de calls:", ", ".join(f"{_n(s)} ({_n(g / 1e6)} M$)" for s, g in r["gex_calls"]),
          "· de puts:", ", ".join(f"{_n(s)} ({_n(g / 1e6)} M$)" for s, g in r["gex_puts"]))


def main(argv=None):
    p = argparse.ArgumentParser(description="Frontera con solo los vencimientos de los próximos N días.")
    p.add_argument("pdfs", nargs="+")
    p.add_argument("--indice", default="NQ")
    p.add_argument("--dias", type=int, help="días naturales (por defecto, vista_corta.dias de config.yaml)")
    a = p.parse_args(argv)
    config = configuracion.para_indice(configuracion.cargar(), a.indice)
    a.dias = a.dias or config["vista_corta"]["dias"]
    df, info = cme_pdf.leer(a.pdfs, config, a.indice)
    res = escalera.calcular(df, info, config)
    opciones, ref, sesion = res["opciones"], res["precio_ref"], res["fecha_sesion"]
    cortas, limite = opciones_cortas(opciones, sesion, a.dias)
    print(f"{a.indice} · sesión {sesion} · precio de referencia {_n(ref, 2)} · boletín del {info['fecha_datos']}")
    imprimir("Ventana de la escalera", resumen(opciones, ref, config))
    imprimir(f"Solo {a.dias} días ({sesion} a {limite})", resumen(cortas, ref, config))


if __name__ == "__main__":
    main()
