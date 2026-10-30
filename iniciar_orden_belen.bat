@echo off
rem ============================================================================
rem  ORDEN BELEN 24x7 desde el codigo fuente (con el .exe basta abrir OrdenBelen.exe)
rem  Abre orden_belen.lanzador con pythonw (sin consola negra). El lanzador abre la
rem  ventana y la vuelve a abrir a los 15 s si se cierra por un fallo, si la matan o
rem  si ella misma se reinicia por un bloqueo o por memoria. Cierre normal = no reabre.
rem  Cada reapertura queda en logs\reinicios.log. Ver COMO_FUNCIONA.md (5.1).
rem ============================================================================
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

rem Primero el Python 3.9 de 32 bits donde se instalaron SQLAlchemy y pyodbc; si no existe, el del PATH.
set "PYW=%LOCALAPPDATA%\Programs\Python\Python39-32\pythonw.exe"
if exist "%PYW%" goto abrir
set "PYW=pythonw"
where pythonw >nul 2>&1
if not errorlevel 1 goto abrir
echo No se encontro pythonw.exe. Instale Python o agreguelo al PATH.
pause
exit /b 1

:abrir
start "" "%PYW%" -m orden_belen.lanzador
endlocal
