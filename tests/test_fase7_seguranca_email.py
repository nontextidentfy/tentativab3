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


