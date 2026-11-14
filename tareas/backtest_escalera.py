"""
Backtest de la estrategia de la escalera para el ES en velas de 2 minutos.

    .venv/bin/python -m tareas.backtest_escalera --velas-a datos/velas/ES_v_0_*.csv \
        --velas-b datos/velas/ESZ6_*.csv [--instrumento MES] [--notas notas.html]

- --entrada vwap (por defecto): rebote en el VWAP · --entrada ifvg: iFVG de 2 minutos.
- Fase A (histórico largo, SIN escalera): solo la confirmación + VWAP, TP a R fijo.
  Sirve para validar la confirmación y el filtro. Se comparan los anclajes del VWAP
  y las opciones (y la tabla cruzada de ENTRADAS[...]["cruce"]).
- Fase B (CON escalera): solo los días que tienen ESC2 en datos/historico/ES_*.json.
  La muestra es de pocos días: el informe lo dice en cada tabla.

Genera salida/backtest_escalera.html. No envía órdenes ni se conecta a nada.
"""
import argparse
import html

import pandas as pd
import json
import sys
from pathlib import Path

from estrategia import esc2, metricas
from estrategia.motor import Config, correr
from estrategia.velas import cargar_varios, clave_sesion, sesiones_de_roll

SALIDA = Path("salida/backtest_escalera.html")
CARPETA_RECONSTRUIDA = "datos/historico_reconstruido"
INSTRUMENTOS = {
    "ES": dict(valor_punto=50.0, comision_lado=2.5, max_contratos=20),
    "MES": dict(valor_punto=5.0, comision_lado=0.75, max_contratos=200),
}
BE = {"break_even": True, "be_modo": "r", "be_r": 1.0}
# Por cada confirmación: variantes de cada fase (una opción cada vez frente a la base, salvo la
# tabla cruzada que se pidió) y la tabla cruzada de la fase A {(fila, columna): variante}.
ENTRADAS = {
    "nivel": {
        "nombre": "rechazo en nivel",
        "a": [],            # sin escalera no hay niveles: no hay fase A de esta entrada
        "cruce": None,
        "b": [
            ("Base: rechazo en nivel protector + VWAP · ratio ≥ 1", {}),
            ("(a) solo el 1.er toque del día de cada nivel", {"primer_toque_nivel": True}),
            ("(b) mecha que entra en la zona ≥ 50 % del rango", {"calidad_mecha": 0.5}),
            ("(c) sin entradas en los primeros 5 min", {"saltar_apertura": True}),
            ("(d) stop por tiempo: 15 velas sin avanzar 0,5 R", {"stop_tiempo": 15, "stop_tiempo_r": 0.5}),
            ("(e) régimen: en gamma negativa solo cortos", {"regimen": True}),
            ("(f) stop mínimo 4 puntos", {"stop_min": 4.0}),
            ("(f) stop mínimo 6 puntos", {"stop_min": 6.0}),
            ("(f) stop mínimo 8 puntos", {"stop_min": 8.0}),
            ("(g) sin días de noticias (CPI, FOMC, NFP)", {"noticias": "LISTA"}),
            ("(estudio) SIN filtro VWAP: no es la estrategia, solo para medir el filtro", {"filtro_vwap": False}),
        ],
        "intro_a": "",
    },
    "vwap": {
        "nombre": "rebote en el VWAP",
        "a": [(f"VWAP {an} · {tq} · {tp}", {**ca, **ct, **cp})
              for an, ca in (("9:30", {}), ("18:00", {"ancla_vwap": "18:00"}))
              for tq, ct in (("1.º y 2.º toque", {}), ("solo 1.er toque", {"toques_max": 1}))
              for tp, cp in (("TP 1 R", {"rr_fijo": 1.0}), ("TP 2 R", {"rr_fijo": 2.0}), ("TP 2 R + BE", {"rr_fijo": 2.0, **BE}))],
        "cruce": {
            "titulo": "Anclaje del VWAP × toques × TP de referencia (no es la lógica real)",
            "columnas": ["TP a 1 R", "TP a 2 R", "TP a 2 R + break even a 1 R"],
            "filas": {f"VWAP {an} · {tq}": [f"VWAP {an} · {tq} · {tp}" for tp in ("TP 1 R", "TP 2 R", "TP 2 R + BE")]
                      for an in ("9:30", "18:00") for tq in ("1.º y 2.º toque", "solo 1.er toque")},
        },
        "b": [
            ("Base: escalera + VWAP 9:30 · 1.º y 2.º toque · ratio ≥ 1", {}),
            ("VWAP anclado a las 18:00", {"ancla_vwap": "18:00"}),
            ("Solo el 1.er toque", {"toques_max": 1}),
            ("Ratio mínimo 1,5", {"rr_min": 1.5}),
            ("+ break even a 1 R", BE),
            ("(a) solo en gamma negativa", {"solo_gamma_neg": True}),
            ("(b) solo con el VWAP a ≤ 3 puntos de un nivel", {"vwap_cerca_nivel": True}),
            ("+ cierre parcial", {"parcial": True}),
            ("Mismos días SIN escalera (TP 2 R de referencia)", {"usar_escalera": False}),
        ],
        "intro_a": "<b>⚠ Referencia, no es la lógica real.</b> Sin escalera no hay niveles para el stop ni para el TP, "
                   "así que aquí el stop va detrás del extremo de la vela de rechazo (y de la del toque) + 1,5 puntos y el "
                   "TP a 1 R o a 2 R fijos, solo para validar la entrada en el VWAP. La estrategia real (fase B) no tiene "
                   "TP ni ratio fijos. Entrada a mercado al cierre del rechazo (se llena a la apertura siguiente con 1 tick "
                   "de deslizamiento). Horario de entradas 9:30–11:30 NY.",
    },
    "ifvg": {
        "nombre": "iFVG 2 min + VWAP",
        "a": [
            ("Base: VWAP 9:30", {}),
            ("VWAP anclado a las 18:00", {"ancla_vwap": "18:00"}),
            ("+ filtro NY Open", {"filtro_ny": True}),
            ("FVG mínimo 0,5 puntos", {"fvg_min": 0.5}),
            ("FVG mínimo 2 puntos", {"fvg_min": 2.0}),
            ("Entrada dividida", {"entrada_dividida": True}),
            ("+ break even a 1 R", BE),
            ("VWAP 18:00 + break even a 1 R", {"ancla_vwap": "18:00", **BE}),
        ],
        "cruce": {
            "titulo": "Anclaje del VWAP × break even",
            "columnas": ["Sin break even", "Con break even a 1 R"],
            "filas": {"VWAP 9:30": ["Base: VWAP 9:30", "+ break even a 1 R"],
                      "VWAP 18:00": ["VWAP anclado a las 18:00", "VWAP 18:00 + break even a 1 R"]},
        },
        "b": [
            ("Base: escalera + VWAP 9:30", {}),
            ("VWAP anclado a las 18:00", {"ancla_vwap": "18:00"}),
            ("+ filtro frontera de gamma", {"filtro_gamma": True}),
            ("+ filtro NY Open", {"filtro_ny": True}),
            ("+ cierre parcial", {"parcial": True}),
            ("+ cierre parcial + break even al cobrarlo", {"parcial": True, "break_even": True, "be_modo": "parcial"}),
            ("+ break even a 1 R", BE),
            ("Entrada dividida", {"entrada_dividida": True}),
            ("Mismos días SIN escalera (iFVG + VWAP)", {"usar_escalera": False}),
        ],
        "intro_a": "Valida la confirmación (iFVG) y el filtro (VWAP) por sí solos: TP a 2 R, stop detrás del iFVG. "
                   "Horario de entradas 9:30–11:30 NY.",
    },
}


def escaleras(cuales="real"):
    """{fecha: Dia} de la fase B: las reales (PDF), las reconstruidas (Databento) o ambas."""
    real = esc2.del_historico()
    if cuales == "real":
        return real
    rec = esc2.del_historico(CARPETA_RECONSTRUIDA)
    return rec if cuales == "reconstruida" else {**real, **rec}


def otro_contrato(velas, dias, tolerancia=15.0):
    """Sesiones cuya escalera está en precios de otro contrato que las velas (p. ej. ES.v.0 ya
    pasado al siguiente trimestral y la escalera todavía en el que vence): la referencia de la
    escalera (liquidación del día anterior) y el cierre de las velas a las 16:00 de ese día se
    separan más de `tolerancia` puntos (entre trimestrales hay ~60-80). Se excluyen."""
    fuera = set()
    for fecha, d in dias.items():
        if d.ref is None:
            continue
        inicio = pd.Timestamp(fecha, tz=velas.index.tz) - pd.Timedelta(hours=7)   # 17:00 del día anterior
        antes = velas.loc[(velas.index < inicio) & (velas.index >= inicio - pd.Timedelta(days=4))]
        antes = antes[antes.index.hour < 16]
        if len(antes) and abs(antes["close"].iloc[-1] - d.ref) > tolerancia:
            fuera.add(fecha)
    return fuera


def muestra(n):
    if n < 30:
        return "sin base", "Menos de 30 operaciones: no permite sacar conclusiones."
    if n < 100:
        return "indicativa", "Entre 30 y 100 operaciones: solo orientativo."
    return "suficiente", "100 operaciones o más."


def semanas_con_sesion(velas, excluir=None):
    """Semanas (ISO) con al menos una sesión operable: para la media de operaciones por semana."""
    excluir = excluir or set()
    fechas = {clave_sesion(t) for t in velas.index[velas.index.hour == 10]} - excluir
    return len({pd.Timestamp(f).isocalendar()[:2] for f in fechas})


def correr_fase(velas, dias, base, variantes, excluir=None, semanas=None):
    filas = []
    for nombre, cambio in variantes:
        cfg = base.con(**cambio)
        ops, descartes = correr(velas, dias, cfg, excluir)
        filas.append({"nombre": nombre, "cfg": cambio, "ops": ops, "res": metricas.resumen(ops),
                      "meses": metricas.por_mes(ops), "anios": metricas.por_anio(ops),
                      "descartes": descartes, "semanas": semanas})
        print(f"  {nombre}: {len(ops)} operaciones", flush=True)
    return filas


# ─── HTML ─────────────────────────────────────────────────────────────────────
def _n(x, dec=2, pct=False, signo=False):
    if x is None:
        return "—"
    if pct:
        return f"{x * 100:.0f} %"
    s = f"{x:+,.{dec}f}" if signo else f"{x:,.{dec}f}"
    return s.replace(",", "\u202f").replace(".", ",")


def _tabla_variantes(filas, unidad, ligado=False):
    cab = ("<tr><th>Variante</th><th>Operaciones</th><th>Días</th><th>% acierto</th><th>Ratio medio</th>"
           "<th>Acierto para empatar</th><th>R medio</th><th>R total</th><th>Profit factor</th><th>Neto ({u})</th><th>Drawdown máx. ({u})</th>"
           "<th>Drawdown (R)</th><th>Pérdidas seguidas (máx.)</th><th>R sin comisiones</th><th>Ops/semana</th>{x}<th>Muestra</th></tr>").format(
        u=unidad, x="<th>iFVG lejos del nivel</th><th>FVG anterior al toque</th>" if ligado else "")
    cuerpo = []
    for f in filas:
        r = f["res"]
        etiqueta, _ = muestra(r["ops"])
        cuerpo.append(
            f"<tr><td>{html.escape(f['nombre'])}{f.get('marca', '')}</td><td>{r['ops']}</td><td>{r['dias']}</td>"
            f"<td>{_n(r['acierto'], pct=True)}</td><td>{_n(r['rr_plan'])}</td>"
            f"<td>{_n(metricas.acierto_para_empatar(r['rr_plan'], r['comision_r']), pct=True)}</td><td>{_n(r['r_medio'], signo=True)}</td>"
            f"<td>{_n(r['r_total'], 1, signo=True)}</td><td>{_n(r['pf'])}</td><td>{_n(r['neto'], 0, signo=True)}</td>"
            f"<td>{_n(r['dd'], 0)}</td><td>{_n(r['dd_r'], 1)}</td>"
            f"<td>{r['perdidas_seguidas']}</td><td>{_n(r['r_sin_comisiones'], 1, signo=True)}</td>"
            f"<td>{_n(r['ops'] / f['semanas'], 2) if f.get('semanas') else '—'}</td>"
            + (f"<td>{f['descartes'].get('iFVG lejos del nivel', 0)}</td><td>{f['descartes'].get('FVG anterior al toque', 0)}</td>"
               if ligado else "") +
            f"<td><span class='m m-{etiqueta.replace(' ', '-')}'>{etiqueta}</span></td></tr>")
    return f"<table>{cab}{''.join(cuerpo)}</table>"


def _tabla_anios(filas, unidad):
    """Por año: detalle de la base y R de cada variante (¿es estable o depende de un periodo?)."""
    base = filas[0]
    anios = sorted({a for f in filas for a in f["anios"]})
    cab = "<tr><th></th>" + "".join(f"<th>{a}</th>" for a in anios) + "</tr>"
    def fila(nombre, valores):
        return f"<tr><td>{html.escape(nombre)}</td>{''.join(valores)}</tr>"
    b = base["anios"]
    detalle = [
        fila("Base · operaciones", [f"<td>{b[a]['ops'] if a in b else 0}</td>" for a in anios]),
        fila("Base · % acierto", [f"<td>{_n(b[a]['ganadas'] / b[a]['ops'], pct=True) if a in b else '—'}</td>" for a in anios]),
        fila("Base · profit factor", [f"<td>{_n(b[a]['pf']) if a in b else '—'}</td>" for a in anios]),
        fila(f"Base · neto ({unidad})", [f"<td>{_n(b[a]['neto'], 0, signo=True) if a in b else '—'}</td>" for a in anios]),
    ]
    r_var = [fila(f"R · {f['nombre']}", [
        f"<td class='{'pos' if f['anios'][a]['r'] > 0 else 'neg'}'>{_n(f['anios'][a]['r'], 1, signo=True)}</td>"
        if a in f["anios"] else "<td>—</td>" for a in anios]) for f in filas]
    return (f"<table>{cab}{''.join(detalle)}</table><p class='nota'>R total de cada variante por año "
            f"(2021 y 2026 son años incompletos):</p><table>{cab}{''.join(r_var)}</table>")


def _tabla_cruce(filas, unidad, cruce):
    por_nombre = {f["nombre"]: f for f in filas}
    if not cruce or not all(n in por_nombre for v in cruce["filas"].values() for n in v):
        return ""
    def celda(f):
        r = f["res"]
        sem = f"{r['ops'] / f['semanas']:.2f}".replace(".", ",") if f.get("semanas") else "—"
        return (f"<td>{r['ops']} ops ({sem}/sem) · {_n(r['acierto'], pct=True)} · <b>{_n(r['r_total'], 1, signo=True)} R</b><br>"
                f"PF {_n(r['pf'])} · DD {_n(r['dd_r'], 1)} R · sin comisiones {_n(r['r_sin_comisiones'], 1, signo=True)} R</td>")
    cab = "".join(f"<th>{html.escape(c)}</th>" for c in cruce["columnas"])
    cuerpo = "".join(f"<tr><td>{html.escape(fila)}</td>{''.join(celda(por_nombre[n]) for n in nombres)}</tr>"
                     for fila, nombres in cruce["filas"].items())
    return f"<h3>{html.escape(cruce['titulo'])}</h3><table><tr><th></th>{cab}</tr>{cuerpo}</table>"


def _tabla_ratios(ops, res):
    """Ratio planeado de cada operación de la base: media, distribución y acierto para empatar."""
    r = metricas.ratios(ops)
    if not r:
        return "<p class='nota'>Sin operaciones: no hay ratios.</p>"
    empate = metricas.acierto_para_empatar(r["media"], res["comision_r"])
    def tramo(lo, hi):
        return f"menos de {_n(hi, 1)}" if lo is None else f"{_n(lo, 1)} o más" if hi is None else f"{_n(lo, 1)} a {_n(hi, 1)}"
    filas = "".join(f"<tr><td>{tramo(lo, hi)}</td><td>{n}</td><td>{_n(n / len(ops), pct=True)}</td></tr>" for lo, hi, n in r["dist"])
    return ("<h3>Ratio de las operaciones (sale de las zonas del stop y del TP)</h3>"
            f"<p>Ratio medio <b>{_n(r['media'])}</b> · mediana {_n(r['mediana'])} · mínimo {_n(r['min'])} · máximo {_n(r['max'])}. "
            f"Con ese ratio medio y {_n(res['comision_r'], 3)} R de comisiones por operación, para empatar hace falta acertar "
            f"<b>{_n(empate, pct=True)}</b>; esta base acierta {_n(res['acierto'], pct=True)}.</p>"
            f"<table><tr><th>Ratio</th><th>Operaciones</th><th>%</th></tr>{filas}</table>")


def _tabla_meses(fila, unidad):
    filas = "".join(
        f"<tr><td>{m}</td><td>{d['ops']}</td><td>{_n(d['ganadas'] / d['ops'], pct=True)}</td>"
        f"<td class='{'pos' if d['r'] > 0 else 'neg'}'>{_n(d['r'], 1, signo=True)}</td>"
        f"<td>{_n(d['neto'], 0, signo=True)}</td></tr>"
        for m, d in fila["meses"].items())
    return (f"<table><tr><th>Mes</th><th>Operaciones</th><th>% acierto</th><th>R</th><th>Neto ({unidad})</th></tr>"
            f"{filas}</table>")


def _tabla_ops(ops, unidad):
    filas = "".join(
        f"<tr><td>{o.fecha} {o.hora}</td><td>{'Largo' if o.dir == 1 else 'Corto'}</td>"
        f"<td>{html.escape(o.reaccion) or '—'}</td><td>{o.qty}</td><td>{_n(o.entrada_plan)}</td><td>{_n(o.stop)}</td>"
        f"<td>{_n(o.tp)}</td><td>{_n(o.rr_plan)}</td>"
        f"<td>{html.escape(', '.join(f'{s[0]} {_n(s[1])}×{s[2]}' for s in o.salidas))}</td>"
        f"<td class='{'pos' if o.pnl > 0 else 'neg'}'>{_n(o.r, 2, signo=True)}</td><td>{_n(o.pnl, 0, signo=True)}</td></tr>"
        for o in ops)
    return ("<table><tr><th>Hora (NY)</th><th>Lado</th><th>Reacción</th><th>Contratos</th><th>Entrada</th><th>Stop</th>"
            f"<th>TP</th><th>Ratio</th><th>Salidas</th><th>R</th><th>Neto ({unidad})</th></tr>{filas}</table>")


def _curva(ops, id_):
    """Línea de R acumulada (una sola serie; el título la nombra). Tooltip con cruceta."""
    puntos = metricas.curva(ops)
    if len(puntos) < 2:
        return "<p class='nota'>Pocas operaciones para dibujar la curva.</p>"
    datos = [{"x": i + 1, "f": f, "y": round(y, 2)} for i, (f, y) in enumerate(puntos)]
    return (f"<figure class='viz' id='{id_}' data-puntos='{html.escape(json.dumps(datos))}'>"
            "<figcaption>R acumulada, operación a operación</figcaption>"
            "<svg role='img' aria-label='R acumulada por operación'></svg><div class='tip' hidden></div></figure>")


CSS = """
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f3f2ee;--text-primary:#0b0b0b;--text-secondary:#52514e;
--grid:#e4e3df;--series-1:#2a78d6;--pos:#008300;--neg:#e34948;--warn:#a86b00}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;--surface-1:#1a1a19;
--surface-2:#242422;--text-primary:#f0efec;--text-secondary:#c3c2b7;--grid:#3a3a37;--series-1:#3987e5;--pos:#2fb52f;--neg:#e66767;--warn:#e0a020}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242422;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--grid:#3a3a37;--series-1:#3987e5;--pos:#2fb52f;--neg:#e66767;--warn:#e0a020}
body{margin:0}.viz-root{background:var(--surface-1);color:var(--text-primary);font:15px/1.5 -apple-system,system-ui,sans-serif;
padding:24px 32px;max-width:1200px;margin:auto}
h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 8px;border-bottom:1px solid var(--grid);padding-bottom:4px}
h3{font-size:16px;margin:20px 0 6px}.nota{color:var(--text-secondary);font-size:13px}
.aviso{border-left:4px solid var(--warn);background:var(--surface-2);padding:8px 12px;margin:12px 0}
table{border-collapse:collapse;margin:8px 0 16px;font-size:13px;width:100%}
th,td{border-bottom:1px solid var(--grid);padding:4px 8px;text-align:right}td{white-space:nowrap}td:first-child{white-space:normal}th{color:var(--text-secondary);font-weight:600}
td:first-child,th:first-child{text-align:left}.pos{color:var(--pos)}.neg{color:var(--neg)}
.m{white-space:nowrap;font-size:12px;padding:1px 6px;border-radius:4px;background:var(--surface-2)}.m-sin-base::before{content:"⚠ "}
figure.viz{margin:8px 0 16px;position:relative}figcaption{color:var(--text-secondary);font-size:13px}
svg{width:100%;height:220px;display:block}.tip{position:absolute;pointer-events:none;background:var(--surface-2);
border:1px solid var(--grid);padding:4px 8px;font-size:12px;border-radius:4px}.tip b{font-size:14px}
details{margin:8px 0}summary{cursor:pointer;color:var(--text-secondary)}
"""

JS = """
document.querySelectorAll('figure.viz').forEach(fig=>{
 const d=JSON.parse(fig.dataset.puntos),svg=fig.querySelector('svg'),tip=fig.querySelector('.tip');
 const W=svg.clientWidth||900,H=220,m={l:44,r:12,t:10,b:24};svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
 const ys=d.map(p=>p.y).concat([0]),y0=Math.min(...ys),y1=Math.max(...ys),n=d.length;
 const X=i=>m.l+(W-m.l-m.r)*(i/(n-1)),Y=v=>m.t+(H-m.t-m.b)*(1-(v-y0)/((y1-y0)||1));
 const ns='http://www.w3.org/2000/svg',el=(t,a)=>{const e=document.createElementNS(ns,t);for(const k in a)e.setAttribute(k,a[k]);svg.appendChild(e);return e};
 const paso=Math.max(1,Math.ceil((y1-y0)/5));
 for(let v=Math.floor(y0);v<=y1;v+=paso){el('line',{x1:m.l,x2:W-m.r,y1:Y(v),y2:Y(v),stroke:'var(--grid)','stroke-width':1});
  const t=el('text',{x:m.l-6,y:Y(v)+4,'text-anchor':'end','font-size':11,fill:'var(--text-secondary)'});t.textContent=v+' R'}
 el('line',{x1:m.l,x2:W-m.r,y1:Y(0),y2:Y(0),stroke:'var(--text-secondary)','stroke-width':1});
 el('path',{d:d.map((p,i)=>(i?'L':'M')+X(i)+','+Y(p.y)).join(''),fill:'none',stroke:'var(--series-1)','stroke-width':2,'stroke-linejoin':'round'});
 const ult=d[n-1],lab=el('text',{x:X(n-1)-4,y:Y(ult.y)-8,'text-anchor':'end','font-size':12,fill:'var(--text-primary)'});lab.textContent=ult.y.toFixed(1)+' R';
 const cruz=el('line',{y1:m.t,y2:H-m.b,stroke:'var(--text-secondary)','stroke-width':1,visibility:'hidden'});
 const pto=el('circle',{r:4,fill:'var(--series-1)',stroke:'var(--surface-1)','stroke-width':2,visibility:'hidden'});
 const hit=el('rect',{x:m.l,y:0,width:W-m.l-m.r,height:H,fill:'transparent'});
 hit.addEventListener('pointermove',ev=>{const r=svg.getBoundingClientRect(),px=(ev.clientX-r.left)*W/r.width;
  const i=Math.max(0,Math.min(n-1,Math.round((px-m.l)/(W-m.l-m.r)*(n-1)))),p=d[i];
  cruz.setAttribute('x1',X(i));cruz.setAttribute('x2',X(i));cruz.setAttribute('visibility','visible');
  pto.setAttribute('cx',X(i));pto.setAttribute('cy',Y(p.y));pto.setAttribute('visibility','visible');
  tip.hidden=false;tip.textContent='';const b=document.createElement('b');b.textContent=p.y.toFixed(2)+' R';tip.appendChild(b);
  tip.appendChild(document.createTextNode(' · operación '+p.x+' · '+p.f));
  tip.style.left=Math.min(X(i)*r.width/W+8,r.width-220)+'px';tip.style.top='24px'});
 hit.addEventListener('pointerleave',()=>{tip.hidden=true;cruz.setAttribute('visibility','hidden');pto.setAttribute('visibility','hidden')});
});
"""


def _seccion(titulo, filas, unidad, intro, id_, detalle_ops=False, cruce=None, ligado=False):
    base = filas[0]
    partes = [f"<h2>{titulo}</h2>", intro, _tabla_variantes(filas, unidad, ligado=ligado)]
    if ligado:
        partes.append("<p class='nota'>«iFVG lejos del nivel» y «FVG anterior al toque»: señales iFVG que ya "
                      "habían pasado VWAP y filtros y se descartan por no estar ligadas al nivel que reaccionó.</p>")
    partes.append("<p class='nota'>«Acierto para empatar» = (1 + comisiones en R) / (ratio medio + 1): con ese % de "
                  "acierto el resultado sería cero (con el ratio medio; no cuenta el deslizamiento).</p>")
    partes.append("<p class='nota'>R = resultado dividido por el riesgo planeado hasta el stop (incluye comisiones y "
                  "deslizamiento). «R sin comisiones» quita solo las comisiones (el deslizamiento se queda). Profit factor = "
                  "ganado / perdido. Drawdown = peor caída desde un máximo de la curva. Las salidas en break even cuentan "
                  "como perdidas (pierden la comisión).</p>")
    if not detalle_ops:
        partes.append(_tabla_cruce(filas, unidad, cruce))
        partes.append("<h3>Por año</h3>")
        partes.append(_tabla_anios(filas, unidad))
    partes.append(f"<h3>{html.escape(base['nombre'])}: curva y meses</h3>")
    partes.append(_curva(base["ops"], id_))
    meses = base["meses"]
    if meses:
        positivos = sum(1 for m in meses.values() if m["r"] > 0)
        partes.append(f"<p class='nota'>Meses en positivo: {positivos} de {len(meses)}.</p>")
    partes.append(_tabla_meses(base, unidad))
    desc = ", ".join(f"{k}: {v}" for k, v in sorted(base["descartes"].items(), key=lambda x: -x[1]))
    partes.append(f"<p class='nota'>Señales iFVG descartadas en la base: {html.escape(desc) or 'ninguna'}.</p>")
    if detalle_ops:
        partes.append(_tabla_ratios(base["ops"], base["res"]))
        partes.append("<details open><summary>Todas las operaciones de la base</summary>"
                      f"{_tabla_ops(base['ops'], unidad)}</details>")
    return "\n".join(partes)


# ─── Prueba del stop mínimo (f) en la fase A: ajuste y comprobación separados ────
AJUSTE = ("2021-10-01", "2024-12-31")          # se elige el valor SOLO con este periodo
COMPROBACION = ("2025-01-01", "2026-12-31")    # y se ejecuta UNA vez aquí
VALORES_STOP_MIN = [0.0, 4.0, 6.0, 8.0]


def _periodo(velas, desde, hasta):
    return velas[(velas.index >= pd.Timestamp(desde, tz=velas.index.tz)) &
                 (velas.index < pd.Timestamp(hasta, tz=velas.index.tz) + pd.Timedelta(days=1))]


def prueba_stop_minimo(va, roll, base, entrada):
    """Con TP a 2 R de referencia: cada valor del stop mínimo en el periodo de ajuste; el que da
    más R total se ejecuta una sola vez en el periodo de comprobación (con la base al lado)."""
    cfg = base.con(entrada=entrada, usar_escalera=False, rr_fijo=2.0)
    ajuste = _periodo(va, *AJUSTE)
    filas = []
    for v in VALORES_STOP_MIN:
        ops, _ = correr(ajuste, {}, cfg.con(stop_min=v), roll)
        filas.append((v, metricas.resumen(ops)))
    elegido = max(filas, key=lambda x: (round(x[1]["r_total"], 6), -x[0]))[0]
    comp = _periodo(va, *COMPROBACION)
    fuera = []
    for v in sorted({0.0, elegido}):
        ops, _ = correr(comp, {}, cfg.con(stop_min=v), roll)
        fuera.append((v, metricas.resumen(ops)))
    return {"entrada": entrada, "ajuste": filas, "elegido": elegido, "comprobacion": fuera,
            "semanas_ajuste": semanas_con_sesion(ajuste, roll), "semanas_comp": semanas_con_sesion(comp, roll)}


ESTUDIO_VWAP = "(estudio) SIN filtro VWAP"


def fase_b_separada(vb, dias, base, variantes, excluir, corte, minimo_ops=30):
    """Fase B con dos periodos: se elige la opción (a)–(g) SOLO con las sesiones anteriores a
    `corte` (la que da más R total con ≥ minimo_ops operaciones y más que la base; si ninguna,
    la base) y se ejecuta UNA vez en las sesiones desde `corte`: base, elegida y la variante de
    estudio sin filtro VWAP (no compite en la elección: no es la estrategia)."""
    fin_aj = (pd.Timestamp(corte) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    d_aj = {f: d for f, d in dias.items() if f < corte}
    d_co = {f: d for f, d in dias.items() if f >= corte}
    v_aj, v_co = _periodo(vb, "2000-01-01", fin_aj), _periodo(vb, corte, "2100-01-01")
    sem = lambda v, dd: semanas_con_sesion(v, {clave_sesion(t) for t in v.index} - set(dd))
    print(f"Ajuste: {len(d_aj)} sesiones · comprobación: {len(d_co)} sesiones (desde {corte})", flush=True)
    ajuste = correr_fase(v_aj, d_aj, base, variantes, excluir | ({clave_sesion(t) for t in v_aj.index} - set(d_aj)),
                         sem(v_aj, d_aj))
    r_base = ajuste[0]["res"]["r_total"]
    candidatas = [f for f in ajuste[1:] if not f["nombre"].startswith(ESTUDIO_VWAP)
                  and f["res"]["ops"] >= minimo_ops and f["res"]["r_total"] > r_base]
    elegida = max(candidatas, key=lambda f: f["res"]["r_total"]) if candidatas else ajuste[0]
    for f in ajuste:
        f["marca"] = " ← elegida" if f is elegida else ""
    en_comp = [ajuste[0]] + ([elegida] if elegida is not ajuste[0] else []) + \
              [f for f in ajuste if f["nombre"].startswith(ESTUDIO_VWAP)]
    comp = correr_fase(v_co, d_co, base, [(f["nombre"], f["cfg"]) for f in en_comp],
                       excluir | ({clave_sesion(t) for t in v_co.index} - set(d_co)), sem(v_co, d_co))
    for f in comp:
        f["marca"] = " ← elegida" if f["nombre"] == elegida["nombre"] else ""
    return {"ajuste": ajuste, "comprobacion": comp, "elegida": elegida["nombre"], "corte": corte,
            "sesiones": (sorted(d_aj), sorted(d_co)), "minimo_ops": minimo_ops}


def _seccion_separada(sep, unidad):
    aj, co = sep["sesiones"]
    rango = lambda l: f"{l[0]} a {l[-1]}, {len(l)} sesiones" if l else "sin sesiones"
    intro_aj = (f"<p class='aviso'>Periodo de AJUSTE ({rango(aj)}). Las opciones se comparan de una en una frente a "
                f"la base y se elige la que da más R total con al menos {sep['minimo_ops']} operaciones y más que la base; "
                f"si ninguna, se queda la base. Elegida: <b>{html.escape(sep['elegida'])}</b>. La variante sin filtro VWAP "
                "es de estudio (no es la estrategia) y no compite en la elección.</p>")
    intro_co = (f"<p class='aviso'>Periodo de COMPROBACIÓN ({rango(co)}): no se ha usado para elegir nada y se ejecuta "
                "una sola vez, con la base, la opción elegida y la variante de estudio sin filtro VWAP. Es el resultado "
                "que cuenta.</p>")
    return (_seccion("Fase B con escaleras reconstruidas · ajuste (9 primeros meses)", sep["ajuste"], unidad, intro_aj,
                     "curva-b-aj") + resumen_comprobacion(sep) +
            _seccion("Fase B con escaleras reconstruidas · comprobación, detalle", sep["comprobacion"],
                     unidad, intro_co, "curva-b-co", detalle_ops=True))


def veredicto(sep):
    """Frase clara: ¿la elegida supera a la base sin mejoras en la comprobación?"""
    co = {f["nombre"]: f["res"] for f in sep["comprobacion"]}
    base = sep["comprobacion"][0]
    if sep["elegida"] == base["nombre"]:
        return ("En el ajuste ninguna opción superó a la base (con al menos "
                f"{sep['minimo_ops']} operaciones), así que se queda la base sin mejoras.")
    e, b = co[sep["elegida"]], base["res"]
    if e["r_total"] > b["r_total"]:
        return (f"La elegida ({sep['elegida']}) SÍ supera a la base en la comprobación: "
                f"{_n(e['r_total'], 1, signo=True)} R frente a {_n(b['r_total'], 1, signo=True)} R.")
    return (f"La elegida ({sep['elegida']}) NO supera a la base en la comprobación: "
            f"{_n(e['r_total'], 1, signo=True)} R frente a {_n(b['r_total'], 1, signo=True)} R. "
            "La mejora que salió en el ajuste no se repite con datos nuevos: no la uses.")


def resumen_comprobacion(sep):
    filas = []
    for f in sep["comprobacion"]:
        r = f["res"]
        nombre = "Base sin mejoras" if f is sep["comprobacion"][0] else f["nombre"]
        filas.append(f"<tr><td>{html.escape(nombre)}{f.get('marca', '')}</td><td>{r['ops']}</td>"
                     f"<td class='{'pos' if r['r_total'] > 0 else 'neg'}'>{_n(r['r_total'], 1, signo=True)}</td>"
                     f"<td>{_n(r['pf'])}</td><td>{_n(r['peor_racha_r'], 1, signo=True)} R "
                     f"({r['perdidas_seguidas']} seguidas)</td></tr>")
    return ("<h2>Comprobación (3 últimos meses, una sola vez) · resumen</h2>"
            f"<p class='aviso'><b>{html.escape(veredicto(sep))}</b></p>"
            "<table><tr><th>Variante</th><th>Operaciones</th><th>R total</th><th>Profit factor</th>"
            "<th>Peor racha (R)</th></tr>" + "".join(filas) + "</table>"
            "<p class='nota'>Peor racha = la serie de operaciones perdidas seguidas que más R suma (entre paréntesis, "
            "cuántas fueron). Muestra: " + html.escape(muestra(sep["comprobacion"][0]["res"]["ops"])[1]) + "</p>")


def _tabla_stop_minimo(pruebas):
    nombres = {"ifvg": "iFVG 2 min", "vwap": "rebote en el VWAP"}
    def fila(v, r, sem, marca=""):
        nombre = "sin mínimo (base)" if not v else f"{_n(v, 0)} puntos"
        return (f"<tr><td>{nombre}{marca}</td><td>{r['ops']}</td><td>{_n(r['ops'] / sem, 2) if sem else '—'}</td>"
                f"<td>{_n(r['acierto'], pct=True)}</td><td>{_n(r['r_medio'], signo=True)}</td>"
                f"<td class='{'pos' if r['r_total'] > 0 else 'neg'}'>{_n(r['r_total'], 1, signo=True)}</td>"
                f"<td>{_n(r['pf'])}</td><td>{_n(r['dd_r'], 1)}</td><td>{_n(r['r_sin_comisiones'], 1, signo=True)}</td></tr>")
    cab = ("<tr><th>Stop mínimo</th><th>Operaciones</th><th>Ops/semana</th><th>% acierto</th><th>R medio</th>"
           "<th>R total</th><th>Profit factor</th><th>Drawdown (R)</th><th>R sin comisiones</th></tr>")
    partes = ["<h2>Prueba del stop mínimo (f) · fase A, sin escalera</h2>",
              "<p class='aviso'>⚠ Referencia, no es la lógica real: sin escalera el stop va detrás de la vela (o del iFVG) "
              "y, si queda más cerca que el mínimo, a la distancia mínima; TP a 2 R fijo. El valor se elige "
              f"<b>solo</b> con {AJUSTE[0][:7]} a {AJUSTE[1][:7]} (el que da más R total) y se ejecuta <b>una vez</b> "
              f"con {COMPROBACION[0][:7]} a hoy, que no se ha usado para elegir.</p>"]
    for pr in pruebas:
        partes.append(f"<h3>{nombres[pr['entrada']]} · periodo de ajuste ({AJUSTE[0][:7]} a {AJUSTE[1][:7]})</h3>")
        partes.append("<table>" + cab + "".join(fila(v, r, pr["semanas_ajuste"], " ← elegido" if v == pr["elegido"] else "")
                                               for v, r in pr["ajuste"]) + "</table>")
        partes.append(f"<h3>{nombres[pr['entrada']]} · periodo de comprobación ({COMPROBACION[0][:7]} a hoy), una sola vez</h3>")
        partes.append("<table>" + cab + "".join(fila(v, r, pr["semanas_comp"], " ← elegido" if v == pr["elegido"] else "")
                                               for v, r in pr["comprobacion"]) + "</table>")
    return "\n".join(partes)


def informe(fase_a, fase_b, instrumento, info, notas="", entrada="vwap", extra=""):
    unidad = f"$ {instrumento}"
    e = ENTRADAS[entrada]
    cuerpo = [f"<h1>Backtest de la estrategia de la escalera · {e['nombre']} · {instrumento}, velas de 2 minutos</h1>",
              f"<p class='nota'>{html.escape(info)}. Solo pruebas: no se ha enviado ninguna orden.</p>"]
    if notas:
        cuerpo.append(f"<h2>Explicación</h2>{notas}")
    if extra:
        cuerpo.append(extra)
    if fase_a:
        cuerpo.append(_seccion(
            f"Fase A · histórico largo, sin escalera ({e['nombre']})", fase_a, unidad,
            f"<p>{e['intro_a']}</p>", "curva-a", cruce=e["cruce"]))
    elif not ENTRADAS[entrada]["a"]:
        cuerpo.append("<h2>Fase A de esta entrada</h2><p class='aviso'>No hay: el rechazo en nivel necesita la escalera "
                      "para saber dónde están los niveles.</p>")
    else:
        cuerpo.append("<h2>Fase A</h2><p class='aviso'>Faltan las velas del histórico largo.</p>")
    if fase_b:
        n = fase_b[0]["res"]["ops"]
        cuerpo.append(_seccion(
            "Fase B · con la escalera (días con ESC2 del ES)", fase_b, unidad,
            f"<p class='aviso'>⚠ Muestra muy pequeña: {info.split('Fase B: ')[-1] if 'Fase B: ' in info else ''}. "
            f"{n} operaciones en la base. {muestra(n)[1]} Ningún resultado de esta fase es estadísticamente fiable; "
            "sirve para comprobar que la lógica hace lo que se acordó.</p>", "curva-b", detalle_ops=True, ligado=entrada == "ifvg"))
    elif "Fase B con escaleras reconstruidas" not in extra:
        cuerpo.append("<h2>Fase B</h2><p class='aviso'>Faltan las velas de los días con escalera.</p>")
    return (f"<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'><title>Backtest escalera ES</title>"
            f"<style>{CSS}</style></head><body><main class='viz-root'>{''.join(cuerpo)}</main>"
            f"<script>{JS}</script></body></html>")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--velas-a", nargs="*", default=[], help="CSV del histórico largo (Databento ES.v.0 o TradingView)")
    p.add_argument("--velas-b", nargs="*", default=[], help="CSV del contrato de los niveles (ESZ2026) en los días con escalera")
    p.add_argument("--instrumento", choices=list(INSTRUMENTOS), default="MES")
    p.add_argument("--notas", help="HTML con la explicación en español (se pone al principio)")
    p.add_argument("--salida", default=str(SALIDA))
    p.add_argument("--entrada", choices=list(ENTRADAS), default="nivel",
                   help="nivel (rechazo en nivel, por defecto) · vwap (rebote en el VWAP) · ifvg")
    p.add_argument("--noticias", default="datos/noticias.txt", help="días de noticias para la mejora (g)")
    p.add_argument("--corte", help="fase B: primer día del periodo de comprobación (AAAA-MM-DD). Se ajusta solo con "
                                   "lo anterior y se ejecuta una vez desde aquí")
    p.add_argument("--escaleras", choices=["real", "reconstruida", "ambas"], default="real",
                   help="fase B: real (datos/historico, del PDF) · reconstruida (datos/historico_reconstruido, de Databento) · "
                        "ambas (la reconstruida y, los días que no la tengan, la real)")
    a = p.parse_args(argv)

    base = Config(entrada=a.entrada, **INSTRUMENTOS[a.instrumento])
    variantes = dict(ENTRADAS[a.entrada])
    noticias = frozenset(l.split("#")[0].strip() for l in Path(a.noticias).read_text().splitlines()
                         if l.split("#")[0].strip()) if Path(a.noticias).exists() else frozenset()
    variantes["b"] = [(n, {k: (noticias if v == "LISTA" else v) for k, v in c.items()}) for n, c in variantes["b"]]
    extra = ""
    info = [f"{a.instrumento}: {base.valor_punto:g} $/punto, comisión {base.comision_lado:g} $ por contrato y lado, "
            f"1 tick de deslizamiento en stops, riesgo máx. {base.riesgo_max:g} $"]
    fase_a = fase_b = None
    if a.velas_a:
        va = cargar_varios(a.velas_a)
        roll = sesiones_de_roll(va)
        if variantes["a"]:
            fase_a = correr_fase(va, {}, base.con(usar_escalera=False), variantes["a"], roll, semanas_con_sesion(va, roll))
        else:
            pruebas = [prueba_stop_minimo(va, roll, base, e) for e in ("ifvg", "vwap")]
            extra = _tabla_stop_minimo(pruebas)
            for pr in pruebas:
                print(f"Stop mínimo {pr['entrada']}: ajuste " + ", ".join(f"{v:g}→{r['r_total']:+.1f} R" for v, r in pr["ajuste"])
                      + f" · elegido {pr['elegido']:g} · comprobación " +
                      ", ".join(f"{v:g}→{r['r_total']:+.1f} R ({r['ops']} ops)" for v, r in pr["comprobacion"]))
        info.append(f"Fase A: {va.index[0]:%Y-%m-%d} a {va.index[-1]:%Y-%m-%d}, {len(roll)} sesiones de cambio de contrato excluidas")
    if a.velas_b:
        vb = cargar_varios(a.velas_b)
        dias = escaleras(a.escaleras)
        # solo sesiones con velas del horario regular (la del 29-oct solo tiene la noche)
        con_velas = sorted(set(dias) & {clave_sesion(t) for t in vb.index[vb.index.hour == 10]})
        distinto = otro_contrato(vb, {f: dias[f] for f in con_velas})
        con_velas = [f for f in con_velas if f not in distinto]
        dias = {f: dias[f] for f in con_velas}
        sin_escalera = {clave_sesion(t) for t in vb.index} - set(dias)
        roll_b = sesiones_de_roll(vb)
        if a.corte:
            sep = fase_b_separada(vb, dias, base, variantes["b"], roll_b, a.corte)
            extra += _seccion_separada(sep, f"$ {a.instrumento}")
            for nombre, filas in (("ajuste", sep["ajuste"]), ("comprobación", sep["comprobacion"])):
                for f in filas:
                    r = f["res"]
                    print(f"B {nombre} · {f['nombre']}{f.get('marca', '')}: {r['ops']} ops, acierto {_n(r['acierto'], pct=True)}, "
                          f"R total {_n(r['r_total'], 1, signo=True)}, PF {_n(r['pf'])}, DD {_n(r['dd_r'], 1)} R, "
                          f"peor racha {_n(r['peor_racha_r'], 1, signo=True)} R ({r['perdidas_seguidas']})")
            print("Veredicto: " + veredicto(sep))
        else:
            fase_b = correr_fase(vb, dias, base, variantes["b"], sin_escalera | roll_b, semanas_con_sesion(vb, sin_escalera))
        origen = {"real": "reales (PDF)", "reconstruida": "reconstruidas (Databento)", "ambas": "reconstruidas y reales"}[a.escaleras]
        lista = ", ".join(con_velas) if len(con_velas) <= 10 else f"{con_velas[0]} a {con_velas[-1]}"
        info.append(f"Fase B: {len(dias)} sesiones con escalera {origen} ({lista})")
        if distinto:
            info.append(f"{len(distinto)} sesiones excluidas porque las velas son de otro contrato que la escalera "
                        f"(cambio de trimestral): {', '.join(sorted(distinto))}")
    if not fase_a and not fase_b and not extra:
        print("No hay velas: pasa --velas-a y/o --velas-b.")
        return 1
    notas = Path(a.notas).read_text() if a.notas else ""
    Path(a.salida).write_text(informe(fase_a, fase_b, a.instrumento, " · ".join(info), notas, a.entrada, extra))
    for nombre, filas in (("A", fase_a), ("B", fase_b)):
        for f in filas or []:
            r = f["res"]
            print(f"Fase {nombre} · {f['nombre']}: {r['ops']} ops, acierto {_n(r['acierto'], pct=True)}, "
                  f"R total {_n(r['r_total'], 1, signo=True)}, PF {_n(r['pf'])}, DD {_n(r['dd_r'], 1)} R")
    print(f"Informe: {a.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
