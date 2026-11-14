"""Carga del archivo de configuración config.yaml."""
import copy
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


def cargar(ruta=RAIZ / "config.yaml"):
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _mezclar(base, encima):
    """Copia de `base` con los valores de `encima` (diccionarios anidados)."""
    resultado = copy.deepcopy(base)
    for clave, valor in encima.items():
        if isinstance(valor, dict) and isinstance(resultado.get(clave), dict):
            resultado[clave] = _mezclar(resultado[clave], valor)
        else:
            resultado[clave] = copy.deepcopy(valor)
    return resultado


def para_indice(config, indice):
    """Configuración para un índice: la global con sus "parametros" encima.

    Un índice sin "parametros" (el NQ) recibe una copia de la global tal cual.
    """
    resultado = _mezclar(config, config["indices"][indice].get("parametros") or {})
    resultado["indice"] = indice
    return resultado
