from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable

from ..errores import (
    CicloFallido,
    Repeticiones,
    es_error_de_conexion,
    es_error_de_configuracion,
    firma_error,
    resumen_error,
    sufijo_repeticiones,
)
from ..reinicio import reiniciar_proceso

logger = logging.getLogger(__name__)


class PollingWorker(threading.Thread):
    """Hilo de fondo que ejecuta `poll_fn` en bucle con intervalo adaptativo.

    Replica el patrón de Thisform.timer1 en temporizado.txt: cuando `poll_fn`
    procesó registros, reintenta pronto (busy_interval); cuando no hubo nada
    pendiente, espera más (idle_interval) -- igual que
    'thisform.timer1.interval=100' cuando RECCOUNT('vorden')=0.

    A diferencia del Timer de VFP (que corre en el hilo de UI), cada worker corre
    en su propio hilo daemon, así que resultados y órdenes se sincronizan en
    paralelo sin bloquearse entre sí.

    Manejo de red intermitente: un error a nivel de conexión/driver
    (`sqlalchemy.exc.DBAPIError`, que envuelve los errores de pyodbc: timeout,
    host inalcanzable, conexión reiniciada, etc.) no se trata como un error de
    datos -- se aplica backoff exponencial (`backoff_initial` -> `backoff_max`,
    multiplicando por `backoff_factor` en cada fallo consecutivo, reseteado al
    primer ciclo exitoso) para no martillar reconexiones contra una red caída.
    Lo mismo vale para cualquier otro fallo del ciclo (tabla inexistente, DBF bloqueado, filas que
    fallan siempre): antes esos casos reintentaban cada `idle_interval` (0,1 s en órdenes).

    Un mismo error se escribe completo en el log una sola vez cada 10 min; las repeticiones se cuentan
    (ver `errores.Repeticiones`). `ultimo_ciclo` permite al monitor detectar un hilo colgado.
    """

    def __init__(
        self,
        name: str,
        poll_fn: Callable[[], int],
        *,
        idle_interval: float,
        busy_interval: float,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
        backoff_factor: float = 2.0,
        on_status: Callable[[str], None] | None = None,
        on_cycle: Callable[[str, int, str], None] | None = None,
    ) -> None:
        super().__init__(name=name, daemon=True)
        self._poll_fn = poll_fn
        self._idle_interval = idle_interval
        self._busy_interval = busy_interval
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._backoff_factor = backoff_factor
        self._current_backoff = backoff_initial
        self._on_status = on_status
        self._on_cycle = on_cycle
        self._stop_event = threading.Event()
        self._repetidos = Repeticiones()
        self._fallos_seguidos = 0
        self._fallando_desde = ""
        self.ultimo_ciclo = time.monotonic()  # fin del último ciclo (monotónico), para el LATIDO
        # Último avance: fin de ciclo o cada fila procesada (marcar_progreso). Si no cambia en minutos, una llamada
        # quedó colgada (un ciclo largo pero sano sí avanza fila por fila).
        self.ultimo_progreso = self.ultimo_ciclo

    def segundos_sin_ciclo(self) -> float:
        return time.monotonic() - self.ultimo_ciclo

    def marcar_progreso(self) -> None:
        self.ultimo_progreso = time.monotonic()

    def segundos_sin_progreso(self) -> float:
        return time.monotonic() - self.ultimo_progreso

    def _notify(self, message: str) -> None:
        # Equivalente a Thisform.caption='...' del original. Nunca debe tumbar
        # el hilo de trabajo si quien escucha (p.ej. la GUI) falla.
        if self._on_status is None:
            return
        try:
            self._on_status(message)
        except Exception:
            logger.exception("Worker '%s': el callback de estado falló", self.name)

    def _emit(self, kind: str, count: int, detail: str) -> None:
        # Resultado estructurado de cada ciclo para monitores (GUI): kind es "ok",
        # "connection" o "error". Igual que _notify, nunca debe tumbar el hilo.
        if self._on_cycle is None:
            return
        try:
            self._on_cycle(kind, count, detail)
        except Exception:
            logger.exception("Worker '%s': el callback de ciclo falló", self.name)

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def stop_requested(self) -> bool:
        """True si se pidió detenerlo (un worker que terminó sin esto, murió)."""
        return self._stop_event.is_set()

    def _run_cycle(self) -> float:
        """Ejecuta un ciclo de polling y devuelve el intervalo de espera a usar.

        Aislado de `run()` para poder testear la lógica de backoff de forma
        determinista, sin threads ni sleeps reales.
        """
        try:
            procesadas = self._poll_fn()
        except Exception as exc:
            return self._ciclo_fallido(exc)
        finally:
            self.ultimo_ciclo = self.ultimo_progreso = time.monotonic()
        self._current_backoff = self._backoff_initial  # ciclo OK: resetea el backoff
        if self._fallos_seguidos:
            logger.info(
                "Worker '%s': recuperado tras %s ciclo(s) con error (fallaba desde %s)",
                self.name,
                self._fallos_seguidos,
                self._fallando_desde,
            )
            self._fallos_seguidos = 0
        self._emit("ok", procesadas or 0, "")
        if procesadas:
            self._notify(f"{procesadas} procesada(s)")
            return self._busy_interval
        self._notify("Sin pendientes...")
        return self._idle_interval

    def _ciclo_fallido(self, exc: Exception) -> float:
        if isinstance(exc, MemoryError):
            # Python de 32 bits sin memoria: reintentar en el mismo proceso no la libera.
            reiniciar_proceso(f"Worker '{self.name}': memoria agotada")
        interval = self._current_backoff
        self._current_backoff = min(self._current_backoff * self._backoff_factor, self._backoff_max)
        if not self._fallos_seguidos:
            self._fallando_desde = datetime.now().strftime("%d/%m %H:%M:%S")
        self._fallos_seguidos += 1

        if es_error_de_conexion(exc):
            kind, aviso = "connection", "problema de conexión"
        elif es_error_de_configuracion(exc):
            kind, aviso = "error", "error de configuración de la base (tabla, columna, procedimiento, permiso o usuario)"
        elif isinstance(exc, CicloFallido):
            kind, aviso = "error", "ninguna fila se pudo procesar"
        else:
            kind, aviso = "error", "fallo no controlado"
        resumen = resumen_error(exc)

        omitidos = self._repetidos.registrar(firma_error(exc))
        if omitidos is not None:
            # Traceback solo la primera vez de un fallo que no es de red (CicloFallido ya lo detalló por fila).
            con_traceback = kind == "error" and not isinstance(exc, CicloFallido) and omitidos == 0
            logger.log(
                logging.WARNING if kind == "connection" else logging.ERROR,
                "Worker '%s': %s (%s). Reintentando en %.0fs%s",
                self.name,
                aviso,
                resumen,
                interval,
                sufijo_repeticiones(omitidos),
                exc_info=con_traceback,
            )
        self._emit(kind, 0, f"{aviso} ({resumen[:150]}), reintentando en {interval:.0f}s")
        if kind == "connection":
            self._notify(f"Sin conexión, reintentando en {interval:.0f}s...")
        else:
            self._notify(f"Error (ver logs), reintentando en {interval:.0f}s...")
        return interval

    def run(self) -> None:
        logger.info("Worker '%s' iniciado", self.name)
        while not self._stop_event.is_set():
            try:
                interval = self._run_cycle()
            except Exception:  # _run_cycle ya atrapa todo; esto solo evita que el hilo muera (24x7)
                logger.exception("Worker '%s': error interno del ciclo", self.name)
                interval = self._backoff_max
            self._stop_event.wait(interval)
        logger.info("Worker '%s' detenido", self.name)
