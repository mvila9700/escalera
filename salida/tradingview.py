"""
Línea de texto con los niveles del día para el indicador de TradingView
(tradingview/escalera_nq.pine).

Formato (una sola línea, campos separados por "|"):

    ESCNQ1|fecha=2026-10-26|contrato=NQZ2026|roll=2026-12-10|ref=29118|flip=29575
          |68=28616-29619|95=28115-30121|C=30000|R=29900|AP=28500|OG=29100-29130

- ESCNQ1: identificador y versión del formato.
- fecha: sesión para la que valen los niveles (el indicador la enseña en una
  esquina y avisa si no es la del gráfico).
- contrato: contrato de futuros de los niveles (el indicador avisa si el
  gráfico es de otro).
- Cada nivel: letras de sus tipos = precio o zona "desde-hasta".
    C Muro de calls · P Muro de puts · R Resistencia · I Imán
    A Aceleración · O OI relevante · G gamma 0DTE (zona gris, no es nivel)
  El primer tipo da el color (mismo orden que la página).
- Opcional tras "@": calls/puts abiertas, p. ej. A=29300@181/219 (se ven en la tarjeta).
- Opcional tras "~": estado del día (R respetado · X roto · T tocado · N no
  alcanzado), p. ej. A=29300@181/219~R. Sin estado, la tarjeta muestra "—".

Formato ESC2 (Fase 2, versionado y multi-índice). Mismos campos de cabecera
más "sym" (índice: NQ, ES...) y "w" (media anchura de las zonas, en puntos):

    ESC2|sym=NQ|fecha=2026-10-26|contrato=NQZ2026|roll=2026-12-10|ref=29118
        |flip=29575|68=28616-29619|95=28115-30121|w=25
        |L=AP:28500-28500:P:177/2864:261027_26,261030_25,261218_18:R:28475-28525|...
        |Z=29085-29135

Cada nivel es "L=" + siete (u ocho) campos separados por ":":
    1. letras de sus tipos (como en ESCNQ1)
    2. desde-hasta (la zona dibujada es desde-w .. hasta+w)
    3. categoría: P principal · S secundario · M menor · G solo gamma 0DTE
    4. calls/puts abiertas
    5. desglose por vencimiento: hasta 3 "AAMMDD_%" (peso de cada vencimiento
       en la gamma del nivel, de mayor a menor) o "-"
    6. estado del día (R/X/T/N, como en ESCNQ1) o "-"
    7. zona dibujada desde-hasta (ya con el ancho; en un nivel fundido cubre
       los strikes absorbidos). Si falta, la zona es desde-w .. hasta+w.
       La etiqueta de la escala va en el centro del campo 2.
    8. (opcional, con vista corta) muros cortos dentro de la zona: c (calls), p
       (puts), cp (los dos) o "-"
Zonas grises de gamma 0DTE (no son niveles, sin etiqueta en la escala):
    Z=desde-hasta (zona dibujada)
Vista corta (opcional; solo vencimientos de los próximos N días), en la cabecera:
    fc=frontera corta · mc=muro de calls corto · mp=muro de puts corto
    rc=régimen combinado (P positivo corto y completo · N negativo · T transición)
    rf=desde-hasta (franja entre las dos fronteras, solo con T)
Vanna y charm (opcionales, en la cabecera; los indicadores antiguos los ignoran):
    ch=V:F:1520:V1300:V220  charm: flujo hacia el cierre (C compra · V venta · N neutro),
                            fuerza (F fuerte · D débil), contratos por día del total, de la
                            0DTE y del resto (cada uno con su dirección)
    va=C:850                vanna: flujo si BAJA la IV (C compra · V venta · N neutro) y
                            contratos por punto de IV
    9. (opcional, tras el campo 8; si no hay vista corta el 8 va como "-") efecto del
       charm en muros e imán: F lo refuerza · D lo debilita (en minúscula si el charm
       es débil) o "-"
Confluencia con QQQ/NDX (opcional; calculos/confluencia.py):
    10. (tras el 9; los 8 y 9 van como "-" si faltan) ★: Q (QQQ), N (NDX), QN (los dos) o "-"
    cq=distancia         en la cabecera: el texto trae confluencia (distancia en puntos NQ)
    LQ=desde-hasta:Q|N   niveles que solo tienen QQQ/NDX (en precio NQ), para «Mostrar niveles solo QQQ»
Volumen de los días anteriores (opcional; SOLO referencia visual, no son niveles de la escalera):
    VP=tipo:precio:AAAAMMDD   VAL, VAH o POC de una de las 5 sesiones anteriores que el precio aún no
                              ha vuelto a tocar (tareas/volumen_previo.py), p. ej. VP=VAL:27650:20261109.
                              Los indicadores antiguos lo ignoran.
Gamma en vivo (opcional, al final): gv=… con calls, puts y σ√T por strike y grupo de
vencimientos para que el indicador recalcule frontera, régimen y fuerza de los niveles con el
precio actual (formato en salida/gamma_vivo.py). Los indicadores antiguos lo ignoran.
Los niveles van ordenados por importancia (categoría y cercanía al precio):
si el indicador no tiene sitio para todas las etiquetas de la escala,
quita las últimas.
"""
ESTADO = {"Tocado y respetado": "R", "Tocado y roto": "X", "Tocado": "T", "No alcanzado": "N"}
LETRA = {"Muro de calls": "C", "Resistencia": "R", "Imán": "I", "Aceleración": "A",
         "Muro de puts": "P", "OI relevante": "O"}


def _num(x):
    return str(int(round(x)))


def texto(d):
    """Construye la línea a partir del JSON del histórico."""
    m = d["movimiento"]
    contrato = d.get("contrato") or {}
    partes = ["ESCNQ1", f"fecha={d['fecha_sesion']}"]
    if contrato.get("tradingview"):
        partes.append(f"contrato={contrato['tradingview']}")
    if contrato.get("roll"):
        partes.append(f"roll={contrato['roll']}")
    partes.append(f"ref={_num(d['precio_ref'])}")
    if d.get("gamma_flip"):
        partes.append(f"flip={_num(d['gamma_flip'])}")
    partes.append(f"68={_num(m['rango_68'][0])}-{_num(m['rango_68'][1])}")
    partes.append(f"95={_num(m['rango_95'][0])}-{_num(m['rango_95'][1])}")
    for n in d["niveles"]:
        if n["tipos"] == ["Frontera"]:
            continue
        letras = "".join(LETRA[t] for t in n["tipos"] if t in LETRA)
        if n.get("notas"):
            letras += "G"
        if not letras:
            continue
        valor = _num(n["desde"]) if n["desde"] == n["hasta"] else f"{_num(n['desde'])}-{_num(n['hasta'])}"
        if n.get("oi_calls") is not None and n.get("oi_puts") is not None:
            valor += f"@{_num(n['oi_calls'])}/{_num(n['oi_puts'])}"   # contratos para la tarjeta
        if n.get("estado") in ESTADO:
            valor += f"~{ESTADO[n['estado']]}"
        partes.append(f"{letras}={valor}")
    for z in d.get("zonas_0dte") or []:
        partes.append(f"G={_num(z['desde'])}-{_num(z['hasta'])}")
    return "|".join(partes)


CATEGORIA = {"principal": "P", "secundario": "S", "menor": "M", "0dte": "G"}
ORDEN_CATEGORIA = ["principal", "secundario", "menor", "0dte"]
MAX_VENCIMIENTOS = 3


def _desglose(n):
    """'261030_45,261218_30' : peso (%) de cada vencimiento en la gamma del nivel."""
    pv = n.get("por_vencimiento") or []
    total = sum(abs(v["gex"]) for v in pv)
    if not total:
        return "-"
    pesos = sorted(((abs(v["gex"]) / total, v["vencimiento"]) for v in pv), reverse=True)[:MAX_VENCIMIENTOS]
    return ",".join(f"{venc[2:4]}{venc[5:7]}{venc[8:10]}_{round(peso * 100)}" for peso, venc in pesos)


def ordenar_por_importancia(niveles, precio_ref):
    """Principal > secundario > menor > 0DTE; dentro de cada una, el más cercano al precio."""
    return sorted(niveles, key=lambda n: (ORDEN_CATEGORIA.index(n.get("categoria") or "menor"),
                                          abs((n["desde"] + n["hasta"]) / 2 - precio_ref)))


def _precio(x, decimales):
    """Precio para ESC2: entero (NQ) o con hasta `decimales` decimales (ES), sin ceros sobrantes."""
    if not decimales:
        return _num(x)
    return f"{x:.{decimales}f}".rstrip("0").rstrip(".")


def texto_esc2(d, sym=None, decimales=None):
    """Línea ESC2 a partir del JSON del histórico.

    decimales: decimales de los precios (zonas, w, niveles, ref, flip, rangos).
    Por defecto, los del índice en config.yaml (indices -> X -> tradingview_decimales;
    el NQ no lo tiene: enteros, como siempre)."""
    from calculos.config import cargar
    config = cargar()
    if sym is None:
        sym = config.get("indice", "NQ")
    if decimales is None:
        decimales = config["indices"].get(sym, {}).get("tradingview_decimales", 0)
    def f(x):
        return _precio(x, decimales)
    m = d["movimiento"]
    contrato = d.get("contrato") or {}
    partes = ["ESC2", f"sym={sym}", f"fecha={d['fecha_sesion']}"]
    if contrato.get("tradingview"):
        partes.append(f"contrato={contrato['tradingview']}")
    if contrato.get("roll"):
        partes.append(f"roll={contrato['roll']}")
    partes.append(f"ref={f(d['precio_ref'])}")
    if d.get("gamma_flip"):
        partes.append(f"flip={f(d['gamma_flip'])}")
    partes.append(f"68={f(m['rango_68'][0])}-{f(m['rango_68'][1])}")
    partes.append(f"95={f(m['rango_95'][0])}-{f(m['rango_95'][1])}")
    partes.append(f"w={f(d['jerarquia']['ancho_zona'])}")
    vista = d.get("vista_corta")
    if vista:
        if vista.get("frontera"):
            partes.append(f"fc={f(vista['frontera'])}")
        if vista.get("muro_calls"):
            partes.append(f"mc={f(vista['muro_calls']['strike'])}")
        if vista.get("muro_puts"):
            partes.append(f"mp={f(vista['muro_puts']['strike'])}")
        comb = vista.get("combinado") or {}
        if comb:
            partes.append("rc=" + {"positivo": "P", "negativo": "N", "transicion": "T"}[comb["tipo"]])
            if comb.get("franja"):
                partes.append(f"rf={f(comb['franja'][0])}-{f(comb['franja'][1])}")
    vch = d.get("vanna_charm")
    if vch:
        partes.extend(campos_vanna_charm(vch))
    if d.get("confluencia"):
        partes.append(f"cq={f(d['confluencia']['distancia'])}")   # trae ★ (distancia en puntos)
    fb = d.get("fiabilidad")
    if fb and fb.get("estudio"):
        partes.append("fb=" + ("R" if fb.get("hay") else "N"))   # R: el régimen cambia los muros (etiquetas)
    niveles = [n for n in d["niveles"] if n["tipos"] != ["Frontera"]]
    for n in ordenar_por_importancia(niveles, d["precio_ref"]):
        letras = "".join(LETRA[t] for t in n["tipos"] if t in LETRA) + ("G" if n.get("notas") else "")
        if not letras:
            continue
        campos = [
            letras,
            f"{f(n['desde'])}-{f(n['hasta'])}",
            CATEGORIA.get(n.get("categoria"), "M"),
            f"{_num(n['oi_calls'] or 0)}/{_num(n['oi_puts'] or 0)}",
            _desglose(n),
            ESTADO.get(n.get("estado"), "-"),
        ]
        if n.get("zona_desde") is not None:
            campos.append(f"{f(n['zona_desde'])}-{f(n['zona_hasta'])}")
            if vista:
                marca = "".join(lado[0] for lado in n.get("muro_corto") or [])
                campos.append(marca or "-")
            if vch:
                if not vista:
                    campos.append("-")
                campos.append(_efecto(n.get("efecto_charm"), vch))
            if d.get("confluencia"):
                from calculos.confluencia import letras as letras_conf
                campos += ["-"] * (9 - len(campos))             # los campos 8 y 9, si faltan
                campos.append(letras_conf(n.get("confluencia")))
            if fb:
                # 11.º campo: clasificación (calculos/fiabilidad.py): F muro fiable · M muro · B muro débil
                # (ruptura) · O OI máximo objetivo (≤ 0,25σ) · L OI máximo lejos · c contexto (un escalón menos)
                campos += ["-"] * (10 - len(campos))
                campos.append(n.get("codigo_fiabilidad") or "-")
        partes.append("L=" + ":".join(campos))
    # Escalera extendida (de 2σ a 4σ, opcional): "LX=" con los 7 primeros campos de "L="
    for n in ordenar_por_importancia(d.get("niveles_extendidos") or [], d["precio_ref"]):
        letras = "".join(LETRA[t] for t in n["tipos"] if t in LETRA)
        if letras:
            partes.append("LX=" + ":".join([
                letras, f"{f(n['desde'])}-{f(n['hasta'])}", CATEGORIA.get(n.get("categoria"), "M"),
                f"{_num(n['oi_calls'] or 0)}/{_num(n['oi_puts'] or 0)}", _desglose(n),
                ESTADO.get(n.get("estado"), "-"), f"{f(n['zona_desde'])}-{f(n['zona_hasta'])}"]))
    for z in d.get("zonas_0dte") or []:
        partes.append(f"Z={f(z['zona_desde'])}-{f(z['zona_hasta'])}")
    for e in (d.get("confluencia") or {}).get("solo") or []:
        partes.append(f"LQ={f(e['desde'])}-{f(e['hasta'])}:{e['fuente'][0]}")   # solo QQQ (Q) o NDX (N)
    for n in (d.get("volumen_previo") or {}).get("niveles") or []:
        partes.append(f"VP={n['tipo']}:{f(n['precio'])}:{str(n['dia']).replace('-', '')}")   # solo referencia
    if d.get("gamma_vivo"):
        partes.append(d["gamma_vivo"])      # "gv=…": al final (opcional, salida/gamma_vivo.py)
    return "|".join(partes)


DIRECCION = {"compra": "C", "venta": "V", "neutro": "N"}


def _dir_num(flujo):
    """Contratos con su dirección: +1300 (compran) → "C1300"; −220 → "V220"."""
    return ("C" if flujo > 0 else "V" if flujo < 0 else "N") + _num(abs(flujo))


def campos_vanna_charm(v):
    """["ch=…", "va=…"] para la cabecera del ESC2 (el flujo es el contrario de la CEX)."""
    ch, va = v["charm"], v["vanna"]
    return [f"ch={DIRECCION[ch['direccion']]}:{'F' if ch['fuerte'] else 'D'}:{_num(abs(ch['flujo']))}"
            f":{_dir_num(-v['cex_0dte'])}:{_dir_num(-v['cex_resto'])}",
            f"va={DIRECCION[va['direccion']]}:{_num(abs(va['flujo_si_baja_iv']))}"]


def _efecto(efecto, v):
    letra = {"refuerza": "F", "debilita": "D"}.get(efecto)
    if not letra:
        return "-"
    return letra if v["charm"]["fuerte"] else letra.lower()


def sin_vanna_charm(texto):
    """ESC2 sin los campos opcionales nuevos (vanna y charm: ch=, va= y el 9.º campo de cada L=;
    gamma en vivo: gv=; confluencia: cq=, LQ=; escalera extendida: LX=; volumen previo: VP=):
    sirve para comparar con textos guardados antes de añadirlos. Un 8.º campo "-" que solo
    estaba para dejar sitio al 9.º también se quita si no hay vista corta (sin "fc"/"rc")."""
    partes = [p for p in texto.strip().split("|") if not p.startswith(("ch=", "va=", "gv=", "LQ=", "cq=", "LX=", "fb=", "VP="))]
    con_vista = any(p.startswith(("fc=", "mc=", "mp=", "rc=")) for p in partes)
    out = []
    for p in partes:
        if p.startswith("L="):
            c = p[2:].split(":")
            if len(c) >= 9:
                c = c[:8] if con_vista else c[:7]
            p = "L=" + ":".join(c)
        out.append(p)
    return "|".join(out)
