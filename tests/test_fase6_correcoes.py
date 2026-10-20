"""
Testes da Fase 6: Correções pós-conferência.
Cobre os itens 3.1, 3.2 e 3.3 do prompt de correções.
"""
import copy
import datetime
import threading
import time
import os
import pandas as pd
import pytest

import banco
import main
import servico_analise as svc
from tests.conftest import EXTRAIR_RAIO_X_REAL


def _criar_dados_antigos_petr4():
    return {
        "Identificacao": {
            "Ticker": "PETR4",
            "Nome_Empresa": "Petróleo Brasileiro S.A. - Petrobras",
            "Tipo_Ativo": "Ação",
            "Setor": "Petróleo e Gás",
            "Industria": "Exploração e Refino",
            "Site_Oficial": "https://petrobras.com.br",
            "Moeda_do_Balanco": "BRL",
            "Moeda_Cotacao": "BRL",
            "Simbolo_Moeda": "R$",
            "Data_Consulta": "20/10/2026",
            "Resumo_Negocio": "Petróleo e Gás",
        },
        "Cotacao_e_Mercado": {
            "Preco_Atual": "R$ 36,50",
            "Variacao_Dia": "+1,25%",
            "Abertura_Dia": "R$ 36,10",
            "Fechamento_Anterior": "R$ 36,05",
            "Minima_52_Semanas": "R$ 31,00",
            "Maxima_52_Semanas": "R$ 42,00",
            "Posicao_52_Semanas": "50,0%",
            "Valor_de_Mercado": "R$ 470,00 Bi",
            "Enterprise_Value_EV": "R$ 610,00 Bi",
            # Sem Minima_Dia e Maxima_Dia
        },
        "Fluxo_e_Volume": {
            "Volume_Financeiro_Dia": "R$ 225,00 Bi",
            "Status_Fluxo": "Volume Normal",
            "Volume_Acoes_Dia": "35 Mi",
            "Volume_Medio_10d": "32 Mi",
            "Volume_Medio_3m": "30 Mi",
        },
        "Valuation": {
            "P_L": "4,50",
            "P_VP": "0,62x",
            "Valor_Intrinseco_Graham": "R$ 45,00",
            "Preco_Teto_Bazin_6pct": "R$ 40,00",
        },
        "Rentabilidade_e_Eficiencia": {
            "ROE": "26,50%",
            "Margem_Liquida": "18,20%",
        },
        "Endividamento_e_Caixa": {
            "Divida_Liquida_EBITDA": "0,62x",
            "Disponibilidades_Caixa": "R$ 15,00 Bi",
        },
        "Proventos": {
            "Dividend_Yield_Real_12m": "26,50%",
            "Total_Pago_12m_por_Cota": "R$ 9,50",
        },
        "Pregoes_Recentes": [],
        "Numeros": {
            "preco_atual": 36.50,
            "variacao_dia_pct": 1.25,
        },
        # Sem a chave Metadados
    }


def test_6_1_cache_ignora_registro_migrado_no_formato_antigo(client, sessao_db):
    """
    Item 3.1: O cache de 24 horas deve ignorar registros migrados no formato antigo
    (sem chave 'Metadados') e realizar nova coleta, evitando Erro 500 em resultado.html.
    """
    agora = banco.agora_utc()
    dados_antigos = _criar_dados_antigos_petr4()

    ativo = banco.Ativo(
        ticker="PETR4",
        nome="Petróleo Brasileiro S.A.",
        tipo="Acao",
        moeda_cotacao="BRL",
        atualizado_em=agora,
    )
    sessao_db.add(ativo)
    sessao_db.flush()

    cot_antiga = banco.Cotacao(
        ticker="PETR4",
        coletado_em=agora,
        preco=36.5,
        variacao_dia_pct=1.25,
        dados=dados_antigos,
    )
    sessao_db.add(cot_antiga)
    sessao_db.flush()
    ativo.ultima_cotacao_id = cot_antiga.id
    sessao_db.commit()

    # 2. GET /analisar?ticker=PETR4 deve devolver 200 com "Tempo Real (B3)" (coleta nova)
    resp1 = client.get("/analisar?ticker=PETR4")
    assert resp1.status_code == 200, f"Falhou com status {resp1.status_code}: {resp1.text[:300]}"
    assert "Tempo Real (B3)" in resp1.text

    # 3. ultima_cotacao_id deve apontar para uma nova cotação com Metadados e tabela ter 2 linhas
    sessao_db.expire_all()
    ativo_atualizado = sessao_db.query(banco.Ativo).filter_by(ticker="PETR4").one()
    assert ativo_atualizado.ultima_cotacao_id != cot_antiga.id
    nova_cotacao = sessao_db.query(banco.Cotacao).filter_by(id=ativo_atualizado.ultima_cotacao_id).one()
    assert isinstance(nova_cotacao.dados, dict) and "Metadados" in nova_cotacao.dados
    total_cotacoes = sessao_db.query(banco.Cotacao).filter_by(ticker="PETR4").count()
    assert total_cotacoes == 2

    # 4. Segundo GET deve devolver 200 com "Cache 24h"
    resp2 = client.get("/analisar?ticker=PETR4")
    assert resp2.status_code == 200
    assert "Cache 24h" in resp2.text

    # 5. Repetir com atualizar=1 e cotação antiga recente (2 minutos atrás):
    # Deve fazer nova coleta sem mensagem de cooldown
    sessao_db.query(banco.Cotacao).delete()
    sessao_db.query(banco.Ativo).delete()
    sessao_db.commit()

    ativo2 = banco.Ativo(
        ticker="PETR4",
        nome="Petróleo Brasileiro S.A.",
        tipo="Acao",
        moeda_cotacao="BRL",
        atualizado_em=agora,
    )
    sessao_db.add(ativo2)
    sessao_db.flush()
    cot_2min = banco.Cotacao(
        ticker="PETR4",
        coletado_em=agora - datetime.timedelta(minutes=2),
        preco=36.5,
        variacao_dia_pct=1.25,
        dados=dados_antigos,
    )
    sessao_db.add(cot_2min)
    sessao_db.flush()
    ativo2.ultima_cotacao_id = cot_2min.id
    sessao_db.commit()

    resp3 = client.get("/analisar?ticker=PETR4&atualizar=1")
    assert resp3.status_code == 200
    assert "Tempo Real (B3)" in resp3.text
    assert "já foram atualizados há menos de" not in resp3.text


def test_6_2_yfinance_paralelo_nao_compartilha_objeto_ticker(monkeypatch):
    """
    Item 3.2: extrair_raio_x_b3 não deve compartilhar o mesmo objeto yf.Ticker
    entre threads concorrentes (dividends e history).
    """
    monkeypatch.setattr(svc, "extrair_raio_x_b3", EXTRAIR_RAIO_X_REAL)

    instancias_criadas = []

    class _TickerConcorrenteDetector:
        def __init__(self, s):
            self.s = s
            self.lock = threading.Lock()
            self.concorrentes = 0
            self.max_concorrentes = 0
            self.metodos_usados = []
            self.info = {
                "longName": "WEG S.A.",
                "currency": "BRL",
                "currentPrice": 50.0,
                "previousClose": 49.5,
                "volume": 500_000,
                "averageVolume": 1_000_000,
            }
            instancias_criadas.append(self)

        @property
        def dividends(self):
            with self.lock:
                self.concorrentes += 1
                if self.concorrentes > self.max_concorrentes:
                    self.max_concorrentes = self.concorrentes
                self.metodos_usados.append("dividends")
            time.sleep(0.05)
            try:
                return pd.Series(dtype=float)
            finally:
                with self.lock:
                    self.concorrentes -= 1

        def history(self, period="6d"):
            with self.lock:
                self.concorrentes += 1
                if self.concorrentes > self.max_concorrentes:
                    self.max_concorrentes = self.concorrentes
                self.metodos_usados.append("history")
            time.sleep(0.05)
            try:
                return pd.DataFrame()
            finally:
                with self.lock:
                    self.concorrentes -= 1

    monkeypatch.setattr(svc.yf, "Ticker", _TickerConcorrenteDetector)

    svc.extrair_raio_x_b3("WEGE3")

    # Se apenas 1 instância foi compartilhada para dividends e history,
    # max_concorrentes será 2. Com instâncias separadas por thread,
    # max_concorrentes em cada instância nunca ultrapassa 1.
    assert len(instancias_criadas) >= 2, (
        f"Esperava múltiplas instâncias isoladas de yf.Ticker, mas apenas {len(instancias_criadas)} foi criada."
    )
    for idx, inst in enumerate(instancias_criadas):
        assert inst.max_concorrentes <= 1, (
            f"Instância {idx} sofreu concorrência simultânea entre métodos ({inst.max_concorrentes} threads simultâneas: {inst.metodos_usados})."
        )


def test_6_3_checar_banco_somente_leitura(sessao_db):
    """
    Item 3.3: Script checar_banco.py deve ser estritamente somente leitura
    (sem comandos de mutação DDL/DML) e retornar métricas consistentes em SQLite e PostgreSQL.
    """
    from scripts.checar_banco import checar_banco, PASTA_PROJETO

    # 1. Validação estática de ausência de comandos de escrita no código do script
    caminho_script = os.path.join(PASTA_PROJETO, "scripts", "checar_banco.py")
    with open(caminho_script, "r", encoding="utf-8") as f:
        codigo = f.read()

    palavras_proibidas = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "op."]
    codigo_upper = codigo.upper()
    for termo in palavras_proibidas:
        if termo == "op.":
            assert "op." not in codigo, f"Expressão {termo} encontrada em checar_banco.py"
        else:
            assert termo not in codigo_upper, f"Palavra proibida {termo} encontrada em checar_banco.py"

    # 2. Inserir dados de teste: um ativo com cotação moderna e outro com cotação antiga
    agora = banco.agora_utc()
    a1 = banco.Ativo(ticker="PETR4", nome="Petrobras", tipo="Acao", moeda_cotacao="BRL", atualizado_em=agora)
    a2 = banco.Ativo(ticker="VALE3", nome="Vale", tipo="Acao", moeda_cotacao="BRL", atualizado_em=agora)
    sessao_db.add_all([a1, a2])
    sessao_db.flush()

    c_antiga = banco.Cotacao(
        ticker="PETR4",
        coletado_em=agora,
        preco=36.5,
        variacao_dia_pct=1.0,
        dados={"Identificacao": {"Ticker": "PETR4"}},  # Sem Metadados
    )
    c_nova = banco.Cotacao(
        ticker="VALE3",
        coletado_em=agora,
        preco=60.0,
        variacao_dia_pct=0.5,
        dados={"Metadados": {"moeda_cotacao": "BRL"}},  # Com Metadados
    )
    sessao_db.add_all([c_antiga, c_nova])
    sessao_db.commit()

    # 3. Executar checar_banco contra o SQLite da suíte
    resultado = checar_banco(engine=banco.engine)
    assert isinstance(resultado, dict)
    assert resultado["contagens"]["ativos"] == 2
    assert resultado["contagens"]["cotacoes"] == 2
    assert resultado["cotacoes_formato_antigo"] == 1
    assert resultado["contagens"]["analises_ativos_legado"] == "não existe"

