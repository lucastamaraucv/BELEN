from unittest.mock import MagicMock

from orden_belen.diagnostico import (
    FALLA,
    OK,
    SIN_CONFIGURAR,
    Chequeo,
    conectividad_ok,
    formatear,
    todo_ok,
    verificar,
    verificar_base,
    verificar_dbf,
)


def _engine(*, objetos_existentes: set, permisos_faltantes: set = frozenset(), falla_conexion: bool = False):
    """Engine falso: SELECT 1 / @@SERVERNAME / OBJECT_ID / HAS_PERMS_BY_NAME."""
    engine = MagicMock()
    if falla_conexion:
        engine.connect.side_effect = ConnectionError("08001 host inalcanzable")
        return engine
    conn = MagicMock()

    def execute(stmt, params=None):
        sql, r = str(stmt), MagicMock()
        if "OBJECT_ID" in sql:
            r.scalar.return_value = 1 if params["o"] in objetos_existentes else None
        elif "HAS_PERMS_BY_NAME" in sql:
            r.scalar.return_value = 0 if (params["o"], params["p"]) in permisos_faltantes else 1
        elif "@@SERVERNAME" in sql:
            r.one.return_value = ("GALENO", "SIGH")
        return r

    conn.execute.side_effect = execute
    engine.connect.return_value.__enter__.return_value = conn
    return engine


TODOS_MINSA = {"LabOrdenesInterop2", "InterLabDetalle"}


def test_todo_correcto() -> None:
    res = verificar_base(_engine(objetos_existentes=TODOS_MINSA), "MINSA")
    assert todo_ok(res)
    assert res[0].nombre == "Conectividad" and "GALENO" in res[0].detalle


def test_sin_conectividad_marca_falla() -> None:
    res = verificar_base(_engine(objetos_existentes=set(), falla_conexion=True), "MINSA")
    assert [(r.nombre, r.estado) for r in res] == [("Conectividad", FALLA)]
    assert not conectividad_ok(res)


def test_objeto_inexistente_es_falla() -> None:
    res = verificar_base(_engine(objetos_existentes=TODOS_MINSA - {"InterLabDetalle"}), "MINSA")
    inter = next(r for r in res if r.nombre == "InterLabDetalle")
    assert inter.estado == FALLA
    assert conectividad_ok(res)  # la red está bien; falta el objeto


def test_permiso_faltante_es_falla() -> None:
    res = verificar_base(
        _engine(objetos_existentes=TODOS_MINSA, permisos_faltantes={("InterLabDetalle", "UPDATE")}), "MINSA"
    )
    inter = next(r for r in res if r.nombre == "InterLabDetalle")
    assert inter.estado == FALLA and "UPDATE" in inter.detalle


def test_verificar_sin_cadenas_es_no_configurado(dbf_dir) -> None:
    settings = MagicMock(minsa_conn_str=None, resultado_conn_str=None, connect_timeout_seconds=5, dbf_dir=dbf_dir)
    res = verificar(settings)
    assert {r.estado for r in res if r.base != "Archivos DBF"} == {SIN_CONFIGURAR}
    assert all(r.estado == OK for r in res if r.base == "Archivos DBF")
    assert not todo_ok(res)


def test_dbf_correctos(dbf_dir) -> None:
    res = verificar_dbf(dbf_dir)
    assert todo_ok(res)
    assert {r.nombre for r in res} == {"Carpeta", "dlhnse2.dbf", "perfiles.dbf"}


def test_dbf_carpeta_inexistente(tmp_path) -> None:
    res = verificar_dbf(str(tmp_path / "no_existe"))
    assert [r.estado for r in res] == [FALLA]


def test_dbf_ausente_o_sin_campos(tmp_path) -> None:
    from conftest import construir_dbf

    construir_dbf(tmp_path / "dlhnse2.dbf", [("DL_NUMERO", "C", 10, 0)])  # faltan casi todos los campos
    res = {r.nombre: r for r in verificar_dbf(str(tmp_path))}
    assert res["dlhnse2.dbf"].estado == FALLA and "faltan campos" in res["dlhnse2.dbf"].detalle
    assert res["perfiles.dbf"].estado == FALLA  # no existe


def test_formatear_muestra_indicadores() -> None:
    texto = formatear([Chequeo("MINSA", "Conectividad", OK, "bien"), Chequeo("MINSA", "vordenobi", FALLA, "no existe")])
    assert "[ OK  ]" in texto and "[FALLA]" in texto and "1 falla(s)" in texto


def test_informe_indica_produccion_con_bases_configuradas() -> None:
    assert "MODO: PRODUCCION" in formatear([Chequeo("MINSA", "Conectividad", OK, "bien")])
    assert "SIN CONFIGURAR" in formatear([Chequeo("MINSA", "Conectividad", SIN_CONFIGURAR, "")])


def test_sin_envio_de_resultados_no_se_exige_interlabdetalle() -> None:
    from orden_belen.diagnostico import objetos_a_verificar

    assert [o for o, _ in objetos_a_verificar("MINSA", False)] == ["LabOrdenesInterop2"]
    assert objetos_a_verificar("Resultado", False) == []
    assert "InterLabDetalle" in [o for o, _ in objetos_a_verificar("MINSA", True)]
    res = verificar_base(_engine(objetos_existentes={"LabOrdenesInterop2"}), "MINSA", objetos_a_verificar("MINSA", False))
    assert all(r.estado != "FALLA" for r in res)
