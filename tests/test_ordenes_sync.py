import os
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from orden_belen import dbf_io
from orden_belen.errores import CicloFallido
from orden_belen.services.ordenes_sync import sync_ordenes


def _orden(**overrides) -> dict:
    base = dict(
        IdMovimiento=100,
        Codigo="99999",
        fecha=datetime(2025, 6, 4, 9, 15),
        Idservicio=7,
        NroHistoriaClinica=12345,
        NroDocumento="12345678",
        paciente="juan perez",
        fechanacimiento=datetime(1990, 1, 1),
        IdDocIdentidad=1,
        IdTipoSexo=1,
        IdtipoServicio=1,
        IdFuenteFinanciamiento=3,
    )
    base.update(overrides)
    return base


def _mock_session(orden_rows: list, *, rowcount_claim: int = 1, falla_claim: "Exception | None" = None) -> MagicMock:
    session = MagicMock()

    def execute_side_effect(stmt, params=None):
        text = str(stmt)
        result = MagicMock()
        if "SELECT" in text and "LabOrdenesInterop2" in text:
            result.mappings.return_value.all.return_value = orden_rows
        elif "SET Flag = 2" in text:
            if falla_claim is not None:
                raise falla_claim
            result.rowcount = rowcount_claim
        return result

    session.execute.side_effect = execute_side_effect
    return session


def _insertados(dbf_dir: str) -> list:
    return list(dbf_io.leer_registros(os.path.join(dbf_dir, "dlhnse2.dbf")))


def test_sin_ordenes_pendientes_no_hace_nada(dbf_dir) -> None:
    session = _mock_session([])
    assert sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir) == 0
    session.commit.assert_not_called()
    assert _insertados(dbf_dir) == []


def test_orden_sin_perfil_inserta_una_sola_fila_en_el_dbf(dbf_dir) -> None:
    session = _mock_session([_orden()])

    assert sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir) == 1

    (fila,) = _insertados(dbf_dir)
    assert fila["DL_NUMERO"] == "100"
    assert fila["DL_FECEXA"] == date(2025, 6, 4)
    assert fila["DL_FECREG"] == date(2025, 6, 4)
    assert fila["DL_CEXAME"] == "99999"
    assert fila["DL_PERFIL"] == "99999"
    assert fila["DL_CODLAB"] == "BIO"
    assert fila["DL_HISTOR"] == "12345"
    assert fila["DL_NOMBRE"] == "JUAN PEREZ"
    assert fila["DL_SEXO"] == "M"
    assert fila["DL_FECNAC"] == date(1990, 1, 1)
    assert fila["DL_TIPDOC"] == "L"
    assert fila["DL_CPRO"] == "31"
    assert fila["DL_CSER"] == "7"
    assert fila["DL_NDOCUM"] == "12345678"
    assert len(fila["DL_HORA"]) == 5 and fila["DL_HORA"][2] == ":"
    session.commit.assert_called_once()


def test_orden_con_perfil_expande_en_el_orden_del_archivo_y_omite_borrados(dbf_dir) -> None:
    session = _mock_session([_orden(Codigo="80051")])

    assert sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir) == 1

    filas = _insertados(dbf_dir)
    assert [f["DL_CEXAME"] for f in filas] == ["EX-1", "EX-2", "EX-3"]  # ALLTRIM; el BORRADO no cuenta
    assert {f["DL_PERFIL"] for f in filas} == {"80051"}
    assert {f["DL_NUMERO"] for f in filas} == {"100"}


def test_replica_quirks_de_foxpro(dbf_dir) -> None:
    session = _mock_session(
        [_orden(NroDocumento=None, Idservicio=7, NroHistoriaClinica=0, IdDocIdentidad=8, paciente="  muñoz ñandú ")]
    )

    sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir)

    (fila,) = _insertados(dbf_dir)
    assert fila["DL_TIPDOC"] == "M"  # 'MDN' truncado a C(1); no se fuerza 'SN' por documento nulo
    assert fila["DL_NDOCUM"] == ""  # ' ' en el original
    assert fila["DL_CSER"] == "7"  # sin ceros a la izquierda
    assert fila["DL_HISTOR"] == "0"
    assert fila["DL_NOMBRE"] == "MUÑOZ ÑANDÚ".replace("Ú", "U")  # 'Ú' no existe en DOS 437


def test_columnas_sin_distinguir_mayusculas(dbf_dir) -> None:
    fila = {k.upper(): v for k, v in _orden(Codigo="80051").items()}
    session = _mock_session([fila])

    assert sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir) == 1
    assert len(_insertados(dbf_dir)) == 3


def test_orden_ya_tomada_por_otro_proceso_no_escribe_nada(dbf_dir) -> None:
    session = _mock_session([_orden()], rowcount_claim=0)

    assert sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir) == 0
    assert _insertados(dbf_dir) == []


def test_error_en_una_orden_no_detiene_el_resto(dbf_dir) -> None:
    mala = _orden(IdMovimiento=1, IdDocIdentidad="no-numerico")  # provoca excepción al transformar
    buena = _orden(IdMovimiento=2)
    session = _mock_session([mala, buena])

    assert sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir) == 1

    assert [f["DL_NUMERO"] for f in _insertados(dbf_dir)] == ["2"]
    session.rollback.assert_called_once()


def test_error_de_conexion_aborta_el_ciclo_para_que_el_worker_haga_backoff(dbf_dir) -> None:
    caida = DBAPIError("UPDATE", {}, ConnectionError("08S01"), connection_invalidated=True)
    session = _mock_session([_orden(IdMovimiento=1), _orden(IdMovimiento=2)], falla_claim=caida)

    with pytest.raises(DBAPIError):
        sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir)
    session.rollback.assert_called()
    assert _insertados(dbf_dir) == []


def test_dbf_ausente_aborta_el_ciclo_sin_marcar_nada(tmp_path) -> None:
    session = _mock_session([_orden()])

    with pytest.raises(dbf_io.DBFError):
        sync_ordenes(session, batch_size=100, dbf_dir=str(tmp_path))  # no hay perfiles.dbf ni dlhnse2.dbf
    session.commit.assert_not_called()


def test_dbf_de_ordenes_bloqueado_revierte_la_orden(dbf_dir, monkeypatch) -> None:
    def bloqueado(*a, **k):
        raise dbf_io.DBFError("archivo en uso")

    monkeypatch.setattr(dbf_io, "agregar_registros", bloqueado)
    session = _mock_session([_orden()])

    with pytest.raises(dbf_io.DBFError):
        sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir)
    session.rollback.assert_called()  # el Flag=2 sin confirmar se descarta: la orden sigue pendiente
    session.commit.assert_not_called()


def test_si_todas_las_ordenes_fallan_avisa_al_worker(dbf_dir) -> None:
    malas = [_orden(IdMovimiento=i, IdDocIdentidad="no-numerico") for i in range(1, 4)]
    session = _mock_session(malas)

    with pytest.raises(CicloFallido):
        sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir)
    assert _insertados(dbf_dir) == []
    session.commit.assert_not_called()


def test_tabla_inexistente_corta_el_ciclo(dbf_dir) -> None:
    from sqlalchemy.exc import ProgrammingError

    falta = ProgrammingError("UPDATE", {}, Exception("42S02", "[42S02] objeto no válido. (208) (SQLExecDirectW)"))
    session = _mock_session([_orden(IdMovimiento=1), _orden(IdMovimiento=2)], falla_claim=falta)

    with pytest.raises(ProgrammingError):
        sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir)
    assert _insertados(dbf_dir) == []



def test_error_de_disco_corta_el_ciclo(dbf_dir, monkeypatch) -> None:
    def disco_lleno(*a, **k):
        raise OSError(28, "No queda espacio en el dispositivo")

    monkeypatch.setattr(dbf_io, "agregar_registros", disco_lleno)
    session = _mock_session([_orden(IdMovimiento=1), _orden(IdMovimiento=2)])

    with pytest.raises(OSError):
        sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir)
    session.rollback.assert_called_once()  # cortó en la primera: no intenta las demás
    session.commit.assert_not_called()


def test_avisa_avance_por_cada_orden_y_memoria_agotada_sube(dbf_dir, monkeypatch) -> None:
    avances = []
    session = _mock_session([_orden(IdMovimiento=1), _orden(IdMovimiento=2), _orden(IdMovimiento=3)])
    assert sync_ordenes(session, batch_size=100, dbf_dir=dbf_dir, progreso=lambda: avances.append(1)) == 3
    assert len(avances) == 3

    def sin_memoria(*a, **k):
        raise MemoryError()

    monkeypatch.setattr(dbf_io, "agregar_registros", sin_memoria)
    with pytest.raises(MemoryError):  # no se trata como error de una orden
        sync_ordenes(_mock_session([_orden(IdMovimiento=4)]), batch_size=100, dbf_dir=dbf_dir)
