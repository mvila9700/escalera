"""
Histórico: un archivo JSON (y un CSV con los niveles) por sesión e índice.

    datos/historico/2026-10-26.json            (NQ)
    datos/historico/2026-10-26_niveles.csv
    datos/historico/ES_2026-10-26.json         (ES)
    datos/historico/ES_2026-10-26_niveles.csv
    datos/historico/ES_2026-10-26_esc2.txt     (texto ESC2 del día, para TradingView y el backtest)

El JSON guarda todo lo necesario para volver a generar la página sin
recalcular nada. El proceso de la noche ("Estado hoy") lo abre, rellena el
estado de cada nivel y vuelve a generar la página. Tus notas del journal se
guardan en el campo "nota" de cada nivel.
"""
import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from calculos import niveles as niv
from calculos.config import RAIZ


def _limpio(x):
    """Convierte fechas y números de numpy a tipos que entiende JSON."""
    if isinstance(x, dict):
        return {k: _limpio(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_limpio(v) for v in x]
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if np.isnan(x) else round(float(x), 4)
    return x


def _tabla(df, columnas):
    if df is None:
        return None
    return [{c: fila[c] for c in columnas} for _, fila in df.iterrows()]


def seguimiento_vacio(ventanas):
    """Hueco para el seguimiento de cada nivel con velas de 1 minuto
    (Fase 3c, aparcada: todavía no hay velas). Se guarda vacío para que el
    formato ya esté fijado cuando se rellene."""
    return {
        "toques": None,              # cuántas veces se tocó
        "hora_primer_toque": None,
        "roto": None,                # True/False
        "aceptado": None,            # True/False (el precio se quedó al otro lado)
        "excursiones": {str(m): {"favor": None, "contra": None} for m in ventanas},  # puntos tras el toque
    }


def desde_resultado(res):
    """Pasa el resultado de calculos.escalera.calcular a un dict guardable."""
    from calculos.config import cargar
    config = cargar()
    ventanas = config.get("seguimiento", {}).get("ventanas_minutos", [15, 30, 60])
    indice = res.get("indice", "NQ")
    lo, hi = res["movimiento"]["rango_95"]
    ancho = res["jerarquia"]["ancho_zona"]
    lista = []

    def nivel_de(z):
        return {
            "desde": z["desde"], "hasta": z["hasta"], "texto": z["texto"],
            "tipos": z["tipos"], "notas": z["notas"],
            "oi_calls": z["oi_calls"], "oi_puts": z["oi_puts"],
            "gex_neto": z["gex_neto"], "gex_abs": z["gex_abs"],
            "gex_calls": z["gex_calls"], "gex_puts": z["gex_puts"], "gex_0dte": z["gex_0dte"],
            "por_vencimiento": z["por_vencimiento"],
            "gex_bruta_estructural": z["gex_bruta_estructural"], "categoria": z["categoria"],
            "zona_desde": z["zona_desde"], "zona_hasta": z["zona_hasta"],
            "fusionados": z.get("fusionados", []),
            "venc_principal": z["venc_principal"], "prob_toque": z["prob_toque"],
            "cex": z.get("cex"), "vex": z.get("vex"), "efecto_charm": z.get("efecto_charm"),
            "confluencia": z.get("confluencia"),   # ★ con QQQ/NDX (opcional; calculos/confluencia.py)
            "estado": None, "hora_toque": None, "nota": "",
            "seguimiento": seguimiento_vacio(ventanas),
        }

    for z in res["zonas"]:
        lista.append(nivel_de(z))
    # Escalera extendida (de 2σ a 4σ): aparte, para que nada de lo de dentro cambie
    extendidos = sorted((dict(nivel_de(z), extendido=True) for z in res.get("zonas_extendidas") or []),
                        key=lambda n: -n["desde"])
    if res["gamma_flip"]:
        from calculos import movimiento
        f = res["gamma_flip"]
        lista.append({
            "desde": f, "hasta": f, "texto": niv.texto_zona({"desde": f, "hasta": f}),
            "tipos": ["Frontera"], "notas": [], "oi_calls": None, "oi_puts": None,
            "gex_neto": None, "gex_abs": None, "gex_calls": None, "gex_puts": None,
            "gex_0dte": None, "por_vencimiento": None, "venc_principal": None,
            "gex_bruta_estructural": None, "categoria": "principal",   # la frontera siempre es principal
            "zona_desde": f - ancho, "zona_hasta": f + ancho, "fusionados": [],
            "prob_toque": float(movimiento.prob_toque(res["precio_ref"], f, res["iv_atm"])),
            "estado": None, "hora_toque": None, "nota": "",
            "seguimiento": seguimiento_vacio(ventanas),
        })
    lista.sort(key=lambda n: -n["desde"])

    from calculos import contrato as ctr
    dias_aviso = config.get("tradingview", {}).get("dias_aviso_roll", 5)
    contrato = ctr.info(res["fecha_sesion"], res.get("contrato_ref"), res.get("futuros", {}), dias_aviso,
                        raiz=indice)
    avisos = list(res["avisos"])
    if contrato and contrato["aviso"]:
        avisos.insert(0, "CAMBIO DE CONTRATO: " + contrato["aviso"])

    perfil = res["perfil"]
    perfil = perfil[(perfil["precio"] >= lo) & (perfil["precio"] <= hi)]
    datos = _limpio({
        "version": 3,   # 2: GEX por nivel, desglose, boletín, seguimiento; 3: categoría, zona, fusión
        "generado": datetime.now().isoformat(timespec="seconds"),
        "indice": indice,
        "fecha_sesion": res["fecha_sesion"],
        "fecha_datos": res["fecha_datos"],
        "preliminar": res["preliminar"],
        "boletin": {
            "tipo": "PRELIMINAR" if res["preliminar"] else "FINAL",
            "comprobaciones": res.get("comprobaciones"),   # identificación de las puts sin etiqueta
        },
        "fuente": res["fuente"],
        "precio_ref": res["precio_ref"],
        "origen_precio": res["origen_precio"],
        "futuro_liquidacion": res["futuro_liquidacion"],
        "contrato_ref": res.get("contrato_ref"),
        "vencimiento_limite": res.get("vencimiento_limite"),
        "vencimientos_incluidos": res.get("vencimientos_incluidos"),   # los que suman calls/puts y GEX
        "regimen": res["regimen"],
        "gex_total": res["gex_total"],
        "gamma_flip": res["gamma_flip"],
        "iv_atm": res["iv_atm"],
        "vencimiento_iv": res["vencimiento_iv"],
        "movimiento": res["movimiento"],
        "straddle": res["straddle"],
        "strike_straddle": res["strike_straddle"],
        "jerarquia": res["jerarquia"],
        "zonas_0dte": res.get("zonas_0dte", []),   # zonas grises: no son niveles
        "niveles": lista,
        "niveles_extendidos": extendidos,
        "graficos": {
            "mapa_0dte": _tabla(res["mapa_0dte"][(res["mapa_0dte"]["tramo"] >= lo - 25) &
                                                 (res["mapa_0dte"]["tramo"] <= hi)]
                                if res["mapa_0dte"] is not None else None,
                                ["tramo", "call", "put", "neto"]),
            "oi_tramos": _tabla(res["oi_tramos"], ["tramo", "call", "put", "neto"]),
            "perfil": _tabla(perfil, ["precio", "gex_total"]),
        },
        "avisos": avisos,
        "contrato": contrato,
        "estado_dia": None,   # lo rellena el proceso de la noche (máximo, mínimo...)
    })
    if res.get("productos_desconocidos"):
        # Productos del boletín que no se identifican (no entran en ningún cálculo)
        datos["productos_desconocidos"] = _limpio(res["productos_desconocidos"])
    vc = res.get("vanna_charm")
    if vc is not None:
        # Vanna y charm (opcional: los JSON antiguos no lo tienen)
        datos["vanna_charm"] = _limpio({**{k: v for k, v in vc.items() if k != "por_strike"},
                                        "por_strike": _tabla(vc["por_strike"], ["strike", "vex", "cex",
                                                                                 "vex_0dte", "cex_0dte"])})
    conf = res.get("confluencia")
    if conf:
        # Confluencia con QQQ/NDX (opcional): distancia usada y niveles que solo tienen QQQ/NDX
        datos["confluencia"] = _limpio({k: conf[k] for k in ("distancia", "fuentes", "ratios", "solo")})
    tv = config.get("tradingview", {})
    if tv.get("gamma_vivo", True) and res.get("opciones") is not None:
        # Bloque opcional para que el indicador recalcule la gamma en vivo (salida/gamma_vivo.py)
        try:
            from salida import gamma_vivo
            from calculos import config as configuracion
            cfg_i = configuracion.para_indice(config, indice)
            datos["gamma_vivo"] = gamma_vivo.bloque(res, cfg_i, tv.get("gamma_vivo_max_caracteres", 24000))
        except Exception:
            datos["gamma_vivo"] = None      # sin bloque: el indicador funciona como siempre
    anadir_vista_corta(datos, res.get("vista_corta"))
    return datos


JOURNAL_VACIO = {
    "regimen_observado": "",   # lo rellenas tú: "rango" o "tendencia"
    "freno_en": "",            # lo rellenas tú: nivel donde se paró el precio (p. ej. "29.700")
}


def anadir_vista_corta(datos, vista):
    """Añade al JSON la vista corta (frontera, muros y régimen de los próximos
    N días), marca "muro_corto" en los niveles y deja el journal del día. No
    toca nada más (sirve también para JSON ya guardados: conserva notas y estado)."""
    from calculos import vista_corta as vc
    if vista is not None:
        datos["vista_corta"] = _limpio(vista)
    vc.marcar_niveles(datos["niveles"], datos.get("vista_corta"))
    datos.setdefault("journal_dia", dict(JOURNAL_VACIO))
    from calculos import fiabilidad        # clasificación según los datos (no cambia los niveles)
    fiabilidad.anotar(datos)
    poner_textos_tradingview(datos)
    return datos


def poner_textos_tradingview(datos):
    """Textos para el indicador: ESC2 (nuevo) y ESCNQ1 (indicadores antiguos)."""
    from salida import tradingview
    if "movimiento" not in datos:     # no es una escalera completa
        return
    indice = datos.get("indice", "NQ")
    # ESCNQ1 es solo del NQ (el indicador antiguo da por hecho que es NQ)
    datos["tradingview"] = tradingview.texto(datos) if indice == "NQ" else None
    if datos.get("jerarquia"):
        datos["tradingview_esc2"] = tradingview.texto_esc2(datos, sym=indice)


def carpeta():
    c = RAIZ / "datos" / "historico"
    c.mkdir(parents=True, exist_ok=True)
    return c


def nombre(fecha_sesion, indice="NQ"):
    """Nombre base de los archivos de una sesión: "2026-10-26" (NQ) o "ES_2026-10-26"."""
    return str(fecha_sesion) if indice == "NQ" else f"{indice}_{fecha_sesion}"


def guardar(datos, conservar_notas=True):
    """Guarda el JSON y el CSV de niveles. Devuelve la ruta del JSON.

    Si ya existía el archivo de esa sesión (p. ej. al repetir la escalera),
    se conservan tus notas del journal de los niveles con el mismo texto.
    """
    base = nombre(datos["fecha_sesion"], datos.get("indice", "NQ"))
    ruta = carpeta() / f"{base}.json"
    if conservar_notas and ruta.exists():
        previo = json.loads(ruta.read_text(encoding="utf-8"))
        # Tu journal del día (régimen observado, freno) se conserva si ya lo rellenaste
        if any((previo.get("journal_dia") or {}).values()):
            datos["journal_dia"] = previo["journal_dia"]
        anteriores = {n["texto"]: n.get("nota", "") for n in previo["niveles"]}
        for n in datos["niveles"]:
            if not n.get("nota") and anteriores.get(n["texto"]):
                n["nota"] = anteriores[n["texto"]]
    poner_textos_tradingview(datos)   # así llevan también el estado del día
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    # El CSV es plano: sin el desglose por vencimiento ni el seguimiento (están en el JSON)
    pd.DataFrame([{**{k: v for k, v in n.items() if k not in ("por_vencimiento", "seguimiento")},
                   "tipos": ", ".join(n["tipos"]), "notas": ", ".join(n["notas"]),
                   "fusionados": ", ".join(n.get("fusionados") or []),
                   "muro_corto": ", ".join(n.get("muro_corto") or [])}
                  for n in datos["niveles"]]).to_csv(
        carpeta() / f"{base}_niveles.csv", index=False)
    # Registro diario del texto ESC2: cada escalera generada deja su línea (la usa el backtest)
    if datos.get("tradingview_esc2"):
        (carpeta() / f"{base}_esc2.txt").write_text(datos["tradingview_esc2"] + "\n", encoding="utf-8")
    return ruta


def cargar(fecha_sesion, indice="NQ"):
    ruta = carpeta() / f"{nombre(fecha_sesion, indice)}.json"
    return json.loads(ruta.read_text(encoding="utf-8"))


# --- Escaleras recentradas -----------------------------------------------------
# Si el precio sale del rango del 68 %, la escalera se recalcula con el MISMO open interest
# centrada en el precio actual. Se guarda aparte (ES_2026-10-26_recentrada_7740.json y
# ..._esc2.txt): el histórico del día no se toca y el backtest no la lee (sus patrones son
# AAAA-MM-DD.json y AAAA-MM-DD_esc2.txt exactos).

def texto_precio(precio):
    """7740.0 → "7740"; 7740.25 → "7740.25" (para el nombre del archivo)."""
    return f"{float(precio):.2f}".rstrip("0").rstrip(".")


def nombre_recentrada(fecha_sesion, indice, precio):
    return f"{nombre(fecha_sesion, indice)}_recentrada_{texto_precio(precio)}"


def guardar_recentrada(datos, precio):
    """Guarda la escalera recentrada (JSON y ESC2). Devuelve la ruta del JSON."""
    base = nombre_recentrada(datos["fecha_sesion"], datos.get("indice", "NQ"), precio)
    poner_textos_tradingview(datos)
    ruta = carpeta() / f"{base}.json"
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    if datos.get("tradingview_esc2"):
        (carpeta() / f"{base}_esc2.txt").write_text(datos["tradingview_esc2"] + "\n", encoding="utf-8")
    return ruta


def recentradas(fecha_sesion, indice="NQ"):
    """Escaleras recentradas de una sesión, de la más reciente a la más antigua:
    [{precio, generado, esc2, flip, rango_68, regimen, rango_68_original}]."""
    out = []
    for ruta in carpeta().glob(f"{nombre(fecha_sesion, indice)}_recentrada_*.json"):
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        r = d.get("recentrada") or {}
        out.append({"precio": r.get("precio", d.get("precio_ref")), "generado": d.get("generado", ""),
                    "esc2": d.get("tradingview_esc2"), "flip": d.get("gamma_flip"),
                    "rango_68": (d.get("movimiento") or {}).get("rango_68"), "regimen": d.get("regimen"),
                    "rango_68_original": r.get("rango_68_original")})
    return sorted(out, key=lambda x: x["generado"], reverse=True)
