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


@pytest.fixture(autouse=True)
def preparar_banco_e_mocks(monkeypatch):
    """Recria o esquema em SQLite em memória e substitui chamadas externas por fixtures."""
    banco.Base.metadata.drop_all(bind=banco.engine)
    banco.Base.metadata.create_all(bind=banco.engine)
    svc._CACHE_BUSCA.clear()

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
        return RELATORIO_PADRAO_MD, "gemini-2.5-flash"

    monkeypatch.setattr(svc.yf, "Search", _BuscaFalsaYahoo)
    monkeypatch.setattr(svc, "extrair_raio_x_b3", _extrair_falso)
    monkeypatch.setattr(svc, "gerar_relatorio_ia", _relatorio_falso)
    yield


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def sessao_db():
    with banco.SessionLocal() as db:
        yield db
