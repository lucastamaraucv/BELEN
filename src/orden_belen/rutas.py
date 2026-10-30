"""Carpeta base de la aplicación, donde viven `config\\` y `logs\\`.

* Desde el código fuente: la carpeta PYTHON (dos niveles sobre este archivo: PYTHON\\src\\orden_belen).
* Compilado con PyInstaller (.exe): la carpeta donde está OrdenBelen.exe. Sin esto los módulos quedan dentro de
  `_internal\\` (o en una carpeta temporal con --onefile) y la configuración guardada se perdería.
"""

from __future__ import annotations

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    CARPETA_APP = Path(sys.executable).resolve().parent
else:
    CARPETA_APP = Path(__file__).resolve().parents[2]
