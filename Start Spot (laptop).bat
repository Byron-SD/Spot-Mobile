@echo off
rem Double-click to drive Spot from this laptop. See README.md.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-laptop.ps1" %*
pause
