@echo off
rem Builds dist\GoogleTVRemote.exe - a single file that needs no Python install.
setlocal
cd /d "%~dp0"

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller || goto :failed
)

python -m PyInstaller --noconfirm --clean ^
    --onefile --windowed ^
    --name GoogleTVRemote ^
    --icon gtvremote\assets\app.ico ^
    --add-data "gtvremote\assets\app.ico;gtvremote\assets" ^
    --add-data "gtvremote\assets\icons;gtvremote\assets\icons" ^
    GoogleTVRemote.pyw || goto :failed

echo.
echo Built: %cd%\dist\GoogleTVRemote.exe
goto :eof

:failed
echo.
echo Build failed.
exit /b 1
