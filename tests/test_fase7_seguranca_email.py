"""Fase 7: segurança (CSRF por origem, sessões, IP do proxy) e envio de e-mail em segundo plano."""
import banco
import seguranca as auth


def _logar(client, sessao_db, email="fase7@exemplo.com", confirmado=True, senha="SenhaForte123!"):
    u = banco.Usuario(nome="Fase Sete", email=email, senha_hash=auth.gerar_hash_senha(senha), email_confirmado=confirmado)
    sessao_db.add(u)
    sessao_db.commit()
    sessao_db.refresh(u)
    client.cookies.set(auth.COOKIE_SESSAO, auth.criar_token({"uid": u.id}, salt="sessao-usuario"))
    return u


# --- Passo 1: CSRF por verificação de origem ------------------------------------------------

def test_csrf_origin_de_outro_site_da_403(client, sessao_db):
    _logar(client, sessao_db)
    resp = client.post(
        "/watchlist/adicionar",
        data={"ticker": "PETR4"},
        headers={"Origin": "https://site-malicioso.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert "Pedido bloqueado por segurança. Abra o site de novo e tente outra vez." in resp.text


def test_csrf_origin_igual_ao_host_passa(client, sessao_db):
    _logar(client, sessao_db)
    resp = client.post(
        "/watchlist/adicionar",
        data={"ticker": "PETR4"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_csrf_sem_origin_passa(client, sessao_db):
    _logar(client, sessao_db)
    resp = client.post("/watchlist/adicionar", data={"ticker": "PETR4"}, follow_redirects=False)
    assert resp.status_code == 303


def test_csrf_sec_fetch_site_cross_site_da_403_e_same_origin_passa(client, sessao_db):
    _logar(client, sessao_db)
    r_cross = client.post("/watchlist/adicionar", data={"ticker": "PETR4"},
                          headers={"Sec-Fetch-Site": "cross-site"}, follow_redirects=False)
    assert r_cross.status_code == 403
    r_same = client.post("/watchlist/adicionar", data={"ticker": "VALE3"},
                         headers={"Sec-Fetch-Site": "same-origin", "Origin": "https://site-malicioso.com"},
                         follow_redirects=False)
    assert r_same.status_code == 303


# --- Passo 2: Sessões e contas antigas -----------------------------------------------------

def test_sessao_antiga_morre_apos_redefinicao(client, sessao_db):
    u = _logar(client, sessao_db, email="redefinir_sessao@exemplo.com", senha="SenhaAntiga123!")
    # Sessão atual carrega o carimbo da senha antiga
    token_sessao = auth.criar_token({"uid": u.id, "ps": auth.obter_carimbo_senha(u.senha_hash)}, salt="sessao-usuario")
    client.cookies.set(auth.COOKIE_SESSAO, token_sessao)
    assert client.get("/watchlist", follow_redirects=False).status_code == 200

    # Dispara token de recuperação e redefine senha
    token_rec = auth.disparar_recuperacao_senha(u)
    token_str = token_rec.split("/")[-1]
    resp_redef = client.post(
        f"/redefinir-senha/{token_str}",
        data={"nova_senha": "NovaSenhaForte456!"},
        follow_redirects=False,
    )
    assert resp_redef.status_code == 303
    # Verifica que o cookie foi limpo na resposta da redefinição
    cookie_header = resp_redef.headers.get("set-cookie", "")
    assert auth.COOKIE_SESSAO in cookie_header

    # Reinsere o cookie antigo e tenta acessar: deve ser tratado como deslogado
    client.cookies.set(auth.COOKIE_SESSAO, token_sessao)
    resp_antiga = client.get("/watchlist", follow_redirects=False)
    assert resp_antiga.status_code == 303
    assert "/login" in resp_antiga.headers["location"]


def test_cadastro_email_antigo_sem_senha_nao_loga_e_dispara_email(client, sessao_db, monkeypatch):
    u_antigo = banco.Usuario(nome="Antigo Sem Senha", email="antigo_semsenha@exemplo.com", senha_hash="", email_confirmado=True)
    sessao_db.add(u_antigo)
    sessao_db.commit()

    disparado = []
    monkeypatch.setattr(auth, "disparar_recuperacao_senha", lambda user: disparado.append(user.email) or "link_mock")

    resp = client.post(
        "/cadastro",
        data={
            "nome": "Tentativa Invasao",
            "email": "antigo_semsenha@exemplo.com",
            "senha": "NovaSenhaQualquer123!",
            "perfil": "Fundamentalista",
            "aceite_termos": "sim",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Esta conta já existe. Enviamos ao e-mail cadastrado um link para definir a senha." in resp.text
    assert disparado == ["antigo_semsenha@exemplo.com"]
    # Não logou
    assert client.cookies.get(auth.COOKIE_SESSAO) is None


def test_cookie_sem_ps_continua_aceito(client, sessao_db):
    u = banco.Usuario(nome="Legado Cookie", email="legado@exemplo.com", senha_hash=auth.gerar_hash_senha("Senha123!"), email_confirmado=True)
    sessao_db.add(u)
    sessao_db.commit()
    sessao_db.refresh(u)

    # Cookie emitido antes da Fase 7 (sem "ps")
    token_legado = auth.criar_token({"uid": u.id}, salt="sessao-usuario")
    client.cookies.set(auth.COOKIE_SESSAO, token_legado)

    resp = client.get("/watchlist", follow_redirects=False)
    assert resp.status_code == 200


# ==============================================================================
# PASSO 3: IP real atrás do proxy (Render usa o último endereço de XFF)
# ==============================================================================

def test_obter_ip_cliente_usa_ultimo_ip_do_proxy(monkeypatch):
    from starlette.requests import Request
    import main

    monkeypatch.setenv("CONFIAR_PROXY", "1")
    scope = {
        "type": "http",
        "headers": [
            (b"x-forwarded-for", b"spoofed.client.ip, 198.51.100.2, 192.0.2.99"),
        ],
        "client": ("127.0.0.1", 54321),
    }
    req = Request(scope)
    assert main.obter_ip_cliente(req) == "192.0.2.99"


# ==============================================================================
# PASSO 4: E-mail que chega (BackgroundTasks, SMTP 465 SSL, SMTP_FROM, /saude)
# ==============================================================================

def test_rota_cadastro_responde_antes_do_envio(client, monkeypatch, sessao_db):
    """Garante que a resposta HTTP é construída antes do disparo do e-mail via BackgroundTasks."""
    from fastapi import BackgroundTasks
    from starlette.requests import Request
    import main

    eventos = []

    def mock_enviar(destinatario, assunto, corpo):
        import time
        time.sleep(0.05)
        eventos.append("envio_concluido")

    monkeypatch.setattr(auth, "enviar_email_transacional", mock_enviar)

    bg = BackgroundTasks()
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "path": "/cadastro",
    }
    req = Request(scope)

    # Invoca o handler passando o background_tasks
    resp = main.processar_cadastro(
        request=req,
        background_tasks=bg,
        nome="Usuario Background",
        email="bg_user@exemplo.com",
        senha="SenhaForte123!",
        perfil="Fundamentalista",
        aceite_termos="on",
        db=sessao_db,
    )

    # A rota já respondeu com redirecionamento (303), e o envio AINDA não rodou
    assert resp.status_code == 303
    assert eventos == []
    assert len(bg.tasks) == 1

    # Ao executar o ciclo de background tasks do FastAPI, o envio ocorre
    import anyio
    anyio.run(bg)
    assert eventos == ["envio_concluido"]


def test_smtp_from_com_nome_aceito_por_email_message(monkeypatch):
    """Garante que o formato 'Investidor IA <email@dominio>' é aceito e configurado corretamente no From."""
    mensagens_capturadas = []

    class MockServer:
        def __init__(self, host, port, timeout=10):
            pass
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
        def starttls(self):
            pass
        def login(self, u, p):
            pass
        def send_message(self, msg):
            mensagens_capturadas.append(msg)

    monkeypatch.setattr(auth.smtplib, "SMTP", MockServer)
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "usuario_smtp")
    monkeypatch.setenv("SMTP_PASS", "senha_smtp")
    monkeypatch.setenv("SMTP_FROM", "Investidor IA <contato@investidoria.com.br>")

    auth.enviar_email_transacional("destinatario@teste.com", "Assunto Teste", "Corpo do e-mail.")

    assert len(mensagens_capturadas) == 1
    msg = mensagens_capturadas[0]
    assert msg["From"] == "Investidor IA <contato@investidoria.com.br>"
    assert msg["To"] == "destinatario@teste.com"
    assert "Investidor IA <contato@investidoria.com.br>" in msg.as_string()


def test_saude_reporta_estado_email(client, monkeypatch):
    """Garante que o endpoint /saude informa com precisão se o SMTP está configurado."""
    # Sem variáveis SMTP
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    r1 = client.get("/saude")
    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"
    assert r1.json()["email_configurado"] is False

    # Com variáveis SMTP completas
    monkeypatch.setenv("SMTP_HOST", "smtp.resend.com")
    monkeypatch.setenv("SMTP_USER", "resend")
    monkeypatch.setenv("SMTP_PASS", "re_secret")
    r2 = client.get("/saude")
    assert r2.status_code == 200
    assert r2.json()["email_configurado"] is True


def test_porta_465_escolhe_smtp_ssl(monkeypatch):
    """Garante que na porta 465 utiliza smtplib.SMTP_SSL diretamente, e em 587 usa SMTP + starttls."""
    chamadas_ssl = []
    chamadas_starttls = []

    class MockSSL:
        def __init__(self, host, port, timeout=10):
            chamadas_ssl.append((host, port, timeout))
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
        def login(self, u, p):
            pass
        def send_message(self, msg):
            pass

    class MockTLS:
        def __init__(self, host, port, timeout=10):
            chamadas_starttls.append((host, port, timeout))
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
        def starttls(self):
            chamadas_starttls.append("starttls_chamado")
        def login(self, u, p):
            pass
        def send_message(self, msg):
            pass

    monkeypatch.setattr(auth.smtplib, "SMTP_SSL", MockSSL)
    monkeypatch.setattr(auth.smtplib, "SMTP", MockTLS)
    monkeypatch.setenv("SMTP_HOST", "smtp.servidor.com")
    monkeypatch.setenv("SMTP_USER", "usuario")
    monkeypatch.setenv("SMTP_PASS", "senha")

    # Caso 1: Porta 465 -> SMTP_SSL
    monkeypatch.setenv("SMTP_PORT", "465")
    auth.enviar_email_transacional("user@ex.com", "SSL", "Texto")
    assert len(chamadas_ssl) == 1
    assert chamadas_ssl[0] == ("smtp.servidor.com", 465, 10)
    assert len(chamadas_starttls) == 0

    # Caso 2: Porta 587 -> SMTP + starttls
    chamadas_ssl.clear()
    chamadas_starttls.clear()
    monkeypatch.setenv("SMTP_PORT", "587")
    auth.enviar_email_transacional("user@ex.com", "TLS", "Texto")
    assert len(chamadas_ssl) == 0
    assert ("smtp.servidor.com", 587, 10) in chamadas_starttls
    assert "starttls_chamado" in chamadas_starttls


def test_producao_sem_smtp_registra_log_error(monkeypatch, caplog):
    """Garante que em produção a falta de variáveis SMTP registra log.error com os nomes das variáveis faltantes."""
    import logging
    caplog.set_level(logging.ERROR)
    auth.log.disabled = False

    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)

    auth.log.addHandler(caplog.handler)
    try:
        auth.validar_configuracao_smtp(em_producao=True)
        assert "SMTP_HOST" in caplog.text and "SMTP_USER" in caplog.text and "SMTP_PASS" in caplog.text
    finally:
        auth.log.removeHandler(caplog.handler)


# ==============================================================================
# PASSO 5: Preparação para o Render (render.yaml e Permissions-Policy)
# ==============================================================================

def test_cabecalho_permissions_policy(client):
    """Garante que o cabeçalho Permissions-Policy é adicionado às respostas pelo middleware."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("Permissions-Policy") == "camera=(), microphone=(), geolocation=()"


def test_render_yaml_configuracao_e_variaveis():
    """Garante que render.yaml existe, está no plano gratuito, com 2 workers e lista as 13 envVars sem valores."""
    import os
    import yaml

    caminho_yaml = os.path.join(auth.PASTA_PROJETO, "render.yaml")
    assert os.path.exists(caminho_yaml), "render.yaml deve existir na raiz do projeto"

    with open(caminho_yaml, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    servicos = config.get("services", [])
    assert len(servicos) == 1
    web = servicos[0]

    assert web.get("plan") == "free"
    assert web.get("buildCommand") == "pip install -r requirements.txt"
    assert "gunicorn main:app -w 2 -k uvicorn_worker.UvicornWorker --bind 0.0.0.0:$PORT" in web.get("startCommand", "")
    assert web.get("healthCheckPath") == "/saude"

    vars_esperadas = {
        "DATABASE_URL",
        "SECRET_KEY",
        "APP_BASE_URL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USER",
        "SMTP_PASS",
        "SMTP_FROM",
        "CONFIAR_PROXY",
        "EMPRESA_RODAPE",
        "PYTHON_VERSION",
    }

    env_vars = web.get("envVars", [])
    vars_presentes = set()
    for item in env_vars:
        chave = item.get("key")
        vars_presentes.add(chave)
        assert item.get("sync") is False, f"Variável {chave} deve ter sync: false"
        # Nenhuma variável de ambiente deve ter valor exposto no render.yaml
        assert not item.get("value"), f"Variável {chave} não deve possuir valor preenchido no render.yaml"

    assert vars_esperadas.issubset(vars_presentes), f"Faltam variáveis no render.yaml: {vars_esperadas - vars_presentes}"




