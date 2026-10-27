"""Revisão de front-end (base.html e home.html): avisos por URL, rankings honestos, logout por POST e acessibilidade."""
import os

import banco
import main
import seguranca as auth

PASTA_TEMPLATES = os.path.join(banco.PASTA_PROJETO, "templates")


def _ler(nome: str) -> str:
    with open(os.path.join(PASTA_TEMPLATES, nome), encoding="utf-8") as f:
        return f.read()


def _logar(client, sessao_db, nome="Maria Investidora", email="maria@exemplo.com"):
    u = banco.Usuario(
        nome=nome,
        email=email,
        senha_hash=auth.gerar_hash_senha("SenhaForte123!"),
        email_confirmado=True,
    )
    sessao_db.add(u)
    sessao_db.commit()
    sessao_db.refresh(u)
    client.cookies.set(auth.COOKIE_SESSAO, auth.criar_token({"uid": u.id}, salt="sessao-usuario"))
    return u


def test_home_ignora_texto_livre_no_parametro_msg(client):
    golpe = "Sua conta foi bloqueada. Ligue para 0800"
    html = client.get("/", params={"msg": golpe}).text
    assert golpe not in html


def test_login_e_watchlist_ignoram_texto_livre(client, sessao_db):
    golpe = "Deposite R$ 100 para liberar"
    assert golpe not in client.get("/login", params={"msg": golpe}).text
    _logar(client, sessao_db)
    html = client.get("/watchlist", params={"msg": golpe, "erro": golpe}).text
    assert golpe not in html


def test_aviso_assinado_pelo_servidor_aparece(client):
    url = main._url_aviso("/", "Mensagem legítima do servidor.")
    html = client.get(url).text
    assert "Mensagem legítima do servidor." in html


def test_logout_exige_post_e_mostra_aviso(client, sessao_db):
    _logar(client, sessao_db)
    assert client.get("/logout", follow_redirects=False).status_code == 405
    resp = client.post("/logout", follow_redirects=False)
    assert resp.status_code == 303
    html = client.get(resp.headers["location"]).text
    assert "Sessão encerrada com segurança." in html


def test_nav_logada_tem_form_post_de_logout_e_nome_vazio_nao_quebra(client, sessao_db):
    _logar(client, sessao_db, nome="   ", email="vazio@exemplo.com")
    html = client.get("/").text
    assert '<form method="post" action="/logout"' in html
    assert 'href="/logout"' not in html
    assert "Meus ativos" in html


def test_variacao_sem_dado_nao_vira_zero_verde():
    from types import SimpleNamespace
    linha = SimpleNamespace(
        ticker="TEST3", nome_empresa="Teste SA", tipo_ativo="ACAO", moeda_cotacao="BRL",
        preco_atual=10.0, variacao_dia_pct=None, volume_financeiro=None, p_l=None, p_vp=None,
        dy_12m_pct=None, coletado_em=None,
    )
    html = main.templates.get_template("home.html").render(painel_ativos=[linha], usuario=None)
    assert "TEST3" in html
    assert "+0,00%" not in html


def test_rankings_deixam_claro_que_sao_ativos_consultados(client):
    html = client.get("/").text
    assert "Destaques do pregão" not in html
    assert "consultados" in html


def test_mensagens_anunciadas_para_leitor_de_tela():
    base = _ler("base.html")
    assert 'class="erro" role="alert"' in base
    assert 'class="sucesso" role="status"' in base
    assert 'id="loading" class="loading" role="status"' in base


def test_mobile_sem_rolagem_lateral_e_movimento_reduzido():
    home = _ler("home.html")
    base = _ler("base.html")
    assert "minmax(300px, 1fr)" not in home
    assert "prefers-reduced-motion" in base


def test_botao_volta_com_texto_original():
    base = _ler("base.html")
    assert "botao.textContent = 'Consultar'" not in base
    assert "dataset.texto" in base
