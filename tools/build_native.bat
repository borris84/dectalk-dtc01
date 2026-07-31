@echo off
rem Build the native DTC-01 emulator DLL.
rem
rem   build_native.bat            -> builds x64 (dev/testing) and x86 (NVDA)
rem   build_native.bat x64        -> just x64
rem   build_native.bat x86        -> just x86
rem
rem x64 matches this dev machine's AMD64 Python. x86 is what NVDA ships.
rem NOTE: this host is ARM64 hardware but MSVC 14.44 here has no arm64
rem TARGET installed (only x64/x86 under Hostx64/Hostarm64), so a native
rem ARM64 NVDA build would need the ARM64 toolchain added first.
rem
rem Implementation note: the MSVC path contains "(x86)", and cmd.exe
rem mis-parses parenthesised paths expanded inside IF/FOR blocks -- hence
rem the CALL-subroutine structure below rather than a FOR loop.

setlocal
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
if not exist "%VCVARS%" goto :no_vcvars

set "ROOT=%~dp0.."
set "NATIVE=%ROOT%\native"
set "OUTDIR=%ROOT%\build"
if not exist "%OUTDIR%" mkdir "%OUTDIR%"

rem m68kfpu.c / m68kmmu.h are #included by m68kcpu.c -- not compiled separately.
set SRC="%NATIVE%\dtc01.c" "%NATIVE%\tms32010.c" "%NATIVE%\duart2681.c" "%NATIVE%\musashi\m68kcpu.c" "%NATIVE%\musashi\m68kops.c" "%NATIVE%\musashi\softfloat\softfloat.c"

rem /O2 /GL + /LTCG: cross-module inlining matters, the hot loop spans
rem dtc01.c (scheduler) and tms32010.c (DSP core).
rem Suppressed warnings are all from vendored Musashi/softfloat, not our code.
set "CFLAGS=/nologo /O2 /GL /MT /W3 /wd4146 /wd4996 /wd4244 /wd4018 /wd4090"

set "WHICH=%~1"
if "%WHICH%"=="" goto :both
if /i "%WHICH%"=="x64" goto :only_x64
if /i "%WHICH%"=="x86" goto :only_x86
echo ERROR: unknown target "%WHICH%" (expected x64 or x86)
exit /b 1

:both
call :build x64 x64
if errorlevel 1 exit /b 1
call :build x86 amd64_x86
if errorlevel 1 exit /b 1
goto :done

:only_x64
call :build x64 x64
if errorlevel 1 exit /b 1
goto :done

:only_x86
call :build x86 amd64_x86
if errorlevel 1 exit /b 1
goto :done

:done
echo.
echo === built ===
dir /b "%OUTDIR%\dtc01_*.dll"
endlocal
exit /b 0

:no_vcvars
echo ERROR: vcvarsall.bat not found at "%VCVARS%"
exit /b 1

rem ---- :build <out-tag> <vcvars-arch> ----------------------------------
:build
setlocal
echo.
echo === building dtc01_%~1.dll (vcvarsall %~2) ===
call "%VCVARS%" %~2 >nul 2>&1
if errorlevel 1 goto :build_fail
set "OBJDIR=%OUTDIR%\obj_%~1"
if not exist "%OBJDIR%" mkdir "%OBJDIR%"
cl %CFLAGS% /I"%NATIVE%" /Fo:"%OBJDIR%\\" %SRC% /Fe:"%OUTDIR%\dtc01_%~1.dll" /LD /link /LTCG
if errorlevel 1 goto :build_fail
endlocal
exit /b 0

:build_fail
echo BUILD FAILED for %~1
endlocal
exit /b 1
