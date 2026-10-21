"""Testes da Fase 2: serviço com números crus, filtros Jinja, migração Alembic, consultas JOIN sem N+1 e retenção."""
import datetime
import json
import os
import pandas as pd
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import banco
import main
import seguranca as auth
import servico_analise as svc
from tests.conftest import EXTRAIR_RAIO_X_REAL


def test_2_a_extrair_raio_x_b3_devolve_numeros_crus(monkeypatch):
    """Verifica que extrair_raio_x_b3 devolve números (float/int/None) e metadados sem formatação textual."""
    monkeypatch.setattr(svc, "extrair_raio_x_b3", EXTRAIR_RAIO_X_REAL)

    class _TickerFalso:
        def __init__(self, simbolo):
            self.simbolo = simbolo
            self.info = {
                "longName": "Sanepar S.A.",
                "currency": "BRL",
                "financialCurrency": "BRL",
                "sector": "Utilities",
                "industry": "Water Utilities",
                "website": "https://ri.sanepar.com.br",
                "currentPrice": 30.0,
                "previousClose": 25.0,
                "open": 26.0,
                "dayLow": 25.5,
                "dayHigh": 30.5,
                "volume": 2_000_000,
                "averageDailyVolume10Day": 1_500_000,
                "averageVolume": 1_000_000,
                "trailingPE": 6.0,
                "forwardPE": 5.5,
                "priceToBook": 0.9,
                "enterpriseToEbitda": 4.2,
                "trailingEps": 5.0,
                "bookValue": 20.0,
                "totalDebt": 5_000_000_000.0,
                "totalCash": 1_000_000_000.0,
                "ebitda": 2_000_000_000.0,
                "fiftyTwoWeekLow": 20.0,
                "fiftyTwoWeekHigh": 40.0,
                "returnOnEquity": 0.18,
                "profitMargins": 0.22,
                "marketCap": 9_000_000_000.0,
                "payoutRatio": 0.35,
            }
            idx = pd.to_datetime(["2026-06-01", "2026-09-01"])
            self.dividends = pd.Series([1.20, 1.80], index=idx)

        def history(self, period="5d"):
            datas = pd.to_datetime(["2026-10-19", "2026-10-20"])
            return pd.DataFrame(
                {
                    "Open": [24.0, 26.0],
                    "Close": [25.0, 30.0],
                    "Volume": [1_000_000, 2_000_000],
                },
                index=datas,
            )

    monkeypatch.setattr(svc.yf, "Ticker", _TickerFalso)
    dados = svc.extrair_raio_x_b3("SAPR4")

    # Graham = sqrt(22.5 * 5.0 * 20.0) = sqrt(2250) = 47.43
    assert isinstance(dados["Valuation"]["Valor_Intrinseco_Graham"], float)
    assert dados["Valuation"]["Valor_Intrinseco_Graham"] == pytest.approx(47.43, abs=0.01)

    # Bazin = 3.00 / 0.06 = 50.0
    assert isinstance(dados["Valuation"]["Preco_Teto_Bazin_6pct"], float)
    assert dados["Valuation"]["Preco_Teto_Bazin_6pct"] == pytest.approx(50.0, abs=0.01)

    # DY = 3.00 / 30.0 = 0.10 (10%)
    assert dados["Proventos"]["Dividend_Yield_Real_12m"] == pytest.approx(0.10, abs=1e-4)
    assert dados["Proventos"]["Dividend_Yield_12m_Pct"] == pytest.approx(10.0, abs=0.01)

    # Posição 52 semanas: (30 - 20) / (40 - 20) = 50%
    assert dados["Cotacao_e_Mercado"]["Posicao_52_Semanas"] == pytest.approx(50.0, abs=0.1)

    # Alavancagem: (5B - 1B) / 2B = 2.0
    assert dados["Endividamento_e_Caixa"]["Divida_Liquida"] == pytest.approx(4_000_000_000.0)
    assert dados["Endividamento_e_Caixa"]["Divida_Liquida_sobre_EBITDA"] == pytest.approx(2.0, abs=0.01)

    # Variação do dia: (30 / 25 - 1) * 100 = 20.0
    assert dados["Cotacao_e_Mercado"]["Variacao_Dia"] == pytest.approx(20.0, abs=0.01)

    # Metadados presentes
    assert dados["Metadados"]["moeda_cotacao"] == "BRL"
    assert dados["Metadados"]["simbolo"] == "R$"
    assert dados["Metadados"]["tipo"] == "Acao"

    # formatar_para_leitura gera textos para IA/terminal sem mutar o dicionário original
    fmt = svc.formatar_para_leitura(dados)
    assert fmt["Cotacao_e_Mercado"]["Preco_Atual"] == "R$ 30,00"
    assert dados["Cotacao_e_Mercado"]["Preco_Atual"] == 30.0


def test_2_a_filtros_jinja():
    assert main.filtro_moeda_br(None) == "N/D"
    assert main.filtro_numero_br(None) == "N/D"
    assert main.filtro_percentual_br(None) == "N/D"
    assert main.filtro_moeda_grande_br(None) == "N/D"
    assert main.filtro_inteiro_br(None) == "N/D"
    assert main.filtro_horario_br(None) == "N/D"

    assert main.filtro_moeda_br(36.5) == "R$ 36,50"
    assert main.filtro_moeda_br(36.5, "USD") == "US$ 36,50"
    assert main.filtro_numero_br(1234.567, 2) == "1.234,57"
    assert main.filtro_percentual_br(0.085) == "8,50%"
    assert main.filtro_moeda_grande_br(1.2e9) == "R$ 1,20 Bi"
    assert main.filtro_inteiro_br(35_000_000) == "35.000.000"


def test_2_c_migracao_alembic_sobe_e_desce_com_dados_exemplo(tmp_path):
    """Testa upgrade e downgrade da migração no SQLite com 3 linhas de analises_ativos e duplicatas na watchlist."""
    caminho_db = tmp_path / "teste_migracao.sqlite"
    url_sqlite = f"sqlite:///{caminho_db.as_posix()}"

    alembic_cfg = Config(os.path.join(banco.PASTA_PROJETO, "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(banco.PASTA_PROJETO, "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", url_sqlite)

    # 1. Sobe até a revisão anterior à reestruturação (c0d49a3075c6)
    command.upgrade(alembic_cfg, "c0d49a3075c6")

    eng = sa.create_engine(url_sqlite)
    with eng.begin() as conn:
        # Insere usuário e duplicatas em monitoramento_usuarios
        conn.execute(
            sa.text(
                "INSERT INTO usuarios (id, nome, email, senha_hash, email_confirmado) "
                "VALUES (1, 'Ana', 'ana@exemplo.com', 'hash', 1)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO monitoramento_usuarios (id, usuario_id, ticker, preco_alvo_alerta, notas_documentacao, data_adicao) "
                "VALUES (1, 1, 'PETR4', 32.0, 'Antigo', '2026-10-01 10:00:00'), "
                "       (2, 1, 'PETR4', 35.0, 'Mais recente', '2026-10-15 10:00:00')"
            )
        )

        # 3 linhas em analises_ativos:
        # Linha 1: completa (IA)
        dados_1 = {
            "Identificacao": {"Ticker": "PETR4", "Nome_Empresa": "Petrobras PN", "Setor": "Energy"},
            "Valuation": {"P_L": 4.5, "P_VP": 1.2, "LPA": 8.0, "VPA": 30.0},
            "Numeros": {
                "preco_atual": 36.5,
                "variacao_dia_pct": 1.25,
                "vol_financeiro_dia": 1_200_000_000.0,
                "dy_12m": 13.5,
                "graham": 73.48,
                "bazin": 80.0,
                "tipo": "Acao",
            },
        }
        # Linha 2: valores "N/D"
        dados_2 = {
            "Identificacao": {"Ticker": "HGLG11", "Nome_Empresa": "CSHG Logística FII"},
            "Valuation": {"P_L": "N/D", "P_VP": 1.02, "Valor_Intrinseco_Graham": "N/D"},
            "Rentabilidade_e_Eficiencia": {"ROE": "N/D"},
            "Numeros": {
                "preco_atual": 160.0,
                "variacao_dia_pct": -0.2,
                "vol_financeiro_dia": 8_000_000.0,
                "dy_12m": 8.25,
                "graham": None,
                "bazin": 220.0,
                "tipo": "FII",
            },
        }
        # Linha 3: variação NULL e modelo algorítmico
        dados_3 = {
            "Identificacao": {"Ticker": "VALE3", "Nome_Empresa": "Vale ON"},
            "Valuation": {"P_L": 6.1, "P_VP": 1.4},
            "Numeros": {
                "preco_atual": 61.0,
                "variacao_dia_pct": None,
                "vol_financeiro_dia": 900_000_000.0,
                "dy_12m": 9.0,
                "graham": 75.0,
                "bazin": 90.0,
                "tipo": "Acao",
            },
        }

        for idx, (tk, dj, mod, var_col) in enumerate(
            [
                ("PETR4", dados_1, "gemini-2.5-flash", 1.25),
                ("HGLG11", dados_2, "gemini-2.5-flash", -0.20),
                ("VALE3", dados_3, "Motor Algorítmico Local", None),
            ],
            start=1,
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO analises_ativos "
                    "(id, ticker, nome_empresa, tipo_ativo, preco_atual, variacao_dia_pct, dados_json, modelo_ia, relatorio_ia, data_dados, data_criacao) "
                    "VALUES (:id, :tk, :nome, 'Acao', 10.0, :var, :dj, :mod, 'Relatorio MD', '2026-10-18 12:00:00', '2026-10-18 12:00:00')"
                ),
                {
                    "id": idx,
                    "tk": tk,
                    "nome": dj["Identificacao"]["Nome_Empresa"],
                    "var": var_col,
                    "dj": json.dumps(dj),
                    "mod": mod,
                },
            )

    # 2. Executa upgrade até a revisão da fase 2 (a1b2c3d4e5f6)
    command.upgrade(alembic_cfg, "a1b2c3d4e5f6")

    with eng.connect() as conn:
        insp = sa.inspect(conn)
        tabelas = set(insp.get_table_names())
        assert {"ativos", "cotacoes", "analises_ia", "analises_ativos_legado"}.issubset(tabelas)
        assert "analises_ativos" not in tabelas

        ativos_rows = conn.execute(sa.text("SELECT ticker, ultima_cotacao_id FROM ativos")).mappings().all()
        assert len(ativos_rows) == 3
        assert all(r["ultima_cotacao_id"] is not None for r in ativos_rows)

        cot_hglg = conn.execute(
            sa.text("SELECT p_l, p_vp, graham, bazin FROM cotacoes WHERE ticker = 'HGLG11'")
        ).mappings().one()
        assert cot_hglg["p_l"] is None
        assert cot_hglg["graham"] is None
        assert cot_hglg["p_vp"] == pytest.approx(1.02)

        cot_vale = conn.execute(
            sa.text("SELECT variacao_dia_pct, preco FROM cotacoes WHERE ticker = 'VALE3'")
        ).mappings().one()
        assert cot_vale["variacao_dia_pct"] == 0.0
        assert cot_vale["preco"] == pytest.approx(61.0)

        ia_rows = conn.execute(
            sa.text("SELECT ticker, veio_da_ia FROM analises_ia ORDER BY id")
        ).mappings().all()
        mapa_ia = {r["ticker"]: bool(r["veio_da_ia"]) for r in ia_rows}
        assert mapa_ia == {"PETR4": True, "HGLG11": True, "VALE3": False}

        # Watchlist deduplicada mantendo o registro mais recente (preco_alvo_alerta = 35.0)
        mon_rows = conn.execute(
            sa.text("SELECT preco_alvo_alerta, notas_documentacao FROM monitoramento_usuarios")
        ).mappings().all()
        assert len(mon_rows) == 1
        assert mon_rows[0]["preco_alvo_alerta"] == pytest.approx(35.0)
        assert mon_rows[0]["notas_documentacao"] == "Mais recente"

    # 2b. Sobe até head (b2c3d4e5f6a7) e valida presença de "plano" nas colunas de usuarios
    command.upgrade(alembic_cfg, "head")
    with eng.connect() as conn:
        insp_head = sa.inspect(conn)
        colunas_usuarios = {c["name"] for c in insp_head.get_columns("usuarios")}
        assert {"id", "nome", "email", "senha_hash", "email_confirmado", "perfil_investidor", "plano", "data_cadastro"}.issubset(
            colunas_usuarios
        )
    command.downgrade(alembic_cfg, "a1b2c3d4e5f6")

    # 3. Executa downgrade -1 e verifica restauração de analises_ativos
    command.downgrade(alembic_cfg, "-1")
    with eng.connect() as conn:
        insp = sa.inspect(conn)
        tabelas_down = set(insp.get_table_names())
        assert "analises_ativos" in tabelas_down
        assert "ativos" not in tabelas_down
        assert "cotacoes" not in tabelas_down
        assert "analises_ia" not in tabelas_down
        qtd_legado = conn.execute(sa.text("SELECT COUNT(*) FROM analises_ativos")).scalar_one()
        assert qtd_legado == 3

    # 4. Executa downgrade também de c0d49a3075c6 para confirmar que o drop_constraint corrigido funciona
    command.downgrade(alembic_cfg, "6c6b96237d69")
    eng.dispose()


def test_2_e_home_com_12_ativos_faz_uma_unica_consulta(client, sessao_db):
    tickers = [
        "PETR4", "VALE3", "ITUB4", "BBAS3", "WEGE3", "SAPR4",
        "TAEE11", "HGLG11", "MXRF11", "XPML11", "B3SA3", "EGIE3",
    ]
    base_dt = banco.agora_utc()
    for i, tk in enumerate(tickers):
        dt = base_dt - datetime.timedelta(minutes=i)
        ativo = banco.Ativo(ticker=tk, nome=f"Empresa {tk}", tipo="Acao", moeda_cotacao="BRL", atualizado_em=dt)
        sessao_db.add(ativo)
        sessao_db.flush()
        cot = banco.Cotacao(
            ticker=tk,
            coletado_em=dt,
            preco=20.0 + i,
            variacao_dia_pct=1.0,
            volume_financeiro=50_000_000.0,
            p_l=8.0,
            p_vp=1.2,
            dy_12m_pct=7.5,
            dados={"Identificacao": {"Ticker": tk}},
        )
        sessao_db.add(cot)
        sessao_db.flush()
        ativo.ultima_cotacao_id = cot.id
    sessao_db.commit()

    comandos_select: list[str] = []

    def _contar_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
        sql = statement.strip().upper()
        if sql.startswith("SELECT"):
            comandos_select.append(statement)

    sa.event.listen(banco.engine, "before_cursor_execute", _contar_sql)
    try:
        resp = client.get("/")
    finally:
        sa.event.remove(banco.engine, "before_cursor_execute", _contar_sql)

    assert resp.status_code == 200
    assert len(comandos_select) == 1
    assert "Atualizações automáticas da B3" not in resp.text
    for tk in tickers:
        assert tk in resp.text


def test_2_e_watchlist_sem_n_mais_1_exige_email_confirmado_e_bloqueia_duplicata(client, sessao_db):
    u = banco.Usuario(
        nome="Carlos Investidor",
        email="carlos@exemplo.com",
        senha_hash=auth.gerar_hash_senha("SenhaForte123!"),
        email_confirmado=False,
    )
    sessao_db.add(u)
    sessao_db.commit()
    sessao_db.refresh(u)

    token = auth.criar_token({"uid": u.id}, salt="sessao-usuario")
    client.cookies.set(auth.COOKIE_SESSAO, token)

    # 1. Tentativa sem e-mail confirmado deve ser bloqueada
    r_nao_conf = client.post(
        "/watchlist/adicionar",
        data={"ticker": "PETR4", "preco_alvo": "35", "notas": "Nota"},
        follow_redirects=True,
    )
    assert r_nao_conf.status_code == 200
    assert "Confirme seu endereço de e-mail" in r_nao_conf.text
    assert sessao_db.query(banco.MonitoramentoUsuario).count() == 0

    # 2. Confirma o e-mail e adiciona ativo
    u.email_confirmado = True
    sessao_db.commit()

    r_ok = client.post(
        "/watchlist/adicionar",
        data={"ticker": "PETR4", "preco_alvo": "35", "notas": "Primeira vez"},
        follow_redirects=True,
    )
    assert r_ok.status_code == 200
    assert "adicionado" in r_ok.text

    # 3. Duplicata na Watchlist é bloqueada pela UniqueConstraint com mensagem amigável
    r_dup = client.post(
        "/watchlist/adicionar",
        data={"ticker": "PETR4", "preco_alvo": "34", "notas": "Duplicado"},
        follow_redirects=True,
    )
    assert r_dup.status_code == 200
    assert "já está cadastrado na sua Watchlist" in r_dup.text

    # 4. Verifica ausência de N+1 ao listar 5 itens na Watchlist
    for tk in ("VALE3", "ITUB4", "WEGE3", "BBAS3"):
        ativo = banco.Ativo(ticker=tk, nome=tk, tipo="Acao", moeda_cotacao="BRL")
        sessao_db.add(ativo)
        sessao_db.flush()
        cot = banco.Cotacao(ticker=tk, preco=40.0, variacao_dia_pct=0.5, dados={})
        sessao_db.add(cot)
        sessao_db.flush()
        ativo.ultima_cotacao_id = cot.id
        sessao_db.add(banco.MonitoramentoUsuario(usuario_id=u.id, ticker=tk, preco_alvo_alerta=45.0))
    sessao_db.commit()

    selects_watchlist: list[str] = []

    def _contar_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.strip().upper().startswith("SELECT"):
            selects_watchlist.append(statement)

    sa.event.listen(banco.engine, "before_cursor_execute", _contar_selects)
    try:
        resp_wl = client.get("/watchlist")
    finally:
        sa.event.remove(banco.engine, "before_cursor_execute", _contar_selects)

    assert resp_wl.status_code == 200
    # 1 SELECT para carregar o usuário da sessão + 1 SELECT com OUTER JOIN para todos os itens e cotações
    assert len(selects_watchlist) == 2
    assert "às" in resp_wl.text  # Data/hora da cotação exibida ao lado do preço


def test_2_e_limpar_historico_preserva_ultima_cotacao(sessao_db):
    antiga_120d = banco.agora_utc() - datetime.timedelta(days=120)
    antiga_100d = banco.agora_utc() - datetime.timedelta(days=100)
    recente_10d = banco.agora_utc() - datetime.timedelta(days=10)

    # Ativo 1: tem cotação antiga (120d) e uma recente (10d, que é a ultima_cotacao)
    a1 = banco.Ativo(ticker="PETR4", nome="Petrobras", tipo="Acao")
    # Ativo 2: sua única cotação é antiga (100d), mas é a ultima_cotacao_id e deve ser preservada!
    a2 = banco.Ativo(ticker="VALE3", nome="Vale", tipo="Acao")
    sessao_db.add_all([a1, a2])
    sessao_db.flush()

    c1_antiga = banco.Cotacao(ticker="PETR4", coletado_em=antiga_120d, preco=30.0, variacao_dia_pct=0.0, dados={})
    c1_recente = banco.Cotacao(ticker="PETR4", coletado_em=recente_10d, preco=36.5, variacao_dia_pct=1.0, dados={})
    c2_unica_antiga = banco.Cotacao(ticker="VALE3", coletado_em=antiga_100d, preco=60.0, variacao_dia_pct=0.0, dados={})
    sessao_db.add_all([c1_antiga, c1_recente, c2_unica_antiga])
    sessao_db.flush()

    a1.ultima_cotacao_id = c1_recente.id
    a2.ultima_cotacao_id = c2_unica_antiga.id

    sessao_db.add_all(
        [
            banco.AnaliseIA(
                ticker="PETR4",
                cotacao_id=c1_antiga.id,
                modelo="gemini-2.5-flash",
                veio_da_ia=True,
                relatorio_md="Antigo",
                criado_em=antiga_120d,
            ),
            banco.AnaliseIA(
                ticker="VALE3",
                cotacao_id=c2_unica_antiga.id,
                modelo="gemini-2.5-flash",
                veio_da_ia=True,
                relatorio_md="Preservado",
                criado_em=antiga_100d,
            ),
        ]
    )
    sessao_db.commit()

    resumo = banco.limpar_historico(dias=90, db=sessao_db)
    assert resumo["cotacoes_removidas"] == 1
    assert resumo["analises_removidas"] == 1

    ids_restantes = {c.id for c in sessao_db.query(banco.Cotacao).all()}
    assert ids_restantes == {c1_recente.id, c2_unica_antiga.id}


def test_2_d_analisar_grava_cotacao_e_so_grava_analise_ia_quando_veio_da_ia(client, sessao_db, monkeypatch):
    # 1. Quando veio_da_ia=True, grava Ativo, Cotacao e AnaliseIA, e serve do cache na segunda chamada
    r1 = client.get("/analisar?ticker=PETR4")
    assert r1.status_code == 200
    assert "Tempo Real (B3)" in r1.text
    assert sessao_db.query(banco.Ativo).filter_by(ticker="PETR4").count() == 1
    assert sessao_db.query(banco.Cotacao).filter_by(ticker="PETR4").count() == 1
    assert sessao_db.query(banco.AnaliseIA).filter_by(ticker="PETR4").count() == 1

    r2 = client.get("/analisar?ticker=PETR4")
    assert r2.status_code == 200
    assert "Banco de Dados (Cache 24h)" in r2.text

    # 2. Quando veio_da_ia=False (fallback algorítmico), grava Ativo e Cotacao mas NÃO grava AnaliseIA
    monkeypatch.setattr(
        svc,
        "gerar_relatorio_ia",
        lambda _d: ("### Fallback", "Motor Algorítmico Local", False),
    )
    r3 = client.get("/analisar?ticker=VALE3")
    assert r3.status_code == 200
    assert sessao_db.query(banco.Ativo).filter_by(ticker="VALE3").count() == 1
    assert sessao_db.query(banco.Cotacao).filter_by(ticker="VALE3").count() == 1
    assert sessao_db.query(banco.AnaliseIA).filter_by(ticker="VALE3").count() == 0

