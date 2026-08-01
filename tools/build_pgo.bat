@echo off
rem Profile-guided-optimisation build of the DTC-01 core (x64).
rem
rem   build_pgo.bat instrument   -> build\pgo\dtc01_x64.dll, instrumented
rem   build_pgo.bat optimize     -> build\pgo\dtc01_x64.dll, PGO-optimised
rem
rem Between the two, run tools\pgo_train.py to exercise the instrumented DLL
rem and produce the .pgc profile data.
rem
rem The core is an interpreter: two indirect-dispatch loops (Musashi's opcode
rem table and tms_step's switch). Branch layout and hot/cold splitting are
rem exactly what a profile can improve and static heuristics cannot.

setlocal
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
set "ROOT=%~dp0.."
set "NATIVE=%ROOT%\native"
set "PGODIR=%ROOT%\build\pgo"
set "MODE=%~1"

if "%MODE%"=="" goto :usage
if not exist "%PGODIR%" mkdir "%PGODIR%"
rem cl will not create the /Fo directory itself -- it fails on the first .obj.
if not exist "%PGODIR%\obj" mkdir "%PGODIR%\obj"

set SRC="%NATIVE%\dtc01.c" "%NATIVE%\tms32010.c" "%NATIVE%\duart2681.c" "%NATIVE%\musashi\m68kcpu.c" "%NATIVE%\musashi\m68kops.c" "%NATIVE%\musashi\softfloat\softfloat.c"
set "CFLAGS=/nologo /O2 /GL /MT /W3 /wd4146 /wd4996 /wd4244 /wd4018 /wd4090"

call "%VCVARS%" x64 >nul 2>&1
if errorlevel 1 goto :fail

rem m68kops.c is generated; reuse build_native.bat's guarded generator.
if not exist "%NATIVE%\musashi\m68kops.c" call "%~dp0build_native.bat" x64 >nul

if /i "%MODE%"=="instrument" goto :instrument
if /i "%MODE%"=="optimize" goto :optimize
goto :usage

:instrument
echo === instrumented build (/GENPROFILE) ===
del /q "%PGODIR%\*.pgc" 2>nul
cl %CFLAGS% /I"%NATIVE%" /Fo:"%PGODIR%\obj\\" %SRC% /Fe:"%PGODIR%\dtc01_x64.dll" /LD ^
   /link /LTCG /GENPROFILE:PGD="%PGODIR%\dtc01.pgd"
if errorlevel 1 goto :fail
rem The instrumented DLL imports the PGO runtime; it is not on PATH by default.
for /f "delims=" %%D in ('where pgort140.dll 2^>nul') do copy /y "%%D" "%PGODIR%\" >nul
if not exist "%PGODIR%\pgort140.dll" (
  echo ERROR: pgort140.dll not found -- instrumented DLL will not load
  goto :fail
)
echo     ready: %PGODIR%\dtc01_x64.dll  ^(now run tools\pgo_train.py^)
goto :done

:optimize
if not exist "%PGODIR%\dtc01.pgd" goto :noprofile
echo === merging profile data ===
pgomgr /merge "%PGODIR%\dtc01.pgd"
if errorlevel 1 goto :fail
echo === optimised build (/USEPROFILE) ===
cl %CFLAGS% /I"%NATIVE%" /Fo:"%PGODIR%\obj\\" %SRC% /Fe:"%PGODIR%\dtc01_x64.dll" /LD ^
   /link /LTCG /USEPROFILE:PGD="%PGODIR%\dtc01.pgd"
if errorlevel 1 goto :fail
rem An optimised build must NOT depend on the PGO runtime; leaving the stale
rem copy behind would hide an accidental instrumented binary.
del /q "%PGODIR%\pgort140.dll" 2>nul
echo     ready: %PGODIR%\dtc01_x64.dll
goto :done

:noprofile
echo ERROR: no %PGODIR%\dtc01.pgd -- run "instrument" then tools\pgo_train.py first
goto :fail

:usage
echo usage: build_pgo.bat [instrument^|optimize]
exit /b 1

:done
endlocal
exit /b 0

:fail
echo PGO BUILD FAILED
endlocal
exit /b 1
