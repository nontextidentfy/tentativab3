"""
Script de conferência do banco de dados (estritamente somente leitura).
Uso: python scripts/checar_banco.py
"""
import json
import os
import sys

import sqlalchemy as sa

PASTA_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PASTA_PROJETO not in sys.path:
    sys.path.append(PASTA_PROJETO)

import banco  # noqa: E402


TABELAS_ALVO = (
    "usuarios",
    "monitoramento_usuarios",
    "ativos",
    "cotacoes",
    "analises_ia",
    "analises_ativos",
    "analises_ativos_legado",
)


def checar_banco(engine=None) -> dict:
    """Executa consultas de leitura no banco e retorna métricas estruturadas."""
    if engine is None:
        url_normalizada = banco.normalizar_url_banco(os.getenv("DATABASE_URL"))
        engine = banco.criar_engine_banco(url_normalizada)

    inspetor = sa.inspect(engine)
    tabelas_existentes = set(inspetor.get_table_names())

    resultado = {
        "revisao_alembic": "tabela ausente",
        "contagens": {},
        "cotacoes_formato_antigo": 0,
    }

    with engine.connect() as conn:
        if "alembic_version" in tabelas_existentes:
            versoes = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalars().all()
            resultado["revisao_alembic"] = ", ".join(versoes) if versoes else "vazio"

        for nome_tabela in TABELAS_ALVO:
            if nome_tabela in tabelas_existentes:
                total = conn.execute(sa.text(f"SELECT count(*) FROM {nome_tabela}")).scalar()
                resultado["contagens"][nome_tabela] = int(total or 0)
            else:
                resultado["contagens"][nome_tabela] = "não existe"

        if "cotacoes" in tabelas_existentes:
            linhas_dados = conn.execute(sa.text("SELECT dados FROM cotacoes")).fetchall()
            qtd_antigas = 0
            for (raw_dados,) in linhas_dados:
                d = raw_dados
                if isinstance(d, str):
                    try:
                        d = json.loads(d)
                    except Exception:
                        d = {}
                if not isinstance(d, dict) or not d.get("Metadados"):
                    qtd_antigas += 1
            resultado["cotacoes_formato_antigo"] = qtd_antigas
        else:
            resultado["cotacoes_formato_antigo"] = "não existe"

    return resultado


def main_cli():
    res = checar_banco()
    print("=== Relatório de Conferência do Banco de Dados ===")
    print(f"Revisão Alembic: {res['revisao_alembic']}")
    print("\nContagem por Tabela:")
    for tab, cnt in res["contagens"].items():
        print(f"  - {tab}: {cnt}")
    print(f"\nCotações no formato antigo (sem Metadados): {res['cotacoes_formato_antigo']}")


if __name__ == "__main__":
    main_cli()
