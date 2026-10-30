from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import ProgrammingError

from orden_belen.errores import CicloFallido
from orden_belen.services.resultados_sync import ID_ESTADO_AUTOMATIZADO, sync_resultados


def _mock_session_with_rows(rows: list[dict]) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.mappings.return_value.all.return_value = rows
    return session


def test_sin_filas_pendientes_no_hace_nada() -> None:
    resultado_session = _mock_session_with_rows([])
    minsa_session = MagicMock()

    procesadas = sync_resultados(minsa_session, resultado_session)

    assert procesadas == 0
    minsa_session.execute.assert_not_called()


def test_procesa_fila_y_actualiza_ambos_sistemas() -> None:
    fila = {
        "identificador": "ORD-1",
        "cptCodigo": "CPT-1",
        "id_determinacion": 42,
        "DNI": "12345678",
        "FechaHoraResultado": "2025-06-04 14:28:01.123",
        "itemResultado": "10.5",
        "item": "Glucosa",
        "itemReferencia": "70 - 110",
    }
    resultado_session = _mock_session_with_rows([fila])
    minsa_session = MagicMock()

    procesadas = sync_resultados(minsa_session, resultado_session)

    assert procesadas == 1

    # UPDATE InterLabDetalle en MINSA con fecha correctamente parseada y estado 'Automatizado' (5)
    update_call = minsa_session.execute.call_args
    params = update_call.args[1]
    # Igual que codigo 1.txt: los segundos siempre quedan en 0 (SUBSTR(...,17,2) = ':0')
    assert params["fecha_hora_resultado"] == datetime(2025, 6, 4, 14, 28, 0)
    assert params["id_estado"] == ID_ESTADO_AUTOMATIZADO
    assert params["identificador"] == "ORD-1"
    assert params["cptcodigo"] == "CPT-1"
    minsa_session.commit.assert_called_once()

    # EXEC sp_MINSA_UpdateResultadosValidacion en Resultado, para marcar como transferido
    validacion_call = resultado_session.execute.call_args
    validacion_params = validacion_call.args[1]
    assert validacion_params == {"identificador": "ORD-1", "id_determinacion": 42}
    resultado_session.commit.assert_called_once()


def test_error_en_una_fila_no_detiene_el_resto() -> None:
    filas = [
        {
            "identificador": "ORD-BAD",
            "cptCodigo": "CPT-1",
            "id_determinacion": 1,
            "DNI": "1",
            "FechaHoraResultado": "2025-13-45 99:99:00.000",  # mes 13: provoca excepción en datetime()
            "itemResultado": "x",
            "item": "x",
            "itemReferencia": "x",
        },
        {
            "identificador": "ORD-OK",
            "cptCodigo": "CPT-2",
            "id_determinacion": 2,
            "DNI": "2",
            "FechaHoraResultado": "2025-06-04 14:28:01.123",
            "itemResultado": "x",
            "item": "x",
            "itemReferencia": "x",
        },
    ]
    resultado_session = _mock_session_with_rows(filas)
    minsa_session = MagicMock()

    procesadas = sync_resultados(minsa_session, resultado_session)

    assert procesadas == 1
    minsa_session.rollback.assert_called_once()


def _fila(identificador: str, fecha: str = "2025-06-04 14:28:01.123") -> dict:
    return {
        "identificador": identificador,
        "cptCodigo": "CPT-1",
        "id_determinacion": 1,
        "DNI": "1",
        "FechaHoraResultado": fecha,
        "itemResultado": "x",
        "item": "x",
        "itemReferencia": "x",
    }


def test_tabla_inexistente_corta_el_ciclo_en_la_primera_fila() -> None:
    # Caso real del 29/10/2026: 'InterLabDetalle' no existe en la base de MINSA. Antes se intentaban las
    # 1254 filas en cada ciclo (un traceback por fila); ahora se corta en la primera y el worker espera.
    resultado_session = _mock_session_with_rows([_fila(f"ORD-{i}") for i in range(1254)])
    minsa_session = MagicMock()
    minsa_session.execute.side_effect = ProgrammingError(
        "UPDATE InterLabDetalle",
        {},
        Exception("42S02", "[42S02] El nombre de objeto 'InterLabDetalle' no es válido. (208) (SQLExecDirectW)"),
    )

    with pytest.raises(ProgrammingError):
        sync_resultados(minsa_session, resultado_session)

    assert minsa_session.execute.call_count == 1
    assert resultado_session.execute.call_count == 1  # solo el SP de pendientes: nada se marcó como transferido


def test_si_todas_las_filas_fallan_avisa_al_worker_y_escribe_el_error_una_vez(caplog) -> None:
    filas = [_fila(f"ORD-{i}", fecha="2025-13-45 99:99:00.000") for i in range(30)]  # mes 13 en todas
    resultado_session = _mock_session_with_rows(filas)

    with caplog.at_level("ERROR", logger="orden_belen.services.resultados_sync"):
        with pytest.raises(CicloFallido):
            sync_resultados(MagicMock(), resultado_session)

    assert len([r for r in caplog.records if "Error procesando resultado" in r.getMessage()]) == 1


def test_avisa_avance_por_cada_fila() -> None:
    avances = []
    resultado_session = _mock_session_with_rows([_fila("A"), _fila("B")])
    assert sync_resultados(MagicMock(), resultado_session, progreso=lambda: avances.append(1)) == 2
    assert len(avances) == 2
