@echo off
chcp 65001 >nul
title RunBeat 跑步节拍训练音乐操作台
cd /d "%~dp0"
set LOG=runbeat_log.txt

echo ============================================
echo    RunBeat 跑步节拍训练音乐操作台
echo ============================================

rem ============================================================
rem 1. 查找可用的 Python：
rem    依次尝试 py 启动器 / python(过滤微软商店假python) / 常见安装路径
rem ============================================================
set "PYEXE="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3"
if not defined PYEXE (
  where python 2>nul | findstr /v /i "WindowsApps" >nul
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  rem 用 PowerShell 在常见位置按路径查找真实 python.exe
  for /f "usebackq delims=" %%i in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path \"$env:LOCALAPPDATA\Doubao\User Data\sandbox_runtime\bases\*\python\python.exe\",\"$env:LOCALAPPDATA\Programs\Python\Python*\python.exe\",\"C:\Python*\python.exe\" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName"`) do set "PYEXE=%%i"
)
if not defined PYEXE (
  echo.
  echo [错误] 未找到可用的 Python 3。
  echo 请到 https://www.python.org/downloads/ 下载安装 Python 3.10 或更高版本，
  echo 安装时务必勾选 "Add Python to PATH"，然后重新双击本文件。
  echo.
  pause
  exit /b 1
)

rem 若 PYEXE 是完整路径(第2个字符是冒号)则加引号使用
set "PYRUN=%PYEXE%"
if "%PYEXE:~1,1%"==":" set "PYRUN="%PYEXE%""

rem ============================================================
rem 2. 检查依赖, 缺失则自动安装（含 pip 配置损坏保护）
rem ============================================================
%PYRUN% -c "import numpy, scipy, librosa, edge_tts" >nul 2>nul
if errorlevel 1 (
  echo 首次运行，正在安装依赖（1-3 分钟），日志见 %LOG% ...
  echo.> "%TEMP%\runbeat_pip.conf"
  set "PIP_CONFIG_FILE=%TEMP%\runbeat_pip.conf"
  %PYRUN% -m pip install -r requirements.txt >%LOG% 2>&1
)
if errorlevel 1 (
  echo 官方源安装失败，改用清华镜像源重试...
  echo.> "%TEMP%\runbeat_pip.conf"
  set "PIP_CONFIG_FILE=%TEMP%\runbeat_pip.conf"
  %PYRUN% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple >>%LOG% 2>&1
)
if errorlevel 1 (
  echo.
  echo [错误] 依赖安装失败。请打开本文件夹中的 %LOG% 查看原因，
  echo 或检查网络后重新双击本文件重试。
  pause
  exit /b 1
)

rem ============================================================
rem 3. 检查 ffmpeg（仅影响"我的歌曲"对齐）
rem ============================================================
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [提示] 未找到 ffmpeg：合成音乐和语音提示不受影响，
  echo        但「我的歌曲」对齐功能不可用。
)

rem ============================================================
rem 4. 启动服务（Python 会自动打开浏览器）
rem ============================================================
echo.
echo 正在启动服务，浏览器将自动打开 http://127.0.0.1:8787 ...
echo 如果没有自动弹出，请手动访问该地址。
echo 手机访问：连同一 Wi-Fi 后，打开操作台页面顶部的局域网地址或扫二维码。
echo 停止服务：按 Ctrl+C
echo.
%PYRUN% server.py 8787
pause
