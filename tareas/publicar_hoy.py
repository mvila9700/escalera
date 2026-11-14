"""
Deja en la raíz del proyecto HOY_NQ.txt y HOY_ES.txt: el texto para TradingView de la escalera de
hoy (o de su última recentrada, si la hay). Lo usa GitHub para que lo copies desde el iPhone.

    python -m tareas.publicar_hoy
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from calculos.config import RAIZ
from salida import historico


def texto_de_hoy(indice, hoy):
    rec = historico.recentradas(hoy, indice)
    if rec and rec[0].get("esc2"):
        return rec[0]["esc2"].strip(), f"recentrada en {rec[0].get('precio')}"
    ruta = historico.carpeta() / f"{historico.nombre(hoy, indice)}_esc2.txt"
    if ruta.exists():
        return ruta.read_text(encoding="utf-8").strip(), "del día"
    return None, None


def main():
    hoy = datetime.now(ZoneInfo("Europe/Madrid")).date()
    for indice in ("NQ", "ES"):
        texto, cual = texto_de_hoy(indice, hoy)
        destino = RAIZ / f"HOY_{indice}.txt"
        if texto:
            destino.write_text(texto + "\n", encoding="utf-8")
            print(f"{indice}: escalera {cual} del {hoy} en {destino.name}")
        else:
            print(f"{indice}: aún no hay escalera del {hoy}")


if __name__ == "__main__":
    main()
