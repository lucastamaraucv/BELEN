import logging
import os
import time
from datetime import date

from orden_belen import logging_config
from orden_belen.logging_config import _LogAcotado

KB = 1024


def _handler(directorio, **kw) -> _LogAcotado:
    opciones = dict(max_bytes_archivo=2 * KB, max_bytes_total=20 * KB, dias_retencion=15)
    opciones.update(kw)
    h = _LogAcotado(directorio, **opciones)
    h.setFormatter(logging.Formatter(logging_config.FORMATO))
    return h


def _escribir(handler, veces: int, texto: str = "x" * 100) -> None:
    for _ in range(veces):
        handler.handle(logging.makeLogRecord({"msg": texto, "levelname": "INFO", "levelno": logging.INFO}))


def _crear(ruta, tamano: int, dias_atras: float = 0) -> None:
    ruta.write_bytes(b"x" * tamano)
    t = time.time() - dias_atras * 86400
    os.utime(ruta, (t, t))


def test_al_llegar_al_tope_sigue_en_otra_parte_del_mismo_dia(tmp_path) -> None:
    h = _handler(tmp_path)
    try:
        _escribir(h, 60)  # ~10 KB con un tope de 2 KB por archivo
    finally:
        h.close()
    hoy = date.today().isoformat()
    partes = sorted(p.name for p in tmp_path.glob(f"orden_belen_{hoy}*.log"))
    assert len(partes) >= 4
    assert f"orden_belen_{hoy}.log" in partes and f"orden_belen_{hoy}_2.log" in partes
    assert all(p.stat().st_size <= 2 * KB + 300 for p in tmp_path.glob("orden_belen_*.log"))


def test_la_carpeta_nunca_pasa_del_tope_total(tmp_path) -> None:
    h = _handler(tmp_path)
    try:
        _escribir(h, 2000)  # ~300 KB escritos con tope total de 20 KB
    finally:
        h.close()
    total = sum(p.stat().st_size for p in tmp_path.glob("orden_belen_*.log"))
    assert total <= 20 * KB + 2 * KB + 300


def test_borra_el_archivo_gigante_y_lo_viejo_pero_conserva_lo_reciente_que_cabe(tmp_path) -> None:
    # Como en la PC servidor: un log de 3,6 GB (aquí 100 KB) entre archivos pequeños.
    _crear(tmp_path / "orden_belen_2026-10-27.log", 3 * KB, dias_atras=3)
    _crear(tmp_path / "orden_belen_2026-10-28.log", 2 * KB, dias_atras=2)
    _crear(tmp_path / "orden_belen_2026-10-29.log", 100 * KB, dias_atras=1)
    _crear(tmp_path / "orden_belen_2026-09-01.log", 1 * KB, dias_atras=40)  # más viejo que la retención
    _crear(tmp_path / "otro_archivo.txt", 100 * KB, dias_atras=1)  # no es un log de la app: no se toca

    h = _handler(tmp_path)
    h.close()

    nombres = {p.name for p in tmp_path.iterdir()}
    assert "orden_belen_2026-10-29.log" not in nombres
    assert "orden_belen_2026-09-01.log" not in nombres
    assert {"orden_belen_2026-10-27.log", "orden_belen_2026-10-28.log", "otro_archivo.txt"} <= nombres


def test_al_reiniciar_el_mismo_dia_continua_en_la_ultima_parte(tmp_path) -> None:
    hoy = date.today().isoformat()
    _crear(tmp_path / f"orden_belen_{hoy}.log", 3 * KB)
    _crear(tmp_path / f"orden_belen_{hoy}_2.log", 100)
    h = _handler(tmp_path)
    try:
        assert os.path.basename(h.baseFilename) == f"orden_belen_{hoy}_2.log"
    finally:
        h.close()


def test_cambia_de_archivo_al_cambiar_el_dia(tmp_path, monkeypatch) -> None:
    h = _handler(tmp_path, max_bytes_archivo=1024 * KB)
    try:
        _escribir(h, 1)

        class _Manana(date):
            @classmethod
            def today(cls):
                return date(2099, 1, 2)

        monkeypatch.setattr(logging_config, "date", _Manana)
        _escribir(h, 1)
        assert os.path.basename(h.baseFilename) == "orden_belen_2099-01-02.log"
    finally:
        h.close()
    assert (tmp_path / "orden_belen_2099-01-02.log").stat().st_size > 0


def test_al_reiniciar_encuentra_la_ultima_parte_aunque_falten_intermedias(tmp_path) -> None:
    hoy = date.today().isoformat()
    _crear(tmp_path / f"orden_belen_{hoy}_3.log", 100)  # la purga borró la 1 y la 2
    h = _handler(tmp_path)
    try:
        assert os.path.basename(h.baseFilename) == f"orden_belen_{hoy}_3.log"
    finally:
        h.close()


def test_el_tope_reserva_el_tamano_de_la_parte_en_uso(tmp_path) -> None:
    # Tope 20 KB, partes de 2 KB: solo caben 18 KB de archivos anteriores.
    _crear(tmp_path / "orden_belen_2026-10-27.log", 10 * KB, dias_atras=2)
    _crear(tmp_path / "orden_belen_2026-10-28.log", 9 * KB, dias_atras=1)
    h = _handler(tmp_path)
    h.close()
    assert (tmp_path / "orden_belen_2026-10-28.log").exists()
    assert not (tmp_path / "orden_belen_2026-10-27.log").exists()


def test_un_fallo_del_log_nunca_llega_al_hilo_que_escribe(tmp_path, monkeypatch) -> None:
    # Disco lleno o carpeta sin permiso al abrir la siguiente parte: antes la excepción escapaba de logger.info().
    monkeypatch.setattr(logging, "raiseExceptions", False)  # como en producción con pythonw (sin stderr)
    h = _handler(tmp_path)
    try:
        def no_abre():
            raise PermissionError("acceso denegado")

        monkeypatch.setattr(h, "_open", no_abre)
        _escribir(h, 3)  # no lanza

        monkeypatch.undo()  # el disco vuelve: la siguiente línea abre el archivo y se escribe
        _escribir(h, 1)
    finally:
        h.close()
    assert (tmp_path / f"orden_belen_{date.today().isoformat()}.log").stat().st_size > 0


def test_carpeta_de_logs_imposible_no_impide_arrancar(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(logging, "raiseExceptions", False)
    archivo = tmp_path / "no_es_carpeta"
    archivo.write_text("x")
    h = _handler(archivo / "logs")  # no se puede crear: no lanza
    try:
        _escribir(h, 2)  # tampoco lanza
    finally:
        h.close()
