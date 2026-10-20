"""Testes da Fase 1: rate limit por IP real, validações de formulário, concorrência e variação nula."""
from types import SimpleNamespace
from sqlalchemy.exc import IntegrityError
import banco
import main
import seguranca as auth


def test_1_1_key_func_com_e_sem_confiar_proxy(monkeypatch):
    req_falsa = SimpleNamespace(
        headers={"X-Forwarded-For": "203.0.113.42, 10.0.0.1"},
        client=SimpleNamespace(host="127.0.0.1"),
    )

    monkeypatch.delenv("CONFIAR_PROXY", raising=False)
    assert main.obter_ip_cliente(req_falsa) == "127.0.0.1"

    monkeypatch.setenv("CONFIAR_PROXY", "0")
    assert main.obter_ip_cliente(req_falsa) == "127.0.0.1"

    monkeypatch.setenv("CONFIAR_PROXY", "1")
    assert main.obter_ip_cliente(req_falsa) == "203.0.113.42"


def test_1_1_rate_limit_estourado_devolve_429_e_diferencia_diario(client, monkeypatch):
    # Estoura o limite de /esqueci-senha (3/minute)
    for _ in range(3):
        r = client.post("/esqueci-senha", data={"email": "teste@exemplo.com"})
        assert r.status_code == 200

    r_bloqueado = client.post("/esqueci-senha", data={"email": "teste@exemplo.com"})
    assert r_bloqueado.status_code == 429
    assert "por minuto" in r_bloqueado.text.lower()

    # Verifica diferenciação quando o detalhe da exceção menciona limite diário ("day")
    exc_diaria = SimpleNamespace(detail="10 per 1 day")
    import asyncio
    req = SimpleNamespace(cookies={}, headers={}, client=SimpleNamespace(host="127.0.0.1"))
    resp_diaria = asyncio.run(main.rate_limit_handler(req, exc_diaria))
    assert resp_diaria.status_code == 429
    assert "limite diário" in bytes(resp_diaria.body).decode("utf-8").lower()


def test_1_2_nome_so_com_espacos_e_usuario_nome_vazio_na_home(client, sessao_db):
    resp = client.post(
        "/cadastro",
        data={
            "nome": "     ",
            "email": "espacos@exemplo.com",
            "senha": "SenhaForte123!",
            "perfil": "Fundamentalista",
            "aceite_termos": "sim",
        },
    )
    assert resp.status_code == 422
    assert "Criar Conta Segura" in resp.text
    assert "nome deve ter entre 2 e 120" in resp.text.lower()

    # Usuário já gravado no banco com nome em branco não pode derrubar a home
    u = banco.Usuario(
        nome="   ",
        email="vazio@exemplo.com",
        senha_hash=auth.gerar_hash_senha("SenhaForte123!"),
        email_confirmado=True,
    )
    sessao_db.add(u)
    sessao_db.commit()
    sessao_db.refresh(u)

    token = auth.criar_token({"uid": u.id}, salt="sessao-usuario")
    client.cookies.set(auth.COOKIE_SESSAO, token)
    resp_home = client.get("/")
    assert resp_home.status_code == 200
    assert "Minha Watchlist (Investidor)" in resp_home.text


def test_1_3_erro_validacao_volta_para_formulario_certo(client):
    # 1. Senha com 101 caracteres no /cadastro -> auth.html modo cadastro com 422
    resp_cad = client.post(
        "/cadastro",
        data={
            "nome": "Maria Investidora",
            "email": "maria@exemplo.com",
            "senha": "a" * 101,
            "perfil": "Fundamentalista",
            "aceite_termos": "sim",
        },
    )
    assert resp_cad.status_code == 422
    assert "Criar Conta Segura" in resp_cad.text
    assert "A senha deve ter entre 8 e 100 caracteres." in resp_cad.text

    # 2. E-mail curto no /login -> auth.html modo login com 422
    resp_login = client.post("/login", data={"email": "a@b", "senha": "123"})
    assert resp_login.status_code == 422
    assert "Entrar na Plataforma" in resp_login.text

    # 3. E-mail curto no /esqueci-senha -> auth.html modo esqueci com 422
    resp_esq = client.post("/esqueci-senha", data={"email": "a"})
    assert resp_esq.status_code == 422
    assert "Recuperar Senha" in resp_esq.text

    # 4. Senha curta em /redefinir-senha/tok -> auth.html modo redefinir com 422
    resp_red = client.post("/redefinir-senha/token-teste", data={"nova_senha": "123"})
    assert resp_red.status_code == 422
    assert "Definir Nova Senha" in resp_red.text
    assert "A senha deve ter entre 8 e 100 caracteres." in resp_red.text

    # 5. Notas com mais de 500 caracteres em /watchlist/adicionar -> redirect 303 para /watchlist?erro=...
    resp_wl = client.post(
        "/watchlist/adicionar",
        data={"ticker": "PETR4", "preco_alvo": "35", "notas": "x" * 501},
        follow_redirects=False,
    )
    assert resp_wl.status_code == 303
    assert resp_wl.headers["location"].startswith("/watchlist?erro=")

    # 6. /analisar com 1 caractere -> home.html com 422
    resp_an = client.get("/analisar?ticker=A")
    assert resp_an.status_code == 422
    assert "Chega de planilhas confusas" in resp_an.text


def test_1_4_perfil_restrito(client):
    resp = client.post(
        "/cadastro",
        data={
            "nome": "João Investidor",
            "email": "joao@exemplo.com",
            "senha": "SenhaForte123!",
            "perfil": "DayTradeInvalido",
            "aceite_termos": "sim",
        },
    )
    assert resp.status_code == 422
    assert "Criar Conta Segura" in resp.text
    assert "perfil de investidor válido" in resp.text.lower()


def test_1_5_cadastro_duplicado_simultaneo_integrity_error(client, monkeypatch):
    original_get_db = main.obter_sessao_banco

    def _sessao_com_conflito():
        gen = original_get_db()
        db = next(gen)

        def _commit_falha():
            raise IntegrityError("INSERT INTO usuarios", {}, Exception("duplicate key value violates unique constraint"))

        monkeypatch.setattr(db, "commit", _commit_falha)
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.obter_sessao_banco] = _sessao_com_conflito
    try:
        resp = client.post(
            "/cadastro",
            data={
                "nome": "Concorrente",
                "email": "concorrente@exemplo.com",
                "senha": "SenhaForte123!",
                "perfil": "Dividendos",
                "aceite_termos": "sim",
            },
        )
        assert resp.status_code == 200
        assert "Este e-mail já está cadastrado" in resp.text
    finally:
        main.app.dependency_overrides.clear()


def test_1_6_variacao_nula_na_home(client, sessao_db):
    reg = banco.RegistroAnalise(
        ticker="PETR4",
        nome_empresa="Petrobras",
        tipo_ativo="Ação Corporativa",
        preco_atual=36.5,
        variacao_dia_pct=None,
        volume_financeiro="R$ 1,00 Bi",
        p_vp="1.2",
        p_l="4.5",
        dividend_yield="12,00%",
        dados_json="{}",
        modelo_ia="gemini-2.5-flash",
        relatorio_ia=" Relatório ",
    )
    sessao_db.add(reg)
    sessao_db.commit()
    # Força NULL direto no banco caso o default da coluna tenha preenchido 0.0 no INSERT
    from sqlalchemy import text
    sessao_db.execute(text("UPDATE analises_ativos SET variacao_dia_pct = NULL"))
    sessao_db.commit()

    resp = client.get("/")
    assert resp.status_code == 200
    assert "PETR4" in resp.text
    assert "+0,00%" in resp.text
