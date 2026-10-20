"""Testes da Fase 5: cooldown de 15 min e lock por ticker, paralelismo yfinance, 6 dias de histórico, volume parcial e zoneinfo."""
from concurrent.futures import ThreadPoolExecutor
import datetime
import os
import time
import zoneinfo
import pandas as pd
import pytest

import banco
import main
import servico_analise as svc
from tests.conftest import EXTRAIR_RAIO_X_REAL


def test_5_1_cooldown_15_minutos_em_forcar_atualizacao(client, sessao_db, monkeypatch):
    contador_coletas = {"n": 0}
    extrair_mock_padrao = svc.extrair_raio_x_b3

    def _extrair_contado(tk):
        contador_coletas["n"] += 1
        return extrair_mock_padrao(tk)

    monkeypatch.setattr(svc, "extrair_raio_x_b3", _extrair_contado)

    # 1ª chamada: coleta em tempo real
    r1 = client.get("/analisar?ticker=PETR4")
    assert r1.status_code == 200
    assert contador_coletas["n"] == 1
    assert "Tempo Real (B3)" in r1.text

    # 2ª chamada com atualizar=1 antes de 15 minutos: serve cache e avisa do cooldown
    r2 = client.get("/analisar?ticker=PETR4&atualizar=1")
    assert r2.status_code == 200
    assert contador_coletas["n"] == 1
    assert "menos de 15 minutos" in r2.text
    assert "Cooldown 15m" in r2.text

    # Envelhece a cotação para 16 minutos atrás -> atualizar=1 agora faz nova coleta
    cot = sessao_db.query(banco.Cotacao).filter_by(ticker="PETR4").one()
    cot.coletado_em = banco.agora_utc() - datetime.timedelta(minutes=16)
    sessao_db.commit()

    r3 = client.get("/analisar?ticker=PETR4&atualizar=1")
    assert r3.status_code == 200
    assert contador_coletas["n"] == 2
    assert "Tempo Real (B3)" in r3.text


def test_5_1_lock_por_ticker_evita_chamadas_concorrentes_ao_gemini(client, monkeypatch):
    chamadas_ia = {"n": 0}
    relatorio_mock_padrao = svc.gerar_relatorio_ia

    def _ia_lenta(dados):
        time.sleep(0.08)
        chamadas_ia["n"] += 1
        return relatorio_mock_padrao(dados)

    monkeypatch.setattr(svc, "gerar_relatorio_ia", _ia_lenta)

    def _requisitar():
        return client.get("/analisar?ticker=WEGE3").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_requisitar)
        f2 = pool.submit(_requisitar)
        assert f1.result() == 200
        assert f2.result() == 200

    # Graças ao lock por ticker + revalidação, apenas a primeira thread chama a IA
    assert chamadas_ia["n"] == 1


def test_5_2_5_3_historico_6_dias_calcula_variacao_do_quinto_pregao():
    parametros_chamados = {}

    class _Ativo6Dias:
        def history(self, period="6d"):
            parametros_chamados["period"] = period
            datas = pd.to_datetime(
                ["2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]
            )
            # Dia 0 (13/10) fecha em 100.0; Dia 1 (14/10) abre em 105.0 e fecha em 110.0.
            # Variação correta do Dia 1 sobre fechamento do Dia 0 = (110 / 100 - 1) * 100 = +10.0%
            return pd.DataFrame(
                {
                    "Open": [100.0, 105.0, 110.0, 110.0, 110.0, 110.0],
                    "Close": [100.0, 110.0, 110.0, 110.0, 110.0, 110.0],
                    "Volume": [1000, 1000, 1000, 1000, 1000, 1000],
                },
                index=datas,
            )

    pregoes = svc.extrair_fluxo_pregoes(_Ativo6Dias(), vol_medio_referencia=2000)
    assert parametros_chamados["period"] == "6d"
    assert len(pregoes) == 5
    # O último item da lista reversa é o dia 14/10/2026
    mais_antigo_exibido = pregoes[-1]
    assert mais_antigo_exibido["data"] == "14/10/2026"
    assert mais_antigo_exibido["variacao_pct"] == pytest.approx(10.0, abs=0.01)


def test_5_4_volume_parcial_durante_pregao(monkeypatch):
    monkeypatch.setattr(svc, "extrair_raio_x_b3", EXTRAIR_RAIO_X_REAL)

    class _TickerPregao:
        def __init__(self, _s):
            self.info = {
                "longName": "WEG S.A.",
                "currency": "BRL",
                "currentPrice": 50.0,
                "previousClose": 49.5,
                "volume": 500_000,
                "averageVolume": 1_000_000,
            }
            self.dividends = pd.Series(dtype=float)

        def history(self, period="6d"):
            return pd.DataFrame()

    monkeypatch.setattr(svc.yf, "Ticker", _TickerPregao)

    # Quarta-feira às 14:30 em Brasília (durante o pregão)
    dt_pregao = datetime.datetime(2026, 10, 21, 14, 30, tzinfo=svc.FUSO_BRASILIA)
    monkeypatch.setattr(svc, "agora_brasilia", lambda: dt_pregao)
    dados_pregao = svc.extrair_raio_x_b3("WEGE3")
    status_pregao = dados_pregao["Fluxo_e_Volume"]["Status_Fluxo"]
    assert "volume até agora" in status_pregao
    assert "Volume Normal" not in status_pregao

    # Quarta-feira às 19:00 em Brasília (após o pregão)
    dt_pos = datetime.datetime(2026, 10, 21, 19, 0, tzinfo=svc.FUSO_BRASILIA)
    monkeypatch.setattr(svc, "agora_brasilia", lambda: dt_pos)
    dados_pos = svc.extrair_raio_x_b3("WEGE3")
    status_pos = dados_pos["Fluxo_e_Volume"]["Status_Fluxo"]
    assert "Volume Normal" in status_pos


def test_5_5_5_6_5_7_zoneinfo_requirements_e_procfile():
    assert isinstance(svc.FUSO_BRASILIA, zoneinfo.ZoneInfo)
    assert svc.FUSO_BRASILIA.key == "America/Sao_Paulo"

    dt_utc = datetime.datetime(2026, 10, 20, 15, 30)  # 15:30 UTC -> 12:30 BRT
    assert main.filtro_horario_br(dt_utc) == "20/10/2026 às 12:30"

    with open(os.path.join(banco.PASTA_PROJETO, "requirements.txt"), "r", encoding="utf-8") as f:
        reqs = f.read()
    assert "gunicorn==" in reqs

    with open(os.path.join(banco.PASTA_PROJETO, "Procfile"), "r", encoding="utf-8") as f:
        procfile = f.read()
    assert "alembic upgrade head" in procfile
