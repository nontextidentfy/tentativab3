"""
Módulo de Segurança e Autenticação do Investidor IA.
Gerencia hashes Argon2, sessões via cookies HTTP-Only e tokens de e-mail/recuperação.
"""
import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Optional
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from dotenv import load_dotenv
from fastapi import Depends, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from banco import Usuario, obter_sessao_banco

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PASTA_PROJETO, ".env"))
log = logging.getLogger("investidor_ia.auth")

SECRET_KEY = os.getenv("SECRET_KEY") or "chave-padrao-desenvolvimento-trocar-em-producao-2026"
APP_BASE_URL = (os.getenv("APP_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
EM_PRODUCAO = os.getenv("RENDER") is not None or APP_BASE_URL.startswith("https://")
if not os.getenv("SECRET_KEY"):
    log.warning("SECRET_KEY não definida no .env: usando chave de desenvolvimento. Defina uma chave forte antes de publicar o site.")

COOKIE_SESSAO = "inv_ia_session"
TEMPO_SESSAO_SEGUNDOS = 60 * 60 * 24 * 7  # 7 dias

ph = PasswordHasher()
serializer = URLSafeTimedSerializer(SECRET_KEY)


def email_configurado() -> bool:
    """Retorna True somente quando SMTP_HOST, SMTP_USER e SMTP_PASS estão preenchidos no ambiente."""
    return bool(
        (os.getenv("SMTP_HOST") or "").strip()
        and (os.getenv("SMTP_USER") or "").strip()
        and (os.getenv("SMTP_PASS") or "").strip()
    )


# ---------------------------------------------------------------------------
# Senhas com Argon2
# ---------------------------------------------------------------------------
def gerar_hash_senha(senha_pura: str) -> str:
    return ph.hash(senha_pura)


def verificar_senha(senha_hash: str, senha_pura: str) -> bool:
    if not senha_hash:
        return False
    try:
        return ph.verify(senha_hash, senha_pura)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# Tokens Temporários (Sessão, Confirmação de E-mail e Recuperação de Senha)
# ---------------------------------------------------------------------------
def criar_token(dados: dict, salt: str) -> str:
    return serializer.dumps(dados, salt=salt)


def ler_token(token: str, salt: str, max_age_segundos: int) -> Optional[dict]:
    try:
        return serializer.loads(token, salt=salt, max_age=max_age_segundos)
    except (SignatureExpired, BadSignature):
        return None


def definir_cookie_sessao(response: Response, usuario_id: int):
    token = criar_token({"uid": usuario_id}, salt="sessao-usuario")
    response.set_cookie(
        key=COOKIE_SESSAO,
        value=token,
        max_age=TEMPO_SESSAO_SEGUNDOS,
        httponly=True,
        samesite="lax",
        secure=EM_PRODUCAO,
    )


def limpar_cookie_sessao(response: Response):
    response.delete_cookie(key=COOKIE_SESSAO)


def obter_usuario_atual(
    request: Request,
    db: Session = Depends(obter_sessao_banco),
) -> Optional[Usuario]:
    """Lê o cookie seguro e retorna o objeto Usuario logado (ou None se visitante)."""
    token = request.cookies.get(COOKIE_SESSAO)
    if not token:
        return None
    dados = ler_token(token, salt="sessao-usuario", max_age_segundos=TEMPO_SESSAO_SEGUNDOS)
    if not dados or "uid" not in dados:
        return None
    return db.query(Usuario).filter(Usuario.id == dados["uid"]).first()


def verificar_usuario_pro(usuario: Optional[Usuario] = Depends(obter_usuario_atual)) -> bool:
    """Verifica se o usuário atual está autenticado e possui plano Pro."""
    return bool(usuario and getattr(usuario, "eh_pro", False))


# ---------------------------------------------------------------------------
# Disparo de E-mails (SMTP ou Fallback no Console para Desenvolvimento)
# ---------------------------------------------------------------------------
def _imprimir_seguro(texto: str):
    """print() que nunca derruba a requisição por causa do encoding do terminal (ex.: emoji no Windows)."""
    try:
        print(texto, flush=True)
    except UnicodeEncodeError:
        print(texto.encode("ascii", errors="replace").decode("ascii"), flush=True)
    except Exception:
        pass


def enviar_email_transacional(destinatario: str, assunto: str, corpo_texto: str):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT") or "587")
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    remetente = os.getenv("SMTP_FROM") or smtp_user or "nao-responda@investidoria.com.br"

    if not email_configurado():
        _imprimir_seguro("\n" + "=" * 75)
        _imprimir_seguro(f"📧 [E-MAIL TRANSACIONAL] Para: {destinatario} | Assunto: {assunto}")
        _imprimir_seguro("-" * 75)
        _imprimir_seguro(corpo_texto)
        _imprimir_seguro("=" * 75 + "\n")
        return

    try:
        msg = EmailMessage()
        msg["Subject"] = assunto
        msg["From"] = remetente
        msg["To"] = destinatario
        msg.set_content(corpo_texto)

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    except Exception:
        log.exception("Falha ao enviar e-mail via SMTP para %s", destinatario)


def disparar_confirmacao_email(usuario: Usuario) -> str:
    token = criar_token({"uid": usuario.id, "email": usuario.email}, salt="confirmar-email")
    link = f"{APP_BASE_URL}/confirmar-email/{token}"
    corpo = (
        f"Olá, {usuario.nome}!\n\n"
        f"Bem-vindo ao Investidor IA. Para confirmar seu e-mail e ativar todos os recursos da sua conta, "
        f"acesse o link abaixo (válido por 24 horas):\n\n{link}\n\n"
        f"Se você não criou esta conta, ignore este e-mail."
    )
    enviar_email_transacional(usuario.email, "Confirme seu cadastro - Investidor IA", corpo)
    return link


def disparar_recuperacao_senha(usuario: Usuario) -> str:
    token = criar_token({"uid": usuario.id, "email": usuario.email}, salt="recuperar-senha")
    link = f"{APP_BASE_URL}/redefinir-senha/{token}"
    corpo = (
        f"Olá, {usuario.nome}!\n\n"
        f"Recebemos uma solicitação para redefinir sua senha no Investidor IA.\n"
        f"Acesse o link seguro abaixo (válido por 1 hora) para criar uma nova senha:\n\n{link}\n\n"
        f"Se não foi você, nenhuma alteração será feita."
    )
    enviar_email_transacional(usuario.email, "Recuperação de Senha - Investidor IA", corpo)
    return link
