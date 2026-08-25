@echo off
rem Builds liblshhttp.a for Windows (x64) using MinGW-w64.
rem
rem The WinHTTP-based backend needs no external dependencies: HTTP and
rem HTTPS are both handled by the operating system.
rem
rem Usage (from a MinGW-w64 shell or with gcc in PATH):
rem     build.bat        -> ..\win\liblshhttp.a

setlocal
set DIR=%~dp0
set OUT_DIR=%DIR%..\win

where gcc >nul 2>nul
if errorlevel 1 (
    echo error: gcc not found in PATH. Install MinGW-w64 first.
    exit /b 1
)

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

echo compiling lshhttp.c...
gcc -c "%DIR%lshhttp.c" -o "%TEMP%\lshhttp-win.o" -O2 -Wall -Wextra
if errorlevel 1 exit /b 1

ar rcs "%OUT_DIR%\liblshhttp.a" "%TEMP%\lshhttp-win.o"
ranlib "%OUT_DIR%\liblshhttp.a"

del "%TEMP%\lshhttp-win.o"
echo built %OUT_DIR%\liblshhttp.a
endlocal
