@echo off
setlocal
cd /d "%~dp0"
set "HEADLESS=true"
set "GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/1oD2W3-Z5zCXeuQkSemhEO0UHofcfUam1FPUOPeAawtE/edit?usp=sharing"
set "GOOGLE_SERVICE_ACCOUNT_JSON=%~dp0restro-database-49ad65bf2a5c.json"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
"%PYTHON%" "%~dp0maps_scraper.py" --repair-missing-reviews-only --headless
if errorlevel 1 goto scraper_failed
call "%~dp0..\website\build_restaurant_guide.bat"
if errorlevel 1 goto site_failed
goto done

:scraper_failed
echo.
echo Scraper exited with an error; website build was skipped.
goto pause_for_error

:site_failed
echo.
echo Review repair finished, but the website build failed.

:pause_for_error
echo Press any key to close...
pause >nul

:done
endlocal
