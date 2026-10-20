"""
Script manual para limpar cotações e análises antigas (> 90 dias),
preservando sempre a última cotação de cada ativo.
Uso: python scripts/limpar_historico.py [dias]
"""
import os
import sys

PASTA_PROJETO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PASTA_PROJETO)

from banco import limpar_historico  # noqa: E402


if __name__ == "__main__":
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    res = limpar_historico(dias=dias)
    print(
        f"Limpeza concluída (>{dias} dias): "
        f"{res['cotacoes_removidas']} cotações e {res['analises_removidas']} análises removidas."
    )
