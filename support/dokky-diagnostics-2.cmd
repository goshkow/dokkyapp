@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
rem второй сбор: где падает Qt и открывается ли Докки без видеокарты. Чаты и файлы не трогает.

set "OUT=%USERPROFILE%\Desktop\dokky-diagnostics-2"
set "ZIP=%USERPROFILE%\Desktop\dokky-diagnostics-2.zip"
if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%ZIP%" del /q "%ZIP%"
mkdir "%OUT%"

rem папка установки из реестра: Докки могла стоять не на диске C
set "APPROOT="
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Dokky" /v InstallLocation 2^>nul ^| find "InstallLocation"') do set "APPROOT=%%B"
if not defined APPROOT for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\Dokky" /v InstallLocation 2^>nul ^| find "InstallLocation"') do set "APPROOT=%%B"
if not defined APPROOT set "APPROOT=%LOCALAPPDATA%\Programs\Dokky"
echo Папка: %APPROOT% > "%OUT%\where.txt"

set "EXE="
for /f "delims=" %%F in ('dir /s /b /o-d "%APPROOT%\DokkyApp.exe" 2^>nul') do if not defined EXE set "EXE=%%F"
if not defined EXE (
  echo DokkyApp.exe не найден >> "%OUT%\where.txt"
  goto pack
)
echo %EXE% >> "%OUT%\where.txt"

rem видеокарты и OpenGL
powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Format-List Name,DriverVersion,DriverDate,VideoProcessor" > "%OUT%\video.txt" 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Processor | Format-List Name" > "%OUT%\cpu.txt" 2>&1

set QT_FORCE_STDERR_LOGGING=1
set QT_ASSUME_STDERR_HAS_CONSOLE=1
set PYTHONFAULTHANDLER=1

echo Шаг 1 из 3. Обычный запуск. Если Докки откроется - закрой окно.
call :run normal ""
echo Шаг 2 из 3. Запуск без видеокарты (Direct3D). Если Докки откроется - закрой окно.
call :run d3d11 "d3d11"
echo Шаг 3 из 3. Запуск полностью на процессоре. Если Докки откроется - закрой окно.
call :run safe "safe"
goto pack

:run
set "KODDI_RENDERER=%~2"
set "START=%TIME%"
"%EXE%" > "%OUT%\run-%~1.txt" 2>&1
echo. >> "%OUT%\run-%~1.txt"
echo Код выхода: !ERRORLEVEL!, начало %START%, конец !TIME! >> "%OUT%\run-%~1.txt"
exit /b

:pack
powershell -NoProfile -Command "Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error'; StartTime=(Get-Date).AddMinutes(-15)} -ErrorAction SilentlyContinue | Format-List TimeCreated,Message" > "%OUT%\events.txt" 2>&1
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\*' -DestinationPath '%ZIP%' -Force"
rmdir /s /q "%OUT%"
echo.
echo Готово. На рабочем столе файл dokky-diagnostics-2.zip - пришли его, пожалуйста.
echo И напиши, на каком шаге Докки открылась, если открылась.
pause
