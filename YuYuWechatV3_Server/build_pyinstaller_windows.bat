@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "PY_CMD="
set "PY_ARGS="

if defined PYTHON_EXE (
    if exist "%PYTHON_EXE%" (
        set "PY_CMD=%PYTHON_EXE%"
    )
)

if not defined PY_CMD (
    if defined VIRTUAL_ENV (
        if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
            set "PY_CMD=%VIRTUAL_ENV%\Scripts\python.exe"
        )
    )
)

if not defined PY_CMD (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=python"
    )
)

if not defined PY_CMD (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3.11 -V >nul 2>nul
        if !errorlevel!==0 (
            set "PY_CMD=py"
            set "PY_ARGS=-3.11"
        ) else (
            py -3.10 -V >nul 2>nul
            if !errorlevel!==0 (
                set "PY_CMD=py"
                set "PY_ARGS=-3.10"
            ) else (
                py -3 -V >nul 2>nul
                if !errorlevel!==0 (
                    set "PY_CMD=py"
                    set "PY_ARGS=-3"
                )
            )
        )
    )
)

if not defined PY_CMD (
    echo No usable Python runtime found.
    echo Install Python 3.10+ or run from an activated virtual environment.
    echo You can also set PYTHON_EXE=C:\Path\To\python.exe before running this script.
    exit /b 1
)

echo Using Python: "%PY_CMD%" %PY_ARGS%
"%PY_CMD%" %PY_ARGS% -V
if errorlevel 1 exit /b 1

"%PY_CMD%" %PY_ARGS% -m venv .venv-pyinstaller
if errorlevel 1 exit /b 1

call .venv-pyinstaller\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 exit /b 1

pip install -r requirements.txt pyinstaller
if errorlevel 1 exit /b 1

python -c "import emoji, packaging; from pycaw.pycaw import AudioUtilities; import pywinauto, pyautogui, win32clipboard, win32gui; print('Windows automation dependencies OK')"
if errorlevel 1 (
    echo Missing Windows automation dependency. Check requirements.txt installation output above.
    exit /b 1
)

tasklist /FI "IMAGENAME eq YuYuWechatV3_Server.exe" 2>nul | find /I "YuYuWechatV3_Server.exe" >nul
if %errorlevel%==0 (
    echo A previous YuYuWechatV3_Server.exe process is still running.
    echo Stop it before rebuilding, or run:
    echo   taskkill /IM YuYuWechatV3_Server.exe /F
    exit /b 1
)

if exist build (
    rmdir /s /q build
    if exist build (
        echo Failed to remove build directory. Close any terminal, Explorer window, or antivirus scan using it.
        exit /b 1
    )
)

if exist dist (
    rmdir /s /q dist
    if exist dist (
        echo Failed to remove dist directory. Close the old server exe and any Explorer window opened inside dist.
        echo If needed, run:
        echo   taskkill /IM YuYuWechatV3_Server.exe /F
        exit /b 1
    )
)

pyinstaller --clean --noconfirm YuYuWechatV3_Server.spec
if errorlevel 1 exit /b 1

if not exist dist\YuYuWechatV3_Server\db.sqlite3 (
    copy /y db.sqlite3 dist\YuYuWechatV3_Server\db.sqlite3 >nul
)

if exist build (
    rmdir /s /q build
    if exist build (
        echo Warning: build directory could not be removed. The packaged exe is still under dist.
    )
)

echo.
echo Build finished:
echo   %cd%\dist\YuYuWechatV3_Server\YuYuWechatV3_Server.exe
echo.
echo Do not run anything under the build directory; it is only PyInstaller intermediate output.
echo.
echo Run server:
echo   dist\YuYuWechatV3_Server\YuYuWechatV3_Server.exe
echo.
echo Custom host/port:
echo   dist\YuYuWechatV3_Server\YuYuWechatV3_Server.exe runserver 127.0.0.1:8000

endlocal
