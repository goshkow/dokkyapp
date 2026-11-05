@echo off
chcp 65001 >nul
setlocal EnableExtensions
rem сбор сведений, почему Докки закрывается сразу после запуска. Чаты и файлы не трогает.

set "OUT=%USERPROFILE%\Desktop\dokky-diagnostics"
set "ZIP=%USERPROFILE%\Desktop\dokky-diagnostics.zip"
set "APPROOT=%LOCALAPPDATA%\Programs\Dokky"
set "DATA=%LOCALAPPDATA%\Koddi"
if exist "%OUT%" rmdir /s /q "%OUT%"
if exist "%ZIP%" del /q "%ZIP%"
mkdir "%OUT%"

echo Собираю сведения для Докки. Ничего не закрывай, это займет около минуты.
echo.

rem система
systeminfo > "%OUT%\system.txt" 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Format-List Name,DriverVersion,DriverDate" > "%OUT%\video.txt" 2>&1
powershell -NoProfile -Command "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct | Format-List displayName" > "%OUT%\antivirus.txt" 2>&1

rem что установлено
dir /s /b "%APPROOT%\*.exe" > "%OUT%\files.txt" 2>&1
if exist "%APPROOT%\current.json" copy /y "%APPROOT%\current.json" "%OUT%\" >nul
if exist "%DATA%\updates\install.log" copy /y "%DATA%\updates\install.log" "%OUT%\" >nul

rem пробный запуск с записью вывода
set "EXE="
for /f "delims=" %%F in ('dir /s /b /o-d "%APPROOT%\DokkyApp.exe" 2^>nul') do if not defined EXE set "EXE=%%F"
if defined EXE (
  echo Запускаю Докки. Если окно откроется - просто закрой его.
  echo %EXE% > "%OUT%\run.txt"
  set PYTHONFAULTHANDLER=1
  "%EXE%" >> "%OUT%\run.txt" 2>&1
  echo Код выхода: %ERRORLEVEL% >> "%OUT%\run.txt"
) else (
  echo DokkyApp.exe не найден в %APPROOT% > "%OUT%\run.txt"
)

rem журнал Windows за последний час: ошибки приложений
timeout /t 3 /nobreak >nul
powershell -NoProfile -Command "Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddHours(-1); Level=1,2,3} -ErrorAction SilentlyContinue | Format-List TimeCreated,ProviderName,Id,Message" > "%OUT%\events.txt" 2>&1
powershell -NoProfile -Command "Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Windows Error Reporting','Application Error'; StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'Dokky' } | Format-List TimeCreated,Id,Message" > "%OUT%\crashes.txt" 2>&1

powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\*' -DestinationPath '%ZIP%' -Force"
rmdir /s /q "%OUT%"

echo.
echo Готово. На рабочем столе файл dokky-diagnostics.zip - пришли его, пожалуйста.
pause
