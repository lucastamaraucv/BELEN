import queue
import threading
import time

import pytest

from orden_belen import monitor as monitor_module
from orden_belen.monitor import CONN_DEMO, Monitor, Stats


def _sin_configuracion(*_args):
    raise RuntimeError("falta configuración")


def test_sin_configuracion_arranca_en_modo_demo_y_se_detiene(monkeypatch) -> None:
    monkeypatch.setattr(monitor_module, "get_settings", _sin_configuracion)
    monkeypatch.setattr(monitor_module, "DEMO_INTERVAL_SECONDS", 0.02)
    stats, events = Stats(), queue.Queue()
    mon = Monitor(stats, events)

    assert mon.start() is True
    assert mon.mode == "demo"
    time.sleep(0.3)

    datos, conexiones = stats.snapshot()
    assert datos["ciclos"] > 0
    assert set(conexiones.values()) == {CONN_DEMO}
    assert any(e[0] == "log" for e in list(events.queue))

    mon.stop()
    detenido = False
    for _ in range(100):
        try:
            if events.get(timeout=0.1)[0] == "stopped":
                detenido = True
                break
        except queue.Empty:
            pass
    assert detenido
    assert not mon.running


class _EngineOk:
    """Engine falso: connect() funciona y SELECT 1 responde."""

    def connect(self):
        from contextlib import contextmanager

        @contextmanager
        def ctx():
            class Conn:
                def execute(self, *_a, **_k):
                    return None

            yield Conn()

        return ctx()


def test_configuracion_parcial_solo_vigila_resultado_y_no_inicia_workers(monkeypatch, tmp_path) -> None:
    from orden_belen.config import Settings
    from orden_belen.monitor import CONN_OK, CONN_UNSET

    settings = Settings(minsa_conn_str=None, resultado_conn_str="DSN=Jalisco;UID=a;PWD=b")

    class FakeDbs:
        minsa_engine = None
        resultado_engine = _EngineOk()

        def __init__(self, _settings):
            pass

        def dispose(self):
            pass

    monkeypatch.setattr(monitor_module, "get_settings", lambda *_a: settings)
    monkeypatch.setattr(monitor_module, "Databases", FakeDbs)
    monkeypatch.setattr(monitor_module, "MONITOR_INTERVAL_SECONDS", 0.02)
    stats, events = Stats(), queue.Queue()
    mon = Monitor(stats, events)

    assert mon.start() is True
    time.sleep(0.3)
    datos, conexiones = stats.snapshot()
    mon.stop()

    assert mon._workers == []  # ni ordenes-sync ni resultados-sync sin MINSA
    assert conexiones["MINSA"] == CONN_UNSET
    assert conexiones["Resultado"] == CONN_OK
    assert datos["ciclos"] > 0 and datos["errores"] == 0


class _WorkerFalso:
    def __init__(self, name: str, *, vivo: bool = True, sin_ciclo: float = 0.0) -> None:
        self.name = name
        self.vivo = vivo
        self.sin_ciclo = sin_ciclo
        self.stop_requested = False
        self.iniciado = False

    def is_alive(self) -> bool:
        return self.vivo

    def segundos_sin_ciclo(self) -> float:
        return self.sin_ciclo

    def segundos_sin_progreso(self) -> float:
        return self.sin_ciclo

    def start(self) -> None:
        self.iniciado = True
        self.vivo = True

    def stop(self) -> None:
        self.stop_requested = True

    def join(self, timeout=None) -> None:
        time.sleep(min(timeout or 0.0, 0.01))


def _errores_en(events: "queue.Queue") -> list:
    return [e for e in list(events.queue) if e[0] == "log" and e[1] == "error"]


@pytest.fixture
def reinicios(monkeypatch) -> list:
    """Registra los pedidos de reinicio del proceso en vez de terminar pytest."""
    pedidos: list = []
    monkeypatch.setattr(monitor_module, "reiniciar_proceso", lambda motivo: pedidos.append(motivo) or True)
    return pedidos


def test_vigilante_reinicia_un_worker_que_murio() -> None:
    stats, events = Stats(), queue.Queue()
    mon = Monitor(stats, events)
    muerto, nuevo = _WorkerFalso("ordenes-sync", vivo=False), _WorkerFalso("ordenes-sync", vivo=False)
    mon._workers = [muerto]
    mon._fabricas = {"ordenes-sync": lambda: nuevo}

    mon._vigilar_workers(threading.Event())

    assert mon._workers == [nuevo] and nuevo.iniciado
    assert len(_errores_en(events)) == 1


def test_vigilante_no_reinicia_si_se_esta_deteniendo() -> None:
    mon = Monitor(Stats(), queue.Queue())
    muerto = _WorkerFalso("ordenes-sync", vivo=False)
    muerto.stop_requested = True
    mon._workers = [muerto]
    mon._fabricas = {"ordenes-sync": lambda: _WorkerFalso("ordenes-sync")}

    mon._vigilar_workers(threading.Event())

    assert mon._workers == [muerto]


def test_vigilante_avisa_una_sola_vez_si_un_worker_no_avanza_y_cuando_vuelve(reinicios) -> None:
    stats, events = Stats(), queue.Queue()
    mon = Monitor(stats, events)
    lento = _WorkerFalso("resultados-sync", sin_ciclo=monitor_module.AVISO_BLOQUEO_SEGUNDOS + 60)
    mon._workers = [lento]
    stop = threading.Event()

    mon._vigilar_workers(stop)
    mon._vigilar_workers(stop)
    assert len(_errores_en(events)) == 1
    assert reinicios == []  # 6 min sin avanzar: solo aviso

    lento.sin_ciclo = 1.0
    mon._vigilar_workers(stop)
    assert any("volvió a responder" in e[2] for e in list(events.queue) if e[0] == "log")


def test_worker_bloqueado_10_min_reinicia_el_proceso_una_vez(reinicios) -> None:
    # Un commit/rollback que no vuelve nunca: el hilo no se puede matar, el proceso sí (el .bat lo reabre).
    mon = Monitor(Stats(), queue.Queue())
    mon._workers = [_WorkerFalso("ordenes-sync", sin_ciclo=monitor_module.REINICIO_POR_BLOQUEO_SEGUNDOS + 1)]
    stop = threading.Event()

    mon._vigilar_workers(stop)
    mon._vigilar_workers(stop)

    assert len(reinicios) == 1 and "ordenes-sync" in reinicios[0]


def test_memoria_alta_reinicia_el_proceso(monkeypatch, reinicios) -> None:
    monkeypatch.setattr(monitor_module, "LIMITE_MEMORIA_MB", 1400.0)
    monkeypatch.setattr(monitor_module, "memoria_mb", lambda: 1500.0)
    Monitor(Stats(), queue.Queue())._vigilar_memoria()
    assert len(reinicios) == 1 and "1500 MB" in reinicios[0]

    reinicios.clear()
    monkeypatch.setattr(monitor_module, "memoria_mb", lambda: 300.0)
    Monitor(Stats(), queue.Queue())._vigilar_memoria()
    assert reinicios == []


def test_latido_escribe_el_estado_en_el_archivo(caplog) -> None:
    mon = Monitor(Stats(), queue.Queue())
    mon._workers = [_WorkerFalso("ordenes-sync", sin_ciclo=2.0)]
    mon._ultimo_estado = ({"MINSA": True, "Resultado": False}, 3)
    with caplog.at_level("INFO", logger="orden_belen.monitor"):
        mon._latido()
    linea = next(r.getMessage() for r in caplog.records if "LATIDO" in r.getMessage())
    assert "MINSA:OK" in linea and "Resultado:FALLA" in linea and "pendientes: 3" in linea
    assert "ordenes-sync activo" in linea and "memoria:" in linea


class _WorkerLento(threading.Thread):
    """Worker real que tarda en terminar su ciclo (como una consulta larga) después de pedirle que pare."""

    def __init__(self, demora: float) -> None:
        super().__init__(name="ordenes-sync", daemon=True)
        self._demora = demora
        self._pedido = threading.Event()
        self.stop_requested = False

    def stop(self) -> None:
        self.stop_requested = True
        self._pedido.set()

    def run(self) -> None:
        self._pedido.wait()
        time.sleep(self._demora)


def test_detener_espera_a_que_terminen_todos_los_hilos_antes_de_publicar_detenido() -> None:
    events = queue.Queue()
    mon = Monitor(Stats(), events)
    lento = _WorkerLento(0.4)
    lento.start()

    class _Dbs:
        liberado_con_worker_vivo = None

        def dispose(self) -> None:
            _Dbs.liberado_con_worker_vivo = lento.is_alive()

    mon._workers = [lento]
    mon._dbs = _Dbs()
    mon.stop()
    assert mon.start() is False  # mientras se detiene no arranca otro juego de workers

    evento = None
    for _ in range(100):
        evento = events.get(timeout=0.1) if not events.empty() else None
        if evento == ("stopped",):
            break
        time.sleep(0.02)
    assert evento == ("stopped",)
    assert not lento.is_alive()
    assert _Dbs.liberado_con_worker_vivo is False  # los pools se liberan recién cuando el worker terminó
    assert mon._deteniendo is False


def test_detener_con_un_hilo_que_no_termina_nunca_reinicia_el_proceso(monkeypatch, reinicios) -> None:
    monkeypatch.setattr(monitor_module, "REINICIO_POR_BLOQUEO_SEGUNDOS", 0.2)
    events = queue.Queue()
    mon = Monitor(Stats(), events)
    colgado = _WorkerLento(3600)  # nunca termina en el test
    colgado.start()
    mon._workers = [colgado]

    mon.stop()
    for _ in range(100):
        if reinicios:
            break
        time.sleep(0.02)

    assert len(reinicios) == 1 and "ordenes-sync" in reinicios[0]
    assert ("stopped",) not in list(events.queue)  # no anuncia una detención que no ocurrió


def test_vigilante_avisa_si_el_chequeo_de_conexiones_se_cuelga() -> None:
    events = queue.Queue()
    mon = Monitor(Stats(), events)
    mon._ultimo_chequeo = time.monotonic() - monitor_module.CHEQUEO_COLGADO_SEGUNDOS - 5
    stop = threading.Event()

    mon._vigilar_workers(stop)
    mon._vigilar_workers(stop)
    assert len([e for e in _errores_en(events) if "chequeo de conexiones" in e[2]]) == 1

    mon._ultimo_chequeo = time.monotonic()
    mon._vigilar_workers(stop)
    assert any("volvió a responder" in e[2] for e in list(events.queue) if e[0] == "log")


def test_por_defecto_solo_procesa_ordenes_como_el_fox_de_belen(monkeypatch) -> None:
    # En BELÉN InterLabDetalle no existe en SIGH y el Fox tiene comentado el envío de resultados.
    from orden_belen.config import Settings

    settings = Settings(minsa_conn_str="SERVER=192.168.2.12;UID=a;PWD=b", resultado_conn_str="SERVER=x;UID=a;PWD=b")
    construidos = []

    class FakeDbs:
        minsa_engine = None
        resultado_engine = None

        def __init__(self, _settings):
            pass

        def dispose(self):
            pass

    def falso(nombre):
        def construir(*_a, **_k):
            construidos.append(nombre)
            return _WorkerFalso(nombre)

        return construir

    monkeypatch.setattr(monitor_module, "get_settings", lambda *_a: settings)
    monkeypatch.setattr(monitor_module, "Databases", FakeDbs)
    monkeypatch.setattr(monitor_module, "build_ordenes_worker", falso("ordenes-sync"))
    monkeypatch.setattr(monitor_module, "build_resultados_worker", falso("resultados-sync"))
    events = queue.Queue()
    mon = Monitor(Stats(), events)
    assert mon.start() is True
    mon._stop_event.set()

    assert construidos == ["ordenes-sync"]
    assert any("resultados desactivado" in e[2] for e in list(events.queue) if e[0] == "log")

    construidos.clear()
    import dataclasses

    settings = dataclasses.replace(settings, procesar_resultados=True)
    mon2 = Monitor(Stats(), queue.Queue())
    assert mon2.start() is True
    mon2._stop_event.set()
    assert sorted(construidos) == ["ordenes-sync", "resultados-sync"]
