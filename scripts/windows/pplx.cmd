@REM [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | BEGIN]]
@REM [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | BEGIN]]
@echo off
setlocal
if not defined DIRECTOR_PPLX_WSL_DISTRIBUTION set "DIRECTOR_PPLX_WSL_DISTRIBUTION=Ubuntu"
if not defined DIRECTOR_PPLX_WSL_EXEC set "DIRECTOR_PPLX_WSL_EXEC=pplx"
wsl.exe --distribution %DIRECTOR_PPLX_WSL_DISTRIBUTION% --exec %DIRECTOR_PPLX_WSL_EXEC% %*
endlocal & exit /b %ERRORLEVEL%
@REM [[OpenAI Codex | GPT-5.6 | 2026-09-01 | 00:28 UTC | END]]
@REM [[Claude | Claude Opus 5 | 2026-09-02 | 04:15 UTC | END]]
