from datetime import date, datetime
from decimal import Decimal

from orden_belen.mappers import (
    codigo_procedimiento,
    codigo_servicio,
    documento_paciente,
    fecha_nacimiento,
    fox_str,
    historia_clinica,
    sexo,
    tipo_documento,
    transform_numero,
)


class TestFoxStr:
    def test_alinea_a_la_derecha_y_redondea(self) -> None:
        assert fox_str(7, 3) == "  7"
        assert fox_str(7.5, 3) == "  8"

    def test_no_cabe_devuelve_asteriscos(self) -> None:
        assert fox_str(1000, 3) == "***"
        assert fox_str(10, 1) == "*"

    def test_nulo_es_nulo(self) -> None:
        assert fox_str(None, 3) is None


class TestTipoDocumento:
    def test_mapea_codigos_conocidos(self) -> None:
        assert tipo_documento(1) == "L"
        assert tipo_documento(2) == "C"
        assert tipo_documento(3) == "PA"
        assert tipo_documento(7) == "U"
        assert tipo_documento(8) == "MDN"
        assert tipo_documento(9) == "MAU"

    def test_codigo_desconocido_cae_en_sn(self) -> None:
        assert tipo_documento(99) == "SN"

    def test_sin_tipo_documento_es_sn(self) -> None:
        assert tipo_documento(None) == "SN"


class TestDocumentoPaciente:
    def test_nulo_es_un_espacio(self) -> None:
        assert documento_paciente(None) == " "

    def test_se_conserva_sin_alltrim(self) -> None:
        assert documento_paciente("123 ") == "123 "


class TestHistoriaClinica:
    def test_numerica_sin_decimales(self) -> None:
        assert historia_clinica(Decimal("12345.00")) == "12345"

    def test_cero_es_cero(self) -> None:
        assert historia_clinica(0) == "0"

    def test_nula_es_nula(self) -> None:
        assert historia_clinica(None) is None


class TestSexo:
    def test_mapea_codigos_conocidos(self) -> None:
        assert sexo(1) == "M"
        assert sexo(2) == "F"
        assert sexo(3) == "I"

    def test_desconocido_o_nulo_es_indeterminado(self) -> None:
        assert sexo(None) == "I"
        assert sexo(99) == "I"

    def test_igualdad_exacta_no_trunca(self) -> None:
        assert sexo(1.9) == "I"
        assert sexo(Decimal("2")) == "F"


class TestCodigoServicio:
    def test_nulo_usa_default(self) -> None:
        assert codigo_servicio(None) == "007"

    def test_no_rellena_con_ceros_como_el_original(self) -> None:
        assert codigo_servicio(7) == "7"
        assert codigo_servicio(123) == "123"


class TestFechaNacimiento:
    def test_nula_usa_hoy(self) -> None:
        assert fecha_nacimiento(None) == date.today()

    def test_datetime_se_convierte_a_date(self) -> None:
        assert fecha_nacimiento(datetime(1990, 5, 1, 10, 30)) == date(1990, 5, 1)

    def test_date_se_mantiene(self) -> None:
        assert fecha_nacimiento(date(1990, 5, 1)) == date(1990, 5, 1)


class TestCodigoProcedimiento:
    def test_tipo_servicio_nulo_es_99(self) -> None:
        assert codigo_procedimiento(None, 3) == "99"

    def test_tipo_servicio_de_dos_digitos_da_asterisco(self) -> None:
        assert codigo_procedimiento(99, 3) == "3*"  # STR(99,1) = '*'

    def test_combina_financiamiento_y_tipo(self) -> None:
        assert codigo_procedimiento(1, 3) == "31"

    def test_financiamiento_nulo_propaga_null(self) -> None:
        assert codigo_procedimiento(1, None) is None


class TestTransformNumero:
    def test_entero_sin_decimales(self) -> None:
        assert transform_numero(100) == "100"
        assert transform_numero(Decimal("100.00")) == "100"
        assert transform_numero(100.0) == "100"
