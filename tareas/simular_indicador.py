"""
Simulación del indicador «Escalera» (Fase F-3) con velas reales: copia en Python de las reglas de
fiabilidad en vivo de tradingview/escalera_nq.pine, para probarlas y para ver cómo quedaría el gráfico.
NO es una captura de TradingView (aquí no se puede compilar Pine).

    .venv/bin/python -m tareas.simular_indicador datos/historico/2026-11-12_esc2.txt velas.csv salida.png

velas.csv: columnas t (hora de Nueva York), open, high, low, close (1 minuto).
"""
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from salida import gamma_vivo

LETRA_COLOR = {"C": (30, 58, 95), "P": (139, 30, 30), "R": (79, 79, 107), "I": (31, 92, 69),
               "A": (176, 96, 30), "O": (122, 116, 104)}
PRIORIDAD = "CPRIAO"
GRIS = (142, 135, 122)
NOMBRE = {"C": "muro de calls", "P": "muro de puts", "R": "resistencia", "I": "OI máximo", "A": "aceleración",
          "O": "OI relevante"}


def leer_esc2(texto):
    """(cabecera, niveles) con los campos que usa el indicador (incluido el 11.º)."""
    partes = texto.strip().split("|")
    cab, niveles = {}, []
    for p in partes[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        if k == "L":
            c = v.split(":")
            lo, hi = (float(x) for x in (c[1].split("-") + c[1].split("-"))[:2])
            zlo, zhi = (float(x) for x in c[6].split("-")) if len(c) >= 7 else (lo, hi)
            niveles.append({"letras": c[0], "lo": lo, "hi": hi, "cat": {"P": 1, "S": 2, "M": 3}.get(c[2], 4),
                            "zlo": zlo, "zhi": zhi, "fb": (("x" if c[10] == "-" else c[10]) if len(c) >= 11 else "")})
        else:
            cab[k] = v
    return cab, niveles


def estado_vivo(n, fb, frontera, precio, sigma, dia_positivo, objetivo_sigmas=0.25, grosor=(2, 1)):
    """Lo mismo que el bucle de dibujo del Pine: (estilo, grosor, rótulo, contexto)."""
    ancho = grosor[0] if n["cat"] == 1 else grosor[1] if n["cat"] == 2 else 1
    estilo, rotulo, contexto = "solido", "", False
    if not n["fb"]:
        return estilo, ancho, rotulo, contexto
    centro = (n["lo"] + n["hi"]) / 2
    muro = "C" in n["letras"] or "P" in n["letras"]
    if muro and fb == "R" and frontera is not None:
        if centro < frontera:
            estilo, rotulo = "discontinuo", "ruptura"
        elif dia_positivo:
            ancho += 1
    elif "I" in n["letras"] and not muro:
        if sigma and abs(centro - precio) <= objetivo_sigmas * sigma:
            rotulo = "objetivo"
        else:
            ancho = 1
    elif "c" in n["fb"]:
        ancho, contexto = 1, True
    return estilo, ancho, rotulo, contexto


def esquina(fb, frontera, precio, dia_positivo):
    if fb != "R" or frontera is None:
        return None, None
    if precio >= frontera:
        return (("Muros fiables (precio por encima de la frontera, día positivo)", (12, 163, 12)) if dia_positivo
                else ("Muros normales (precio por encima de la frontera, día negativo)", (220, 220, 220)))
    return "Todo rompe más (precio por debajo de la frontera)", (208, 59, 59)


def simular(ruta_esc2, ruta_velas, ruta_png, desde="06:00"):
    from PIL import Image, ImageDraw, ImageFont
    texto = open(ruta_esc2, encoding="utf-8").read()
    cab, niveles = leer_esc2(texto)
    v = pd.read_csv(ruta_velas, parse_dates=["t"])
    v["t"] = pd.to_datetime(v["t"], utc=True).dt.tz_convert("America/New_York")
    v = v[v["t"].dt.strftime("%H:%M") >= desde].set_index("t")
    v5 = v.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    precio = float(v["close"].iloc[-1])
    lo68, hi68 = (float(x) for x in cab["68"].split("-"))
    sigma = (hi68 - lo68) / 2
    ref, flip = float(cab["ref"]), float(cab["flip"])
    dia_pos = ref >= flip
    b = gamma_vivo.leer(cab["gv"]) if "gv" in cab else None
    ahora = v.index[-1]
    horas = (ahora - ahora.replace(hour=9, minute=30, second=0)).total_seconds() / 3600
    frontera = gamma_vivo.frontera(b, precio, sigma, gamma_vivo.s_en_vivo(b, max(horas, 0))) if b else flip
    fb = cab.get("fb", "")

    W, H, M = 1700, 950, 70
    img = Image.new("RGB", (W, H), (19, 23, 34))
    d = ImageDraw.Draw(img, "RGBA")
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 15)
        fg = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 18)
    except OSError:
        f = fg = ImageFont.load_default()
    pmin = min(v5["low"].min(), precio - 1.6 * sigma)
    pmax = max(v5["high"].max(), precio + 1.6 * sigma)
    y = lambda p: M + (pmax - p) / (pmax - pmin) * (H - 2 * M)
    xs = np.linspace(M, W - 380, len(v5))
    ancho_v = max(2, (W - 380 - M) / len(v5) * 0.6)
    # Zonas
    for n in niveles:
        if n["zhi"] < pmin or n["zlo"] > pmax:
            continue
        tipo = next(t for t in PRIORIDAD if t in n["letras"]) if any(t in n["letras"] for t in PRIORIDAD) else "O"
        col = LETRA_COLOR[tipo]
        estilo, grosor, rotulo, ctx = estado_vivo(n, fb, frontera, precio, sigma, dia_pos)
        borde = GRIS if ctx else col
        alfa = {1: 90, 2: 50, 3: 30}.get(n["cat"], 20) // (2 if ctx else 1)
        y1, y2 = y(n["zhi"]), y(n["zlo"])
        d.rectangle([M, y1, W - 370, y2], fill=col + (alfa,))
        brillo = tuple(min(255, int(c * 1.9) + 40) for c in borde)
        for yy in (y1, y2):
            if estilo == "discontinuo":
                for x0 in range(M, W - 370, 16):
                    d.line([x0, yy, x0 + 8, yy], fill=brillo, width=grosor + 1)
            else:
                d.line([M, yy, W - 370, yy], fill=brillo + ((140,) if ctx else (255,)), width=grosor + (1 if not ctx else 0))
        nombre = " · ".join(NOMBRE[t] for t in n["letras"] if t in NOMBRE)
        etiqueta = f"{(n['lo'] + n['hi']) / 2:,.0f}".replace(",", ".") + f"  {nombre}" + (f"  · {rotulo}" if rotulo else "")
        d.text((W - 365, (y1 + y2) / 2 - 8), etiqueta, fill=(255, 120, 120) if rotulo == "ruptura" else
               (120, 230, 160) if rotulo == "objetivo" else (200, 200, 200), font=f)
    # Fronteras
    for valor, col, txt in ((flip, (217, 89, 38), "frontera apertura"), (frontera, (255, 160, 60), "frontera en vivo")):
        if valor and pmin < valor < pmax:
            for x0 in range(M, W - 370, 10):
                d.line([x0, y(valor), x0 + 5, y(valor)], fill=col, width=2)
            d.text((M + 5, y(valor) - 18), f"{txt} {valor:,.0f}".replace(",", "."), fill=col, font=f)
    # Velas
    for x, (_, r) in zip(xs, v5.iterrows()):
        sube = r["close"] >= r["open"]
        c = (38, 166, 154) if sube else (239, 83, 80)
        d.line([x, y(r["high"]), x, y(r["low"])], fill=c, width=1)
        d.rectangle([x - ancho_v / 2, y(max(r["open"], r["close"])), x + ancho_v / 2, y(min(r["open"], r["close"]))], fill=c)
    # Esquina
    txt, col = esquina(fb, frontera, precio, dia_pos)
    d.rectangle([M, 12, M + 760, 40 + (24 if txt else 0)], fill=(0, 0, 0, 160))
    d.text((M + 8, 16), f"SIMULACIÓN (no es TradingView) · ESCALERA {cab.get('sym')} · {cab.get('fecha')} · "
                        f"precio {precio:,.2f} a las {ahora:%H:%M} NY · fb={fb or '—'}".replace(",", "X").replace(".", ",").replace("X", "."),
           fill=(230, 230, 230), font=f)
    if txt:
        d.text((M + 8, 40), txt, fill=col, font=fg)
    img.save(ruta_png)
    return {"precio": precio, "frontera_vivo": frontera, "frontera_apertura": flip, "dia_positivo": dia_pos, "fb": fb,
            "esquina": txt, "niveles": [(n["letras"], (n["lo"] + n["hi"]) / 2, *estado_vivo(n, fb, frontera, precio, sigma, dia_pos))
                                       for n in niveles]}


if __name__ == "__main__":
    r = simular(*sys.argv[1:4])
    print({k: v for k, v in r.items() if k != "niveles"})
    for x in r["niveles"]:
        print("  ", x)
