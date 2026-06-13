@echo off
setlocal

if "%~1"=="" (
    echo Usage: run_test.bat [source_name]
    echo Example: run_test.bat DarkHorse_ArtBook
    exit /b 1
)

if not exist "%~dp0.env.bat" (
    echo ERROR: .env.bat not found. Copy .env.bat.example and fill in credentials.
    exit /b 1
)
call "%~dp0.env.bat"

if not exist "%RSS_OUTPUT_DIR%" mkdir "%RSS_OUTPUT_DIR%"

echo [TEST] Source  : %~1
echo [TEST] DB      : 192.168.0.104:2665
echo [TEST] RSS Out : %RSS_OUTPUT_DIR%
echo.

pushd "\\192.168.0.104\web\codeserver\scraper"
C:\Users\Guieunuch\.venv\crawler\Scripts\python.exe run_one.py %~1
popd

endlocal
