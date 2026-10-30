"""Motor del monitor (sin tkinter): arranca los workers, vigila las conexiones SQL Server
y publica contadores + líneas de log para que la GUI las dibuje.

Dos modos:
  * REAL: usa `get_settings()` + `Databases` y corre los workers de verdad
    (resultados-sync y ordenes-sync). Un hilo de monitoreo comprueba cada
    `MONITOR_INTERVAL_SECONDS` las dos conexiones (MINSA y Resultado) y cuenta los
    pendientes (`LabOrdenesInterop2.Flag = 0`). Además (24x7):
      - cada `LATIDO_SEGUNDOS` escribe en el archivo de log una línea "LATIDO" con el estado
        (conexiones, contadores, hilos, memoria): si el log se corta sin latidos, el proceso murió;
      - vigila los workers (hilo "vigilante", aparte de las consultas): si uno muere lo reinicia; si uno no
        avanza (ni una fila ni un ciclo) en `AVISO_BLOQUEO_SEGUNDOS` lo avisa, y en `REINICIO_POR_BLOQUEO_SEGUNDOS`
        reinicia el proceso completo (un hilo colgado en el driver no se puede matar; ver reinicio.py). Lo mismo si
        la memoria pasa de `LIMITE_MEMORIA_MB` (Python de 32 bits).
  * DEMO: si falta la configuración de conexión, genera datos simulados para poder
    ver y probar la interfaz sin tocar ninguna base de datos. Siempre se rotula como DEMO.
"""

from __future__ import annotations

import logging
import os
import queue
import random
import sys
import threading
import time
from datetime import datetime
from typing import Callable

from sqlalchemy import text

from .config import Settings, get_settings
from .config_store import CONNECTION_NAMES, ConfigStore
from .connection import describe, describe_error
from .db import Databases
from .logging_config import memoria_mb
from .reinicio import reiniciar_proceso
from .workers.base_worker import PollingWorker
from .workers.ordenes_worker import build_ordenes_worker
from .workers.resultados_worker import build_resultados_worker

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_SECONDS = 3.0
DEMO_INTERVAL_SECONDS = 1.5
VIGILANTE_INTERVALO_SEGUNDOS = 3.0
LATIDO_SEGUNDOS = 600.0  # una línea de estado en el archivo cada 10 min
# Sin avance de un worker (cada fila o fin de ciclo cuenta). Una consulta tarda como mucho 60 s (query timeout), la
# conexión 5 s y la espera entre ciclos 60 s: 5 min sin avanzar ya es anormal; 10 min es un bloqueo.
AVISO_BLOQUEO_SEGUNDOS = 300.0
REINICIO_POR_BLOQUEO_SEGUNDOS = 600.0
CHEQUEO_COLGADO_SEGUNDOS = 900.0  # el chequeo de conexiones (solo pantalla/latido): solo se avisa
# Python de 32 bits no pasa de ~2 GB: antes de llegar, se reinicia el proceso. En 64 bits no se limita.
LIMITE_MEMORIA_MB = float(os.environ.get("ORDEN_LIMITE_MEMORIA_MB", "1400" if sys.maxsize <= 2**32 else "0"))
MEMORIA_CADA_SEGUNDOS = 60.0

# Estado de cada conexión SQL Server (color del indicador en la GUI)
CONN_OK, CONN_FAIL, CONN_RETRY, CONN_DEMO, CONN_UNKNOWN = "ok", "fail", "retry", "demo", "unknown"
CONN_UNSET = "unset"  # cadena de conexión todavía no configurada
# Nivel de cada línea del monitor (color del texto en la GUI)
LEVEL_OK, LEVEL_INFO, LEVEL_WARN, LEVEL_ERROR = "ok", "info", "warn", "error"

CONNECTIONS = CONNECTION_NAMES

_LOG_LEVELS = {
    LEVEL_OK: logging.INFO,
    LEVEL_INFO: logging.INFO,
    LEVEL_WARN: logging.WARNING,
    LEVEL_ERROR: logging.ERROR,
}
_COUNT_PENDIENTES = text("SELECT COUNT(*) FROM LabOrdenesInterop2 WHERE Flag = 0")


_describe = describe_error


class Stats:
    """Contadores y estado de conexiones, compartidos entre hilos (leídos por la GUI)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = {
            "ciclos": 0,
            "pendientes": 0,
            "resultados": 0,
            "ordenes": 0,
            "errores": 0,
            "ultimo_ciclo": "--:--:--",
        }
        self._conn = {name: CONN_UNKNOWN for name in CONNECTIONS}

    def incr(self, key: str, amount: int = 1) -> int:
        with self._lock:
            self._data[key] += amount
            return self._data[key]

    def set(self, key: str, value: object) -> None:
        with self._lock:
            self._data[key] = value

    def set_conn(self, name: str, state: str) -> None:
        with self._lock:
            self._conn[name] = state

    def get_conn(self, name: str) -> str:
        with self._lock:
            return self._conn[name]

    def snapshot(self) -> "tuple[dict, dict]":
        with self._lock:
            return dict(self._data), dict(self._conn)


class Monitor:
    """Ciclo de vida del servicio: start() / stop(). Nunca bloquea el hilo de la GUI.

    Los mensajes para la GUI salen por `events` (queue.Queue):
      ("log", nivel, "[HH:MM:SS] texto")  y  ("stopped",)
    """

    def __init__(self, stats: Stats, events: "queue.Queue[tuple]", store: "ConfigStore | None" = None) -> None:
        self.stats = stats
        self._store = store
        self._events = events
        self.mode = "real"
        self.interval = MONITOR_INTERVAL_SECONDS
        self.labels = {name: name for name in CONNECTIONS}  # nombre visible (servidor/base o DSN) de cada conexión
        self._dbs: "Databases | None" = None
        self._workers: "list[PollingWorker]" = []
        self._fabricas: "dict[str, Callable[[], PollingWorker]]" = {}  # para reiniciar un worker que murió
        self._workers_lock = threading.Lock()  # el vigilante no reinicia workers mientras se detiene el servicio
        self._colgados: "set[str]" = set()
        self._reinicio_pedido: "set[str]" = set()
        # Mientras se detiene no se arranca otro juego de workers (dos ordenes-sync escribirían a la vez el DBF).
        self._deteniendo = False
        self._hilo_vigilante: "threading.Thread | None" = None
        self._ultimo_estado: "tuple[dict, int | None]" = ({name: None for name in CONNECTIONS}, None)
        self._ultimo_chequeo = time.monotonic()  # último ciclo del monitor (chequeo de conexiones)
        self._thread: "threading.Thread | None" = None
        self._stop_event = threading.Event()
        self._pend_error: "str | None" = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- ciclo de vida -----------------------------------------------------------

    def start(self) -> bool:
        if self._deteniendo:
            return False
        if self.running:
            return True
        self._stop_event = threading.Event()  # uno nuevo por arranque: los hilos viejos no reviven
        try:
            settings = get_settings(self._store)
        except RuntimeError:
            return self._start_demo()
        except Exception as exc:
            self.stats.incr("errores")
            self._log(LEVEL_ERROR, f"ERROR | Configuración inválida: {_describe(exc)}")
            return False
        if not settings.minsa_conn_str and not settings.resultado_conn_str:
            return self._start_demo()  # ninguna cadena configurada
        return self._start_real(settings)

    def stop(self) -> None:
        """Pide la detención y espera en un hilo aparte; al terminar publica ("stopped",)."""
        self._deteniendo = True
        threading.Thread(target=self._stop_blocking, name="monitor-stop", daemon=True).start()

    def _stop_blocking(self) -> None:
        self._stop_event.set()
        with self._workers_lock:
            for worker in self._workers:
                worker.stop()
        # Se espera a TODOS los hilos (cada uno termina su ciclo; una consulta dura como mucho 60 s). Solo entonces se
        # liberan los pools y se publica "detenido": así nunca hay dos juegos de workers. Si uno no termina nunca
        # (colgado en el driver), el proceso se reinicia (con el .bat); la configuración ya quedó guardada.
        hilos = [h for h in (*self._workers, self._thread, self._hilo_vigilante) if h is not None]
        inicio = time.monotonic()
        proximo_aviso = inicio + 30.0
        reinicio_pedido = False
        while True:
            vivos = [h for h in hilos if h.is_alive()]
            if not vivos:
                break
            vivos[0].join(timeout=1.0)
            espera = time.monotonic() - inicio
            if time.monotonic() >= proximo_aviso:
                proximo_aviso = time.monotonic() + 60.0
                self._log(LEVEL_WARN, f"ADVERTENCIA | Deteniendo: esperando a {', '.join(h.name for h in vivos)} "
                                      f"(hace {espera:.0f} s)")
            if espera >= REINICIO_POR_BLOQUEO_SEGUNDOS and not reinicio_pedido:
                reinicio_pedido = True
                reiniciar_proceso(f"La detención lleva {espera / 60:.0f} min esperando a "
                                  f"{', '.join(h.name for h in vivos)}")
        if self._dbs is not None:
            self._dbs.dispose()
        self._dbs = None
        self._workers = []
        self._fabricas = {}
        for name in CONNECTIONS:
            self.stats.set_conn(name, CONN_UNKNOWN)
        self._log(LEVEL_INFO, "INFO | Servicio detenido", file=self.mode == "real")
        self._deteniendo = False
        self._events.put(("stopped",))

    # --- modo real ---------------------------------------------------------------

    def _start_real(self, settings: Settings) -> bool:
        try:
            dbs = Databases(settings)
        except Exception as exc:
            self.stats.incr("errores")
            self._log(LEVEL_ERROR, f"ERROR | No se pudieron crear las conexiones: {_describe(exc)}")
            return False

        self.mode = "real"
        self.interval = MONITOR_INTERVAL_SECONDS
        cadenas = {"MINSA": settings.minsa_conn_str, "Resultado": settings.resultado_conn_str}
        self.labels = {name: describe(cadena) if cadena else name for name, cadena in cadenas.items()}
        for name, cadena in cadenas.items():
            self.stats.set_conn(name, CONN_UNKNOWN if cadena else CONN_UNSET)
        self._dbs = dbs

        # Cada worker arranca solo si tiene todo lo que necesita:
        #   ordenes-sync    -> MINSA
        #   resultados-sync -> MINSA + Resultado (actualiza MINSA con lo leído de Resultado)
        self._fabricas = {}
        if settings.procesar_resultados and settings.minsa_conn_str and settings.resultado_conn_str:
            self._fabricas["resultados-sync"] = lambda: build_resultados_worker(
                dbs,
                settings,
                on_cycle=self._cycle_handler("resultados-sync", "resultados", "resultado(s) transferido(s)"),
            )
        if settings.minsa_conn_str:
            self._fabricas["ordenes-sync"] = lambda: build_ordenes_worker(
                dbs, settings, on_cycle=self._cycle_handler("ordenes-sync", "ordenes", "orden(es) procesada(s)")
            )
        self._colgados = set()
        self._reinicio_pedido = set()
        self._workers = [fabrica() for fabrica in self._fabricas.values()]
        for worker in self._workers:
            worker.start()
        self._ultimo_chequeo = time.monotonic()
        self._spawn(self._loop_real)
        self._hilo_vigilante = threading.Thread(
            target=self._loop_vigilante, args=(self._stop_event,), name="vigilante", daemon=True
        )
        self._hilo_vigilante.start()

        conectadas = " ".join(f"{n} [{self.labels[n]}]" for n, c in cadenas.items() if c)
        self._log(LEVEL_INFO, f"INFO | Servicio iniciado (modo PRODUCCION) | {conectadas}")
        if not settings.minsa_conn_str:
            self._log(
                LEVEL_WARN,
                "ADVERTENCIA | MINSA sin configurar: solo se vigila la conexión de Resultado; "
                "ordenes-sync y resultados-sync no se inician hasta configurar MINSA",
            )
        elif not settings.procesar_resultados:
            self._log(
                LEVEL_INFO,
                "INFO | Envío de resultados desactivado: igual que el Fox de BELÉN, solo se procesan órdenes",
            )
        elif not settings.resultado_conn_str:
            self._log(
                LEVEL_WARN,
                "ADVERTENCIA | Resultado sin configurar: resultados-sync no se inicia hasta configurarla",
            )
        return True

    def _cycle_handler(self, worker: str, counter: str, noun: str):
        # Solo pantalla: el worker y el servicio ya escriben cada ciclo en el archivo (sin repetir errores).
        def handler(kind: str, count: int, detail: str) -> None:
            if kind == "ok":
                if count:
                    self.stats.incr(counter, count)
                    self._log(LEVEL_OK, f"PROCESADO | {worker}: {count} {noun}", file=False)
            else:
                self.stats.incr("errores")
                self._log(LEVEL_ERROR, f"ERROR | {worker}: {detail}", file=False)

        return handler

    def _loop_real(self, stop: threading.Event) -> None:
        dbs = self._dbs
        if dbs is None:
            return
        engines = {"MINSA": dbs.minsa_engine, "Resultado": dbs.resultado_engine}
        while not stop.is_set():
            try:
                # None = sin configurar (no se prueba); True/False = resultado del SELECT 1
                estados = {
                    name: None if engine is None else self._check(name, engine) for name, engine in engines.items()
                }
                pendientes = self._contar_pendientes(dbs) if estados["MINSA"] else None
                if pendientes is not None:
                    self.stats.set("pendientes", pendientes)
                self._log_ciclo(estados, pendientes)
                self._ultimo_estado = (estados, pendientes)
                self._ultimo_chequeo = time.monotonic()
            except Exception:  # el monitor no debe morir nunca (24x7)
                logger.exception("Error interno del monitor; se continúa")
            stop.wait(self.interval)

    def _loop_vigilante(self, stop: threading.Event) -> None:
        """Hilo aparte: si una consulta del monitor se cuelga, el vigilante y el LATIDO siguen funcionando."""
        proximo_latido = time.monotonic() + 15.0  # el primero tras el primer chequeo de conexiones
        proxima_memoria = time.monotonic() + MEMORIA_CADA_SEGUNDOS
        while not stop.wait(VIGILANTE_INTERVALO_SEGUNDOS):
            try:
                self._vigilar_workers(stop)
                if time.monotonic() >= proximo_latido:
                    self._latido()
                    proximo_latido = time.monotonic() + LATIDO_SEGUNDOS
                if time.monotonic() >= proxima_memoria:
                    proxima_memoria = time.monotonic() + MEMORIA_CADA_SEGUNDOS
                    self._vigilar_memoria()
            except Exception:
                logger.exception("Error interno del vigilante; se continúa")

    def _vigilar_workers(self, stop: threading.Event) -> None:
        """Reinicia un worker que murió; avisa si uno no avanza y reinicia el proceso si sigue sin avanzar."""
        for i, worker in enumerate(list(self._workers)):
            if not worker.is_alive():
                with self._workers_lock:
                    if stop.is_set() or worker.stop_requested:
                        return  # se está deteniendo el servicio: no es una caída
                    fabrica = self._fabricas.get(worker.name)
                    self.stats.incr("errores")
                    self._log(LEVEL_ERROR, f"ERROR | {worker.name} se detuvo inesperadamente; se reinicia")
                    if fabrica is not None:
                        nuevo = fabrica()
                        nuevo.start()
                        self._workers[i] = nuevo
                continue
            sin_avance = worker.segundos_sin_progreso()
            if sin_avance >= AVISO_BLOQUEO_SEGUNDOS:
                if worker.name not in self._colgados:
                    self._colgados.add(worker.name)
                    self.stats.incr("errores")
                    self._log(
                        LEVEL_ERROR,
                        f"ERROR | {worker.name} lleva {sin_avance / 60:.0f} min sin avanzar "
                        "(posible bloqueo en SQL Server o en el DBF)",
                    )
                if sin_avance >= REINICIO_POR_BLOQUEO_SEGUNDOS and worker.name not in self._reinicio_pedido:
                    self._reinicio_pedido.add(worker.name)
                    reiniciar_proceso(f"{worker.name} lleva {sin_avance / 60:.0f} min bloqueado")
            elif worker.name in self._colgados:
                self._colgados.discard(worker.name)
                self._reinicio_pedido.discard(worker.name)
                self._log(LEVEL_OK, f"INFO | {worker.name} volvió a responder")
        sin_chequeo = time.monotonic() - self._ultimo_chequeo
        if sin_chequeo >= CHEQUEO_COLGADO_SEGUNDOS and "monitor" not in self._colgados:
            self._colgados.add("monitor")
            self._log(LEVEL_ERROR, f"ERROR | el chequeo de conexiones lleva {sin_chequeo / 60:.0f} min sin responder")
        elif sin_chequeo < CHEQUEO_COLGADO_SEGUNDOS and "monitor" in self._colgados:
            self._colgados.discard("monitor")
            self._log(LEVEL_OK, "INFO | el chequeo de conexiones volvió a responder")

    def _vigilar_memoria(self) -> None:
        if LIMITE_MEMORIA_MB <= 0:
            return
        memoria = memoria_mb()
        if memoria is not None and memoria >= LIMITE_MEMORIA_MB:
            reiniciar_proceso(f"Memoria del proceso en {memoria:.0f} MB (límite {LIMITE_MEMORIA_MB:.0f} MB)")

    def _latido(self) -> None:
        estados, pendientes = self._ultimo_estado
        datos, _ = self.stats.snapshot()
        marca = {nombre: "-" if ok is None else "OK" if ok else "FALLA" for nombre, ok in estados.items()}
        hilos = ", ".join(
            f"{w.name} {'activo' if w.is_alive() else 'CAIDO'} (último ciclo hace {w.segundos_sin_ciclo():.0f}s)"
            for w in self._workers
        )
        memoria = memoria_mb()
        self._log(
            LEVEL_INFO,
            f"LATIDO | MINSA:{marca['MINSA']} Resultado:{marca['Resultado']} | "
            f"pendientes: {'?' if pendientes is None else pendientes} | resultados: {datos['resultados']} | "
            f"órdenes: {datos['ordenes']} | errores: {datos['errores']} | hilos: {hilos or 'ninguno'} | "
            f"chequeo hace {time.monotonic() - self._ultimo_chequeo:.0f}s | "
            f"memoria: {'?' if memoria is None else f'{memoria:.0f} MB'}",
        )

    def _check(self, name: str, engine) -> bool:
        """Prueba la conexión (SELECT 1). Verde = ok, rojo = falla, ámbar = reintentando."""
        previo = self.stats.get_conn(name)
        if previo == CONN_FAIL:
            self.stats.set_conn(name, CONN_RETRY)
        etiqueta = self.labels[name]
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            self.stats.set_conn(name, CONN_FAIL)
            if previo != CONN_FAIL:  # solo al pasar a "caída", no en cada reintento
                self.stats.incr("errores")
                self._log(
                    LEVEL_ERROR, f"ERROR | SQL Server [{etiqueta}] sin conexión: {_describe(exc)}. Reintentando..."
                )
            return False
        self.stats.set_conn(name, CONN_OK)
        if previo == CONN_FAIL:
            self._log(LEVEL_OK, f"INFO | SQL Server [{etiqueta}] reconectado")
        return True

    def _contar_pendientes(self, dbs: Databases) -> "int | None":
        try:
            with dbs.minsa_engine.connect() as conn:
                total = conn.execute(_COUNT_PENDIENTES).scalar_one()
        except Exception as exc:
            mensaje = _describe(exc)
            if mensaje != self._pend_error:  # no repetir el mismo error cada ciclo
                self._pend_error = mensaje
                self.stats.incr("errores")
                self._log(LEVEL_ERROR, f"ERROR | No se pudieron contar los pendientes: {mensaje}")
            return None
        self._pend_error = None
        return int(total)

    def _log_ciclo(self, estados: "dict[str, bool | None]", pendientes: "int | None") -> None:
        ciclo = self.stats.incr("ciclos")
        self.stats.set("ultimo_ciclo", datetime.now().strftime("%H:%M:%S"))
        datos, _ = self.stats.snapshot()
        marca = {nombre: "–" if ok is None else "✓" if ok else "✗" for nombre, ok in estados.items()}
        pend_txt = "?" if pendientes is None else str(pendientes)
        linea = (
            f"Ciclo {ciclo} | MINSA:{marca['MINSA']} Resultado:{marca['Resultado']} | "
            f"pendientes: {pend_txt} | resultados: {datos['resultados']} | órdenes: {datos['ordenes']}"
        )
        if False in estados.values():
            nivel = LEVEL_ERROR
        elif pendientes:
            nivel = LEVEL_WARN
        else:
            nivel = LEVEL_OK
        # Solo pantalla (una cada 3 s llenaría el disco): al archivo van los cambios de conexión y el LATIDO.
        self._log(nivel, linea, file=False)

    # --- modo demostración -------------------------------------------------------

    def _start_demo(self) -> bool:
        self.mode = "demo"
        self.interval = DEMO_INTERVAL_SECONDS
        self.labels = {name: name for name in CONNECTIONS}
        for name in CONNECTIONS:
            self.stats.set_conn(name, CONN_DEMO)
        self._spawn(self._loop_demo)
        self._log(
            LEVEL_WARN,
            "ADVERTENCIA | Sin cadenas de conexión: MODO DEMOSTRACIÓN (datos simulados, no se toca ninguna "
            "base de datos). Configúrelas en la pestaña Conexiones SQL Server",
        )
        return True

    def _loop_demo(self, stop: threading.Event) -> None:
        azar = random.Random()
        pendientes = 0
        while not stop.is_set():
            dado = azar.random()
            if dado < 0.2:
                nuevos = azar.randint(1, 4)
                pendientes += nuevos
                self._log(
                    LEVEL_WARN, f"ADVERTENCIA | Se encontraron {nuevos} órdenes pendientes", file=False
                )
            if pendientes and dado > 0.3:
                for _ in range(min(pendientes, azar.randint(1, 3))):
                    pendientes -= 1
                    self.stats.incr("ordenes")
                    self._log(
                        LEVEL_OK, f"PROCESADO | Orden {azar.randint(10000, 99999)} enviada correctamente", file=False
                    )
            if azar.random() < 0.3:
                self.stats.incr("resultados")
                self._log(
                    LEVEL_OK, f"PROCESADO | Resultado {azar.randint(10000, 99999)} transferido", file=False
                )
            if azar.random() < 0.04:
                self.stats.incr("errores")
                self._log(LEVEL_ERROR, "ERROR | (simulado) tiempo de espera agotado en MINSA", file=False)
            self.stats.set("pendientes", pendientes)
            self._log_ciclo({name: True for name in CONNECTIONS}, pendientes)
            stop.wait(self.interval)

    # --- utilidades --------------------------------------------------------------

    def _spawn(self, target) -> None:
        self._thread = threading.Thread(target=target, args=(self._stop_event,), name="monitor", daemon=True)
        self._thread.start()

    def _log(self, level: str, message: str, *, file: bool = True) -> None:
        self._events.put(("log", level, f"[{datetime.now():%H:%M:%S}] {message}"))
        if file:
            logger.log(_LOG_LEVELS[level], message)
