@echo off
REM Double-click this from Windows Explorer to launch the llm-xray UI.
REM It runs the bash launcher inside WSL, from this folder.
echo Starting llm-xray UI inside WSL...
echo When it's running, open http://127.0.0.1:8000 in your browser.
echo (Close this window or press Ctrl-C to stop.)
echo.
wsl --cd "%~dp0" bash run_this_to_start.sh %*
echo.
echo Server stopped.
pause
