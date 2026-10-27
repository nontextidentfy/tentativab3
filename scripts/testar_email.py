"""
Script utilitário para testar o envio real de e-mails transacionais via SMTP.
Uso: python scripts/testar_email.py destinatario@exemplo.com
"""
import os
import sys
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

PASTA_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(PASTA_PROJETO, ".env"))


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Uso: python scripts/testar_email.py <destinatario@exemplo.com>")
        sys.exit(1)

    destinatario = sys.argv[1].strip()

    smtp_host = (os.getenv("SMTP_HOST") or "").strip()
    smtp_port_raw = (os.getenv("SMTP_PORT") or "").strip() or "587"
    smtp_user = (os.getenv("SMTP_USER") or "").strip()
    smtp_pass = (os.getenv("SMTP_PASS") or "").strip()
    remetente = (os.getenv("SMTP_FROM") or "").strip() or smtp_user or "nao-responda@investidoria.com.br"

    faltantes = []
    if not smtp_host:
        faltantes.append("SMTP_HOST")
    if not smtp_user:
        faltantes.append("SMTP_USER")
    if not smtp_pass:
        faltantes.append("SMTP_PASS")

    if faltantes:
        print(f"Falha de configuração: variáveis SMTP ausentes ({', '.join(faltantes)}).")
        sys.exit(1)

    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        print(f"Falha de configuração: SMTP_PORT inválida ('{smtp_port_raw}').")
        sys.exit(1)

    msg = EmailMessage()
    msg["Subject"] = "Teste de Conexão - Investidor IA"
    msg["From"] = remetente
    msg["To"] = destinatario
    msg.set_content(
        "Olá!\n\n"
        "Esta é uma mensagem de teste enviada pelo script utilitário do Investidor IA para validar a conectividade SMTP.\n"
        "Se você recebeu este e-mail, o envio transacional está operando corretamente.\n"
    )

    def _sanitizar(texto: str) -> str:
        s = str(texto)
        if smtp_pass:
            s = s.replace(smtp_pass, "******")
        if smtp_user:
            s = s.replace(smtp_user, "******")
        return s

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as server:
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
        print("enviado")
    except smtplib.SMTPException as e:
        print(f"Falha no envio SMTP: {type(e).__name__}: {_sanitizar(e)}")
        sys.exit(1)
    except OSError as e:
        print(f"Falha de rede/conexão: {type(e).__name__}: {_sanitizar(e)}")
        sys.exit(1)
    except Exception as e:
        print(f"Falha inesperada: {type(e).__name__}: {_sanitizar(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
