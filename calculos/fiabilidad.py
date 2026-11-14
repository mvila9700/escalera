"""
Clasificación de los niveles según lo que dicen los datos (Fase F-2, 2026-11-12).

NO cambia cómo se calculan los niveles (GEX, OI, fusión, zonas, categoría): solo añade cómo se
CLASIFICAN, se MUESTRAN y se ETIQUETAN, con los números del estudio de fiabilidad de cada índice
(salida/fiabilidad_<INDICE>.json, de tareas/estudio_fiabilidad.py; método de calculos/respeto.py,
primer toque, rechazo a 0,2σ, control de 20 zonas a la misma distancia de la apertura).

En cada nivel del JSON (y de la escalera extendida):
- "fiabilidad": % de rechazo histórico de su tipo, régimen local (por encima / por debajo de la
  frontera) y papel (techo si está por encima del precio de referencia, suelo si está por debajo),
  con su n, el control y la ventaja. n < n_minimo → «pocos datos».
- "estado_muro" (muros): "fiable" (por encima de la frontera y día positivo), "muro" (por encima y
  día negativo) o "ruptura" (por debajo de la frontera). Solo se ETIQUETA si el estudio del índice
  muestra efecto de régimen (muros por encima − por debajo ≥ `diferencia_regimen_minima` puntos):
  en el NQ sí; en el ES no (se dice en la página); en el oro aún no hay estudio.
- "oi_maximo" (el antiguo imán): distancia al precio en σ, «objetivo» si está a ≤ 0,25σ, y el %
  histórico de llegada de su tramo de distancia (sin decir que atrae ni que repele).
- "contexto" (aceleración, OI relevante y resistencia sin muro ni imán): bajan un escalón en la
  jerarquía visual ("categoria_visual") y llevan la nota de su ventaja histórica.
- "codigo_fiabilidad": letras para el 11.º campo de cada L= del ESC2 (ver salida/tradingview.py).
"""
import json
from pathlib import Path

from calculos.config import RAIZ

PRIORIDAD = ["Muro de calls", "Muro de puts", "Imán", "Resistencia", "Aceleración", "OI relevante"]
MUROS = {"Muro de calls", "Muro de puts"}
CONTEXTO = {"Resistencia", "Aceleración", "OI relevante"}
UN_ESCALON = {"principal": "secundario", "secundario": "menor", "menor": "menor"}
NOMBRE_OI_MAXIMO = "OI máximo"
TEXTO_RUPTURA = "por debajo de la frontera todo rompe más: el muro no protege más que un precio cualquiera"
ETIQUETA_MURO = {"fiable": "Muro fiable", "muro": "Muro", "ruptura": "Muro débil · ruptura"}
CODIGO_MURO = {"fiable": "F", "muro": "M", "ruptura": "B"}
ETIQUETAS_MURO = set(ETIQUETA_MURO.values())
X = "0.2"


def cargar_estudio(indice, carpeta=None):
    ruta = Path(carpeta or RAIZ / "salida") / f"fiabilidad_{indice}.json"
    if not ruta.exists():
        return None
    return json.loads(ruta.read_text(encoding="utf-8"))


def _cfg(config=None):
    from calculos.config import cargar
    c = (config or cargar()).get("fiabilidad", {})
    return {"n_minimo": int(c.get("n_minimo", 20)),
            "diferencia_regimen_minima": float(c.get("diferencia_regimen_minima", 10)),
            "ventaja_minima": float(c.get("ventaja_minima", 5)),
            "objetivo_sigmas": float(c.get("objetivo_sigmas", 0.25))}


def efecto_regimen(estudio, cfg):
    """{hay, encima, debajo, n_encima, n_debajo}: si los muros aguantan claramente más por encima de la
    frontera que por debajo en este índice."""
    if not estudio:
        return {"hay": False, "estudio": False}
    c = estudio["casillas"][X]
    e, d = c.get("Muros|positiva") or {}, c.get("Muros|negativa") or {}
    hay = (e.get("rechazo") is not None and d.get("rechazo") is not None
           and min(e["n"], d["n"]) >= cfg["n_minimo"]
           and e["rechazo"] - d["rechazo"] >= cfg["diferencia_regimen_minima"])
    return {"hay": bool(hay), "estudio": True, "encima": e.get("rechazo"), "debajo": d.get("rechazo"),
            "n_encima": e.get("n", 0), "n_debajo": d.get("n", 0),
            "control_encima": e.get("control"), "control_debajo": d.get("control"),
            "sesiones": estudio.get("sesiones"), "desde": estudio.get("desde"), "hasta": estudio.get("hasta")}


def _pct(x):
    return "—" if x is None else f"{x:.0f} %"


def texto_casilla(c, n_minimo):
    if not c or not c.get("n"):
        return "sin datos"
    if c["n"] < n_minimo:
        return f"pocos datos (n={c['n']})"
    v = c.get("ventaja")
    return (f"{_pct(c['rechazo'])} (n={c['n']}; control {_pct(c.get('control'))}"
            + (f", {v:+.0f})" if v is not None else ")"))


def _tramo(dist):
    for lo, hi in ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.5)):
        if lo <= dist < hi:
            return f"{lo}-{hi}"
    return None


def anotar_nivel(n, ref, flip, sigma, regimen_dia, estudio, efecto, cfg):
    tipos = [t for t in n.get("tipos", []) if t in PRIORIDAD]
    if not tipos:
        return n
    centro = (n["desde"] + n["hasta"]) / 2
    tipo = next(t for t in PRIORIDAD if t in tipos)
    local = None if flip is None else ("positiva" if centro > flip else "negativa")
    papel = "techo" if centro > ref else "suelo"
    casilla = None
    if estudio and local:
        casilla = estudio["casillas"][X].get(f"{tipo}|{local}|{papel}")
    n["fiabilidad"] = {"tipo": tipo, "local": local, "papel": papel,
                       **({k: casilla.get(k) for k in ("rechazo", "n", "control", "ventaja")} if casilla else {}),
                       "texto": texto_casilla(casilla, cfg["n_minimo"]) if estudio else "sin estudio"}
    codigo = ""
    etiquetas = []
    # Muros: estado según el régimen local y el del día
    if MUROS & set(tipos) and local:
        estado = "ruptura" if local == "negativa" else ("fiable" if regimen_dia == "positiva" else "muro")
        n["estado_muro"] = estado
        if efecto["hay"]:
            etiquetas.append(ETIQUETA_MURO[estado])
            codigo += CODIGO_MURO[estado]
    # OI máximo (el antiguo imán)
    if "Imán" in tipos and sigma:
        dist = abs(centro - ref) / sigma
        tramo = _tramo(dist)
        ll = (estudio or {}).get("iman", {}).get(tramo) if tramo else None
        objetivo = dist <= cfg["objetivo_sigmas"]
        n["oi_maximo"] = {"distancia_sigmas": round(dist, 2), "objetivo": objetivo, "tramo": tramo,
                          "llegada": ll.get("iman") if ll else None, "control": ll.get("control") if ll else None,
                          "n": ll.get("n") if ll else 0}
        # La etiqueta va junto a la de su tipo («OI máximo»): solo el detalle
        txt = ("objetivo · " if objetivo else "") + f"a {dist:.2f}σ".replace(".", ",")
        if ll and ll.get("n"):
            txt += f": llega el {_pct(ll['iman'])} de las veces (control {_pct(ll['control'])}, n={ll['n']})"
        elif estudio:
            txt += ": sin datos de llegada a esa distancia"
        etiquetas.append(txt)
        codigo += "O" if objetivo else "L"
    # Contexto: sin muro ni imán
    if not (MUROS | {"Imán"}) & set(tipos):
        n["categoria_visual"] = UN_ESCALON.get(n.get("categoria"), n.get("categoria"))
        c = (estudio or {}).get("casillas", {}).get(X, {}).get(f"{tipo}|todas") if estudio else None
        if c and c.get("ventaja") is not None and c["n"] >= cfg["n_minimo"] and abs(c["ventaja"]) >= cfg["ventaja_minima"]:
            n["contexto"] = f"contexto: ventaja histórica {c['ventaja']:+.0f} puntos"
        else:
            n["contexto"] = "contexto: sin ventaja histórica"
        codigo += "c"
    else:
        n["categoria_visual"] = n.get("categoria")
    n["etiquetas_fiabilidad"] = etiquetas
    n["codigo_fiabilidad"] = codigo or "-"
    return n


def anotar(datos, estudio=None, config=None):
    """Añade la clasificación a todos los niveles del JSON del histórico (y la cabecera "fiabilidad")."""
    if "movimiento" not in datos:
        return datos
    cfg = _cfg(config)
    indice = datos.get("indice", "NQ")
    estudio = estudio if estudio is not None else cargar_estudio(indice)
    efecto = efecto_regimen(estudio, cfg)
    ref, flip, sigma = datos["precio_ref"], datos.get("gamma_flip"), datos["movimiento"]["desviacion"]
    for n in list(datos.get("niveles") or []) + list(datos.get("niveles_extendidos") or []):
        if n.get("tipos") != ["Frontera"]:
            anotar_nivel(n, ref, flip, sigma, datos.get("regimen"), estudio, efecto, cfg)
    datos["fiabilidad"] = {**efecto, "indice": indice, "n_minimo": cfg["n_minimo"],
                           "nota_ruptura": TEXTO_RUPTURA if efecto["hay"] else None,
                           "nota_indice": nota_indice(indice, efecto)}
    return datos


def nota_indice(indice, efecto):
    if not efecto.get("estudio"):
        return (f"El {indice} aún no tiene estudio de fiabilidad (hacen falta escaleras pasadas y velas): "
                "los niveles salen sin etiquetas de régimen.")
    if efecto["hay"]:
        return (f"En el {indice}, los muros por encima de la frontera rechazan el {_pct(efecto['encima'])} al primer "
                f"toque (n={efecto['n_encima']}; control {_pct(efecto['control_encima'])}); por debajo, el "
                f"{_pct(efecto['debajo'])} (n={efecto['n_debajo']}; control {_pct(efecto['control_debajo'])}). "
                f"{TEXTO_RUPTURA[0].upper() + TEXTO_RUPTURA[1:]}.")
    return (f"En el {indice} el régimen no cambia cómo aguantan los muros: {_pct(efecto['encima'])} por encima de la "
            f"frontera (n={efecto['n_encima']}) y {_pct(efecto['debajo'])} por debajo (n={efecto['n_debajo']}); "
            f"control {_pct(efecto['control_encima'])} y {_pct(efecto['control_debajo'])}. Sin etiquetas de régimen.")
