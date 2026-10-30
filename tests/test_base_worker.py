from sqlalchemy.exc import DBAPIError

from orden_belen.workers.base_worker import PollingWorker


def _dbapi_error() -> DBAPIError:
    return DBAPIError("SELECT 1", {}, ConnectionError("host unreachable"), connection_invalidated=True)


def _worker(poll_fn) -> PollingWorker:
    return PollingWorker(
        "t",
        poll_fn,
        idle_interval=10.0,
        busy_interval=0.5,
        backoff_initial=1.0,
        backoff_max=4.0,
        backoff_factor=2.0,
    )


def test_ciclo_con_trabajo_usa_busy_interval() -> None:
    worker = _worker(lambda: 3)
    assert worker._run_cycle() == 0.5


def test_ciclo_sin_trabajo_usa_idle_interval() -> None:
    worker = _worker(lambda: 0)
    assert worker._run_cycle() == 10.0


def test_error_de_conexion_activa_backoff_exponencial_acotado() -> None:
    def poll_fn():
        raise _dbapi_error()

    worker = _worker(poll_fn)

    assert worker._run_cycle() == 1.0  # backoff_initial
    assert worker._run_cycle() == 2.0  # * backoff_factor
    assert worker._run_cycle() == 4.0  # * backoff_factor, ya en backoff_max
    assert worker._run_cycle() == 4.0  # acotado, no sigue creciendo


def test_backoff_se_resetea_tras_un_ciclo_exitoso() -> None:
    state = {"fail": True}

    def poll_fn():
        if state["fail"]:
            raise _dbapi_error()
        return 0

    worker = _worker(poll_fn)

    assert worker._run_cycle() == 1.0
    assert worker._run_cycle() == 2.0

    state["fail"] = False
    assert worker._run_cycle() == 10.0  # ciclo OK, vuelve a idle_interval

    state["fail"] = True
    assert worker._run_cycle() == 1.0  # backoff arrancó de nuevo desde el inicial


def test_error_generico_tambien_activa_backoff() -> None:
    # Antes caía al idle_interval (0,1 s en órdenes) y reintentaba sin pausa un error que se repetía siempre.
    def poll_fn():
        raise ValueError("dato inválido, no es un problema de red")

    worker = _worker(poll_fn)

    assert worker._run_cycle() == 1.0
    assert worker._run_cycle() == 2.0
    assert worker._run_cycle() == 4.0


def test_error_de_configuracion_se_escribe_una_vez_y_se_informa_la_recuperacion(caplog) -> None:
    from sqlalchemy.exc import ProgrammingError

    estado = {"falla": True}

    def poll_fn():
        if estado["falla"]:
            raise ProgrammingError(
                "UPDATE",
                {},
                Exception("42S02", "[42S02] El nombre de objeto 'InterLabDetalle' no es válido. (208) (SQLExecDirectW)"),
            )
        return 0

    worker = _worker(poll_fn)
    with caplog.at_level("INFO", logger="orden_belen.workers.base_worker"):
        for _ in range(50):
            worker._run_cycle()
        estado["falla"] = False
        worker._run_cycle()

    errores = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errores) == 1  # 50 fallos iguales: una sola línea (con traceback), no 50
    assert "configuración" in errores[0].getMessage()
    assert errores[0].exc_info is not None
    assert any("recuperado tras 50 ciclo(s)" in r.getMessage() for r in caplog.records)


def test_ultimo_ciclo_se_actualiza_aunque_falle() -> None:
    def poll_fn():
        raise ValueError("x")

    worker = _worker(poll_fn)
    worker.ultimo_ciclo = 0.0
    worker._run_cycle()
    assert worker.segundos_sin_ciclo() < 5


def test_stop_requested() -> None:
    worker = _worker(lambda: 0)
    assert not worker.stop_requested
    worker.stop()
    assert worker.stop_requested


def test_on_status_recibe_mensajes_legibles_por_ciclo() -> None:
    mensajes: list[str] = []
    worker = PollingWorker(
        "t",
        lambda: 2,
        idle_interval=10.0,
        busy_interval=0.5,
        on_status=mensajes.append,
    )
    worker._run_cycle()
    assert mensajes == ["2 procesada(s)"]


def test_on_status_que_falla_no_rompe_el_ciclo() -> None:
    def on_status(_msg: str) -> None:
        raise RuntimeError("la GUI se cerró a mitad de camino")

    worker = PollingWorker(
        "t", lambda: 0, idle_interval=10.0, busy_interval=0.5, on_status=on_status
    )
    assert worker._run_cycle() == 10.0  # no propaga la excepción del callback


def test_on_cycle_informa_ok_conexion_y_error() -> None:
    eventos = []

    def con_cb(poll_fn) -> PollingWorker:
        return PollingWorker(
            "t", poll_fn, idle_interval=1.0, busy_interval=0.5, on_cycle=lambda *args: eventos.append(args)
        )

    con_cb(lambda: 3)._run_cycle()
    assert eventos[-1] == ("ok", 3, "")

    def caida():
        raise _dbapi_error()

    con_cb(caida)._run_cycle()
    assert eventos[-1][0] == "connection"

    def rota():
        raise ValueError("x")

    con_cb(rota)._run_cycle()
    assert eventos[-1][0] == "error"


def test_memoria_agotada_pide_reiniciar_el_proceso(monkeypatch) -> None:
    from orden_belen.workers import base_worker

    pedidos = []
    monkeypatch.setattr(base_worker, "reiniciar_proceso", pedidos.append)

    def poll_fn():
        raise MemoryError()

    _worker(poll_fn)._run_cycle()
    assert len(pedidos) == 1 and "memoria" in pedidos[0]


def test_marcar_progreso() -> None:
    worker = _worker(lambda: 0)
    worker.ultimo_progreso = 0.0
    assert worker.segundos_sin_progreso() > 1000
    worker.marcar_progreso()
    assert worker.segundos_sin_progreso() < 5
