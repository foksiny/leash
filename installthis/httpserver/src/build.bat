@echo off
rem Builds liblshhttpd.a for Windows (MinGW-w64) -> ..\win\liblshhttpd.a
rem The server needs ws2_32 (sockets), secur32 (SChannel TLS) and
rem crypt32 + ncrypt (PEM cert/key loading). The Leash linker provides
rem all four for --target win64, so consumers need no extra flags.

setlocal
set DIR=%~dp0
set OUT_DIR=%DIR%..\win
set CC=%CC%
if "%CC%"=="" set CC=gcc

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

echo compiling lshhttpd.c...
"%CC%" -c "%DIR%lshhttpd.c" -o "%DIR%lshhttpd.o" -O2 -Wall -Wextra
if errorlevel 1 goto :fail

ar rcs "%OUT_DIR%\liblshhttpd.a" "%DIR%lshhttpd.o"
if errorlevel 1 goto :fail

echo built %OUT_DIR%\liblshhttpd.a
exit /b 0

:fail
echo build failed
exit /b 1
