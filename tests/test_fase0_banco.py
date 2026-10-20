import pytest
import banco


def test_normalizar_url_banco():
    assert banco.normalizar_url_banco("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert banco.normalizar_url_banco("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert banco.normalizar_url_banco("sqlite+pysqlite:///:memory:") == "sqlite+pysqlite:///:memory:"


def test_criar_engine_rejeita_url_invalida():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        banco.criar_engine_banco("")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        banco.criar_engine_banco("mysql://user:pass@localhost/db")


def test_engine_teste_eh_sqlite_em_memoria():
    assert banco.engine.dialect.name == "sqlite"
    # sincronizar_sequencias_postgres não deve falhar em SQLite
    banco.sincronizar_sequencias_postgres()
