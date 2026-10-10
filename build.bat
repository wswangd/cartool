@echo off
REM Package cartool as Windows executable (PyInstaller).
REM Output: dist\cartool\cartool.exe with DLLs bundled.

cd /d "%~dp0"

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller not found. Please run: pip install pyinstaller
    pause
    exit /b 1
)

echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist\cartool rmdir /s /q dist\cartool
if exist cartool.spec del /q cartool.spec

echo Packaging cartool...
pyinstaller --name cartool --noconfirm --clean --windowed --add-data "lib;lib" main.py

if errorlevel 1 (
    echo.
    echo Build failed. See errors above.
    pause
    exit /b 1
)

echo Copying default config files...
if exist signals.json copy /y signals.json dist\cartool\ >nul
if exist experiments.json copy /y experiments.json dist\cartool\ >nul

echo.
echo Build OK: dist\cartool\cartool.exe
echo.
echo Notes:
echo   1. signals.json / experiments.json copied next to exe as defaults.
echo   2. settings.json will be created on first run.
echo   3. Distribute the whole dist\cartool folder, not just the exe.
echo   4. Remove --windowed from the pyinstaller command to keep the console for debugging.
echo.
pause
