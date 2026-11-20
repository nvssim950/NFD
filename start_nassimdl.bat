@echo off
title NassimDL Accelerator
echo Starting NassimDL Background Service...
python -m nassimdl app --background
echo NassimDL is now running in the background and listening for Chrome downloads!
pause
