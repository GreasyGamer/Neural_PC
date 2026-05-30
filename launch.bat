@echo off
title NEURAL_PC v1.0
color 02

:: ─────────────────────────────────────────────────────────────
::  NEURAL_PC v1.0 — LAUNCHER
::  Requires: Python 3.10+ on PATH
::  Dependencies: pip install -r requirements.txt
:: ─────────────────────────────────────────────────────────────

set SCRIPT=%~dp0neural_pc.py

if not exist "%SCRIPT%" (
    echo.
    echo  [!!] neural_pc.py not found next to this launcher.
    echo  [!!] Make sure both files are in the same folder.
    echo.
    pause
    exit /b 1
)

python "%SCRIPT%"

if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Something went wrong. See above for details.
    echo  Common fix: pip install -r requirements.txt
    echo.
    pause
)
