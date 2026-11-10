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






# ==============================================================================
# PÓS-FASE 7: e-mail de boas-vindas após a primeira confirmação
# ==============================================================================

def _usuario_nao_confirmado(sessao_db, email):
    u = banco.Usuario(nome="Maria Teste", email=email, senha_hash=auth.gerar_hash_senha("Senha123!"), email_confirmado=False)
    sessao_db.add(u)
    sessao_db.commit()
    sessao_db.refresh(u)
    return u


def test_confirmar_email_envia_boas_vindas_uma_unica_vez(client, sessao_db, monkeypatch):
    enviados = []
    monkeypatch.setattr(auth, "enviar_email_transacional", lambda dest, assunto, corpo: enviados.append((dest, assunto, corpo)))

    u = _usuario_nao_confirmado(sessao_db, "boasvindas@exemplo.com")
    token = auth.criar_token({"uid": u.id, "email": u.email}, salt="confirmar-email")

    r1 = client.get(f"/confirmar-email/{token}", follow_redirects=False)
    assert r1.status_code == 303
    assert len(enviados) == 1
    dest, assunto, corpo = enviados[0]
    assert dest == "boasvindas@exemplo.com"
    assert "Bem-vindo" in assunto
    assert "Maria" in corpo
    # Texto educativo: sem recomendação de compra/venda nem promessa de retorno
    for proibida in ("compre", "venda suas", "garantido", "preço-alvo"):
        assert proibida not in corpo.lower()
    assert "não é recomendação" in corpo.lower()

    # Clicar de novo no mesmo link não reenvia
    client.get(f"/confirmar-email/{token}", follow_redirects=False)
    assert len(enviados) == 1


def test_confirmar_email_com_token_invalido_nao_envia_boas_vindas(client, monkeypatch):
    enviados = []
    monkeypatch.setattr(auth, "enviar_email_transacional", lambda *a, **k: enviados.append(a))
    client.get("/confirmar-email/token-invalido", follow_redirects=False)
    assert enviados == []


# ==============================================================================
# AUDITORIA LGPD E ROTAS DE PRIVACIDADE
# ==============================================================================

def test_rota_privacidade_redireciona_301_para_termos_privacidade(client):
    """Garante que /privacidade redireciona para /termos#privacidade com status 301."""
    resp = client.get("/privacidade", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/termos#privacidade"


def test_pagina_termos_e_lgpd_sem_placeholders_e_com_contato_dpo(client):
    """Garante que templates/legal.html renderiza 200 e não contém placeholders não preenchidos."""
    resp = client.get("/termos")
    assert resp.status_code == 200
    assert "pedromoura132123@gmail.com" in resp.text
    assert "LGPD" in resp.text
    assert "[E-MAIL DO ENCARREGADO/DPO]" not in resp.text
    assert "[RAZÃO SOCIAL]" not in resp.text
    assert "[E-MAIL DE CONTATO]" not in resp.text
    assert "[DATA]" not in resp.text


def test_cadastro_sem_aceite_lgpd_retorna_422_ou_erro(client):
    """Garante que o cadastro recusa submissão sem aceite dos termos LGPD."""
    resp = client.post(
        "/cadastro",
        data={
            "nome": "Usuario Teste Sem LGPD",
            "email": "semlgpd@exemplo.com",
            "senha": "SenhaForte123!",
            "perfil": "Fundamentalista",
            # aceite_termos omitido propositalmente
        },
    )
    assert resp.status_code in (200, 422)
    assert "Você precisa aceitar os Termos de Uso e Política de Privacidade" in resp.text


# ==============================================================================
# AUDITORIA AVANÇADA LGPD, SESSÕES, RECUPERAÇÃO E CONTAS
# ==============================================================================

def test_cadastro_com_aceite_lgpd_persiste_timestamp_no_banco(client, sessao_db):
    """Garante que o aceite dos termos LGPD preenche e persiste 'aceitou_termos_em' no banco."""
    import datetime
    email_teste = "consentimento_lgpd@exemplo.com"
    resp = client.post(
        "/cadastro",
        data={
            "nome": "Investidor Consentido",
            "email": email_teste,
            "senha": "SenhaForte123!",
            "perfil": "Fundamentalista",
            "aceite_termos": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    usuario = sessao_db.query(banco.Usuario).filter_by(email=email_teste).first()
    assert usuario is not None
    assert usuario.aceitou_termos_em is not None
    assert isinstance(usuario.aceitou_termos_em, datetime.datetime)
    delta = (banco.agora_utc() - usuario.aceitou_termos_em).total_seconds()
    assert delta < 10.0


def test_cadastro_recusado_sem_aceite_lgpd_nao_persiste_no_banco(client, sessao_db):
    """Garante que a recusa do cadastro por falta de aceite não cria nenhum registro no banco."""
    email_teste = "recusado_lgpd@exemplo.com"
    resp = client.post(
        "/cadastro",
        data={
            "nome": "Usuario Recusado",
            "email": email_teste,
            "senha": "SenhaForte123!",
            "perfil": "Dividendos",
        },
    )
    assert resp.status_code in (200, 422)
    assert "Você precisa aceitar os Termos de Uso e Política de Privacidade" in resp.text
    assert sessao_db.query(banco.Usuario).filter_by(email=email_teste).first() is None


def test_confirmar_email_ativa_flag_no_banco_e_persiste(client, sessao_db, monkeypatch):
    """Garante que o clique no link válido efetivamente comuta 'email_confirmado = True' no banco."""
    monkeypatch.setattr(auth, "enviar_email_transacional", lambda *args, **kwargs: None)
    u = _usuario_nao_confirmado(sessao_db, "ativar_flag@exemplo.com")
    assert u.email_confirmado is False

    token = auth.criar_token({"uid": u.id, "email": u.email}, salt="confirmar-email")
    resp = client.get(f"/confirmar-email/{token}", follow_redirects=False)
    assert resp.status_code == 303

    sessao_db.refresh(u)
    assert u.email_confirmado is True


def test_confirmar_email_com_token_invalido_renderiza_erro(client):
    """Garante que token inválido exibe a mensagem amigável na tela de login."""
    resp = client.get("/confirmar-email/token-totalmente-invalido")
    assert resp.status_code == 200
    assert "Link de confirmação inválido ou expirado." in resp.text


def test_esqueci_senha_anti_enumeracao_respostas_identicas(client, sessao_db, monkeypatch):
    """Garante que a resposta HTTP é IDÊNTICA para e-mail existente e inexistente, impedindo enumeração."""
    import main
    _usuario_nao_confirmado(sessao_db, "cadastrado@exemplo.com")

    emails_enviados = []
    monkeypatch.setattr(auth, "disparar_recuperacao_senha", lambda user, bg=None: emails_enviados.append(user.email))

    r_existe = client.post("/esqueci-senha", data={"email": "cadastrado@exemplo.com"})
    assert r_existe.status_code == 200
    assert len(emails_enviados) == 1

    if hasattr(main.limiter, "_storage") and hasattr(main.limiter._storage, "reset"):
        main.limiter._storage.reset()

    r_nao_existe = client.post("/esqueci-senha", data={"email": "nao_existe_jamais@exemplo.com"})
    assert r_nao_existe.status_code == 200
    assert len(emails_enviados) == 1  # Não enviou segundo e-mail

    assert "Se o e-mail estiver cadastrado" in r_existe.text
    assert "Se o e-mail estiver cadastrado" in r_nao_existe.text
    assert r_existe.text == r_nao_existe.text


def test_redefinir_senha_reuso_rejeitado_em_get_e_post(client, sessao_db):
    """Garante que link de recuperação usado uma vez não pode ser aberto nem submetido novamente."""
    u = _logar(client, sessao_db, email="reuso_link@exemplo.com", senha="SenhaAntiga123!")
    token_link = auth.disparar_recuperacao_senha(u).split("/")[-1]

    r_get1 = client.get(f"/redefinir-senha/{token_link}")
    assert r_get1.status_code == 200
    assert "Definir Nova Senha" in r_get1.text

    r_post1 = client.post(
        f"/redefinir-senha/{token_link}",
        data={"nova_senha": "NovaSenhaForte456!"},
        follow_redirects=False,
    )
    assert r_post1.status_code == 303

    r_get2 = client.get(f"/redefinir-senha/{token_link}")
    assert r_get2.status_code == 200
    assert "Este link de recuperação já foi utilizado" in r_get2.text

    r_post2 = client.post(
        f"/redefinir-senha/{token_link}",
        data={"nova_senha": "OutraSenhaTentada789!"},
        follow_redirects=False,
    )
    assert r_post2.status_code == 200
    assert "Este link de recuperação já foi utilizado" in r_post2.text


def test_excluir_conta_senha_incorreta_recusa_e_preserva_dados(client, sessao_db):
    """Garante que POST /conta/excluir com senha incorreta não apaga o usuário nem seus dados."""
    u = _logar(client, sessao_db, email="nao_excluir@exemplo.com", senha="SenhaCerta123!")
    monit = banco.MonitoramentoUsuario(usuario_id=u.id, ticker="PETR4")
    sessao_db.add(monit)
    sessao_db.commit()

    resp = client.post("/conta/excluir", data={"senha_confirmacao": "SenhaIncorreta999!"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "/watchlist?erro=" in resp.headers["location"]

    assert sessao_db.query(banco.Usuario).filter_by(id=u.id).first() is not None
    assert sessao_db.query(banco.MonitoramentoUsuario).filter_by(usuario_id=u.id).count() == 1


def test_excluir_conta_senha_correta_executa_cascade_lgpd_e_limpa_cookie(client, sessao_db):
    """Garante exclusão definitiva do usuário, exclusão em cascata dos monitoramentos e expiração do cookie de sessão."""
    u = _logar(client, sessao_db, email="excluir_definitivo@exemplo.com", senha="SenhaSecreta123!")
    u_id = u.id

    sessao_db.add_all([
        banco.MonitoramentoUsuario(usuario_id=u_id, ticker="VALE3"),
        banco.MonitoramentoUsuario(usuario_id=u_id, ticker="ITUB4"),
    ])
    sessao_db.commit()
    assert sessao_db.query(banco.MonitoramentoUsuario).filter_by(usuario_id=u_id).count() == 2

    resp = client.post("/conta/excluir", data={"senha_confirmacao": "SenhaSecreta123!"}, follow_redirects=False)
    assert resp.status_code == 303
    assert "/?msg=" in resp.headers["location"]

    set_cookie = resp.headers.get("set-cookie", "")
    assert auth.COOKIE_SESSAO in set_cookie
    assert "max-age=0" in set_cookie.lower() or "expires=" in set_cookie.lower()

    assert sessao_db.query(banco.Usuario).filter_by(id=u_id).first() is None
    assert sessao_db.query(banco.MonitoramentoUsuario).filter_by(usuario_id=u_id).count() == 0


def test_excluir_conta_sem_autenticacao_redireciona_login(client):
    """Garante que requisição anônima para excluir conta redireciona para /login."""
    client.cookies.clear()
    resp = client.post("/conta/excluir", data={"senha_confirmacao": "QualquerCoisa"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


