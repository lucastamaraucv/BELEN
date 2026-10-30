import pyodbc
import pytest

from orden_belen.config import get_settings
from orden_belen.config_store import ConfigStore
from orden_belen.connection import build_url, describe, parse_conn_str, probar_conexion, validar

CADENA = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=10.100.100.3;DATABASE=Auxiliar;UID=usuario;PWD=clave;secreta"


def test_store_guarda_y_lee_en_sqlite(tmp_path) -> None:
    store = ConfigStore(tmp_path / "sub" / "cfg.db")
    assert store.get("MINSA") is None

    store.set("MINSA", "DSN=MINSA;UID=a;PWD=b")
    store.set("MINSA", "DSN=MINSA;UID=a;PWD=c")  # reemplaza, no duplica
    assert store.get("MINSA") == "DSN=MINSA;UID=a;PWD=c"
    assert ConfigStore(tmp_path / "sub" / "cfg.db").get("MINSA") == "DSN=MINSA;UID=a;PWD=c"  # persiste


def test_get_settings_admite_configuracion_parcial(tmp_path) -> None:
    store = ConfigStore(tmp_path / "cfg.db")
    vacia = get_settings(store)
    assert vacia.minsa_conn_str is None and vacia.resultado_conn_str is None
    assert not vacia.completa

    store.set("Resultado", "DSN=Jalisco;UID=c;PWD=d")  # MINSA todavía no instalado
    parcial = get_settings(store)
    assert parcial.minsa_conn_str is None
    assert parcial.resultado_url.drivername == "mssql+pyodbc"
    assert not parcial.completa

    store.set("MINSA", "DSN=MINSA;UID=a;PWD=b")
    assert get_settings(store).completa

    store.delete("MINSA")
    assert get_settings(store).minsa_conn_str is None


def test_parse_respeta_llaves_y_no_pierde_la_clave() -> None:
    pares = parse_conn_str(CADENA)
    assert pares["SERVER"] == "10.100.100.3"
    assert pares["PWD"] == "clave"  # sin llaves, ';' parte la cadena (limitación de ODBC)
    assert parse_conn_str("SERVER=x;PWD={a;b}")["PWD"] == "a;b"


def test_describe_nunca_expone_usuario_ni_clave() -> None:
    assert describe(CADENA) == "10.100.100.3/Auxiliar"
    assert "secreta" not in describe("SERVER=galeno;DATABASE=SIGH;UID=nobilis;PWD=secreta")


def test_validar() -> None:
    assert validar("") is not None
    assert validar("UID=a;PWD=b") is not None
    assert validar("DSN=MINSA") is not None  # ya no se admiten DSN de ODBC
    assert validar(CADENA) is None


def test_build_url_lleva_la_cadena_completa() -> None:
    cadena = "DRIVER={ODBC Driver 17 for SQL Server};SERVER=x;UID=a;PWD=b"
    assert build_url(cadena).query["odbc_connect"] == cadena


@pytest.mark.skipif("ODBC Driver 17 for SQL Server" not in pyodbc.drivers(), reason="requiere ODBC Driver 17")
def test_probar_conexion_fallida_no_filtra_la_clave() -> None:
    ok, mensaje = probar_conexion(
        "DRIVER={ODBC Driver 17 for SQL Server};SERVER=127.0.0.1,1;UID=nobody;PWD=claveultrasecreta", timeout=2
    )
    assert ok is False
    assert "claveultrasecreta" not in mensaje


def test_build_conn_str_desde_campos() -> None:
    from orden_belen.connection import build_conn_str

    cadena = build_conn_str(
        {
            "driver": "ODBC Driver 17 for SQL Server",
            "servidor": "10.100.100.3",
            "base": "Jalisco",
            "usuario": "sa",
            "password": "p;w=}x",
        }
    )
    assert cadena == (
        "DRIVER={ODBC Driver 17 for SQL Server};SERVER=10.100.100.3;DATABASE=Jalisco;UID=sa;PWD={p;w=}}x}"
    )


def test_campos_ida_y_vuelta_con_clave_rara() -> None:
    from orden_belen.connection import build_conn_str, parse_fields

    campos = {
        "driver": "ODBC Driver 17 for SQL Server",
        "servidor": r".\SQLEXPRESS",
        "base": "Jalisco",
        "usuario": "usuario",
        "password": " a;b}{c= ",
    }
    assert parse_fields(build_conn_str(campos)) == campos


def test_validar_campos_y_vacios() -> None:
    from orden_belen.connection import campos_vacios, validar_campos

    assert campos_vacios({"servidor": "", "usuario": "", "password": "", "base": ""})
    assert validar_campos({"servidor": ""}) is not None
    assert validar_campos({"servidor": "x"}) is None  # sin usuario => autenticación de Windows
    assert validar_campos({"servidor": "x", "usuario": "sa"}) is None
    assert validar_campos({"dsn": "Resultado"}) is not None  # el DSN ya no cuenta como servidor


def test_vista_segura_oculta_la_clave() -> None:
    from orden_belen.connection import vista_segura

    vista = vista_segura({"servidor": "x", "usuario": "sa", "password": "claveultrasecreta"})
    assert "claveultrasecreta" not in vista
    assert "PWD=********" in vista


def _con_drivers(monkeypatch, *drivers):
    from orden_belen import connection

    monkeypatch.setattr(connection, "drivers_sql_server", lambda: list(drivers))
    return connection


def test_sin_usuario_usa_autenticacion_de_windows(monkeypatch) -> None:
    connection = _con_drivers(monkeypatch, "ODBC Driver 17 for SQL Server")
    assert connection.resolver_cadena("SERVER=localhost;DATABASE=Jalisco") == (
        "DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost;DATABASE=Jalisco;Trusted_Connection=yes"
    )
    # con usuario: autenticación SQL, sin Trusted_Connection
    assert "Trusted_Connection" not in connection.resolver_cadena("SERVER=x;UID=sa;PWD=b")
    assert connection.autenticacion({"servidor": "x"}).startswith("Windows")
    assert connection.autenticacion({"servidor": "x", "usuario": "sa"}).startswith("SQL Server")


def test_certificado_se_confia_solo_con_driver_18(monkeypatch) -> None:
    connection = _con_drivers(monkeypatch, "ODBC Driver 18 for SQL Server")
    assert connection.resolver_cadena("SERVER=x;UID=a;PWD=b") == (
        "DRIVER={ODBC Driver 18 for SQL Server};SERVER=x;UID=a;PWD=b;TrustServerCertificate=yes"
    )
    # lo que el usuario ya fijó no se pisa
    assert "TrustServerCertificate" not in connection.resolver_cadena("SERVER=x;UID=a;Encrypt=yes")

    connection = _con_drivers(monkeypatch, "ODBC Driver 17 for SQL Server")
    assert "TrustServerCertificate" not in connection.resolver_cadena("SERVER=x;UID=a;PWD=b")


def test_driver_explicito_no_se_toca(monkeypatch) -> None:
    connection = _con_drivers(monkeypatch, "ODBC Driver 18 for SQL Server")
    assert connection.resolver_cadena("DRIVER={ODBC Driver 17 for SQL Server};SERVER=x;UID=a") == (
        "DRIVER={ODBC Driver 17 for SQL Server};SERVER=x;UID=a"
    )
    assert "ODBC Driver 18" in connection.build_url("SERVER=x;UID=a;PWD=b").query["odbc_connect"]


def test_prefiere_17_si_estan_los_dos_y_avisa_si_no_hay_ninguno(monkeypatch) -> None:
    import pyodbc

    from orden_belen import connection

    monkeypatch.setattr(pyodbc, "drivers", lambda: ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"])
    assert connection.driver_automatico() == "ODBC Driver 17 for SQL Server"

    monkeypatch.setattr(pyodbc, "drivers", lambda: ["Microsoft Access Driver (*.mdb)"])
    assert connection.driver_automatico() is None
    ok, mensaje = connection.probar_conexion("SERVER=x;UID=a;PWD=b")
    assert ok is False and "driver" in mensaje.lower()


def test_siempre_puerto_por_defecto() -> None:
    from orden_belen.connection import build_conn_str, parse_fields

    cadena = build_conn_str({"servidor": r"WIN-O9B57ROO2HN\SQLEXPRESS", "puerto": "9999", "base": "Jalisco"})
    assert "9999" not in cadena and "," not in cadena  # el puerto se ignora
    assert parse_fields("SERVER=192.168.2.12,1433;DATABASE=SIGH")["servidor"] == "192.168.2.12"


def test_ip_remota_fuerza_tcp_y_nombres_o_instancias_no(monkeypatch) -> None:
    # Con el servidor caído, sin tcp: el driver también prueba Named Pipes y tarda ~26 s en vez de 5 s.
    connection = _con_drivers(monkeypatch, "ODBC Driver 17 for SQL Server")
    assert connection.resolver_cadena("SERVER=192.168.2.12;DATABASE=SIGH;UID=a;PWD=b") == (
        "DRIVER={ODBC Driver 17 for SQL Server};SERVER=tcp:192.168.2.12;DATABASE=SIGH;UID=a;PWD=b"
    )
    assert "SERVER={tcp:192.168.2.12}" in connection.resolver_cadena("SERVER={192.168.2.12};UID=a")
    for sin_cambio in ("WIN-O9B57ROO2HN\\SQLEXPRESS", "galeno", "127.0.0.1", "tcp:192.168.2.12", "localhost"):
        assert f"SERVER={sin_cambio};" in connection.resolver_cadena(f"SERVER={sin_cambio};UID=a")


def test_envio_de_resultados_apagado_por_defecto(monkeypatch, tmp_path) -> None:
    from orden_belen.config import get_settings
    from orden_belen.config_store import ConfigStore

    store = ConfigStore(tmp_path / "c.db")
    monkeypatch.delenv("ORDEN_PROCESAR_RESULTADOS", raising=False)
    assert get_settings(store).procesar_resultados is False
    monkeypatch.setenv("ORDEN_PROCESAR_RESULTADOS", "1")
    assert get_settings(store).procesar_resultados is True
