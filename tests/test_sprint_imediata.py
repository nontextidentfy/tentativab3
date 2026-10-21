"""
Testes automatizados da Sprint Imediata (Segurança & Performance Base).
Segue a metodologia TDD estrita (AGENTS.md R7).
"""
import pytest
from starlette.testclient import TestClient
import banco
import seguranca
import main
from main import app, renderizar_markdown, obter_ip_cliente


def test_t01_gzip_middleware_ativo(client: TestClient):
    """Garante que respostas volumosas (>1KB) recebem compressão GZip quando o cliente suporta."""
    headers = {"Accept-Encoding": "gzip"}
    resp = client.get("/", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"


def test_t02_cabecalhos_seguranca_http(client: TestClient):
    """Garante a presença dos cabeçalhos essenciais contra Clickjacking e MIME Sniffing."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_t03_renderizar_markdown_sanitiza_links_javascript():
    """Garante que links maliciosos gerados por IA com esquema javascript: não passam."""
    md_malicioso = "[Clique aqui](javascript:alert(document.cookie))\n\n[Link Seguro](https://b3.com.br)"
    html = renderizar_markdown(md_malicioso)
    assert "javascript:" not in html.lower()
    assert 'href="#"' in html or 'href=""' in html or 'rel="noopener noreferrer"' in html
    assert "https://b3.com.br" in html


def test_t04_obter_ip_cliente_com_e_sem_confiar_proxy(monkeypatch):
    """Garante que com CONFIAR_PROXY=1 o IP do cliente é extraído de X-Forwarded-For e fallback para client.host."""
    monkeypatch.setenv("CONFIAR_PROXY", "1")
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [
            (b"x-forwarded-for", b"203.0.113.50, 10.0.0.1"),
        ],
        "client": ("127.0.0.1", 12345),
    }
    req = Request(scope)
    ip = obter_ip_cliente(req)
    assert ip == "203.0.113.50"

    monkeypatch.delenv("CONFIAR_PROXY", raising=False)
    assert obter_ip_cliente(req) == "127.0.0.1"


def test_t05_secret_key_fail_closed_em_producao(monkeypatch):
    """Garante que em produção a aplicação não inicia com SECRET_KEY vazia ou padrão."""
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        seguranca.validar_configuracao_seguranca(em_producao=True, secret_key="")


def test_t06_token_recuperacao_invalida_apos_uso(client: TestClient, sessao_db):
    """Garante que o token de recuperação de senha não pode ser reutilizado após a alteração da senha."""
    # 1. Cria usuário
    u = banco.Usuario(
        nome="Usuario Reset Teste",
        email="reset_seguro@teste.com",
        senha_hash=seguranca.gerar_hash_senha("SenhaAntiga123!"),
        email_confirmado=1,
    )
    sessao_db.add(u)
    sessao_db.commit()

    # 2. Gera token com carimbo de senha atual
    token = seguranca.criar_token(
        {"uid": u.id, "email": u.email, "pwd_stamp": seguranca.obter_carimbo_senha(u.senha_hash)},
        salt="recuperar-senha"
    )

    # 3. Primeiro uso: redefinição bem-sucedida
    r1 = client.post(
        f"/redefinir-senha/{token}",
        data={"nova_senha": "NovaSenhaSegura456!"},
        follow_redirects=False
    )
    assert r1.status_code == 303
    assert "/login" in r1.headers.get("location", "")

    # 4. Segundo uso com o MESMO token: deve ser rejeitado como expirado/já utilizado
    r2 = client.post(
        f"/redefinir-senha/{token}",
        data={"nova_senha": "TentativaInvasao789!"},
        follow_redirects=False
    )
    # Deve retornar a página com mensagem de erro, sem redirecionar para login
    assert r2.status_code == 200
    assert "já foi utilizado" in r2.text or "expirado" in r2.text or "inválido" in r2.text
