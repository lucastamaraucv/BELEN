"""Logging de la aplicación (24x7): consola + archivo diario con tope de tamaño y borrado automático.

Archivos en `logs/` (carpeta PYTHON\\logs):
  * orden_belen_AAAA-MM-DD.log  (y _2, _3... si un día pasa de LOG_MAX_MB_ARCHIVO)
  * fallos_python.log           volcado de faulthandler si Python se cae por un error interno (pyodbc, ODBC...)
Al arrancar, al cambiar de día y al abrir cada parte nueva se borran los logs de más de LOG_DIAS_RETENCION
días y, empezando por los más viejos, los que no quepan en LOG_MAX_MB_TOTAL. Así la carpeta nunca crece sin
límite (el 29/10/2026 un solo archivo llegó a 3,6 GB).
"""

from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

from .rutas import CARPETA_APP

FORMATO = "%(asctime)s | %(levelname)-8s | %(threadName)-16s | %(name)s | %(message)s"

# PYTHON\logs o, en el .exe, logs junto a OrdenBelen.exe (ORDEN_LOG_DIR la cambia, p. ej. para pruebas).
DIRECTORIO_LOGS = Path(os.environ.get("ORDEN_LOG_DIR") or CARPETA_APP / "logs")

# Topes por defecto (sobreescribibles con variables de entorno ORDEN_LOG_*).
LOG_MAX_MB_ARCHIVO = float(os.environ.get("ORDEN_LOG_MAX_MB_ARCHIVO", "10"))
LOG_MAX_MB_TOTAL = float(os.environ.get("ORDEN_LOG_MAX_MB_TOTAL", "100"))
LOG_DIAS_RETENCION = float(os.environ.get("ORDEN_LOG_DIAS_RETENCION", "15"))

_PATRON = "orden_belen_*.log"
_MB = 1024 * 1024
_MAX_BYTES_FALLOS = 5 * _MB

logger = logging.getLogger(__name__)


def configure_logging(level: str = "INFO") -> None:
    """Reemplaza los WAIT WINDOW / Thisform.caption del original por logging estructurado."""
    # Con pythonw (sin consola) sys.stdout es None: no se agrega salida por pantalla.
    consola = logging.StreamHandler(sys.stdout) if sys.stdout is not None else logging.NullHandler()
    logging.basicConfig(level=level, format=FORMATO, handlers=[consola])


class _LogAcotado(logging.FileHandler):
    """Archivo diario `orden_belen_AAAA-MM-DD.log` que se parte al llegar a `max_bytes_archivo` y purga
    los archivos viejos para que la carpeta no pase de `max_bytes_total` ni guarde más de `dias_retencion`."""

    def __init__(
        self, directory: Path, *, max_bytes_archivo: int, max_bytes_total: int, dias_retencion: float
    ) -> None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # emit() reintentará abrir el archivo en cada línea; el programa arranca igual
        self._directory = directory
        self._max_archivo = max_bytes_archivo
        self._max_total = max_bytes_total
        self._dias = dias_retencion
        self._day = date.today()
        self._parte = self._ultima_parte()
        # delay=True: el archivo se abre en la primera línea, dentro de emit(), donde un error no detiene nada.
        super().__init__(self._current_path(), encoding="utf-8", delay=True)
        self._purgar()

    def _ruta(self, parte: int) -> Path:
        sufijo = f"_{parte}" if parte > 1 else ""
        return self._directory / f"orden_belen_{self._day.isoformat()}{sufijo}.log"

    def _current_path(self) -> str:
        return str(self._ruta(self._parte))

    def _ultima_parte(self) -> int:
        # Si la app se reinicia el mismo día, sigue escribiendo en la última parte (sin pisar nada). Se busca el
        # número más alto: la purga puede haber borrado partes intermedias.
        partes = [1]
        try:
            for p in self._directory.glob(f"orden_belen_{self._day.isoformat()}_*.log"):
                sufijo = p.stem.rsplit("_", 1)[-1]
                if sufijo.isdigit():
                    partes.append(int(sufijo))
        except OSError:
            pass
        return max(partes)

    def _tamano_actual(self) -> int:
        try:
            return os.fstat(self.stream.fileno()).st_size if self.stream is not None else 0
        except (OSError, ValueError):
            return 0

    def _cambiar_archivo(self) -> None:
        if self.stream is not None:
            try:
                self.stream.close()
            finally:
                self.stream = None  # FileHandler.emit abre el nuevo archivo
        self.baseFilename = os.path.abspath(self._current_path())
        self._purgar()

    def emit(self, record: logging.LogRecord) -> None:
        # Handler.handle() ya tiene tomado self.lock mientras se llama a emit().
        # Un fallo del log (disco lleno, carpeta sin permiso, archivo bloqueado) nunca debe llegar al hilo que
        # escribe: FileHandler.emit abre el archivo FUERA de su propio try, y la excepción mataría un worker.
        # Se descarta esa línea (handleError) y el archivo se vuelve a intentar en la siguiente.
        try:
            hoy = date.today()
            if hoy != self._day:
                self._day = hoy
                self._parte = self._ultima_parte()
                self._cambiar_archivo()
            elif self._tamano_actual() >= self._max_archivo:
                self._parte += 1
                self._cambiar_archivo()
            super().emit(record)
        except Exception:
            self.stream = None
            self.handleError(record)

    def _purgar(self) -> None:
        """Del más nuevo al más viejo: conserva lo que quepa en el tope total y no sea más viejo que la
        retención; borra el resto. Nunca toca el archivo en uso. Un archivo gigante se borra sin
        arrastrar a los pequeños más viejos que sí caben."""
        actual = os.path.normcase(os.path.abspath(self.baseFilename))
        limite = time.time() - self._dias * 86400
        try:
            archivos = []
            for p in self._directory.glob(_PATRON):
                if p.is_file() and os.path.normcase(os.path.abspath(p)) != actual:
                    st = p.stat()
                    archivos.append((st.st_mtime, st.st_size, p))
        except OSError:
            return
        # Se reserva el tamaño completo de la parte en uso (todavía va a crecer hasta max_bytes_archivo).
        disponible = self._max_total - max(self._tamano_actual(), self._max_archivo)
        for mtime, size, p in sorted(archivos, key=lambda t: t[0], reverse=True):
            if mtime >= limite and size <= disponible:
                disponible -= size
                continue
            try:
                p.unlink()
            except OSError:
                pass  # abierto por otro programa: se intentará en la próxima purga


def add_file_logging(
    directory: Path = DIRECTORIO_LOGS,
    level: str = "INFO",
    *,
    max_mb_archivo: float = LOG_MAX_MB_ARCHIVO,
    max_mb_total: float = LOG_MAX_MB_TOTAL,
    dias_retencion: float = LOG_DIAS_RETENCION,
) -> Path:
    """Agrega el log de archivo acotado en `directory` (además de la consola). Devuelve la carpeta usada."""
    handler = _LogAcotado(
        directory,
        max_bytes_archivo=int(max_mb_archivo * _MB),
        max_bytes_total=int(max_mb_total * _MB),
        dias_retencion=dias_retencion,
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FORMATO))
    logging.getLogger().addHandler(handler)
    return directory


_archivo_fallos = None  # se mantiene abierto toda la vida del proceso (lo exige faulthandler)


def instalar_capturas(directory: Path = DIRECTORIO_LOGS) -> None:
    """Deja rastro de cualquier cierre: excepciones no atrapadas (hilo principal y workers), caídas internas
    de Python (faulthandler -> fallos_python.log) y la salida normal del proceso.

    Si el log termina sin "Proceso terminado (salida normal)", el proceso fue cerrado desde fuera
    (apagado de la PC, Administrador de tareas, cierre de sesión) o se cayó: ver fallos_python.log y el
    Visor de eventos de Windows.
    """
    global _archivo_fallos
    ruta = directory / "fallos_python.log"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        modo = "w" if ruta.exists() and ruta.stat().st_size > _MAX_BYTES_FALLOS else "a"
        _archivo_fallos = open(ruta, modo, encoding="utf-8")
        _archivo_fallos.write(f"=== Inicio {datetime.now():%Y-%m-%d %H:%M:%S} PID {os.getpid()} ===\n")
        _archivo_fallos.flush()
        faulthandler.enable(file=_archivo_fallos, all_threads=True)
    except OSError:
        logger.warning("No se pudo activar el registro de fallos internos en %s", ruta)

    estado = {"error": False}

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        estado["error"] = True
        logger.critical("Excepción no controlada: el proceso termina", exc_info=(exc_type, exc_value, exc_tb))

    def _al_salir() -> None:
        if estado["error"]:
            logger.info("Proceso terminado tras una excepción no controlada")
        else:
            logger.info("Proceso terminado (salida normal)")

    def _thread_excepthook(args) -> None:
        if args.exc_type is SystemExit:
            return
        nombre = args.thread.name if args.thread is not None else "?"
        logger.critical(
            "Excepción no controlada en el hilo '%s'", nombre, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    atexit.register(_al_salir)
    logger.info("Proceso iniciado (PID %s, Python %s)", os.getpid(), sys.version.split()[0])


def memoria_mb() -> "float | None":
    """Memoria privada del proceso en MB (Windows), para el latido. None si no se puede medir.

    Python de 32 bits no puede pasar de ~2 GB: si este valor crece día a día hay una fuga."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Contadores(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Contadores), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        contadores = _Contadores()
        contadores.cb = ctypes.sizeof(_Contadores)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(contadores), contadores.cb):
            return None
        return contadores.PagefileUsage / _MB
    except Exception:
        return None
