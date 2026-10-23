"""Melhorias de interface, legal e checagem cruzada de indicadores (favicon, meta description, 404, travessões, LGPD)."""
import os
import re

import banco
import main
import seguranca as auth
import servico_analise as svc

PASTA_TEMPLATES = os.path.join(banco.PASTA_PROJETO, "templates")


def test_home_tem_favicon_meta_description_e_skip_link(client):
    html = client.get("/").text
    assert '<link rel="icon" href="data:image/svg+xml' in html
    assert re.search(r'<meta name="description" content="[^"]{40,}"', html)
    assert 'href="#conteudo"' in html and 'id="conteudo"' in html


def test_pagina_404_personalizada(client):
    resp = client.get("/pagina-que-nao-existe")
    assert resp.status_code == 404
    assert "Essa página não existe" in resp.text
    assert 'href="/"' in resp.text


def test_erros_http_que_nao_sao_404_continuam_json(client):
    # /watchlist/remover exige POST: GET deve devolver 405 em JSON, como antes
    resp = client.get("/watchlist/remover/1")
    assert resp.status_code == 405
    assert resp.headers["content-type"].startswith("application/json")


def test_templates_e_textos_sem_travessoes():
    for nome in os.listdir(PASTA_TEMPLATES):
        with open(os.path.join(PASTA_TEMPLATES, nome), encoding="utf-8") as f:
            assert "—" not in f.read(), f"travessão em {nome}"
    assert "—" not in main.renderizar_markdown("Lucro alto — mas dívida alta — cuidado")


def test_relatorio_markdown_remove_travessoes():
    html = main.renderizar_markdown("Lucro alto — mas dívida alta")
    assert "—" not in html and "Lucro alto, mas dívida alta" in html


def test_termos_tem_cookies_reembolso_e_dados_da_empresa(client):
    html = client.get("/termos").text
    for trecho in ('id="cookies"', "inv_ia_session", 'id="reembolso"', "art. 49", 'id="empresa"', "CNPJ", "art. 18"):
        assert trecho in html, trecho


def test_formularios_com_label_associada(client):
    html = client.get("/cadastro").text
    for campo in ("nome", "email", "senha", "perfil"):
        assert f'for="campo-{campo}"' in html and f'id="campo-{campo}"' in html, campo


def test_botoes_azuis_com_contraste_minimo_wcag():
    from tests.test_visual_consistency import _ler_template, _extrair_variaveis_root, _parse_cor, _razao_contraste

    vars_root = _extrair_variaveis_root(_ler_template("base.html"))
    r, g, b, _ = _parse_cor(vars_root["--botao"])
    assert _razao_contraste((255.0, 255.0, 255.0), (r, g, b)) >= 4.5


# --- Checagem cruzada de indicadores ---------------------------------------------------------

def _dados(**kw):
    base = {
        "Valuation": {"P_L": kw.get("pl"), "P_L_Projetado": kw.get("pl_proj")},
        "Rentabilidade_e_Eficiencia": {"ROE": kw.get("roe"), "ROA": kw.get("roa"), "Margem_Liquida": kw.get("margem", 0.10),
                                       "Crescimento_Lucros_YoY": kw.get("cresc_lucro"), "Crescimento_Receita_YoY": kw.get("cresc_receita")},
        "Endividamento_e_Caixa": {"Receita_Total_12m": kw.get("receita", 1_000.0), "Fluxo_Caixa_Livre": kw.get("fcl"),
                                  "Fluxo_Caixa_Operacional": kw.get("fco"), "Divida_Liquida_sobre_EBITDA": kw.get("alav")},
        "Proventos": {"Dividend_Yield_Real_12m": kw.get("dy"), "Payout_Ratio": kw.get("payout")},
    }
    if kw.get("fii"):
        base["Metadados"] = {"tipo": "FII"}
    return base


def _por_gatilho(checks, prefixo):
    achados = [c for c in checks if c["gatilho"].startswith(prefixo)]
    assert len(achados) == 1, checks
    return achados[0]


def test_sem_gatilho_nao_gera_checagem():
    assert svc.checar_coerencia_indicadores(_dados(pl=12.0, roe=0.10, dy=0.03)) == []
    assert svc.checar_coerencia_indicadores({}) == []


def test_fii_nao_recebe_checagens():
    assert svc.checar_coerencia_indicadores(_dados(pl=5.0, dy=0.12, fii=True)) == []


def test_pl_baixo_checa_lucro_recorrente():
    alerta = _por_gatilho(svc.checar_coerencia_indicadores(_dados(pl=4.0, pl_proj=9.0)), "P/L baixo")
    assert alerta["status"] == "alerta" and "projetado" in alerta["leitura"]
    ok = _por_gatilho(svc.checar_coerencia_indicadores(_dados(pl=4.0, pl_proj=4.5, fcl=90.0)), "P/L baixo")
    assert ok["status"] == "ok"
    sem = _por_gatilho(svc.checar_coerencia_indicadores(_dados(pl=4.0)), "P/L baixo")
    assert sem["status"] == "sem_dado"


def test_roe_alto_checa_roa_e_alavancagem():
    c = _por_gatilho(svc.checar_coerencia_indicadores(_dados(roe=0.30, roa=0.05, alav=4.0)), "ROE alto")
    assert c["status"] == "alerta" and "ROA" in c["leitura"] and "Alavancagem" in c["leitura"]
    c_ok = _por_gatilho(svc.checar_coerencia_indicadores(_dados(roe=0.25, roa=0.15, alav=1.0)), "ROE alto")
    assert c_ok["status"] == "ok"


def test_lucro_crescendo_checa_caixa_operacional():
    c = _por_gatilho(svc.checar_coerencia_indicadores(_dados(cresc_lucro=0.20, fco=-10.0)), "Lucro crescendo")
    assert c["status"] == "alerta"
    c_sem = _por_gatilho(svc.checar_coerencia_indicadores(_dados(cresc_lucro=0.12)), "Lucro crescendo")
    assert c_sem["status"] == "sem_dado"


def test_yield_alto_checa_payout_e_fcl():
    c = _por_gatilho(svc.checar_coerencia_indicadores(_dados(dy=0.10, payout=1.3, fcl=-5.0)), "Yield alto")
    assert c["status"] == "alerta" and "Payout" in c["leitura"] and "caixa livre" in c["leitura"]


def test_crescimento_forte_checa_divida_ebitda():
    c = _por_gatilho(svc.checar_coerencia_indicadores(_dados(cresc_receita=0.30, alav=4.2)), "Crescimento forte")
    assert c["status"] == "alerta"
    c_ok = _por_gatilho(svc.checar_coerencia_indicadores(_dados(cresc_receita=0.30, alav=1.2)), "Crescimento forte")
    assert c_ok["status"] == "ok"


def test_checagens_aparecem_no_dossie_para_usuario_logado(client, sessao_db):
    u = banco.Usuario(nome="Analista", email="analista@exemplo.com", senha_hash=auth.gerar_hash_senha("SenhaForte123!"), email_confirmado=True)
    sessao_db.add(u)
    sessao_db.commit()
    sessao_db.refresh(u)
    client.cookies.set(auth.COOKIE_SESSAO, auth.criar_token({"uid": u.id}, salt="sessao-usuario"))
    html = client.get("/analisar?ticker=PETR4").text
    assert "Checagem Cruzada" in html
