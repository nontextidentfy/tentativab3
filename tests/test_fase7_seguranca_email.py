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
