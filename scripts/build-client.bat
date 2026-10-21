@echo off
rem Build the ats-strike client executable and install it into ats-strike-data\gab.
rem
rem The Windows counterpart of build-client.sh. The spec and the build command
rem are the ones CI uses (.github/workflows/ci.yml, job "client"). PyInstaller
rem does not cross-compile, so this produces dist\cv.exe for Windows.
rem
rem No `uv sync` first: `uv run` syncs what it needs, and a plain `uv sync` would
rem remove the docs group from the venv.
rem
rem Only the executable is replaced. ats-strike-data\gab also holds the user's own
rem data and a cv.toml with a bearer token in it; nothing else there is
rem touched.
setlocal

pushd "%~dp0.." || exit /b 1

set "DEST=ats-strike-data\gab"

rem A Windows virtualenv of its own, so it never clobbers the Linux .venv of a
rem checkout shared between the two systems.
set "UV_PROJECT_ENVIRONMENT=.venv_win"

uv run --with pyinstaller pyinstaller --noconfirm client\packaging\cv.spec || goto :fail

dist\cv.exe --help >nul || goto :fail
dist\cv.exe version || goto :fail

copy /y dist\cv.exe "%DEST%\cv.exe" >nul || goto :fail

rem From inside the folder, so its cv.toml is the one picked up.
pushd "%DEST%" || goto :fail
cv.exe version || (popd & goto :fail)
popd

popd
exit /b 0

:fail
popd
exit /b 1
