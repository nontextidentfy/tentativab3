"""
Versão de terminal do Investidor IA.
Toda a lógica fica em servico_analise.py; aqui é entrada e exibição no console.
Uso: python analisador.py
"""
import sys
from servico_analise import (
    MODELO_GEMINI,
    ErroAtivo,
    br,
    extrair_raio_x_b3,
    formatar_para_leitura,
    gerar_relatorio_ia,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def exibir_painel_terminal(dados: dict):
    """Imprime o raio-x numérico, fluxo de volume e balanço no terminal."""
    fmt = formatar_para_leitura(dados)
    ide = fmt["Identificacao"]
    cot = fmt["Cotacao_e_Mercado"]
    fluxo = fmt["Fluxo_e_Volume"]
    val = fmt["Valuation"]
    rent = fmt["Rentabilidade_e_Eficiencia"]
    end = fmt["Endividamento_e_Caixa"]
    prov = fmt["Proventos"]

    print("\n" + "=" * 80)
    print(f" 📊 RAIO-X FUNDAMENTALISTA: {ide['Ticker']} - {ide['Nome_Empresa']}")
    print(f" 🏢 Tipo: {ide['Tipo_Ativo']} | Setor: {ide['Setor']} ({ide['Industria']})")
    print("=" * 80)

    print("\n💰 COTAÇÃO E FLUXO DE VOLUME:")
    print(f"  • Preço Atual        : {cot['Preco_Atual']} ({cot['Variacao_Dia']} no dia)")
    print(f"  • Banda 52 Semanas   : Mín {cot['Minima_52_Semanas']} | Máx {cot['Maxima_52_Semanas']}")
    print(f"  • Volume Financeiro  : {fluxo['Volume_Financeiro_Dia']} ({fluxo['Volume_Acoes_Dia']} papéis)")
    print(f"  • Média Volume (3m)  : {fluxo['Volume_Medio_3m']} | Status: {fluxo['Status_Fluxo']}")
    print(f"  • Valor de Mercado   : {cot['Valor_de_Mercado']} | EV: {cot['Enterprise_Value_EV']}")

    if fluxo.get("Pregoes_Recentes"):
        print("\n📅 ÚLTIMOS 5 PREGÕES:")
        for p in fluxo["Pregoes_Recentes"]:
            sinal = "+" if p["variacao_pct"] >= 0 else ""
            print(f"  • {p['data']} | Fech: {p['fechamento']} ({sinal}{br(p['variacao_pct'])}%) | Vol: {p['volume_financeiro']} -> {p['fluxo']}")

    print("\n📐 MÚLTIPLOS DE VALUATION:")
    print(f"  • P/L: {val['P_L']} (Projetado: {val['P_L_Projetado']}) | P/VP: {val['P_VP']} | EV/EBITDA: {val['EV_EBITDA']}")
    print(f"  • LPA: {val['LPA']} | VPA: {val['VPA']}")
    print(f"  • Preço Justo Graham : {val['Valor_Intrinseco_Graham']} | Preço Teto Bazin (6%): {val['Preco_Teto_Bazin_6pct']}")

    print("\n⚙️ EFICIÊNCIA E RENTABILIDADE:")
    print(f"  • ROE: {rent['ROE']} | ROA: {rent['ROA']}")
    print(f"  • Margem Bruta: {rent['Margem_Bruta']} | Margem EBITDA: {rent['Margem_EBITDA']} | Margem Líquida: {rent['Margem_Liquida']}")
    print(f"  • Crescimento Receita (YoY): {rent['Crescimento_Receita_YoY']} | Crescimento Lucro: {rent['Crescimento_Lucros_YoY']}")

    print("\n🏦 DOCUMENTAÇÃO DE BALANÇO E ENDIVIDAMENTO:")
    print(f"  • Receita 12m: {end['Receita_Total_12m']} | EBITDA: {end['EBITDA']} | Fluxo Caixa Livre: {end['Fluxo_Caixa_Livre']}")
    print(f"  • Caixa: {end['Caixa_Total']} | Dívida Bruta: {end['Divida_Total']} | Dívida Líquida: {end['Divida_Liquida']}")
    print(f"  • Alavancagem (Dív. Líq/EBITDA): {end['Divida_Liquida_sobre_EBITDA']} | Liquidez Corrente: {end['Liquidez_Corrente']}")

    print("\n💵 DIVIDENDOS E PROVENTOS (12m):")
    print(f"  • Dividend Yield (12m): {prov['Dividend_Yield_Real_12m']} ({prov['Total_Pago_12m_por_Cota']} por ação/cota)")
    print(f"  • Histórico Recente   : {prov['Frequencia_Pagamentos_12m']} | Payout: {prov['Payout_Ratio']}")


def main():
    print("=" * 80)
    print(" TERMINAL DINÂMICO DE ANÁLISE B3 + FLUXO DE VOLUME".center(80))
    print(f" Motor configurado: {MODELO_GEMINI}".center(80))
    print("=" * 80)

    while True:
        print("\nDigite o código de QUALQUER ativo da B3 (ex: VALE3, BBAS3, TAEE11, HGLG11)")
        entrada_ticker = input("👉 Ticker (ou 'sair' para encerrar): ").strip()
        if entrada_ticker.lower() in ("sair", "exit", "q"):
            print("Encerrando o analisador. Até logo!")
            break
        if not entrada_ticker:
            continue

        try:
            print(f"\n⏳ Extraindo balanço, fluxo de volume e cotações de {entrada_ticker.upper()} na B3...")
            dados = extrair_raio_x_b3(entrada_ticker)
            exibir_painel_terminal(dados)
        except ErroAtivo as erro:
            print(f"\n❌ {erro}")
            continue
        except Exception as erro:
            print(f"\n❌ Erro inesperado ao processar '{entrada_ticker}': {erro}")
            continue

        print("\n🤖 Gerando diagnóstico fundamentalista...")
        print("-" * 80)
        relatorio, motor, _veio_da_ia = gerar_relatorio_ia(dados)
        print(f"[Fonte do Parecer: {motor}]\n")
        print(relatorio)
        print("=" * 80)


if __name__ == "__main__":
    main()
