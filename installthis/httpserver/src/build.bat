@echo off
rem Builds liblshhttpd.a for Windows (MinGW-w64) -> ..\win\liblshhttpd.a
rem The server needs only ws2_32, which the Leash linker already provides.

setlocal
set DIR=%~dp0
set OUT_DIR=%DIR%..\win
set CC=%CC%
if "%CC%"=="" set CC=gcc

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

echo compiling lshhttpd.c...
"%CC%" -c "%DIR%lshhttpd.c" -o "%DIR%lshhttpd.o" -O2 -Wall -Wextra -lws2_32
if errorlevel 1 goto :fail

ar rcs "%OUT_DIR%\liblshhttpd.a" "%DIR%lshhttpd.o"
if errorlevel 1 goto :fail

echo built %OUT_DIR%\liblshhttpd.a
exit /b 0

:fail
echo build failed
exit /b 1
