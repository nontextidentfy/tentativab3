"""Testes da Fase 3: busca com prioridade e desambiguação, caminhos absolutos, mensagens SMTP, erros Gemini e textos CVM/LGPD."""
import os
from types import SimpleNamespace
import pandas as pd
import pytest
from google.genai import errors as genai_errors

import banco
import main
import seguranca as auth
import servico_analise as svc
from tests.conftest import EXTRAIR_RAIO_X_REAL, GERAR_RELATORIO_IA_REAL
from tests.fixtures.ativos import FIXTURE_ACAO_PETR4


def test_3_1_busca_com_prioridade_e_desambiguacao(client):
    # 1. "PETR4" -> grupo 1 (ticker exato)
    res_petr4 = svc.buscar_ativos("PETR4")
    assert res_petr4[0]["ticker"] == "PETR4"
    assert svc.resolver_ticker("PETR4") == "PETR4"

    # 2. "petr" -> grupo 2 (prefixo de ticker: PETR4, PETR3 vêm antes de PetroReconcavo RECV3 que é grupo 3)
    res_petr = svc.buscar_ativos("petr")
    assert [r["ticker"] for r in res_petr[:2]] == ["PETR4", "PETR3"]
    assert svc.resolver_ticker("petr") == "PETR4"

    # 3. "sanepar" -> grupo 3 com múltiplos candidatos -> levanta ErroAtivo com "Você quis dizer: ..."
    res_sanepar = svc.buscar_ativos("sanepar")
    assert [r["ticker"] for r in res_sanepar] == ["SAPR4", "SAPR11", "SAPR3"]
    with pytest.raises(svc.ErroAtivo) as exc_sanepar:
        svc.resolver_ticker("sanepar")
    assert "Você quis dizer: SAPR4 Sanepar PN, SAPR11 Sanepar Unit, SAPR3 Sanepar ON" in str(exc_sanepar.value)

    # Verifica que /analisar?ticker=sanepar devolve a home com a lista de candidatos
    resp_sanepar = client.get("/analisar?ticker=sanepar")
    assert resp_sanepar.status_code == 200
    assert "Você quis dizer: SAPR4 Sanepar PN, SAPR11 Sanepar Unit, SAPR3 Sanepar ON" in resp_sanepar.text

    # 4. "itau" -> grupo 3 com múltiplos candidatos (ITUB4, ITUB3, ITSA4) -> desambiguação
    res_itau = svc.buscar_ativos("itau")
    assert [r["ticker"] for r in res_itau] == ["ITUB4", "ITUB3", "ITSA4"]
    with pytest.raises(svc.ErroAtivo) as exc_itau:
        svc.resolver_ticker("itau")
    assert "Você quis dizer: ITUB4 Itaú Unibanco PN, ITUB3 Itaú Unibanco ON, ITSA4 Itaúsa PN" in str(exc_itau.value)

    # 5. "bb" -> grupo 2 (tickers que começam com BB: BBAS3, BBDC4, BBDC3, BBSE3)
    res_bb = svc.buscar_ativos("bb")
    assert [r["ticker"] for r in res_bb[:4]] == ["BBAS3", "BBDC4", "BBDC3", "BBSE3"]
    assert svc.resolver_ticker("bb") == "BBAS3"

    # 6. "pe" -> grupo 2 (PETR4, PETR3) vem antes de grupo 3 (PetroReconcavo) e grupo 4 (Sanepar, CPEL, etc.)
    res_pe = svc.buscar_ativos("pe")
    assert [r["ticker"] for r in res_pe[:2]] == ["PETR4", "PETR3"]
    assert svc.resolver_ticker("pe") == "PETR4"

    # 7. "xyz123" -> inexistente -> lista vazia na busca e ErroAtivo no resolver_ticker
    assert svc.buscar_ativos("xyz123") == []
    with pytest.raises(svc.ErroAtivo) as exc_xyz:
        svc.resolver_ticker("xyz123")
    assert "Não encontramos nenhum ativo" in str(exc_xyz.value)


def test_3_2_caminhos_absolutos_fora_da_pasta_do_projeto(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert os.path.isabs(banco.PASTA_PROJETO)
    assert os.path.isabs(svc.PASTA_PROJETO)
    assert os.path.isabs(auth.PASTA_PROJETO)
    assert os.path.isabs(main.PASTA_PROJETO)

    resp = client.get("/")
    assert resp.status_code == 200
    resp_termos = client.get("/termos")
    assert resp_termos.status_code == 200


def test_3_3_mensagens_desenvolvimento_com_e_sem_smtp(client, monkeypatch):
    # Sem SMTP configurado -> menciona terminal
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    assert auth.email_configurado() is False

    r_esq_local = client.post("/esqueci-senha", data={"email": "ninguem@exemplo.com"})
    assert r_esq_local.status_code == 200
    assert "confira o terminal" in r_esq_local.text.lower()

    # Com SMTP configurado -> texto definitivo sem mencionar "confira o terminal"
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.com")
    monkeypatch.setenv("SMTP_USER", "no-reply@exemplo.com")
    monkeypatch.setenv("SMTP_PASS", "segredo123")
    monkeypatch.setattr(auth, "enviar_email_transacional", lambda *_a, **_kw: None)
    assert auth.email_configurado() is True

    r_esq_prod = client.post("/esqueci-senha", data={"email": "ninguem@exemplo.com"})
    assert r_esq_prod.status_code == 200
    assert "confira o terminal" not in r_esq_prod.text.lower()
    assert "enviamos um link para o seu e-mail" in r_esq_prod.text.lower()

    r_cad_prod = client.post(
        "/cadastro",
        data={
            "nome": "Usuária Produção",
            "email": "prod@exemplo.com",
            "senha": "SenhaForte123!",
            "perfil": "Fundamentalista",
            "aceite_termos": "sim",
        },
        follow_redirects=True,
    )
    assert r_cad_prod.status_code == 200
    assert "terminal de teste" not in r_cad_prod.text.lower()
    assert "confira o terminal" not in r_cad_prod.text.lower()
    assert "enviamos um link para o seu e-mail" in r_cad_prod.text.lower()

    r_reenvio_prod = client.post("/conta/reenviar-confirmacao", follow_redirects=True)
    assert r_reenvio_prod.status_code == 200
    assert "confira o terminal" not in r_reenvio_prod.text.lower()
    assert "enviamos um link para o seu e-mail" in r_reenvio_prod.text.lower()


def test_3_4_tratamento_erro_gemini_por_codigo_http(monkeypatch):
    monkeypatch.setattr(svc, "gerar_relatorio_ia", GERAR_RELATORIO_IA_REAL)

    # 1. Simula erro 429 (cota) via APIError do google-genai
    class _ModelsQuota429:
        def generate_content(self, **_kwargs):
            raise genai_errors.APIError(429, {"error": {"message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})

    monkeypatch.setattr(svc, "client", SimpleNamespace(models=_ModelsQuota429()))
    texto_429, motor_429, veio_ia_429 = svc.gerar_relatorio_ia(FIXTURE_ACAO_PETR4)
    assert veio_ia_429 is False
    assert "Limite de cota da API atingido" in motor_429
    assert "### 1. Valuation" in texto_429

    # 2. Simula erro 404 (modelo inexistente) via APIError do google-genai
    class _ModelsNotFound404:
        def generate_content(self, **_kwargs):
            raise genai_errors.APIError(404, {"error": {"message": "Model not found", "status": "NOT_FOUND"}})

    monkeypatch.setattr(svc, "client", SimpleNamespace(models=_ModelsNotFound404()))
    texto_404, motor_404, veio_ia_404 = svc.gerar_relatorio_ia(FIXTURE_ACAO_PETR4)
    assert veio_ia_404 is False
    assert "inválido no .env" in motor_404
    assert "### 1. Valuation" in texto_404


def test_3_5_3_6_3_7_textos_regulatorios_fluxo_e_lgpd(client, monkeypatch):
    # 3.5 e 3.6 na Home
    resp_home = client.get("/")
    assert resp_home.status_code == 200
    html_home = resp_home.text
    assert "auditor" not in html_home.lower()
    assert "auditoria" not in html_home.lower()
    assert "clique para auditar" not in html_home.lower()
    assert "pressão institucional" not in html_home.lower()
    assert "Leitura Automática de Balanços" in html_home
    assert "Clique para analisar" in html_home
    assert "faixa de 52 semanas e beta" in html_home

    # 3.6 Rótulos de fluxo em extrair_fluxo_pregoes
    class _AtivoHist:
        def history(self, period="5d"):
            datas = pd.to_datetime(["2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19"])
            return pd.DataFrame(
                {
                    "Open": [10.0, 10.0, 11.0, 10.5],
                    "Close": [10.0, 11.0, 10.5, 10.51],
                    "Volume": [1000, 2500, 3000, 800],
                },
                index=datas,
            )

    pregoes = svc.extrair_fluxo_pregoes(_AtivoHist(), vol_medio_referencia=2000)
    rotulos = [p["fluxo"] for p in pregoes]
    assert "Dia de alta com volume acima da média" in rotulos
    assert "Dia de queda com volume acima da média" in rotulos
    assert "Estável" in rotulos

    # 3.7 Política de privacidade (legal.html)
    resp_legal = client.get("/termos")
    assert resp_legal.status_code == 200
    html_legal = resp_legal.text
    assert "perfil de investidor" in html_legal
    assert "aceite dos termos" in html_legal
    assert "preço-alvo" in html_legal
    assert "anotações" in html_legal
    assert "endereço IP" in html_legal
    assert "90 dias" in html_legal
    assert "[E-MAIL DE CONTATO]" in html_legal


def test_3_8_busca_marcas_cotidiano_e_cooperativas(client):
    # 1. Resolução de marcas populares do cotidiano
    assert svc.resolver_ticker("Magalu") == "MGLU3"
    assert svc.resolver_ticker("Posto Ipiranga") == "UGPA3"
    assert svc.resolver_ticker("Ipiranga") == "UGPA3"
    assert svc.resolver_ticker("Banco do Brasil") == "BBAS3"
    assert svc.resolver_ticker("Ambev") == "ABEV3"
    assert svc.resolver_ticker("Casas Bahia") == "BHIA3"
    assert svc.resolver_ticker("Atacadão") == "CRFB3"
    assert svc.resolver_ticker("Drogasil") == "RADL3"
    assert svc.resolver_ticker("Nubank") == "ROXO34"

    # 2. Autocomplete com marcas
    res_magalu = svc.buscar_ativos("Magalu")
    assert any(r["ticker"] == "MGLU3" for r in res_magalu)

    res_ipiranga = svc.buscar_ativos("Posto Ipiranga")
    assert any(r["ticker"] == "UGPA3" for r in res_ipiranga)

    # 3. Interceptação didática de Cooperativas (Sicredi, Sicoob, Cresol) e Caixa
    with pytest.raises(svc.EntidadeNaoListada) as exc_sicredi:
        svc.resolver_ticker("Sicredi")
    assert "Cooperativa de Crédito" in str(exc_sicredi.value)

    res_sicredi = svc.buscar_ativos("Sicredi")
    assert any("Sicredi" in r["ticker"] for r in res_sicredi)
    assert any(r.get("tipo") == "cooperativa" for r in res_sicredi)

    # 4. Acesso HTTP /analisar com Sicredi deve devolver HTTP 200 e alternativas
    resp_sicredi = client.get("/analisar?ticker=Sicredi")
    assert resp_sicredi.status_code == 200
    assert "Cooperativa de Crédito" in resp_sicredi.text
    assert "BBAS3" in resp_sicredi.text
    assert "ITUB4" in resp_sicredi.text
    assert "BBDC4" in resp_sicredi.text
