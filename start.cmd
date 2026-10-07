@echo off
setlocal
set "PYTHONUTF8=1"
pushd "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] uv was not found. Install uv and try again.
    pause
    popd
    exit /b 1
)
uv run --locked python -m rss_v2.main start %*
set "rss_exit=%errorlevel%"
if not "%rss_exit%"=="0" pause
popd
exit /b %rss_exit%
