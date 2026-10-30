@echo off
rem ============================================================================
rem  Compila dist\OrdenBelen.exe (PyInstaller, un solo archivo --onefile, sin consola).
rem  Requiere un Python 3.9 de 32 bits con:  pip install pyinstaller typing_extensions pyodbc
rem                                          pip install --no-deps "SQLAlchemy==2.0.*"
rem  Uso:  compilar_exe.bat  [ruta\a\python.exe]   (por defecto el Python39-32 del usuario)
rem ============================================================================
setlocal
cd /d "%~dp0"
set "PY=%~1"
if "%PY%"=="" set "PY=%LOCALAPPDATA%\Programs\Python\Python39-32\python.exe"
"%PY%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build OrdenBelen.spec
if errorlevel 1 (
    echo ERROR: no se pudo compilar.
    exit /b 1
)
echo.
echo Listo: dist\OrdenBelen.exe  (basta copiar ese archivo)
endlocal
