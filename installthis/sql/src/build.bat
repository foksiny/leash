@echo off
rem Builds liblshsql.a for Windows (x64) using MinGW-w64.
rem
rem Fully self-contained: embeds the SQLite amalgamation vendored in this
rem directory. No external dependencies.
rem
rem Usage (from a MinGW-w64 shell or with gcc in PATH):
rem     build.bat        -> ..\win\liblshsql.a

setlocal
set DIR=%~dp0
set OUT_DIR=%DIR%..\win

where gcc >nul 2>nul
if errorlevel 1 (
    echo error: gcc not found in PATH. Install MinGW-w64 first.
    exit /b 1
)

if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

echo compiling sqlite3.c (this can take a minute)...
gcc -c "%DIR%sqlite3.c" -o "%TEMP%\sqlite3-win.o" -O1 -DSQLITE_THREADSAFE=1 -DSQLITE_OMIT_LOAD_EXTENSION
if errorlevel 1 exit /b 1

echo compiling lshsql.c...
gcc -c "%DIR%lshsql.c" -o "%TEMP%\lshsql.o" -O2 -Wall -Wextra
if errorlevel 1 exit /b 1

ar rcs "%OUT_DIR%\liblshsql.a" "%TEMP%\sqlite3-win.o" "%TEMP%\lshsql.o"
ranlib "%OUT_DIR%\liblshsql.a"

del "%TEMP%\sqlite3-win.o" "%TEMP%\lshsql.o"
echo built %OUT_DIR%\liblshsql.a
endlocal
