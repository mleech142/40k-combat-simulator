@echo off
title 40K Combat Simulator

echo ============================================
echo    40K Combat Simulator - Launcher
echo ============================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Check if venv exists, create if not
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate venv
call venv\Scripts\activate.bat

:: Install/upgrade dependencies
echo Checking dependencies...
pip install -q -r requirements.txt

:: Build the data file from the Excel workbook if it doesn't exist yet
if not exist "data k_data.json" (
    echo.
    echo Building data k_data.json from the Excel workbook...
    python scripts\migrate_excel.py
)

:: Start the application
echo.
echo Starting 40K Combat Simulator...
echo.
python run.py

pause
