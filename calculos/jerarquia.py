"""
Limpieza visual de la escalera (Fase 2): GEX bruta estructural, fusión de
niveles cercanos, categoría de cada nivel y ancho de las zonas.

- GEX bruta estructural = |GEX de calls| + |GEX de puts| del nivel SIN la
  0DTE (las opciones que vencen en la propia sesión). Mide cuánta gamma
  "de fondo" hay en el nivel, sin que se anulen calls y puts y sin el ruido
  de lo que vence hoy. La 0DTE va aparte (zonas grises, niveles.zonas_0dte)
  y la GEX neta sigue para el régimen y la frontera.
- Fusión: los niveles a 2 × ancho de zona o menos (de centro a centro) se
  quedan en uno: el de mayor GEX bruta estructural absorbe a sus vecinos,
  sumando su OI y su GEX. Sin cadenas, y muros/imán nunca son absorbidos.
- Categoría por percentil de la GEX bruta estructural de los niveles del
  día: principal (>= p80, o muro/imán/frontera), secundario (p40-p80) y
  menor (< p40). Los niveles que solo son "gamma 0DTE" quedan fuera
  (categoría "0dte", en gris).
- Ancho de zona: ± proporción del movimiento esperado de 1σ, entre un
  mínimo y un máximo. La zona de un nivel fundido se amplía hasta cubrir
  los strikes que ha absorbido (zona_de).
Todos los números están en config.yaml (sección "jerarquia").
"""
import numpy as np

from calculos.niveles import ORDEN_TIPOS

CATEGORIAS = ["principal", "secundario", "menor", "0dte"]


def gex_bruta_estructural(filas, fecha_sesion):
    """|GEX calls| + |GEX puts| de las filas de un nivel, sin la 0DTE."""
    f = filas[filas["vencimiento"] != fecha_sesion]
    return float(abs(f.loc[f["tipo"] == "call", "gex"].sum()) + abs(f.loc[f["tipo"] == "put", "gex"].sum()))


def _centro(z):
    return (z["desde"] + z["hasta"]) / 2


def _sumar_desglose(a, b):
    """Une dos listas "por_vencimiento" sumando por fecha."""
    juntos = {}
    for v in (a or []) + (b or []):
        x = juntos.setdefault(v["vencimiento"], {"vencimiento": v["vencimiento"], "productos": [],
                                                  "gex": 0.0, "oi_calls": 0.0, "oi_puts": 0.0})
        x["productos"] = sorted(set(x["productos"]) | set(v.get("productos", [])))
        for k in ("gex", "oi_calls", "oi_puts"):
            x[k] += v[k]
    return [juntos[k] for k in sorted(juntos)]


def _absorber(z, otro):
    """El nivel z se queda con el OI, la GEX y los tipos de `otro`."""
    for k in ("oi_calls", "oi_puts", "gex_calls", "gex_puts", "gex_neto", "gex_0dte", "gex_bruta_estructural"):
        z[k] = z.get(k, 0.0) + otro.get(k, 0.0)
    z["gex_abs"] = abs(z["gex_neto"])
    z["por_vencimiento"] = _sumar_desglose(z.get("por_vencimiento"), otro.get("por_vencimiento"))
    if z["por_vencimiento"]:
        z["venc_principal"] = max(z["por_vencimiento"], key=lambda v: v["oi_calls"] + v["oi_puts"])["vencimiento"]
    tipos = [t for t in ORDEN_TIPOS if t in set(z["tipos"]) | set(otro["tipos"])]
    if len(tipos) > 1 and "OI relevante" in tipos:
        tipos.remove("OI relevante")
    z["tipos"] = tipos
    z["notas"] = sorted(set(z["notas"]) | set(otro["notas"]))
    z["fusionados"] = z.get("fusionados", []) + [otro["texto"]] + otro.get("fusionados", [])
    # Strikes absorbidos (la zona del nivel fundido debe cubrirlos)
    z["fusion_desde"] = min(z.get("fusion_desde", otro["desde"]), otro["desde"])
    z["fusion_hasta"] = max(z.get("fusion_hasta", otro["hasta"]), otro["hasta"])


def fusionar(zonas, distancia, nunca_absorbidos=()):
    """Une los niveles cercanos, sin cadenas.

    Se recorren de mayor a menor GEX bruta estructural. Cada nivel que no
    haya sido absorbido absorbe a los vecinos a `distancia` puntos o menos
    (de centro a centro, distancia incluida) que todavía no se hayan
    recorrido ni absorbido. Un nivel absorbido no absorbe a nadie, y los
    que tienen un tipo de `nunca_absorbidos` (muros, imán) no son absorbidos
    nunca: si dos coinciden, se quedan los dos. Devuelve la lista nueva,
    ordenada por precio.
    """
    orden = [dict(z, fusionados=list(z.get("fusionados", [])))
             for z in sorted(zonas, key=lambda z: -z["gex_bruta_estructural"])]
    protegido = [bool(set(nunca_absorbidos) & set(z["tipos"])) for z in orden]
    absorbido = [False] * len(orden)
    for i, z in enumerate(orden):
        if absorbido[i]:
            continue
        for k in range(i + 1, len(orden)):          # solo los que aún no se han recorrido
            if not absorbido[k] and not protegido[k] and abs(_centro(orden[k]) - _centro(z)) <= distancia:
                _absorber(z, orden[k])
                absorbido[k] = True
    return sorted((z for z, a in zip(orden, absorbido) if not a), key=lambda z: z["desde"])


def categorizar(zonas, cfg):
    """Pone z["categoria"] y devuelve los umbrales usados {p_secundario, p_principal}."""
    con_tipo = [z for z in zonas if z["tipos"]]
    valores = [z["gex_bruta_estructural"] for z in con_tipo]
    p_alto = float(np.percentile(valores, cfg["percentil_principal"])) if valores else 0.0
    p_bajo = float(np.percentile(valores, cfg["percentil_secundario"])) if valores else 0.0
    siempre = set(cfg["tipos_siempre_principales"])
    for z in zonas:
        if not z["tipos"]:
            z["categoria"] = "0dte"
        elif siempre & set(z["tipos"]) or z["gex_bruta_estructural"] >= p_alto:
            z["categoria"] = "principal"
        elif z["gex_bruta_estructural"] >= p_bajo:
            z["categoria"] = "secundario"
        else:
            z["categoria"] = "menor"
    return {"p_secundario": p_bajo, "p_principal": p_alto}


def ancho_zona(desviacion, cfg):
    """Media anchura de las zonas (puntos): proporción de 1σ, entre mín. y máx."""
    return float(min(max(cfg["ancho_proporcion_sigma"] * desviacion, cfg["ancho_minimo"]), cfg["ancho_maximo"]))


def zona_de(z, ancho):
    """(desde, hasta) de la zona dibujada: ± ancho alrededor del nivel y, si
    ha absorbido otros, hasta cubrir sus strikes."""
    desde, hasta = z["desde"] - ancho, z["hasta"] + ancho
    if "fusion_desde" in z:
        desde, hasta = min(desde, z["fusion_desde"]), max(hasta, z["fusion_hasta"])
    return desde, hasta
