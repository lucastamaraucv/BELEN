import importlib

import pytest

MODULOS = [
    "orden_belen.config",
    "orden_belen.config_store",
    "orden_belen.connection",
    "orden_belen.db",
    "orden_belen.dbf_io",
    "orden_belen.diagnostico",
    "orden_belen.errores",
    "orden_belen.gui",
    "orden_belen.instancia",
    "orden_belen.lanzador",
    "orden_belen.logging_config",
    "orden_belen.main",
    "orden_belen.mappers",
    "orden_belen.monitor",
    "orden_belen.rutas",
    "orden_belen.reinicio",
    "orden_belen.services.ordenes_sync",
    "orden_belen.services.resultados_sync",
    "orden_belen.workers.base_worker",
    "orden_belen.workers.ordenes_worker",
    "orden_belen.workers.resultados_worker",
]


@pytest.mark.parametrize("nombre", MODULOS)
def test_el_modulo_se_importa(nombre) -> None:
    # La GUI y main no tienen tests propios: esto al menos detecta errores de sintaxis o de import.
    importlib.import_module(nombre)


def test_instancia_unica_en_el_mismo_proceso_no_se_bloquea_a_si_misma() -> None:
    from orden_belen.instancia import tomar_instancia_unica

    assert tomar_instancia_unica() is True
