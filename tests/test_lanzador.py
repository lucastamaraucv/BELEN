import importlib
import sys

from orden_belen import lanzador


def test_reabre_mientras_la_app_se_cierre_con_error_y_para_con_cierre_normal(tmp_path) -> None:
    codigos = iter([3, 1, 0])  # se reinició sola, la mataron, se cerró normal
    llamadas, esperas = [], []

    def ejecutar(comando, env):
        llamadas.append(env)
        return next(codigos)

    assert lanzador.supervisar(["app"], ejecutar=ejecutar, esperar=esperas.append, carpeta=tmp_path) == 0
    assert len(llamadas) == 3 and esperas == [15.0, 15.0]
    assert all(e["ORDEN_SUPERVISADO"] == "1" and e[lanzador.VARIABLE_HIJO] == "1" for e in llamadas)
    lineas = (tmp_path / "reinicios.log").read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2 and "codigo 3" in lineas[0] and "codigo 1" in lineas[1]


def test_si_no_puede_abrir_la_app_reintenta(tmp_path) -> None:
    intentos = iter([OSError("no existe"), 0])

    def ejecutar(comando, env):
        resultado = next(intentos)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    assert lanzador.supervisar(["app"], ejecutar=ejecutar, esperar=lambda s: None, carpeta=tmp_path) == 0
    assert "No se pudo abrir" in (tmp_path / "reinicios.log").read_text(encoding="utf-8")


def test_reinicios_log_no_pasa_de_1_mb(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lanzador, "MAX_BYTES_REINICIOS", 100)
    for _ in range(10):
        lanzador.anotar_reinicio("x" * 30, tmp_path)
    assert (tmp_path / "reinicios.log").stat().st_size < 200
    assert (tmp_path / "reinicios.log.1").exists()


def test_comando_hijo_desde_el_codigo_fuente() -> None:
    assert lanzador.comando_hijo()[1:] == ["-m", "orden_belen.gui"]


def test_en_el_exe_config_y_logs_quedan_junto_al_ejecutable(monkeypatch, tmp_path) -> None:
    from orden_belen import rutas

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "OrdenBelen.exe"))
    try:
        assert importlib.reload(rutas).CARPETA_APP == tmp_path.resolve()
    finally:
        monkeypatch.undo()
        importlib.reload(rutas)
    assert (rutas.CARPETA_APP / "src" / "orden_belen").is_dir()  # desde el fuente: la carpeta PYTHON


def test_limpia_temporales_huerfanos_pero_no_los_en_uso_ni_los_recientes(tmp_path) -> None:
    import os
    import time

    viejo = time.time() - 3600

    def carpeta(nombre: str, *, antigua: bool = True):
        d = tmp_path / nombre
        (d / "lib").mkdir(parents=True)
        (d / "python39.dll").write_bytes(b"x" * 100)
        if antigua:
            os.utime(d, (viejo, viejo))
        return d

    huerfana = carpeta("_MEI111")
    propia = carpeta("_MEI222")
    en_uso = carpeta("_MEI333")
    reciente = carpeta("_MEI444", antigua=False)
    otra_cosa = tmp_path / "config"
    otra_cosa.mkdir()
    os.utime(otra_cosa, (viejo, viejo))

    with open(en_uso / "python39.dll", "rb"):  # como una instancia viva con la DLL cargada
        borradas = lanzador.limpiar_temporales_huerfanos(tmp_path, propia)

    assert borradas == 1
    assert not huerfana.exists()
    assert propia.exists() and en_uso.exists() and reciente.exists() and otra_cosa.exists()



def _config(ruta, **conexiones):
    from orden_belen.config_store import ConfigStore

    ruta.parent.mkdir(parents=True, exist_ok=True)
    store = ConfigStore(ruta)
    for nombre, cadena in conexiones.items():
        store.set(nombre, cadena)
    return ruta


def test_prefiere_las_conexiones_de_la_instalacion_anterior_de_esa_pc(tmp_path) -> None:
    from orden_belen.config_store import ConfigStore

    anterior = _config(tmp_path / "Desktop" / "PYTHON" / "config" / "orden_belen.db", MINSA="SERVER=de_la_pc_servidor")
    incluida = _config(tmp_path / "exe" / "orden_belen.db", MINSA="SERVER=del_exe")
    destino = tmp_path / "C_OrdenBelen" / "config" / "orden_belen.db"

    assert lanzador.sembrar_configuracion(destino, [anterior, incluida]) == anterior
    assert ConfigStore(destino).get("MINSA") == "SERVER=de_la_pc_servidor"


def test_sin_instalacion_anterior_usa_las_incluidas_y_despues_no_las_pisa(tmp_path) -> None:
    from orden_belen.config_store import ConfigStore

    incluida = _config(tmp_path / "exe" / "orden_belen.db", MINSA="SERVER=del_exe")
    destino = tmp_path / "pc" / "config" / "orden_belen.db"

    assert lanzador.sembrar_configuracion(destino, [tmp_path / "no_existe.db", incluida]) == incluida
    ConfigStore(destino).set("MINSA", "SERVER=cambiada_en_la_gui")
    assert lanzador.sembrar_configuracion(destino, [incluida]) is None
    assert ConfigStore(destino).get("MINSA") == "SERVER=cambiada_en_la_gui"


def test_config_vacia_de_una_prueba_en_demo_se_reemplaza(tmp_path) -> None:
    from orden_belen.config_store import ConfigStore

    incluida = _config(tmp_path / "incluida.db", Resultado="SERVER=x")
    vacia = _config(tmp_path / "Desktop" / "PYTHON" / "config" / "orden_belen.db")  # sin conexiones: se salta
    destino = tmp_path / "config" / "orden_belen.db"
    ConfigStore(destino).get("MINSA")  # abrir en demo crea el archivo vacío

    assert lanzador.sembrar_configuracion(destino, [vacia, incluida]) == incluida
    assert ConfigStore(destino).get("Resultado") == "SERVER=x"


def test_busca_la_instalacion_anterior_en_el_escritorio(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    origenes = lanzador.origenes_de_configuracion(tmp_path / "incluida.db")
    assert origenes[0] == tmp_path / "Desktop" / "PYTHON" / "config" / "orden_belen.db"
    assert origenes[-1] == tmp_path / "incluida.db"
