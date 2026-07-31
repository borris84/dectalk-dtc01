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

rem Musashi's opcode tables are generated, not checked in. Produce them if
rem missing, before anything tries to compile m68kops.c.
if not exist "%NATIVE%\musashi\m68kops.c" call :genops
if errorlevel 1 exit /b 1

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

rem ---- :genops -- generate Musashi's opcode tables ---------------------
rem
rem m68kmake MUST be compiled /Od. At /O2 MSVC miscompiles it: every
rem generated opcode mask and match loses bit 14, so no entry ends up with
rem the 0xff00 mask that Musashi's own table builder scans for as a
rem terminator. The scan then runs off the end of the array and m68k_init()
rem dies with an access violation before a single instruction executes --
rem which presents as "the DLL loads but dtc01_create() crashes", nowhere
rem near the real cause. See native/musashi/VENDORING.md.
:genops
setlocal
echo.
echo === generating Musashi opcode tables (m68kmake, /Od -- see VENDORING.md) ===
call "%VCVARS%" x64 >nul 2>&1
if errorlevel 1 goto :genops_fail
pushd "%NATIVE%\musashi"
cl /nologo /Od /Fe:m68kmake.exe m68kmake.c
if errorlevel 1 goto :genops_fail_pop
rem ".\" is required: cmd does not always search the current directory.
.\m68kmake.exe . m68k_in.c
if errorlevel 1 goto :genops_fail_pop
if not exist m68kops.c goto :genops_fail_pop
rem Sanity check: the 0xff00 group must exist, or the tables are miscompiled
rem output and m68k_init() will crash. Better to fail here than at runtime.
findstr /c:", 0xff00, " m68kops.c >nul
if errorlevel 1 (
  echo ERROR: generated m68kops.c has no 0xff00 mask group.
  echo        m68kmake was miscompiled -- it must be built /Od.
  del /q m68kops.c m68kops.h 2>nul
  goto :genops_fail_pop
)
del /q m68kmake.exe m68kmake.obj 2>nul
popd
echo     opcode tables generated and verified
endlocal
exit /b 0

:genops_fail_pop
popd
:genops_fail
echo ERROR: could not generate Musashi opcode tables
endlocal
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
