@echo off
chcp 65001 >nul
cd /d "%~dp0"
python --version
pip install -r requirements.txt
python auto_otvet.py
pause
