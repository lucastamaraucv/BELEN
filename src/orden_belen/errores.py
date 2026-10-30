"""Clasificación de errores de base de datos y control de errores repetidos en el log.

Distingue tres casos:
  * caída/intermitencia de SQL Server (red, timeout, servidor reiniciándose): se aborta el ciclo y se
    reintenta con backoff;
  * error de configuración (tabla/columna/procedimiento inexistente, permisos, login): TODAS las filas
    van a fallar igual, así que también se aborta el ciclo en la primera fila;
  * error de datos de una fila: se registra y se continúa con la siguiente.

Sin el segundo caso, 'InterLabDetalle no es válido' se reintentaba fila por fila (hasta 1254 filas por
ciclo, cada una con su traceback) y el log del 29/10/2026 llegó a 3,6 GB en un día.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

# SQLSTATE de pyodbc: 08xxx = conexión; HYT00/HYT01 = timeout; 40001 = deadlock (reintentable).
_PREFIJOS_SQLSTATE = ("08", "HYT", "40001")

# Errores nativos de SQL Server (el número entre paréntesis del mensaje de pyodbc).
# Transitorios: el servidor se está apagando/pausando (llegan como ProgrammingError 42000, no como de red).
_CODIGOS_TRANSITORIOS = frozenset({6005, 17142})
# De configuración: 208 objeto inexistente, 207 columna inexistente, 2812 procedimiento inexistente,
# 229/230 permiso denegado, 262 permiso de CREATE, 297 sin permiso de servidor, 18456 login fallido,
# 4060 no se puede abrir la base pedida en la conexión.
_CODIGOS_CONFIGURACION = frozenset({208, 207, 2812, 229, 230, 262, 297, 18456, 4060})
# SQLSTATE de objeto inexistente (42S..) y de autorización (28...).
_PREFIJOS_SQLSTATE_CONFIGURACION = ("42S", "28")

_RE_CODIGO_NATIVO = re.compile(r"\((\d+)\)\s*\(SQL\w+\)")


class CicloFallido(Exception):
    """Todas las filas del ciclo fallaron con errores de datos: el worker debe esperar (backoff)."""


def _sqlstate_de(orig: object) -> str:
    """SQLSTATE de un error crudo de pyodbc (primer argumento)."""
    args = getattr(orig, "args", ())
    return str(args[0]) if args else ""


def _codigos_de(orig: object) -> "set[int]":
    """Números de error nativos de SQL Server en el mensaje de pyodbc: '... (6005) (SQLExecDirectW)'."""
    texto = " ".join(str(a) for a in getattr(orig, "args", ()))
    return {int(n) for n in _RE_CODIGO_NATIVO.findall(texto)}


def _sqlstate(exc: DBAPIError) -> str:
    return _sqlstate_de(exc.orig)


def _codigos_nativos(exc: DBAPIError) -> "set[int]":
    return _codigos_de(exc.orig)


def es_desconexion_dbapi(orig: BaseException) -> bool:
    """Para el evento handle_error de SQLAlchemy: ¿este error crudo de pyodbc deja muerta la conexión?

    SQLAlchemy solo reconoce algunos SQLSTATE (08S01, 08001, HYT00...). No reconoce "SHUTDOWN en curso"
    (42000, error 6005), que es justo lo que llega cuando el SQL Server de Galeno se reinicia."""
    estado = _sqlstate_de(orig)
    return estado.startswith(("08", "HYT")) or bool(_codigos_de(orig) & _CODIGOS_TRANSITORIOS)


def es_error_de_conexion(exc: BaseException) -> bool:
    """True si el error es de red/conexión/timeout/deadlock (transitorio), False si es de datos."""
    if isinstance(exc, PoolTimeoutError):  # no hay conexión libre en el pool
        return True
    if not isinstance(exc, DBAPIError):
        return False
    if exc.connection_invalidated:
        return True
    if _sqlstate(exc).startswith(_PREFIJOS_SQLSTATE):
        return True
    if _codigos_nativos(exc) & _CODIGOS_TRANSITORIOS:
        return True
    return isinstance(exc, (OperationalError, InterfaceError))


def es_error_de_configuracion(exc: BaseException) -> bool:
    """True si el error no depende de la fila: falta una tabla/columna/procedimiento, permisos o login."""
    if not isinstance(exc, DBAPIError) or es_error_de_conexion(exc):
        return False
    if _sqlstate(exc).startswith(_PREFIJOS_SQLSTATE_CONFIGURACION):
        return True
    return bool(_codigos_nativos(exc) & _CODIGOS_CONFIGURACION)


def aborta_el_ciclo(exc: BaseException) -> bool:
    """Errores que harían fallar igual a todas las filas siguientes: hay que cortar el ciclo y reintentar luego."""
    return es_error_de_conexion(exc) or es_error_de_configuracion(exc)


def resumen_error(exc: BaseException) -> str:
    """Una línea corta: tipo + mensaje (pyodbc no incluye credenciales en sus mensajes)."""
    causa = getattr(exc, "orig", None) or exc
    mensaje = " ".join(str(causa).replace("\\r\\n", " ").split())  # pyodbc trae \r\n escapados en el texto
    return f"{type(causa).__name__}: {mensaje[:200]}"


def firma_error(exc: BaseException) -> str:
    """Identifica 'el mismo error' aunque cambien los datos (números normalizados): sirve para no repetirlo."""
    return re.sub(r"\d+", "N", resumen_error(exc))[:160]


def rollback_seguro(session) -> None:
    """Rollback que nunca oculta el error original (con la conexión caída también puede fallar)."""
    try:
        session.rollback()
    except Exception:
        pass


class Repeticiones:
    """Decide si un error se escribe completo en el log o solo se cuenta.

    Cada firma se escribe a lo sumo una vez por `ventana_segundos`; las repeticiones intermedias se
    cuentan y se informan la siguiente vez que se escribe ("se repitió N veces").
    """

    _MAX_FIRMAS = 500  # la app corre 24x7: el diccionario no debe crecer sin límite

    def __init__(self, ventana_segundos: float = 600.0, reloj: "Callable[[], float]" = time.monotonic) -> None:
        self._ventana = ventana_segundos
        self._reloj = reloj
        self._ultimo: "dict[str, float]" = {}
        self._omitidos: "dict[str, int]" = {}
        self._lock = threading.Lock()

    def registrar(self, firma: str) -> "int | None":
        """None = ya se escribió hace poco (solo se cuenta). Un entero = escribirlo ahora; el valor es
        cuántas repeticiones se omitieron desde la última vez."""
        ahora = self._reloj()
        with self._lock:
            ultimo = self._ultimo.get(firma)
            if ultimo is not None and ahora - ultimo < self._ventana:
                self._omitidos[firma] = self._omitidos.get(firma, 0) + 1
                return None
            if len(self._ultimo) >= self._MAX_FIRMAS:
                self._podar(ahora)
            self._ultimo[firma] = ahora
            return self._omitidos.pop(firma, 0)

    def _podar(self, ahora: float) -> None:
        for firma in [f for f, t in self._ultimo.items() if ahora - t >= self._ventana]:
            del self._ultimo[firma]
            self._omitidos.pop(firma, None)
        if len(self._ultimo) >= self._MAX_FIRMAS:
            self._ultimo.clear()
            self._omitidos.clear()


def sufijo_repeticiones(omitidos: int, ventana_segundos: float = 600.0) -> str:
    """Texto que se agrega al mensaje cuando hubo repeticiones que no se escribieron."""
    if not omitidos:
        return ""
    veces = "1 vez" if omitidos == 1 else f"{omitidos} veces"
    return f" (se repitió {veces} en los últimos {ventana_segundos / 60:.0f} min sin escribirse)"
