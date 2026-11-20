"""
Diagnóstico (gratis o casi): ¿por qué Databento tarda tanto en dar el open interest de hoy?

    python -m tareas.diagnostico_databento

1. Hasta dónde llega cada esquema del histórico (get_dataset_range) y el estado de los últimos días.
2. ¿Deja el histórico pedir el tramo del OI de hoy aunque el "end" no llegue? (solo el coste, gratis)
3. ¿Da el API Live (repetición intradía, últimas 24 h) el open interest de esta noche ya? Solo el NQ,
   como mucho 90 segundos. Si la cuenta no tiene licencia Live de CME, lo dice y no gasta nada.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import databento as db

from calculos.calendario import anterior_habil
from tareas.databento_oi import POR_INDICE

OPEN_INTEREST = 9


def main():
    clave = os.environ["DATABENTO_API_KEY"]
    h = db.Historical(clave)
    ahora = datetime.now(timezone.utc)
    hoy = ahora.date()
    dia = anterior_habil(hoy - timedelta(days=1))
    print(f"Ahora {ahora:%Y-%m-%d %H:%M} UTC. Sesión {hoy}, boletín {dia}.", flush=True)

    print("\n1) Rango del histórico GLBX.MDP3:", flush=True)
    rango = h.metadata.get_dataset_range(dataset="GLBX.MDP3")
    print("   total:", rango.get("start"), "→", rango.get("end"))
    for esquema, r in sorted((rango.get("schema") or {}).items()):
        print(f"   {esquema:12s} → {r.get('end')}")
    try:
        cond = h.metadata.get_dataset_condition(dataset="GLBX.MDP3",
                                                start_date=str(hoy - timedelta(days=5)))
        print("   estado:", json.dumps(cond, default=str))
    except Exception as e:
        print("   estado: error", e)

    opciones = POR_INDICE["NQ"][0]
    ini, fin = f"{hoy}T00:30", f"{hoy}T05:00"
    print(f"\n2) Coste del tramo del OI ({ini} → {fin} UTC) en el histórico:", flush=True)
    try:
        c = h.metadata.get_cost(dataset="GLBX.MDP3", schema="statistics", symbols=opciones,
                                stype_in="parent", start=ini, end=fin)
        print(f"   OK: {c:.4f} $ (el histórico SÍ lo deja pedir)")
    except Exception as e:
        print("   NO:", str(e)[:400])

    print("\n3) API Live, repetición intradía de statistics del NQ desde las 00:30 UTC:", flush=True)
    cuenta = {"vistos": 0, "oi": 0, "oi_dia": 0, "primero": None}

    def recibir(r):
        if not hasattr(r, "stat_type"):
            return
        cuenta["vistos"] += 1
        if r.stat_type == OPEN_INTEREST:
            cuenta["oi"] += 1
            if r.ts_ref and r.ts_ref < 2**63 - 1 and datetime.fromtimestamp(r.ts_ref / 1e9, timezone.utc).date() == dia:
                cuenta["oi_dia"] += 1
                cuenta["primero"] = cuenta["primero"] or datetime.fromtimestamp(r.ts_recv / 1e9, timezone.utc)

    try:
        live = db.Live(key=clave)
        live.subscribe(dataset="GLBX.MDP3", schema="statistics", stype_in="parent",
                       symbols=opciones, start=f"{hoy}T00:30:00")
        live.add_callback(recibir)
        live.start()
        try:
            live.block_for_close(timeout=90)
        except Exception:
            pass
        try:
            live.stop()
        except Exception:
            pass
        p = cuenta["primero"]
        print(f"   registros statistics: {cuenta['vistos']}; open interest: {cuenta['oi']}; "
              f"del boletín {dia}: {cuenta['oi_dia']}" + (f" (el primero recibido a las {p:%H:%M} UTC)" if p else ""))
        print("   → el Live SÍ tiene el OI de esta noche" if cuenta["oi_dia"] else "   → el Live no ha dado el OI del boletín")
    except Exception as e:
        print("   error:", str(e)[:600])

    print("\n4) Prueba del OI adelantado (tramo del NQ solo hasta las 02:30 UTC, ≈ 0,005 $):", flush=True)
    try:
        from pathlib import Path
        from tareas import databento_oi as dbo
        from tareas.escalera_databento import _oi_anterior, _opciones_con_oi
        tmp = Path("datos/oi/diag_adelanto.dbn.zst")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        dbo.bajar(h, ("statistics", opciones, ini, f"{hoy}T02:30"), tmp)
        oi = _opciones_con_oi(tmp, dia)
        print(f"   opciones con OI del {dia}: {oi['instrument_id'].nunique()}; "
              f"primero {oi['ts_recv'].min()}, último {oi['ts_recv'].max()}")
        print("   boletín anterior en la caché:", _oi_anterior(Path(f"datos/oi/NQ_{dia}_oi.dbn.zst")))
        tmp.unlink()
    except Exception as e:
        print("   error:", repr(e)[:600])


if __name__ == "__main__":
    main()
