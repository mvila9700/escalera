"""
Lo que ves desde el iPhone (lo publica GitHub):
  - docs/index.html  -> la pagina de la escalera del NQ (igual que escalera_hoy.html en el Mac)
  - docs/es.html     -> la del ES
  - HOY_NQ.txt / HOY_ES.txt -> solo el texto para TradingView
Usa la escalera de hoy o, si aun no esta, la ultima que haya. Si hay recentrada, el texto es el de la ultima.

    python -m tareas.publicar_hoy
"""
import json
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

from calculos.config import RAIZ
from salida import historico, pagina

DOCS = RAIZ / "docs"
PAGINA = {"NQ": "index.html", "ES": "es.html"}


def ultima_sesion(indice):
    """Ultima escalera del indice en el historico (sin recentradas): (fecha, datos) o (None, None)."""
    pref = "" if indice == "NQ" else f"{indice}_"
    rutas = sorted(r for r in historico.carpeta().glob(f"{pref}2*.json")
                   if "_recentrada_" not in r.name and (indice != "NQ" or r.name[0].isdigit()))
    if not rutas:
        return None, None
    datos = json.loads(rutas[-1].read_text(encoding="utf-8"))
    return datos.get("fecha_sesion"), datos


def texto(indice, fecha):
    rec = historico.recentradas(fecha, indice)
    if rec and rec[0].get("esc2"):
        return rec[0]["esc2"].strip()
    ruta = historico.carpeta() / f"{historico.nombre(fecha, indice)}_esc2.txt"
    return ruta.read_text(encoding="utf-8").strip() if ruta.exists() else None


def main():
    hoy = str(datetime.now(ZoneInfo("Europe/Madrid")).date())
    DOCS.mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("")
    for indice in ("NQ", "ES"):
        fecha, datos = ultima_sesion(indice)
        if not datos:
            print(f"{indice}: no hay ninguna escalera")
            continue
        ruta = pagina.escribir(datos, indice=indice)
        shutil.copyfile(ruta, DOCS / PAGINA[indice])
        t = texto(indice, fecha)
        if t:
            (RAIZ / f"HOY_{indice}.txt").write_text(t + "\n", encoding="utf-8")
        print(f"{indice}: escalera del {fecha}{'' if str(fecha) == hoy else ' (la de hoy aun no esta)'}")


if __name__ == "__main__":
    main()
