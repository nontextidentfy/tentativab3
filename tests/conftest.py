"""Configuração global de testes (pytest): SQLite em memória e bloqueio de rede."""
import os

# Força SQLite em memória ANTES de qualquer import de banco/main
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

import pytest
from fastapi.testclient import TestClient

import banco
import main
import servico_analise as svc
from tests.fixtures.ativos import RELATORIO_PADRAO_MD, obter_fixture_ativo

EXTRAIR_RAIO_X_REAL = svc.extrair_raio_x_b3
GERAR_RELATORIO_IA_REAL = svc.gerar_relatorio_ia


@pytest.fixture(autouse=True)
def preparar_banco_e_mocks(monkeypatch):
    """Recria o esquema em SQLite em memória e substitui chamadas externas por fixtures."""
    banco.Base.metadata.drop_all(bind=banco.engine)
    banco.Base.metadata.create_all(bind=banco.engine)
    svc._CACHE_BUSCA.clear()
    if hasattr(svc, "_CACHE_SGS_BCB"):
        svc._CACHE_SGS_BCB.clear()

    # Limpa o estado do rate limiter entre testes
    if hasattr(main.limiter, "_storage") and hasattr(main.limiter._storage, "reset"):
        main.limiter._storage.reset()

    class _BuscaFalsaYahoo:
        def __init__(self, *_args, **_kwargs):
            self.quotes = []

    def _extrair_falso(ticker_usuario: str) -> dict:
        ticker = svc.resolver_ticker(ticker_usuario)
        if ticker == "ERRO99":
            raise svc.ErroAtivo(f"O ticker {ticker} não foi encontrado na B3 ou não possui cotação ativa.")
        return obter_fixture_ativo(ticker)

    def _relatorio_falso(_dados: dict):
        return RELATORIO_PADRAO_MD, "gemini-2.5-flash", True

    def _sgs_falso(codigo_serie: int, data_inicial: str = "", data_final: str = "", timeout: float = 5.0):
        # Retorna 60 meses sintéticos para evitar chamadas HTTP externas nos testes
        dados = []
        for i in range(60):
            m = (i % 12) + 1
            a = 2021 + (i // 12)
            if codigo_serie == 12:
                # 21 dias úteis no mês com taxa diária ~0.04%
                for d in range(1, 22):
                    dados.append({"data": f"{d:02d}/{m:02d}/{a}", "valor": "0.039"})
            else:
                dados.append({"data": f"01/{m:02d}/{a}", "valor": "0.55"})
        return dados

    class _TickerPadraoMock:
        def __init__(self, simbolo):
            self.simbolo = str(simbolo or "")
            tk = self.simbolo.replace(".SA", "")
            self.info = {
                "shortName": f"Empresa {tk}",
                "longName": f"Empresa {tk} S.A.",
                "currentPrice": 28.50,
                "trailingPE": 7.8,
                "priceToBook": 1.25,
                "dividendYield": 0.065,
                "returnOnEquity": 0.16,
                "profitMargins": 0.14,
                "totalDebt": 2_000_000_000.0,
                "totalCash": 500_000_000.0,
                "ebitda": 1_000_000_000.0,
            }

    monkeypatch.setattr(svc.yf, "Search", _BuscaFalsaYahoo)
    monkeypatch.setattr(svc.yf, "Ticker", _TickerPadraoMock)
    monkeypatch.setattr(svc, "extrair_raio_x_b3", _extrair_falso)
    monkeypatch.setattr(svc, "gerar_relatorio_ia", _relatorio_falso)
    monkeypatch.setattr(svc, "_consultar_sgs_bcb_http", _sgs_falso, raising=False)
    yield


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def sessao_db():
    with banco.SessionLocal() as db:
        yield db
