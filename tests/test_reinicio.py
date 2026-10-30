import pytest

from orden_belen import reinicio


@pytest.fixture
def terminaciones(monkeypatch) -> list:
    codigos: list = []
    monkeypatch.setattr(reinicio, "_terminar", codigos.append)
    monkeypatch.setattr(reinicio, "_pedido", False)
    monkeypatch.setattr(reinicio, "_ESPERA_MAX_LOG_SEGUNDOS", 0.05)
    return codigos


def test_sin_el_bat_solo_avisa_y_no_termina(monkeypatch, terminaciones, caplog) -> None:
    monkeypatch.delenv("ORDEN_SUPERVISADO", raising=False)
    with caplog.at_level("ERROR", logger="orden_belen.reinicio"):
        assert reinicio.reiniciar_proceso("prueba") is False
    assert terminaciones == []
    assert "iniciar_orden_belen.bat" in caplog.records[0].getMessage()


def test_con_el_bat_termina_con_codigo_3_una_sola_vez(monkeypatch, terminaciones) -> None:
    import time

    monkeypatch.setenv("ORDEN_SUPERVISADO", "1")
    assert reinicio.reiniciar_proceso("bloqueo") is True
    assert reinicio.reiniciar_proceso("otro") is True  # ya pedido: no repite
    time.sleep(0.2)  # el temporizador de respaldo también llama a terminar (por si el log estuviera colgado)
    assert terminaciones and set(terminaciones) == {reinicio.CODIGO_REINICIO}
