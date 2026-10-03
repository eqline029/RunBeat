@echo off
rem RunBeat 依赖一键安装（Windows）
chcp 65001 >nul
cd /d "%~dp0"

rem ---- 查找可用的 Python（同 启动-Windows.bat 的逻辑）----
set "PYEXE="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3"
if not defined PYEXE (
  where python 2>nul | findstr /v /i "WindowsApps" >nul
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path \"$env:LOCALAPPDATA\Doubao\User Data\sandbox_runtime\bases\*\python\python.exe\",\"$env:LOCALAPPDATA\Programs\Python\Python*\python.exe\",\"C:\Python*\python.exe\" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName"`) do set "PYEXE=%%i"
)
if not defined PYEXE (
  echo [错误] 未找到可用的 Python 3。
  echo 请到 https://www.python.org/downloads/ 下载安装 Python 3.10 或更高版本，
  echo 安装时务必勾选 "Add Python to PATH"，然后重新双击本文件。
  pause
  exit /b 1
)

rem 若 PYEXE 是完整路径(第2个字符是冒号)则加引号使用
set "PYRUN=%PYEXE%"
if "%PYEXE:~1,1%"==":" set "PYRUN="%PYEXE%""

echo 使用 %PYEXE% 安装 RunBeat 依赖...
echo.> "%TEMP%\runbeat_pip.conf"
set "PIP_CONFIG_FILE=%TEMP%\runbeat_pip.conf"
%PYRUN% -m pip install -r requirements.txt
if errorlevel 1 (
  echo 官方源安装失败，改用清华镜像源重试...
  %PYRUN% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if errorlevel 1 (
  echo.
  echo [错误] 安装失败，请检查网络后重试。
  pause
  exit /b 1
)
echo.
echo 安装完成，双击「启动-Windows.bat」即可使用。
pause
