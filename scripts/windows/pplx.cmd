@REM [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | BEGIN]]
@echo off
wsl.exe --distribution Ubuntu --exec /home/edward/.local/bin/pplx %*
exit /b %ERRORLEVEL%
@REM [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | END]]
