import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from orden_belen import db as db_module
from orden_belen.errores import es_desconexion_dbapi


def test_shutdown_en_curso_es_desconexion_y_un_error_de_datos_no() -> None:
    shutdown = Exception("42000", "[42000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]SHUTDOWN en curso. (6005) (SQLExecDirectW)")
    enlace = Exception("08S01", "[08S01] Communication link failure (10054) (SQLExecDirectW)")
    datos = Exception("23000", "[23000] Clave duplicada. (2627) (SQLExecDirectW)")
    tabla = Exception("42S02", "[42S02] El nombre de objeto no es válido. (208) (SQLExecDirectW)")
    assert es_desconexion_dbapi(shutdown)
    assert es_desconexion_dbapi(enlace)
    assert not es_desconexion_dbapi(datos)
    assert not es_desconexion_dbapi(tabla)


def test_el_evento_invalida_la_conexion_cuando_el_error_es_de_desconexion(monkeypatch) -> None:
    # Con SQLite no hay errores de SQL Server: se fuerza la clasificación para probar el enganche con SQLAlchemy.
    monkeypatch.setattr(db_module, "es_desconexion_dbapi", lambda _orig: True)
    engine = create_engine("sqlite://")
    db_module.instalar_deteccion_de_desconexion(engine)

    with engine.connect() as conn:
        with pytest.raises(DBAPIError) as info:
            conn.execute(text("SELECT * FROM tabla_que_no_existe"))
    assert info.value.connection_invalidated  # SQLAlchemy descarta esa conexión (y las viejas del pool)
    engine.dispose()


def test_el_pool_de_odbc_queda_desactivado() -> None:
    # SQLAlchemy ya tiene su pool; el de ODBC devolvería conexiones muertas tras una caída del servidor.
    pyodbc = pytest.importorskip("pyodbc")
    import orden_belen.connection  # noqa: F401  (lo desactiva al importarse)

    assert pyodbc.pooling is False
