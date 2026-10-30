from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from orden_belen.errores import (
    Repeticiones,
    aborta_el_ciclo,
    es_error_de_conexion,
    es_error_de_configuracion,
    firma_error,
    sufijo_repeticiones,
)


def _err(cls, orig):
    return cls("SELECT", {}, orig)


def test_conexion_invalidada_es_de_conexion() -> None:
    assert es_error_de_conexion(DBAPIError("x", {}, ConnectionError("c"), connection_invalidated=True))


def test_sqlstate_08_y_timeout_son_de_conexion() -> None:
    assert es_error_de_conexion(_err(DBAPIError, Exception("08S01", "Communication link failure")))
    assert es_error_de_conexion(_err(DBAPIError, Exception("HYT00", "Login timeout expired")))
    assert es_error_de_conexion(_err(OperationalError, Exception("x")))


def test_pool_agotado_es_de_conexion() -> None:
    assert es_error_de_conexion(PoolTimeoutError("pool"))


def test_errores_de_datos_no_son_de_conexion() -> None:
    assert not es_error_de_conexion(_err(IntegrityError, Exception("23000", "duplicate key")))
    assert not es_error_de_conexion(_err(ProgrammingError, Exception("42S02", "invalid object")))
    assert not es_error_de_conexion(ValueError("x"))


def _pyodbc(sqlstate: str, texto: str) -> Exception:
    return Exception(sqlstate, f"[{sqlstate}] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]{texto} (SQLExecDirectW)")


def test_tabla_inexistente_es_de_configuracion_y_aborta_el_ciclo() -> None:
    exc = _err(ProgrammingError, _pyodbc("42S02", "El nombre de objeto 'InterLabDetalle' no es válido. (208)"))
    assert es_error_de_configuracion(exc)
    assert not es_error_de_conexion(exc)
    assert aborta_el_ciclo(exc)


def test_permiso_login_y_procedimiento_son_de_configuracion() -> None:
    assert es_error_de_configuracion(_err(ProgrammingError, _pyodbc("42000", "Permiso UPDATE denegado. (229)")))
    assert es_error_de_configuracion(_err(ProgrammingError, _pyodbc("42000", "No se encontró el procedimiento. (2812)")))
    assert es_error_de_configuracion(_err(DBAPIError, _pyodbc("28000", "Error de inicio de sesión. (18456)")))


def test_sql_server_apagandose_es_de_conexion() -> None:
    exc = _err(ProgrammingError, _pyodbc("42000", "SHUTDOWN en curso. (6005)"))
    assert es_error_de_conexion(exc)
    assert not es_error_de_configuracion(exc)


def test_error_de_datos_no_aborta_el_ciclo() -> None:
    assert not aborta_el_ciclo(_err(IntegrityError, _pyodbc("23000", "Clave duplicada. (2627)")))
    assert not aborta_el_ciclo(ValueError("mes 13"))


def test_firma_ignora_los_numeros_de_cada_fila() -> None:
    a = _err(ProgrammingError, _pyodbc("42S02", "objeto no válido fila 1666144 (208)"))
    b = _err(ProgrammingError, _pyodbc("42S02", "objeto no válido fila 1666356 (208)"))
    assert firma_error(a) == firma_error(b)


def test_repeticiones_escribe_una_vez_por_ventana_y_cuenta_las_omitidas() -> None:
    ahora = [0.0]
    rep = Repeticiones(ventana_segundos=600, reloj=lambda: ahora[0])
    assert rep.registrar("E") == 0  # primera vez: se escribe
    assert rep.registrar("E") is None  # repetido: solo se cuenta
    assert rep.registrar("E") is None
    assert rep.registrar("OTRO") == 0  # otro error distinto sí se escribe
    ahora[0] = 601
    assert rep.registrar("E") == 2  # pasada la ventana: se escribe e informa 2 omitidas
    assert "2 veces" in sufijo_repeticiones(2)
    assert sufijo_repeticiones(0) == ""


def test_repeticiones_no_crece_sin_limite() -> None:
    ahora = [0.0]
    rep = Repeticiones(ventana_segundos=10, reloj=lambda: ahora[0])
    for i in range(5000):
        ahora[0] = i * 20.0
        rep.registrar(f"firma-{i}")
    assert len(rep._ultimo) <= Repeticiones._MAX_FIRMAS
