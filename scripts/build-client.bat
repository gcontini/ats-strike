@echo off
rem Build the resumix client executable and install it into resumix-data\gab.
rem
rem The Windows counterpart of build-client.sh. The spec and the build command
rem are the ones CI uses (.github/workflows/ci.yml, job "client"). PyInstaller
rem does not cross-compile, so this produces dist\resumix.exe for Windows.
rem
rem No `uv sync` first: `uv run` syncs what it needs, and a plain `uv sync` would
rem remove the docs group from the venv.
rem
rem Only the executable is replaced. resumix-data\gab also holds the user's own
rem data and a resumix.toml with a bearer token in it; nothing else there is
rem touched.
setlocal

pushd "%~dp0.." || exit /b 1

set "DEST=resumix-data\gab"

rem A Windows virtualenv of its own, so it never clobbers the Linux .venv of a
rem checkout shared between the two systems.
set "UV_PROJECT_ENVIRONMENT=.venv_win"

uv run --with pyinstaller pyinstaller --noconfirm client\packaging\resumix.spec || goto :fail

dist\resumix.exe --help >nul || goto :fail
dist\resumix.exe version || goto :fail

copy /y dist\resumix.exe "%DEST%\resumix.exe" >nul || goto :fail

rem From inside the folder, so its resumix.toml is the one picked up.
pushd "%DEST%" || goto :fail
resumix.exe version || (popd & goto :fail)
popd

popd
exit /b 0

:fail
popd
exit /b 1
