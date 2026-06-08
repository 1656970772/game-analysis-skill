@echo off
setlocal

if "%~1"=="" (
  echo Usage: launch_blender_python.cmd ^<blender.exe^> ^<script.py^>
  exit /b 2
)

if "%~2"=="" (
  echo Usage: launch_blender_python.cmd ^<blender.exe^> ^<script.py^>
  exit /b 2
)

set "BLENDER=%~1"
set "SCRIPT=%~2"

if not exist "%BLENDER%" (
  echo Blender executable not found: "%BLENDER%"
  exit /b 1
)

if not exist "%SCRIPT%" (
  echo Python script not found: "%SCRIPT%"
  exit /b 1
)

start "" "%BLENDER%" --python "%SCRIPT%"
