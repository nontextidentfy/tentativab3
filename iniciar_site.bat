@echo off
REM ==========================================================================
REM  Investidor IA - inicia o site localmente com um duplo clique.
REM ==========================================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".env" (
  echo [ERRO] Arquivo .env nao encontrado nesta pasta.
  echo Copie o arquivo .env.example para .env e preencha a DATABASE_URL do Neon.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente virtual Python pela primeira vez...
  python -m venv .venv || (echo [ERRO] Python nao encontrado. Instale o Python 3.12+ & pause & exit /b 1)
)

echo Verificando dependencias...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [ERRO] Falha ao instalar as dependencias.
  pause
  exit /b 1
)

echo.
echo  Site rodando em: http://127.0.0.1:8000
echo  Para encerrar, feche esta janela ou pressione CTRL+C.
echo.
REM Abre o navegador depois de alguns segundos, quando o servidor ja estiver de pe
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start "" http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
pause
