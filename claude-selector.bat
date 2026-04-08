@echo off
chcp 65001 >nul
echo.
echo =======================================
echo    Claude Code - Selector de Proveedor
echo =======================================
echo.
echo   1) Claude (Anthropic) - modelos nativos
echo   2) GitHub Copilot     - todos los modelos
echo.
choice /c 12 /n /m "Elige una opcion [1/2]: "
if %errorlevel%==1 goto anthropic
if %errorlevel%==2 goto copilot_models

:anthropic
echo.
echo Usando Claude (Anthropic)...
set ANTHROPIC_BASE_URL=
set ANTHROPIC_API_KEY=
claude
goto end

:copilot_models
echo.
echo --- Modelos disponibles via Copilot ---
echo.
echo  [Claude]
echo   1) claude-sonnet-4.6     (recomendado)
echo   2) claude-opus-4.6
echo   3) claude-opus-4.6-fast
echo   4) claude-sonnet-4.5
echo   5) claude-haiku-4.5
echo.
echo  [OpenAI]
echo   6) gpt-4o
echo   7) gpt-4.1
echo   8) gpt-5.4
echo   9) gpt-5.4-mini
echo.
echo  [Google]
echo   A) gemini-2.5-pro
echo   B) gemini-3.1-pro-preview
echo.
echo  [Otro]
echo   C) Escribe el modelo manualmente
echo.
choice /c 123456789ABC /n /m "Elige modelo: "
set MC=%errorlevel%

if %MC%==1  set MODEL=claude-sonnet-4.6
if %MC%==2  set MODEL=claude-opus-4.6
if %MC%==3  set MODEL=claude-opus-4.6-fast
if %MC%==4  set MODEL=claude-sonnet-4.5
if %MC%==5  set MODEL=claude-haiku-4.5
if %MC%==6  set MODEL=gpt-4o
if %MC%==7  set MODEL=gpt-4.1
if %MC%==8  set MODEL=gpt-5.4
if %MC%==9  set MODEL=gpt-5.4-mini
if %MC%==10 set MODEL=gemini-2.5-pro
if %MC%==11 set MODEL=gemini-3.1-pro-preview
if %MC%==12 (
    set /p MODEL="Escribe el ID del modelo: "
)

goto copilot_start

:copilot_start
echo.
echo Verificando proxy de Copilot...
curl -s http://localhost:3000 >nul 2>&1
if %errorlevel% neq 0 (
    echo Arrancando proxy...
    start /b cmd /c "npx copilot-api-plus@latest start --port 3000"
    timeout /t 6 /nobreak >nul
)
set ANTHROPIC_BASE_URL=http://localhost:3000
set ANTHROPIC_API_KEY=copilot
echo Modelo: %MODEL%
echo.
claude --model %MODEL%
goto end

:end
