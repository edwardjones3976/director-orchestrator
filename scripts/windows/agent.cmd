@rem [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | BEGIN]]
@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0agent.ps1" %*
exit /b %ERRORLEVEL%
@rem [[OpenAI Codex | GPT-5.6 | 2026-08-31 | 23:55 UTC | END]]
