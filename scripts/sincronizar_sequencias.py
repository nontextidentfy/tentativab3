"""
Script manual para sincronizar sequências SERIAL do PostgreSQL.
Uso: python scripts/sincronizar_sequencias.py
"""
import os
import sys

PASTA_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PASTA_PROJETO)

from banco import sincronizar_sequencias_postgres  # noqa: E402


if __name__ == "__main__":
    sincronizar_sequencias_postgres()
    print("Sequências do PostgreSQL sincronizadas com sucesso.")
