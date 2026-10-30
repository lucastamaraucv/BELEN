"""Reglas de transformación puras extraídas de temporizado.txt.

Aisladas del acceso a datos para poder testearlas sin base de datos, a diferencia
del original donde estaban entremezcladas con el SCAN y los REPLACE.

Replican el comportamiento REAL de Visual FoxPro (no el que parece intencionado):
STR(n, w) redondea, alinea a la derecha y devuelve '*' si no cabe; STR(NULL) es NULL.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# temporizado.txt líneas 48-63 (DO CASE sobre lnTipo = vorden.IdDocIdentidad)
_TIPO_DOCUMENTO = {1: "L", 2: "C", 3: "PA", 7: "U", 8: "MDN", 9: "MAU"}

# temporizado.txt líneas 85-94 (DO CASE sobre vorden.IdTipoSexo)
_SEXO = {1: "M", 2: "F", 3: "I"}


def fila_minusculas(row: Any) -> dict[str, Any]:
    """Los campos de un cursor VFP no distinguen mayúsculas (vorden.Idservicio == vorden.IDSERVICIO);
    aquí se normalizan los nombres de columna a minúsculas para el mismo comportamiento."""
    return {str(k).lower(): v for k, v in dict(row).items()}


def fox_str(valor: Any, ancho: int) -> str | None:
    """STR(valor, ancho) de VFP: NULL -> NULL; redondea al entero; alinea a la derecha con espacios;
    si el número no cabe en `ancho` devuelve '*' repetido."""
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor
    entero = int(Decimal(str(valor)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    texto = str(entero)
    if len(texto) > ancho:
        return "*" * ancho
    return texto.rjust(ancho)


def tipo_documento(id_doc_identidad: int | None) -> str:
    """DO CASE de tipo de documento (líneas 46-63).

    El `if isnull(mpacdoc) -> 'SN'` de las líneas 96-98 es código muerto en el original:
    mpacdoc ya fue reemplazado por ' ' en la línea 30, así que NUNCA fuerza 'SN'.
    """
    if id_doc_identidad is None:
        return "SN"
    return _TIPO_DOCUMENTO.get(int(id_doc_identidad), "SN")


def documento_paciente(nro_documento: str | None) -> str:
    """temporizado.txt línea 30: iif(isnull(NroDocumento), ' ', NroDocumento) -- sin ALLTRIM."""
    return " " if nro_documento is None else nro_documento


def sexo(id_tipo_sexo: int | None) -> str:
    """DO CASE con igualdad exacta (líneas 85-94): 1.9 NO es 1 (cae en 'I'), a diferencia de int()."""
    if id_tipo_sexo is None:
        return "I"
    return _SEXO.get(id_tipo_sexo, "I")


def transform_numero(valor: Any) -> str:
    """TRANSFORM(IdMovimiento) (línea 125): un número entero se escribe sin decimales
    (Decimal('100.00') / 100.0 -> '100')."""
    if isinstance(valor, (Decimal, float)) and valor == int(valor):
        return str(int(valor))
    return str(valor)


def codigo_servicio(id_servicio: int | None) -> str:
    """temporizado.txt líneas 25-26 y 137: dl_cser = ALLTRIM(STR(Idservicio, 3)).

    NO rellena con ceros (7 -> '7'; el padl está comentado en la línea 27). Con Idservicio nulo
    el original abortaba (STR('007',3) es un error de VFP); aquí se usa '007', que era su intención.
    """
    if id_servicio is None:
        return "007"
    return (fox_str(id_servicio, 3) or "").strip()


def historia_clinica(nro_historia: Any) -> str | None:
    """temporizado.txt línea 28: ALLTRIM(STR(NroHistoriaClinica, 10)) (NULL si es nulo)."""
    texto = fox_str(nro_historia, 10)
    return None if texto is None else texto.strip()


def fecha_nacimiento(fecha_nacimiento_raw: datetime | date | None) -> date:
    """temporizado.txt líneas 32-33: hoy si no hay fecha de nacimiento registrada."""
    if fecha_nacimiento_raw is None:
        return date.today()
    if isinstance(fecha_nacimiento_raw, datetime):
        return fecha_nacimiento_raw.date()
    return fecha_nacimiento_raw


def codigo_procedimiento(id_tipo_servicio: int | None, id_fuente_financiamiento: int | None) -> str | None:
    """temporizado.txt líneas 35-43, con STR(x,1) real de VFP.

    mproceini = STR(IdtipoServicio,1) ('99' si es nulo); mfina = STR(IdFuenteFinanciamiento,1).
    Si mproceini = '99' -> '99'; si no -> mfina + mproceini (NULL si mfina es NULL, como en VFP).
    Un valor de 2+ dígitos produce '*' (igual que el original).
    """
    proc_ini = fox_str(id_tipo_servicio, 1)
    if proc_ini is None:
        proc_ini = "99"
    if proc_ini == "99":
        return "99"
    financiamiento = fox_str(id_fuente_financiamiento, 1)
    if financiamiento is None:
        return None
    return financiamiento + proc_ini
