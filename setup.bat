@echo off
setlocal enabledelayedexpansion
title NEURAL_PC v1.0 - Setup Wizard
color 02

echo.
echo  ============================================================
echo              NEURAL_PC v1.0 - SETUP WIZARD
echo  ============================================================
echo.
echo  This wizard will:
echo    1. Check your Python installation
echo    2. Install required packages
echo    3. Check GPU support
echo    4. Set your models folder
echo    5. Write config and finalize
echo.
echo  Press any key to begin...
pause >nul

:: STEP 1 - Check Python
echo.
echo  [1/5] Checking Python...
echo  -----------------------------------------------------------
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [!!] Python not found on PATH.
    echo.
    echo  Install Python 3.10+ from https://www.python.org/downloads/
    echo  Check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo  [+] Found Python %PY_VER%

for /f "tokens=1,2 delims=." %%a in ("%PY_VER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)

if %PY_MAJOR% LSS 3 (
    echo  [!!] Python 3.10+ required. You have %PY_VER%.
    pause
    exit /b 1
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 10 (
    echo  [!!] Python 3.10+ required. You have %PY_VER%.
    pause
    exit /b 1
)
echo  [+] Version OK.

:: STEP 2 - Install packages
echo.
echo  [2/5] Installing required packages...
echo  -----------------------------------------------------------
echo.

set REQ=%~dp0requirements.txt
if not exist "%REQ%" (
    echo  [!!] requirements.txt not found next to this script.
    pause
    exit /b 1
)

echo  Installing from requirements.txt...
echo.
pip install -r "%REQ%"
if %errorlevel% neq 0 (
    echo.
    echo  [!!] Package install failed. See above for details.
    echo  Try: python -m pip install --upgrade pip
    echo.
    pause
    exit /b 1
)
echo.
echo  [+] Packages installed OK.

:: STEP 3 - GPU check
echo.
echo  [3/5] Checking GPU support...
echo  -----------------------------------------------------------
echo.

set GPU_SCRIPT=%TEMP%\npc_gpu_check.py
echo from llama_cpp import llama_cpp > "%GPU_SCRIPT%"
echo result = llama_cpp.llama_supports_gpu_offload() >> "%GPU_SCRIPT%"
echo print("GPU_OK" if result else "GPU_NO") >> "%GPU_SCRIPT%"

python "%GPU_SCRIPT%" >"%TEMP%\npc_gpu_result.txt" 2>nul
set /p GPU_RESULT=<"%TEMP%\npc_gpu_result.txt"
del "%GPU_SCRIPT%" >nul 2>&1
del "%TEMP%\npc_gpu_result.txt" >nul 2>&1

if "!GPU_RESULT!"=="GPU_OK" (
    echo  [+] CUDA detected - GPU acceleration available.
) else (
    echo  [!] GPU acceleration not detected.
    echo.
    echo  To enable GPU support:
    echo    pip uninstall llama-cpp-python -y
    echo    pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121
    echo.
    echo  Replace cu121 with your CUDA version. Check with: nvidia-smi
    echo.
    echo  Press any key to continue without GPU, or close to fix first.
    pause >nul
)

:: STEP 4 - Models folder
echo.
echo  [4/5] Models folder...
echo  -----------------------------------------------------------
echo.

:: Auto-detect a good default: prefer C:\Models if it exists, else %USERPROFILE%\Models
set AUTO_DEFAULT=%USERPROFILE%\Models
if exist "C:\Models\" (
    set AUTO_DEFAULT=C:\Models
    echo  [*] Detected existing models folder: C:\Models
) else (
    echo  [*] No existing C:\Models found.
    echo  [*] Default will be: %USERPROFILE%\Models
)

echo.
echo  Where are your .gguf model files stored?
echo  Press ENTER to use the detected default, or type a custom path.
echo.
set /p MODELS_DIR="  Path [!AUTO_DEFAULT!]: "

if "!MODELS_DIR!"=="" set MODELS_DIR=!AUTO_DEFAULT!
set MODELS_DIR=!MODELS_DIR:"=!

echo.
if not exist "!MODELS_DIR!" (
    echo  Folder not found - creating it...
    mkdir "!MODELS_DIR!" >nul 2>&1
    if !errorlevel! neq 0 (
        echo  [!!] Could not create folder. Check the path and try again.
        pause
        exit /b 1
    )
    echo  [+] Created: !MODELS_DIR!
) else (
    echo  [+] Found: !MODELS_DIR!
)

if not exist "!MODELS_DIR!\chatlogs" (
    mkdir "!MODELS_DIR!\chatlogs" >nul 2>&1
    echo  [+] Created chatlogs subfolder.
)

echo.
echo  Scanning for .gguf models...
set GGUF_COUNT=0
for %%f in ("!MODELS_DIR!\*.gguf") do (
    set /a GGUF_COUNT+=1
    echo    [+] %%~nxf
)

if !GGUF_COUNT! EQU 0 (
    echo.
    echo  [!] No .gguf files found in: !MODELS_DIR!
    echo  [!] Add at least one model before launching.
    echo.
    echo  Recommended models from HuggingFace:
    echo    Qwen2.5-3B-Q4_K_M.gguf   ^(fast, ~2GB^)
    echo    Qwen3-8B-Q4_K_M.gguf     ^(balanced, ~5GB^)
    echo    Qwen2.5-14B-Q4_K_M.gguf  ^(deep, ~9GB^)
) else (
    echo.
    echo  [+] Found !GGUF_COUNT! model(s). Good to go.
)

:: STEP 5 - Write config and finalize
echo.
echo  [5/5] Writing config and finalizing...
echo  -----------------------------------------------------------
echo.

set CONFIG=%~dp0config.json
set CONFIG_SCRIPT=%TEMP%\npc_write_config.py

echo import json, sys, subprocess >> "%CONFIG_SCRIPT%"
echo. >> "%CONFIG_SCRIPT%"
echo def detect_vram_gb(): >> "%CONFIG_SCRIPT%"
echo     try: >> "%CONFIG_SCRIPT%"
echo         r = subprocess.run(["nvidia-smi","--query-gpu=memory.total","--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5) >> "%CONFIG_SCRIPT%"
echo         if r.returncode == 0: return int(r.stdout.strip().splitlines()[0].strip()) / 1024 >> "%CONFIG_SCRIPT%"
echo     except: pass >> "%CONFIG_SCRIPT%"
echo     return 0.0 >> "%CONFIG_SCRIPT%"
echo. >> "%CONFIG_SCRIPT%"
echo def detect_gpu_layers(vram): >> "%CONFIG_SCRIPT%"
echo     if vram < 2:  return 0 >> "%CONFIG_SCRIPT%"
echo     if vram < 4:  return 10 >> "%CONFIG_SCRIPT%"
echo     if vram < 6:  return 20 >> "%CONFIG_SCRIPT%"
echo     if vram < 8:  return 28 >> "%CONFIG_SCRIPT%"
echo     if vram < 12: return 35 >> "%CONFIG_SCRIPT%"
echo     if vram < 16: return 50 >> "%CONFIG_SCRIPT%"
echo     return 99 >> "%CONFIG_SCRIPT%"
echo. >> "%CONFIG_SCRIPT%"
echo vram = detect_vram_gb() >> "%CONFIG_SCRIPT%"
echo layers = detect_gpu_layers(vram) >> "%CONFIG_SCRIPT%"
echo print(f"[*] Detected VRAM: {vram:.1f}GB — setting gpu_layers={layers}") >> "%CONFIG_SCRIPT%"
echo config = {"theme": "green", "font_size": 11, "models_dir": sys.argv[1], "gpu_layers": layers} >> "%CONFIG_SCRIPT%"
echo with open(sys.argv[2], "w") as f: json.dump(config, f, indent=2) >> "%CONFIG_SCRIPT%"
echo print("[+] Config saved.") >> "%CONFIG_SCRIPT%"

python "%CONFIG_SCRIPT%" "!MODELS_DIR!" "%CONFIG%"
if %errorlevel% neq 0 (
    echo  [!!] Failed to write config.json.
    del "%CONFIG_SCRIPT%" >nul 2>&1
    pause
    exit /b 1
)
del "%CONFIG_SCRIPT%" >nul 2>&1

set LAUNCHER=%~dp0launch.bat
if not exist "%LAUNCHER%" (
    (
        echo @echo off
        echo title NEURAL_PC v1.0
        echo color 02
        echo set SCRIPT=%%~dp0neural_pc.py
        echo if not exist "%%SCRIPT%%" ^( echo [!!] neural_pc.py not found. ^& pause ^& exit /b 1 ^)
        echo python "%%SCRIPT%%"
        echo if %%errorlevel%% neq 0 ^( echo [ERROR] Something went wrong. ^& pause ^)
    ) > "%LAUNCHER%"
    echo  [+] Created launch.bat
) else (
    echo  [+] launch.bat already exists.
)

:: Done
echo.
echo  ============================================================
echo                    SETUP COMPLETE
echo  ============================================================
echo.
echo  Models folder : !MODELS_DIR!
echo  Config file   : %CONFIG%
echo.
if !GGUF_COUNT! GTR 0 (
    echo  Ready. Run launch.bat to start NEURAL_PC.
) else (
    echo  Add .gguf models to !MODELS_DIR! then run launch.bat.
)
echo.
echo  Commands to know:
echo    /help          show all commands
echo    /gpu ^<n^>       set GPU layers ^(auto-detected at boot^)
echo    /theme ^<name^>  green / amber / blue / red / white
echo    /setmodels     change models folder after setup
echo    /pong          ...
echo.
pause
endlocal
