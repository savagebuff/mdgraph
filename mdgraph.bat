@echo off
rem mdgraph: запуск без зависимости от того, как в системе доступен Python.
rem Store-stub "python" отсекается проверкой реального запуска (errorlevel).
setlocal
set "PYEXE="
python -c "import sys" >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (py -c "import sys" >nul 2>nul && set "PYEXE=py")
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE (
  echo Python не найден. Установите: winget install Python.Python.3.12
  exit /b 1
)
"%PYEXE%" "%~dp0mdgraph.py" %*
