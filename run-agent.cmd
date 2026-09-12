@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Creating the project environment...
    py -m venv "%PROJECT_DIR%.venv"
    if errorlevel 1 (
        echo Could not create the Python environment.
        exit /b 1
    )
    "%PYTHON%" -m pip install -e "%PROJECT_DIR%"
    if errorlevel 1 (
        echo Could not install the project dependencies.
        exit /b 1
    )
)

"%PYTHON%" "%PROJECT_DIR%agent.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
