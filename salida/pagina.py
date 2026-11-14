"""
Genera la página HTML (estilo oscuro) a partir del JSON del histórico.

- salida/html/escalera_AAAA-MM-DD.html   una página por sesión (NQ)
- salida/escalera_hoy.html               la última (la que se abre cada día)
- salida/html/escalera_es_AAAA-MM-DD.html y salida/escalera_es_hoy.html (ES)

La página no necesita internet: los gráficos se dibujan con un poco de
JavaScript incluido en el propio archivo.
"""
import html
import json
from datetime import date

from calculos import config as configuracion
from calculos.config import RAIZ
from calculos.niveles import QUE_SUELE_HACER

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

CLASE_TIPO = {
    "Muro de calls": "t-calls", "Resistencia": "t-resist", "Imán": "t-iman",
    "Aceleración": "t-acel", "Muro de puts": "t-puts", "Frontera": "t-frontera",
    "OI relevante": "t-oi",
}

ESTADOS = {
    "No alcanzado": ("e-no", "○"),
    "Tocado": ("e-tocado", "●"),
    "Tocado y respetado": ("e-respetado", "✓"),
    "Tocado y roto": ("e-roto", "✕"),
}


def _fecha_larga(iso):
    d = date.fromisoformat(iso)
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


def _fecha_corta(iso):
    d = date.fromisoformat(iso)
    return f"{DIAS[d.weekday()][:3]} {d.day} {MESES[d.month - 1][:3]}"


def _n(x, dec=0):
    """Número con punto de miles y coma decimal (formato español)."""
    if x is None:
        return "—"
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _precio(x, indice="NQ"):
    """Precio de una zona: entero en el NQ; en el ES, con los decimales que haga
    falta (hasta 2: 7.797,5), porque sus zonas son de pocos puntos."""
    dec = configuracion.cargar()["indices"].get(indice, {}).get("tradingview_decimales", 0)
    if not dec or float(x).is_integer():
        return _n(x)
    texto = _n(x, dec)
    return texto.rstrip("0").rstrip(",") if "," in texto else texto


def _e(texto):
    return html.escape(str(texto))


NOMBRE_CATEGORIA = {"principal": "Principal", "secundario": "Secundario", "menor": "Menor", "0dte": "0DTE"}
AYUDA_CATEGORIA = {
    "principal": "Muro, imán o frontera, o de los que más gamma estructural tienen del día (percentil 80 o más)",
    "secundario": "Gamma estructural media del día (entre el percentil 40 y el 80)",
    "menor": "Poca gamma estructural comparada con el resto de niveles del día (menos del percentil 40)",
    "0dte": "Solo gamma de las opciones que vencen hoy: no es un nivel",
}


NOMBRE_INDICE = {"NQ": "E-mini Nasdaq 100", "ES": "E-mini S&P 500"}


def _config_de(indice):
    return configuracion.para_indice(configuracion.cargar(), indice)


def _nombre_archivo(indice, fecha=None):
    """escalera_hoy.html / escalera_2026-10-26.html (NQ); escalera_es_hoy.html... (ES)."""
    base = "escalera" if indice == "NQ" else f"escalera_{indice.lower()}"
    return f"{base}_{fecha}.html" if fecha else f"{base}_hoy.html"


NOMBRE_TIPO = {"Imán": "OI máximo"}          # el imán se muestra como «OI máximo» (Fase F-2)
CLASE_MURO = {"fiable": "t-fiable", "muro": "t-muro", "ruptura": "t-ruptura"}


def _fiabilidad(n):
    """Celda «Fiabilidad histórica»: % de rechazo de su tipo, régimen local y papel (calculos/fiabilidad.py)."""
    f = n.get("fiabilidad")
    if not f:
        return '<span class="muted">—</span>'
    tipo = f["tipo"].replace("Imán", "OI máximo")
    lado = "por encima" if f.get("local") == "positiva" else "por debajo"
    ayuda = (f"{tipo} · régimen local {f.get('local') or '¿?'} (nivel {lado} de la frontera) · papel {f['papel']}. "
             "% de rechazo al primer toque (0,2σ) frente a 20 zonas de control a la misma distancia de la apertura.")
    clase = "muted"
    if f.get("ventaja") is not None and (f.get("n") or 0) >= 20:
        clase = "f-mas" if f["ventaja"] >= 5 else "f-menos" if f["ventaja"] <= -5 else ""
    return (f'<span class="{clase}" title="{_e(ayuda)}">{_e(f["texto"])}</span>'
            f'<div class="sub">{_e(tipo)} · {_e(f.get("local") or "")} · {_e(f["papel"])}</div>')


def _fila_nivel(n, indice="NQ"):
    tipos = "".join(
        f'<span class="tag {CLASE_TIPO[t]}" title="{_e(QUE_SUELE_HACER[t])}">{_e(NOMBRE_TIPO.get(t, t))}</span>'
        for t in n["tipos"])
    for et in n.get("etiquetas_fiabilidad") or []:
        from calculos.fiabilidad import ETIQUETAS_MURO
        clase = CLASE_MURO.get(n.get("estado_muro"), "t-muro") if et in ETIQUETAS_MURO else "t-oimax"
        tipos += f'<span class="tag {clase}">{_e(et)}</span>'
    explicacion = " ".join(QUE_SUELE_HACER[t] for t in n["tipos"][:1])
    if n.get("estado_muro") == "ruptura" and n.get("etiquetas_fiabilidad"):
        from calculos.fiabilidad import TEXTO_RUPTURA
        explicacion = TEXTO_RUPTURA[0].upper() + TEXTO_RUPTURA[1:] + "."
    if n.get("contexto"):
        explicacion += f" ({n['contexto']})"
    for lado in n.get("muro_corto") or []:
        tipos += (f'<span class="tag t-corto" title="El muro de {lado} de la vista corta (solo los vencimientos de '
                  f'los próximos días) cae dentro de la zona de este nivel">muro corto ({lado})</span>')

    if n["tipos"] == ["Frontera"]:
        que_hay = '<span class="muted">Cambio de gamma positiva a negativa</span>'
    else:
        neta = n["gex_neto"]
        absoluta = n.get("gex_abs", abs(neta) if neta is not None else None)   # JSON antiguos no la traen
        desglose = "\n".join(f'{_fecha_corta(v["vencimiento"])}: {_n(v["gex"] / 1e6)} M$'
                             for v in n.get("por_vencimiento") or [])
        titulo = ("GEX por vencimiento:\n" + desglose) if desglose else ""
        que_hay = (f'{indice}: <b>{_n(n["oi_calls"])}</b> calls · <b>{_n(n["oi_puts"])}</b> puts'
                   f'<div class="sub" title="{_e(titulo)}">GEX neta {_n(neta / 1e6 if neta is not None else None)} M$'
                   f' · |GEX| {_n(absoluta / 1e6 if absoluta is not None else None)} M$'
                   + (f' · sobre todo vto. {_fecha_corta(n["venc_principal"])}' if n["venc_principal"] else "")
                   + "</div>")
    for nota in n["notas"]:
        que_hay += f'<div class="nota-gris" title="Mucha gamma de las opciones que vencen hoy, pero poco open interest: no es un nivel">{_e(nota)}</div>'

    if n.get("estado"):
        clase, icono = ESTADOS.get(n["estado"], ("e-no", "·"))
        hora = f' <span class="muted">{_e(n["hora_toque"])}</span>' if n.get("hora_toque") else ""
        estado = f'<span class="estado {clase}">{icono} {_e(n["estado"])}</span>{hora}'
    else:
        estado = '<span class="muted">Pendiente</span>'
    if n.get("nota"):
        estado += f'<div class="sub">📝 {_e(n["nota"])}</div>'

    cat = n.get("categoria")
    if cat in NOMBRE_CATEGORIA and n["tipos"] != ["Frontera"]:
        bruta = n.get("gex_bruta_estructural")
        que_hay += (f'<div class="sub"><span class="cat c-{cat}" title="{_e(AYUDA_CATEGORIA[cat])}">'
                    f'{NOMBRE_CATEGORIA[cat]}</span> GEX bruta estructural '
                    f'<b class="num">{_n(bruta / 1e6 if bruta is not None else None)} M$</b></div>')
    if n.get("fusionados"):
        que_hay += f'<div class="sub">Incluye el nivel {_e(", ".join(n["fusionados"]))} (fusionado por cercanía)</div>'
    zona = ""
    if n.get("zona_desde") is not None and n["zona_desde"] != n["zona_hasta"]:
        zona = f'<div class="sub">zona {_precio(n["zona_desde"], indice)}–{_precio(n["zona_hasta"], indice)}</div>'

    efecto = n.get("efecto_charm")
    if efecto:
        tipos += (f'<span class="tag t-corto" title="{_e(AYUDA_EFECTO_CHARM[efecto])}">'
                  f'el charm lo {efecto}</span>')

    visual = n.get("categoria_visual") or cat           # contexto: un escalón menos (Fase F-2)
    if visual != cat and cat in NOMBRE_CATEGORIA:
        que_hay += (f'<div class="sub muted">Se muestra como {NOMBRE_CATEGORIA[visual].lower()}: '
                    f'{_e(n.get("contexto") or "contexto")}</div>')
    clases = [c for c in ("fila-gris" if not n["tipos"] else "", f"cat-{visual}" if visual else "",
                          "fila-contexto" if n.get("contexto") else "") if c]
    prob = f'{n["prob_toque"] * 100:.0f} %' if n["prob_toque"] is not None else "—"
    return f"""
      <tr class="{" ".join(clases)}">
        <td class="nivel num">{_estrella(n)}{_e(n["texto"])}{zona}</td>
        <td>{tipos or '<span class="muted">—</span>'}<div class="sub">{_e(explicacion)}</div></td>
        <td>{que_hay}</td>
        <td><span class="muted" title="Sin datos del NDX en la versión 1">Sin datos NDX</span></td>
        <td class="num">{prob}</td>
        <td>{_fiabilidad(n)}</td>
        <td>{estado}</td>
      </tr>"""


def _nota_fiabilidad(d):
    """Qué dicen los datos de este índice (calculos/fiabilidad.py) y dónde ver el estudio."""
    f = d.get("fiabilidad")
    if not f:
        return ""
    indice = d.get("indice", "NQ")
    from calculos.config import RAIZ
    ruta = (RAIZ / "salida" / f"fiabilidad_{indice}.html").as_uri()
    enlace = (f' <a href="{ruta}">Estudio completo</a> ({f["sesiones"]} sesiones, '
              f'{_fecha_corta(f["desde"])} – {_fecha_corta(f["hasta"])}).') if f.get("estudio") else ""
    return (f'<p class="nota-fiab"><b>Fiabilidad histórica.</b> {_e(f["nota_indice"])}{enlace} '
            'Fiabilidad = % de rechazo al primer toque de su tipo, régimen local y papel (techo/suelo), con su n '
            'y la ventaja frente a un precio cualquiera a la misma distancia; con n &lt; '
            f'{f.get("n_minimo", 20)}, «pocos datos». Es lo que pasó en el pasado, no una garantía.</p>')


def _estrella(n):
    """★ si el nivel tiene confluencia con un nivel de QQQ/NDX (en precio NQ)."""
    c = n.get("confluencia")
    if not c:
        return ""
    ayuda = (f"Confluencia: nivel de {' y '.join(c['fuentes'])} (trasladado a precio NQ) a "
             f"{c['distancia']:.0f} puntos, en {_n(c['con'])}")
    return f'<span class="estrella" title="{_e(ayuda)}">★ </span>'


def _fila_precio(d):
    return f"""
      <tr class="fila-precio">
        <td class="nivel num">{_n(d["precio_ref"])}</td>
        <td><span class="tag t-precio">Precio ref.</span></td>
        <td colspan="5">Gamma <b>{d["regimen"]}</b> en este precio · {_e(d["origen_precio"])}</td>
      </tr>"""


def _bloque_estado(d):
    """Tarjeta con el máximo/mínimo del día y de dónde salen."""
    e = d.get("estado_dia")
    if not e:
        return ('<div class="tarjeta estado-dia"><h2>Estado del día: pendiente</h2>'
                '<p class="sub">Se rellena solo en cuanto esté el Daily Bulletin de este día (normalmente es el '
                'PDF que descargas al día siguiente). El máximo y el mínimo del boletín son de la sesión '
                'electrónica completa de Globex (17:00 a 16:00 de Chicago), no solo del horario regular. '
                'Si en datos/entrada hay un CSV de velas de 1 minuto de ese día, se añade la hora del toque '
                'y si el nivel se respetó o se rompió.</p></div>')
    if e.get("velas"):
        velas = (f'Hora del toque y respetado/roto: velas de 1 minuto de <b>{_e(e["velas"])}</b> '
                 f'(ventana {"sesión Globex completa" if e["ventana_velas"] == "globex" else "horario regular 9:30-16:00 NY"}). Horas en hora de Madrid.')
    else:
        velas = ('Sin velas de 1 minuto: solo se distingue «Tocado» / «No alcanzado» (sin hora ni '
                 'respetado/roto). Deja un CSV de velas de ese día en datos/entrada para tenerlo.')
    avisos = "".join(f'<li>{_e(a)}</li>' for a in e.get("avisos", []))
    return (f'<div class="tarjeta estado-dia"><h2>Estado del día</h2>'
            f'<p>Máximo <b class="num">{_n(e["maximo"], 2 if e["maximo"] % 1 else 0)}</b> · '
            f'Mínimo <b class="num">{_n(e["minimo"], 2 if e["minimo"] % 1 else 0)}</b>'
            f' <span class="muted">· {_e(e["fuente"])}</span></p>'
            f'<p class="sub">Alcance: {_e(e["alcance"])}</p><p class="sub">{velas}</p>'
            + (f'<ul class="sub">{avisos}</ul>' if avisos else "") + '</div>')


def _bloque_0dte(d):
    """Zonas grises de gamma 0DTE, aparte de la escalera (no son niveles)."""
    indice = d.get("indice", "NQ")
    zonas = d.get("zonas_0dte")
    if not zonas:
        return ""
    items = "".join(
        f'<li><span class="nota-gris">{_e(z["texto"])}</span> GEX 0DTE '
        f'<b class="num">{_n(z["gex_0dte"] / 1e6)} M$</b> · se dibuja {_precio(z["zona_desde"], indice)}–'
        f'{_precio(z["zona_hasta"], indice)}</li>'
        for z in zonas)
    return (f'<div class="gris-0dte"><h3>Gamma 0DTE — zonas grises (no son niveles)</h3>'
            f'<p class="sub">Strikes donde la gamma de las opciones que vencen hoy es más de la mitad de toda su '
            f'gamma y está entre la más alta del día. Pueden mover el precio durante la sesión, pero desaparecen '
            f'al vencer: no tienen categoría ni etiqueta en la escala.</p><ul>{items}</ul></div>')


REGIMEN_CLASE = {"positivo": "e-respetado", "negativo": "e-roto", "transicion": "aviso-txt"}


def _texto_regimen(comb, indice):
    if not comb:
        return ""
    texto = comb["texto"]
    if comb.get("franja"):
        texto += f" (entre {_precio(comb['franja'][0], indice)} y {_precio(comb['franja'][1], indice)})"
    return texto


AYUDA_EFECTO_CHARM = {
    "refuerza": "El flujo del charm hacia el cierre aleja el precio de este muro (o lo lleva hacia este imán)",
    "debilita": "El flujo del charm hacia el cierre empuja el precio contra este muro (o lo aleja de este imán)",
}
_PALABRA_FLUJO = {"compra": "comprador", "venta": "vendedor", "neutro": "neutro"}


def textos_vanna_charm(v):
    """(línea del charm, línea de la vanna) en texto plano, o None si el JSON no los trae."""
    if not v:
        return None

    def con_dir(flujo):
        return ("compra " if flujo > 0 else "venta " if flujo < 0 else "") + _n(abs(flujo))

    ch, va = v["charm"], v["vanna"]
    fuerza = "fuerte" if ch["fuerte"] else "débil"
    if ch["direccion"] == "neutro":
        charm = "Charm: neutro"
    else:
        charm = f"Charm: {ch['direccion']} hacia el cierre, {fuerza}"
    charm += (f" ({_n(abs(ch['flujo']))} contratos/día; 0DTE {con_dir(-v['cex_0dte'])}"
              f" · resto {con_dir(-v['cex_resto'])})")
    vanna = (f"Vanna: si baja la IV, flujo {_PALABRA_FLUJO[va['direccion']]}"
             f" ({_n(abs(va['flujo_si_baja_iv']))} contratos por punto de IV)")
    return charm, vanna


def _lineas_vanna_charm(d):
    t = textos_vanna_charm(d.get("vanna_charm"))
    if not t:
        return ""
    ayuda = ("Lo que tendrían que comprar o vender los creadores de mercado para seguir cubiertos, con el "
             "mismo supuesto de posición que la GEX (largos de calls, cortos de puts). Charm: por el paso del "
             "tiempo, con precio e IV quietos (calculado en la apertura). Vanna: si la IV baja 1 punto.")
    return (f'<p class="regimen-combinado" title="{_e(ayuda)}">{_e(t[0])}</p>'
            f'<p class="regimen-combinado" title="{_e(ayuda)}">{_e(t[1])}</p>')


def _linea_regimen(d):
    """Régimen combinado, para la cabecera de la página."""
    v = d.get("vista_corta") or {}
    comb = v.get("combinado")
    if not comb:
        return ""
    return (f'<p class="regimen-combinado">Régimen: <b class="{REGIMEN_CLASE[comb["tipo"]]}">'
            f'{_e(_texto_regimen(comb, d.get("indice", "NQ")))}</b> '
            f'<span class="sub">(vista corta de {v["dias"]} días y escalera completa)</span></p>')


def _bloque_vista_corta(d):
    """Recuadro «Vista corta (N días)»: frontera, muros y régimen con solo los
    vencimientos de los próximos N días. No cambia la escalera de abajo."""
    v = d.get("vista_corta")
    if not v:
        return ""
    indice = d.get("indice", "NQ")

    def donde(strike):
        for n in d["niveles"]:
            if n["tipos"] != ["Frontera"] and n.get("zona_desde") is not None and n["zona_desde"] <= strike <= n["zona_hasta"]:
                return f' · coincide con el nivel <b>{_e(n["texto"])}</b>'
        return ' · <span class="muted">no coincide con ningún nivel de la escalera</span>'

    def muro(m, nombre, lado):
        if not m:
            motivo = (v.get("sin_muro") or {}).get(lado, "no hay ninguno")
            return f'<li>{nombre}: <span class="muted">{_e(motivo)}</span></li>'
        distancia = ""
        if m.get("distancia_puntos") is not None:
            sigma = m.get("distancia_sigma")
            distancia = (f' · a {"+" if m["distancia_puntos"] > 0 else ""}{_precio(m["distancia_puntos"], indice)} puntos'
                         + (f' ({_n(sigma, 2)} σ)' if sigma is not None else "") + " del precio")
        return (f'<li>{nombre}: <b class="num">{_n(m["strike"])}</b> · {_n(m["open_interest"])} contratos · '
                f'GEX {_n(m["gex"] / 1e6)} M${distancia}{donde(m["strike"])}</li>')

    comb = v.get("combinado")
    frontera = _precio(v["frontera"], indice) if v.get("frontera") else "no encontrada"
    completa = _precio(d["gamma_flip"], indice) if d.get("gamma_flip") else "no encontrada"
    regimen_corto = {"positiva": "positiva", "negativa": "negativa"}.get(v.get("regimen"), "—")
    return (f'<div class="tarjeta corta"><h2>Vista corta ({v["dias"]} días: del {_fecha_corta(d["fecha_sesion"])} '
            f'al {_fecha_corta(v["hasta"])})</h2>'
            + (f'<p>Régimen combinado: <b class="{REGIMEN_CLASE[comb["tipo"]]}">{_e(_texto_regimen(comb, indice))}</b></p>'
               if comb else "")
            + f'<ul><li>Frontera corta: <b class="num">{frontera}</b> <span class="muted">(completa: {completa})</span> · '
              f'gamma corta {regimen_corto} en el precio</li>'
            + muro(v.get("muro_calls"), "Muro de calls corto (más GEX de calls por encima del precio)", "calls")
            + muro(v.get("muro_puts"), "Muro de puts corto (más GEX de puts por debajo del precio)", "puts")
            + f'</ul><p class="sub">Solo los {len(v["vencimientos"])} vencimientos hasta el {_fecha_corta(v["hasta"])}, '
              f'dentro del rango del 95 %. La escalera de abajo sigue usando todos los vencimientos de su ventana.</p></div>')


def _bloque_desconocidos(d):
    """Productos del boletín que no se identifican (p. ej. SME OPT en el ES):
    no entran en ningún cálculo; se enseña su open interest para saber qué se deja."""
    lista = d.get("productos_desconocidos") or []
    if not lista:
        return ""
    items = "".join(
        f'<li><b>{_e(p["etiqueta"])}</b>: {_n(p["oi_total"])} contratos en total. Strikes con más OI: '
        + ", ".join(f'{_n(f["strike"])} ({_e(f["mes"])} {_e(f["tipo"])}, {_n(f["open_interest"])})'
                    for f in p["strikes_mas_oi"]) + "</li>"
        for p in lista)
    return (f'<div class="tarjeta desconocidos"><h2>Productos sin identificar (no entran en ningún cálculo)</h2>'
            f'<ul>{items}</ul></div>')


def _nota_vencimientos(d):
    """Qué vencimientos suman las calls/puts y la GEX de cada nivel."""
    v = d.get("vencimientos_incluidos")
    if not v:
        return ""
    return (f'<div class="sub" style="text-transform:none;letter-spacing:0;font-weight:400">'
            f'calls/puts y GEX: suma de {len(v)} vencimientos, del {_fecha_corta(v[0])} al {_fecha_corta(v[-1])}</div>')


def _bloque_tv(d):
    """Tarjeta con el texto para el indicador de TradingView y el botón de copiar."""
    texto = d.get("tradingview_esc2") or d.get("tradingview")
    antiguo = d.get("tradingview") if d.get("tradingview_esc2") else None
    if not texto:
        return ""
    c = d.get("contrato") or {}
    indice = d.get("indice", "NQ")
    simbolo = c.get("tradingview", "el contrato de los niveles")
    mini = simbolo.replace(indice, "M" + indice, 1) if simbolo.startswith(indice) else ""
    return (f'<div class="tarjeta tv"><h2>TradingView</h2>'
            f'<div class="tv-fila"><input id="texto-tv" readonly value="{_e(texto)}" aria-label="Texto para TradingView">'
            f'<button id="copiar-tv" type="button" data-campo="texto-tv">Copiar para TradingView</button></div>'
            f'<p class="sub">Pégalo en los ajustes del indicador «Escalera» añadido al gráfico del {indice} (campo «Niveles del día»). '
            f'Úsalo en el gráfico <b>{_e(simbolo)}</b>{" (o " + _e(mini) + ")" if mini else ""}: los niveles salen de '
            f'las opciones sobre ese contrato y están en sus precios. En {indice}1! también vale mientras TradingView '
            f'tenga detrás ese mismo contrato; el indicador avisa si no.</p>'
            + (f'<details class="sub"><summary>¿Tu indicador es la versión anterior? Copia el formato antiguo (ESCNQ1)</summary>'
               f'<div class="tv-fila"><input id="texto-tv-1" readonly value="{_e(antiguo)}" aria-label="Texto ESCNQ1">'
               f'<button type="button" class="secundario" data-campo="texto-tv-1">Copiar ESCNQ1</button></div>'
               f'<p class="sub">El formato antiguo no lleva categorías ni zonas: el indicador antiguo dibuja líneas. '
               f'Actualiza el indicador con el archivo tradingview/escalera_nq.pine para ver las zonas.</p></details>'
               if antiguo else "")
            + '</div>')


def _bloque_recentrado(d):
    """Comprobación del precio actual frente al rango del 68 % y escaleras recentradas del día.

    La página no sabe el precio en vivo: lo escribes en la casilla y avisa si sale del rango (con
    la orden para recentrar). Las recentradas ya generadas salen con su botón de copiar."""
    lo, hi = d["movimiento"]["rango_68"]
    indice = d.get("indice", "NQ")
    lista = d.get("recentradas") or []
    filas = []
    for i, r in enumerate(lista):
        if not r.get("esc2"):
            continue
        r68 = r.get("rango_68") or (None, None)
        hora = (r.get("generado") or "").replace("T", " ")[:16]
        filas.append(
            f'<div class="recentrada"><p><b>Recentrada en <span class="num">{_n(r["precio"], 2 if r["precio"] % 1 else 0)}</span></b>'
            f' <span class="muted">· generada {_e(hora)}</span> · frontera <span class="num">{_n(r["flip"]) if r.get("flip") else "—"}</span>'
            f' · gamma {_e(r.get("regimen") or "—")}'
            + (f' · rango 68 % <span class="num">{_n(r68[0])} – {_n(r68[1])}</span>' if r68[0] is not None else "")
            + f'</p><div class="tv-fila"><input id="texto-rec-{i}" readonly value="{_e(r["esc2"])}" aria-label="ESC2 recentrado">'
            f'<button type="button" data-campo="texto-rec-{i}">Copiar ESC2 recentrado</button></div></div>')
    aviso = ""
    if filas:
        r = lista[0]
        aviso = (f'<p class="aviso-txt" id="aviso-rec-fijo">⚠ El precio salió del rango del 68 % '
                 f'({_n(lo)} – {_n(hi)}): a las {_e((r.get("generado") or "")[11:16])} estaba en '
                 f'{_n(r["precio"], 2 if r["precio"] % 1 else 0)}. Esta escalera se calculó en {_n(d["precio_ref"])}: '
                 'usa la recentrada (la más reciente arriba).</p>')
    orden = f".venv/bin/python -m tareas.escalera --indice {indice} --recentrar --precio "
    return (f'<div class="tarjeta tv recentrar" data-lo="{lo}" data-hi="{hi}" data-orden="{_e(orden)}">'
            f'<h2>Precio actual y escalera recentrada</h2>{aviso}'
            f'<div class="tv-fila"><input id="precio-actual" type="number" step="0.25" inputmode="decimal" '
            f'placeholder="Precio actual del {_e(indice)}" aria-label="Precio actual">'
            f'<span class="sub" style="align-self:center">Rango del 68 %: <span class="num">{_n(lo)} – {_n(hi)}</span></span></div>'
            f'<p class="sub" id="estado-precio">Escribe el precio actual: si sale del rango del 68 %, la página te avisa y '
            f'te da la orden para recentrar la escalera (mismo open interest, centrada en ese precio).</p>'
            f'<div class="tv-fila oculto" id="fila-orden"><input id="orden-rec" readonly aria-label="Orden para recentrar">'
            f'<button type="button" class="secundario" data-campo="orden-rec">Copiar la orden</button></div>'
            + "".join(filas) + '</div>')


def generar(d, error=None):
    """Devuelve el HTML completo. Si `error`, genera la página de error."""
    if error:
        return _pagina_error(error, d)
    m = d["movimiento"]
    indice = d.get("indice", "NQ")
    config = _config_de(indice)
    tramo = config["tramo_puntos"]
    multiplicador = config["multiplicadores"][config["indices"][indice].get("subyacente", indice)]
    filas = []
    precio_puesto = False
    for n in d["niveles"]:
        if not precio_puesto and n["desde"] < d["precio_ref"]:
            filas.append(_fila_precio(d))
            precio_puesto = True
        filas.append(_fila_nivel(n, indice))
    if not precio_puesto:
        filas.append(_fila_precio(d))

    positiva = d["regimen"] == "positiva"
    regimen_html = (
        '<span class="estado e-respetado">✓ Positiva</span><div class="sub">El precio tiende a calmarse</div>'
        if positiva else
        '<span class="estado e-roto">▲▼ Negativa</span><div class="sub">Movimientos más amplios</div>')
    preliminar = ' · <span class="aviso-txt">PRELIMINAR</span>' if d["preliminar"] else ""
    avisos = "".join(f"<li>{_e(a)}</li>" for a in d["avisos"])
    flip = _n(d["gamma_flip"]) if d["gamma_flip"] else "No encontrado"

    graficos = {**d["graficos"], "precio": d["precio_ref"], "flip": d["gamma_flip"]}
    if tramo != 25:                      # el NQ (25) usa los valores por defecto del gráfico
        graficos.update(tramo=tramo, etiquetas=tramo * 5)
    datos_graficos = json.dumps(graficos)
    # Ejemplo para el pie de los gráficos: con tramos de 25, "la barra 29.000 incluye de 29.000 a 29.024"
    ejemplo = int(d["precio_ref"] // (tramo * 40)) * tramo * 40
    contrato = (d.get("contrato") or {}).get("tradingview")
    return PLANTILLA.format(
        titulo=f"Escalera {indice} · {_fecha_corta(d['fecha_sesion'])}",
        indice=indice,
        nombre_indice=_e(NOMBRE_INDICE.get(indice, indice)),
        contrato=(f' · contrato <b class="contrato">{_e(contrato)}</b>' if contrato else ""),
        tramo=tramo,
        pie_tramo=(f"la barra {_n(ejemplo)} incluye los strikes de {_n(ejemplo)} a {_n(ejemplo + tramo - 1)}"),
        multiplicador=_n(multiplicador),
        bloque_desconocidos=_bloque_desconocidos(d),
        bloque_corta=_bloque_vista_corta(d),
        linea_regimen=_linea_regimen(d),
        linea_vanna_charm=_lineas_vanna_charm(d),
        sesion=_fecha_larga(d["fecha_sesion"]),
        datos=_fecha_larga(d["fecha_datos"]),
        preliminar=preliminar,
        fuente=_e(d["fuente"]),
        precio=_n(d["precio_ref"]),
        origen=_e(d["origen_precio"]),
        regimen=regimen_html,
        flip=flip,
        r68=f"{_n(m['rango_68'][0])} – {_n(m['rango_68'][1])}",
        r95=f"{_n(m['rango_95'][0])} – {_n(m['rango_95'][1])}",
        sigma=_n(m["desviacion"]),
        iv=_n(d["iv_atm"] * 100, 1),
        venc_iv=_fecha_corta(d["vencimiento_iv"]),
        straddle=_n(d["straddle"], 1),
        k_straddle=_n(d["strike_straddle"]),
        filas="".join(filas),
        avisos=avisos,
        clase_avisos="" if avisos else "oculto",
        bloque_estado=_bloque_estado(d),
        bloque_tv=_bloque_tv(d),
        bloque_recentrado=_bloque_recentrado(d),
        nota_vencimientos=_nota_vencimientos(d),
        zonas_0dte=_bloque_0dte(d),
        nota_fiabilidad=_nota_fiabilidad(d),
        ventana=(f" · Vencimientos incluidos: hasta el {_fecha_corta(d['vencimiento_limite'])}"
                 if d.get("vencimiento_limite") else ""),
        generado=_e(d["generado"].replace("T", " ")),
        datos_graficos=datos_graficos,
    )


def _pagina_error(error, d=None, indice=None):
    indice = indice or (d or {}).get("indice", "NQ")
    secciones = _config_de(indice)["indices"][indice]["cme_pdf"]["secciones"]
    extra = ""
    if d and d.get("fecha_sesion"):
        extra = (f'<p>Últimos datos disponibles: sesión del {_fecha_larga(d["fecha_sesion"])} '
                 f'(datos del {_fecha_larga(d["fecha_datos"])}). <b>No son de hoy: no los uses como tales.</b></p>')
    texto_secciones = ("el PDF de la sección " + str(secciones[0]) if len(secciones) == 1 else
                       "los PDF de las secciones " + " y ".join(map(str, secciones)))
    return PLANTILLA_ERROR.format(error=_e(error), extra=extra, indice=indice, secciones=texto_secciones)


def escribir(d, error=None, es_la_ultima=True, indice=None):
    """Escribe la página de la sesión y, si es la última, la de hoy
    (escalera_hoy.html del NQ, escalera_es_hoy.html del ES).

    Devuelve la ruta escrita (la de hoy, o la de la sesión si no es la última).
    """
    indice = indice or (d or {}).get("indice", "NQ")
    if d and not error and "recentradas" not in d and d.get("fecha_sesion"):
        from salida import historico
        d = {**d, "recentradas": historico.recentradas(d["fecha_sesion"], indice)}
    contenido = _pagina_error(error, d, indice) if error else generar(d)
    carpeta = RAIZ / "salida" / "html"
    carpeta.mkdir(parents=True, exist_ok=True)
    if not error:
        ruta_sesion = carpeta / _nombre_archivo(indice, d["fecha_sesion"])
        ruta_sesion.write_text(contenido, encoding="utf-8")
        if not es_la_ultima:
            return ruta_sesion
    hoy = RAIZ / "salida" / _nombre_archivo(indice)
    hoy.write_text(contenido, encoding="utf-8")
    return hoy


ESTILOS = """
:root {
  color-scheme: dark;
  --plano: #0d0d0d; --superficie: #1a1a19; --superficie-2: #232321;
  --texto: #f0efec; --texto-2: #c3c2b7; --muted: #898781;
  --linea: #2c2c2a; --eje: #383835; --borde: rgba(255,255,255,0.10);
  --calls: #3987e5; --puts: #e66767;
  --bien: #0ca30c; --serio: #ec835a; --aviso: #fab219; --critico: #d03b3b;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--plano); color: var(--texto);
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 24px; margin: 0 0 2px; }
h2 { font-size: 16px; margin: 0 0 12px; color: var(--texto-2); font-weight: 600; }
.cabecera p { margin: 0; color: var(--texto-2); }
.aviso-txt { color: var(--aviso); font-weight: 700; }
.muted { color: var(--muted); }
.sub { color: var(--muted); font-size: 12.5px; margin-top: 2px; }
.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.tarjeta { background: var(--superficie); border: 1px solid var(--borde); border-radius: 12px; padding: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 20px 0; }
.tile .etq { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.tile .valor { font-size: 22px; font-weight: 650; margin-top: 4px; }
.avisos { border-color: rgba(250,178,25,0.45); margin-bottom: 20px; }
.avisos h2 { color: var(--aviso); }
.avisos ul { margin: 0; padding-left: 20px; color: var(--texto-2); font-size: 13.5px; }
.oculto { display: none; }
.tabla-scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 760px; }
th { text-align: left; color: var(--muted); font-weight: 600; font-size: 12px;
  text-transform: uppercase; letter-spacing: .04em; padding: 8px 10px; border-bottom: 1px solid var(--eje); }
td { padding: 10px; border-bottom: 1px solid var(--linea); vertical-align: top; }
td.nivel { font-size: 17px; font-weight: 650; }
tr.fila-precio td { background: var(--superficie-2); border-top: 1px solid var(--eje); border-bottom: 1px solid var(--eje); }
tr.fila-gris td { color: var(--muted); }
tr.fila-contexto td.nivel { color: var(--texto-2); }
.t-fiable { background: #1a7f37; }
.t-muro { background: #57606a; }
.t-ruptura { background: transparent; color: #d1242f; border: 1px dashed #d1242f; }
.t-oimax { background: transparent; color: var(--texto-2); border: 1px solid var(--eje); }
.f-mas { color: #1a7f37; font-weight: 600; }
.f-menos { color: #d1242f; font-weight: 600; }
.nota-fiab { font-size: 13px; color: var(--texto-2); margin: 10px 2px 0; }
tr.fila-gris td.nivel { font-weight: 500; font-size: 15px; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12.5px; font-weight: 600;
  margin: 0 4px 4px 0; color: #fff; cursor: help; }
.t-calls { background: var(--calls); }
.t-puts { background: var(--puts); }
.t-resist { background: #9085e9; }
.t-iman { background: #199e70; }
.t-acel { background: #c98500; color: #0b0b0b; }
.t-frontera { background: #d95926; }
.t-oi { background: transparent; border: 1px solid var(--texto-2); color: var(--texto); padding: 1px 7px; }
.t-precio { background: var(--eje); color: var(--texto); cursor: default; }
.nota-gris { display: inline-block; margin-top: 4px; padding: 1px 7px; border: 1px solid var(--eje);
  border-radius: 999px; color: var(--muted); font-size: 12px; cursor: help; }
.estado { font-weight: 600; }
.e-respetado { color: var(--bien); }
.e-roto { color: var(--serio); }
.e-no { color: var(--muted); }
.e-tocado { color: var(--texto); }
.estado-dia { margin-bottom: 20px; }
.estado-dia p { margin: 4px 0; }
.pie { color: var(--muted); font-size: 12px; margin: 8px 0 0; }
.tv { margin-bottom: 20px; }
.tv-fila { display: flex; gap: 8px; flex-wrap: wrap; }
.tv-fila input { flex: 1 1 320px; min-width: 0; background: var(--plano); color: var(--texto-2); border: 1px solid var(--eje);
  border-radius: 8px; padding: 8px 10px; font: 12.5px ui-monospace, Menlo, monospace; }
.tv-fila button { background: var(--calls); color: #fff; border: 0; border-radius: 8px; padding: 8px 14px;
  font: 600 14px system-ui, -apple-system, sans-serif; cursor: pointer; }
.tv-fila button:hover { filter: brightness(1.1); }
.tv-fila button.secundario { background: var(--eje); color: var(--texto); }
.tv details { margin-top: 10px; }
.tv summary { cursor: pointer; }
.recentrada { margin-top: 12px; }
.recentrada p { margin: 0 0 6px; }
/* Jerarquía de los niveles: principal / secundario / menor */
tr.cat-principal td.nivel { font-size: 19px; font-weight: 750; box-shadow: inset 4px 0 0 var(--texto); }
tr.cat-secundario td.nivel { box-shadow: inset 2px 0 0 var(--eje); }
tr.cat-menor td { opacity: .62; }
tr.cat-menor td.nivel { font-size: 14.5px; font-weight: 500; }
.cat { display: inline-block; padding: 0 6px; margin-right: 4px; border-radius: 4px; font-size: 11px;
  font-weight: 700; text-transform: uppercase; letter-spacing: .04em; cursor: help; }
.c-principal { background: var(--texto); color: var(--plano); }
.c-secundario { border: 1px solid var(--texto-2); color: var(--texto); }
.c-menor { border: 1px dashed var(--muted); color: var(--muted); }
.c-0dte { border: 1px dotted var(--muted); color: var(--muted); }
.indice { color: var(--calls); }
.nombre-indice { font-size: 15px; font-weight: 500; color: var(--texto-2); margin-left: 6px; }
.contrato { color: var(--aviso); }
.desconocidos { margin-bottom: 20px; }
.corta { margin-bottom: 20px; border-color: rgba(245,197,66,0.45); }
.corta h2 { color: #f5c542; }
.corta ul { margin: 6px 0; padding-left: 20px; color: var(--texto-2); font-size: 14px; }
.corta p { margin: 4px 0; }
.regimen-combinado { margin: 4px 0 0; }
.t-corto { background: transparent; border: 1px dashed #f5c542; color: #f5c542; padding: 1px 7px; }
.desconocidos ul { margin: 0; padding-left: 20px; color: var(--texto-2); font-size: 13.5px; }
.gris-0dte { margin-top: 14px; padding: 12px 14px; border: 1px dashed var(--eje); border-radius: 10px;
  background: rgba(93,92,88,0.12); }
.gris-0dte h3 { margin: 0 0 4px; font-size: 14px; color: var(--texto-2); font-weight: 600; }
.gris-0dte ul { margin: 8px 0 0; padding-left: 18px; color: var(--texto-2); font-size: 13.5px; }
.graficos { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 20px; }
@media (max-width: 820px) { .graficos { grid-template-columns: 1fr; } }
.leyenda { display: flex; gap: 14px; font-size: 12.5px; color: var(--texto-2); margin: -4px 0 8px; }
.leyenda i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
.grafico { position: relative; }
.grafico svg { display: block; width: 100%; }
.tooltip { position: absolute; pointer-events: none; background: var(--superficie-2); border: 1px solid var(--borde);
  border-radius: 8px; padding: 6px 9px; font-size: 12.5px; white-space: nowrap; opacity: 0; transition: opacity .1s; }
.metodo { margin-top: 20px; color: var(--muted); font-size: 12.5px; }
.metodo p { margin: 4px 0; }
"""

GRAFICOS_JS = """
const D = JSON.parse(document.getElementById('datos-graficos').textContent);
const fmt = (x, dec=0) => x.toLocaleString('es-ES', {minimumFractionDigits: dec, maximumFractionDigits: dec});

// Barras horizontales enfrentadas: calls a la derecha, puts a la izquierda.
// El eje vertical es el precio (de arriba abajo, como la escalera).
function barras(id, filas, opc) {
  const cont = document.getElementById(id);
  if (!filas || !filas.length) { cont.innerHTML = '<p class="muted">' + opc.vacio + '</p>'; return; }
  filas = [...filas].sort((a, b) => b.tramo - a.tramo);
  const W = cont.clientWidth, alto = 9, sep = 2, mI = 54, mD = 12, mS = 8, mInf = 26;
  const H = mS + filas.length * (alto + sep) + mInf;
  const maximo = Math.max(...filas.map(f => Math.max(Math.abs(f.call), Math.abs(f.put)))) || 1;
  const x0 = mI + (W - mI - mD) / 2, esc = (W - mI - mD) / 2 / maximo;
  const tramoMax = filas[0].tramo, paso = opc.tramo;
  const y = t => mS + (tramoMax - t) / paso * (alto + sep);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('height', H);
  const el = (tag, at, txt) => { const e = document.createElementNS(ns, tag);
    for (const k in at) e.setAttribute(k, at[k]); if (txt !== undefined) e.textContent = txt; svg.appendChild(e); return e; };
  // rejilla y etiquetas de precio cada 100 puntos (NQ) o cada D.etiquetas (ES)
  filas.forEach(f => { if (f.tramo % (D.etiquetas || 100) === 0) {
    el('line', {x1: mI, x2: W - mD, y1: y(f.tramo) + alto / 2, y2: y(f.tramo) + alto / 2, stroke: 'var(--linea)'});
    el('text', {x: mI - 6, y: y(f.tramo) + alto / 2 + 4, 'text-anchor': 'end', fill: 'var(--muted)', 'font-size': 11}, fmt(f.tramo));
  }});
  el('line', {x1: x0, x2: x0, y1: mS, y2: H - mInf, stroke: 'var(--eje)'});
  // barras (extremo redondeado, anclado en el cero)
  filas.forEach(f => {
    const c = Math.abs(f.call) * esc, p = Math.abs(f.put) * esc, yy = y(f.tramo);
    if (c >= 0.5) el('rect', {x: x0 + 1, y: yy, width: c, height: alto, rx: 3, fill: 'var(--calls)'});
    if (p >= 0.5) el('rect', {x: x0 - 1 - p, y: yy, width: p, height: alto, rx: 3, fill: 'var(--puts)'});
  });
  // líneas de referencia: precio y frontera
  const refs = [{v: D.precio, t: 'Precio ' + fmt(D.precio), c: 'var(--texto-2)'}];
  if (D.flip) refs.push({v: D.flip, t: 'Frontera ' + fmt(D.flip), c: '#d95926'});
  refs.forEach(r => { if (r.v <= tramoMax + paso && r.v >= filas[filas.length - 1].tramo) {
    const yy = y(r.v) + alto + sep;  // el tramo t ocupa de t (abajo) a t+paso (arriba)
    el('line', {x1: mI, x2: W - mD, y1: yy, y2: yy, stroke: r.c, 'stroke-dasharray': '4 3', 'stroke-width': 1.5});
    el('text', {x: W - mD, y: yy - 4, 'text-anchor': 'end', fill: r.c, 'font-size': 11, 'font-weight': 600}, r.t);
  }});
  // eje x
  el('text', {x: mI, y: H - 8, fill: 'var(--muted)', 'font-size': 11}, '← ' + opc.izq);
  el('text', {x: W - mD, y: H - 8, 'text-anchor': 'end', fill: 'var(--muted)', 'font-size': 11}, opc.der + ' →');
  // capa de hover: una banda por tramo (más grande que la barra)
  const tip = document.createElement('div'); tip.className = 'tooltip'; cont.appendChild(tip);
  filas.forEach(f => {
    const r = el('rect', {x: mI, y: y(f.tramo) - sep / 2, width: W - mI - mD, height: alto + sep, fill: 'transparent'});
    r.addEventListener('mousemove', ev => {
      tip.innerHTML = `<b>${fmt(f.tramo)}–${fmt(f.tramo + paso - 1)}</b><br>Calls: ${opc.valor(f.call)}<br>Puts: ${opc.valor(f.put)}<br>Neto: ${opc.valor(f.neto)}`;
      const b = cont.getBoundingClientRect();
      tip.style.left = Math.min(ev.clientX - b.left + 12, W - 170) + 'px';
      tip.style.top = (ev.clientY - b.top + 12) + 'px'; tip.style.opacity = 1;
    });
    r.addEventListener('mouseleave', () => tip.style.opacity = 0);
  });
  cont.prepend(svg);
}

function dibujar() {
  for (const id of ['g-0dte', 'g-oi']) document.getElementById(id).innerHTML = '';
  barras('g-0dte', D.mapa_0dte, {tramo: D.tramo || 25, izq: 'GEX puts', der: 'GEX calls',
    valor: v => fmt(v / 1e6, 1) + ' M$', vacio: 'No hay opciones que venzan en la sesión.'});
  barras('g-oi', D.oi_tramos, {tramo: D.tramo || 25, izq: 'OI puts', der: 'OI calls',
    valor: v => fmt(v) + ' contratos', vacio: 'Sin datos.'});
}
dibujar();

// Botón «Copiar para TradingView»
for (const boton of document.querySelectorAll('button[data-campo]')) {
  const original = boton.textContent;
  boton.addEventListener('click', async () => {
    const campo = document.getElementById(boton.dataset.campo);
    try { await navigator.clipboard.writeText(campo.value); }
    catch (e) { campo.select(); document.execCommand('copy'); }
    boton.textContent = 'Copiado ✓';
    setTimeout(() => boton.textContent = original, 2000);
  });
}
// Precio actual frente al rango del 68 % (la página no sabe el precio en vivo: lo escribes tú)
const caja = document.querySelector('.recentrar');
if (caja) {
  const lo = parseFloat(caja.dataset.lo), hi = parseFloat(caja.dataset.hi);
  const entrada = document.getElementById('precio-actual'), estado = document.getElementById('estado-precio');
  const fila = document.getElementById('fila-orden'), orden = document.getElementById('orden-rec');
  const texto0 = estado.textContent;
  entrada.addEventListener('input', () => {
    const p = parseFloat(entrada.value.replace(',', '.'));
    if (!isFinite(p)) { estado.textContent = texto0; estado.className = 'sub'; fila.classList.add('oculto'); return; }
    const fuera = p > hi ? p - hi : (p < lo ? lo - p : 0);
    if (fuera > 0) {
      estado.textContent = '⚠ ' + fmt(p, 2) + ' está ' + (p > hi ? 'por encima' : 'por debajo') + ' del rango del 68 % (a ' +
        fmt(fuera, 2) + ' puntos). La frontera, la gamma y los niveles que dependen del precio pueden haber cambiado: ' +
        'recentra la escalera con esta orden (en la Terminal, en la carpeta del proyecto) y vuelve a abrir esta página.';
      estado.className = 'aviso-txt';
      orden.value = caja.dataset.orden + p;
      fila.classList.remove('oculto');
    } else {
      estado.textContent = '✓ ' + fmt(p, 2) + ' está dentro del rango del 68 %: no hace falta recentrar.';
      estado.className = 'sub';
      fila.classList.add('oculto');
    }
  });
}
let t; window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(dibujar, 150); });
"""

PLANTILLA = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo}</title>
<style>""" + ESTILOS.replace("{", "{{").replace("}", "}}") + """</style></head>
<body><main>
  <div class="cabecera">
    <h1>Escalera de niveles <span class="indice">{indice}</span> <span class="nombre-indice">{nombre_indice}</span></h1>
    <p>Sesión del <b>{sesion}</b>{contrato}</p>
    {linea_regimen}
    {linea_vanna_charm}
    <p class="sub">Datos: liquidación del {datos}{preliminar} · {fuente}{ventana}</p>
  </div>

  <div class="tiles">
    <div class="tarjeta tile"><div class="etq">Precio ref.</div><div class="valor num">{precio}</div><div class="sub">{origen}</div></div>
    <div class="tarjeta tile"><div class="etq">Gamma</div><div class="valor">{regimen}</div></div>
    <div class="tarjeta tile"><div class="etq">Frontera (gamma flip)</div><div class="valor num">{flip}</div></div>
    <div class="tarjeta tile"><div class="etq">Rango 68 %</div><div class="valor num" style="font-size:18px">{r68}</div><div class="sub">± {sigma} puntos (1 desviación)</div></div>
    <div class="tarjeta tile"><div class="etq">Rango 95 %</div><div class="valor num" style="font-size:18px">{r95}</div><div class="sub">± 2 desviaciones</div></div>
    <div class="tarjeta tile"><div class="etq">IV at the money</div><div class="valor num">{iv} %</div><div class="sub">vencimiento {venc_iv} · straddle {k_straddle}: {straddle} pts</div></div>
  </div>

  {bloque_corta}

  {bloque_recentrado}

  {bloque_tv}

  <div class="tarjeta avisos {clase_avisos}"><h2>⚠ Avisos y aproximaciones</h2><ul>{avisos}</ul></div>

  {bloque_desconocidos}

  {bloque_estado}

  <div class="tarjeta">
    <h2>Escalera</h2>
    <div class="tabla-scroll"><table>
      <thead><tr><th>Nivel</th><th>Tipo</th><th>Qué hay{nota_vencimientos}</th><th>Coincide con NDX</th><th>Prob. tocarlo</th><th>Fiabilidad histórica</th><th>Estado hoy</th></tr></thead>
      <tbody>{filas}</tbody>
    </table></div>
    {nota_fiabilidad}
    {zonas_0dte}
  </div>

  <div class="graficos">
    <div class="tarjeta"><h2>Gamma de la 0DTE por tramos de {tramo} puntos</h2>
      <div class="leyenda"><span><i style="background:var(--calls)"></i>Calls</span><span><i style="background:var(--puts)"></i>Puts</span></div>
      <div class="grafico" id="g-0dte"></div>
      <p class="pie">Cada barra suma un tramo de {tramo} puntos: {pie_tramo}. Solo opciones que vencen en la sesión.</p></div>
    <div class="tarjeta"><h2>Open interest por tramos (rango del 95 %)</h2>
      <div class="leyenda"><span><i style="background:var(--calls)"></i>Calls</span><span><i style="background:var(--puts)"></i>Puts</span></div>
      <div class="grafico" id="g-oi"></div>
      <p class="pie">Cada barra suma un tramo de {tramo} puntos ({pie_tramo}), de todos los vencimientos incluidos. Por eso puede no coincidir con el OI de un strike concreto de la escalera.</p></div>
  </div>

  <div class="metodo">
    <p><b>Cómo leer esto.</b> GEX = gamma × open interest × {multiplicador} $ × precio² × 1 %, con el supuesto estándar de que los creadores de mercado están largos de calls y cortos de puts (no es su posición real). La gamma se calcula en la apertura de la sesión; la volatilidad, con la liquidación del día anterior (Black-76, tiempo en días hábiles/252). La probabilidad de tocar un nivel es una aproximación lognormal (2 × probabilidad de cerrar más allá). "Qué suele hacer el precio" describe el comportamiento habitual, no una garantía. Pasa el ratón por las etiquetas para ver qué significa cada tipo.</p>
    <p>Herramienta de contexto. No envía órdenes ni es una recomendación de inversión. Generado: {generado}.</p>
  </div>
</main>
<script type="application/json" id="datos-graficos">{datos_graficos}</script>
<script>""" + GRAFICOS_JS.replace("{", "{{").replace("}", "}}") + """</script>
</body></html>"""

PLANTILLA_ERROR = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Escalera {indice} · sin datos</title>
<style>""" + ESTILOS.replace("{", "{{").replace("}", "}}") + """
.error {{ border-color: var(--critico); margin-top: 24px; }}
.error h1 {{ color: var(--critico); }}
</style></head>
<body><main>
  <div class="tarjeta error">
    <h1>✕ Hoy no hay escalera</h1>
    <p><b>Motivo:</b> {error}</p>
    {extra}
    <p class="sub">Qué hacer: comprueba que {secciones} del Daily Bulletin está en datos/entrada
    (o carga un CSV manual) y vuelve a ejecutar la tarea. Mira también datos/registro.log.</p>
  </div>
</main></body></html>"""
