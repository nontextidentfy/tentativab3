"""Dados fixos de ativos da B3 (uma ação e um FII) para testes sem acesso à rede."""
import copy

RELATORIO_PADRAO_MD = (
    "### 1. Valuation: descontado ou esticado?\n"
    "Ativo negociando com múltiplos moderados frente ao histórico.\n\n"
    "### 2. Fluxo de Volume e Dinâmica de Mercado\n"
    "Liquidez diária consistente nos últimos pregões.\n\n"
    "### 3. Qualidade Operacional e Documentação de Balanço\n"
    "Margens operacionais e geração de caixa positivas.\n\n"
    "### 4. Proventos e Principais Riscos Setoriais\n"
    "Histórico regular de distribuição. Este diagnóstico é informativo e não constitui recomendação de compra ou venda."
)

FIXTURE_ACAO_PETR4 = {
    "Identificacao": {
        "Ticker": "PETR4",
        "Nome_Empresa": "Petróleo Brasileiro S.A. - Petrobras",
        "Tipo_Ativo": "Ação Corporativa",
        "Setor": "Energy",
        "Industria": "Oil & Gas Integrated",
        "Site_Oficial": "https://petrobras.com.br",
        "Moeda_do_Balanco": "BRL",
        "Data_Consulta": "20/10/2026",
        "Resumo_Negocio": "Empresa integrada de energia com foco em exploração e produção de petróleo e gás.",
    },
    "Cotacao_e_Mercado": {
        "Preco_Atual": "R$ 36,50",
        "Variacao_Dia": "+1,25%",
        "Abertura_Dia": "R$ 36,10",
        "Fechamento_Anterior": "R$ 36,05",
        "Faixa_Dia": "R$ 35,90 - R$ 36,80",
        "Minima_52_Semanas": "R$ 31,00",
        "Maxima_52_Semanas": "R$ 42,00",
        "Valor_de_Mercado": "R$ 470,00 Bi",
        "Enterprise_Value_EV": "R$ 610,00 Bi",
    },
    "Fluxo_e_Volume": {
        "Volume_Financeiro_Dia": "R$ 1,28 Bi",
        "Volume_Acoes_Dia": "35.000.000",
        "Volume_Medio_10d": "32.000.000",
        "Volume_Medio_3m": "30.000.000",
        "Status_Fluxo": "Volume Normal (117% da média 3m)",
        "Beta_Volatilidade": 0.85,
        "Total_Acoes_Emitidas": "13,04 Bi",
        "Pregoes_Recentes": [
            {
                "data": "20/10/2026",
                "fechamento": "R$ 36,50",
                "variacao_pct": 1.25,
                "volume_acoes": "35.000.000",
                "volume_financeiro": "R$ 1,28 Bi",
                "fluxo": "Entrada Compradora",
            }
        ],
    },
    "Valuation": {
        "P_L": 4.5,
        "P_L_Projetado": 4.2,
        "P_VP": 1.2,
        "EV_EBITDA": 2.8,
        "LPA": 8.11,
        "VPA": 30.42,
        "Valor_Intrinseco_Graham": "R$ 74,50",
        "Preco_Teto_Bazin_6pct": "R$ 85,00",
    },
    "Rentabilidade_e_Eficiencia": {
        "ROE": "26,50%",
        "ROA": "11,20%",
        "Margem_Bruta": "50,10%",
        "Margem_EBITDA": "44,30%",
        "Margem_Liquida": "21,40%",
        "Crescimento_Receita_YoY": "4,50%",
        "Crescimento_Lucros_YoY": "8,10%",
    },
    "Endividamento_e_Caixa": {
        "Receita_Total_12m": "R$ 510,00 Bi",
        "EBITDA": "R$ 225,00 Bi",
        "Caixa_Total": "R$ 65,00 Bi",
        "Divida_Total": "R$ 205,00 Bi",
        "Divida_Liquida": "R$ 140,00 Bi",
        "Divida_Liquida_sobre_EBITDA": "0,62x",
        "Liquidez_Corrente": 1.15,
        "Fluxo_Caixa_Livre": "R$ 150,00 Bi",
    },
    "Proventos": {
        "Dividend_Yield_Real_12m": "13,97%",
        "Total_Pago_12m_por_Cota": "R$ 5,10",
        "Frequencia_Pagamentos_12m": "4 pagamentos nos últimos 12 meses",
        "Payout_Ratio": "55,00%",
    },
    "Numeros": {
        "preco_atual": 36.50,
        "variacao_dia_pct": 1.25,
        "vol_financeiro_dia": 1_277_500_000.0,
        "min_52s": 31.0,
        "max_52s": 42.0,
        "pos_52s": 50.0,
        "dividendos_12m": 5.10,
        "dy_12m": 13.9726,
        "graham": 74.50,
        "obs_graham": "",
        "bazin": 85.0,
        "obs_bazin": "",
        "tipo": "Acao",
    },
}

FIXTURE_FII_HGLG11 = {
    "Identificacao": {
        "Ticker": "HGLG11",
        "Nome_Empresa": "CSHG Logística FII",
        "Tipo_Ativo": "Fundo Imobiliário (FII)",
        "Setor": "Real Estate",
        "Industria": "REIT - Industrial",
        "Site_Oficial": "https://imobiliario.cshg.com.br",
        "Moeda_do_Balanco": "BRL",
        "Data_Consulta": "20/10/2026",
        "Resumo_Negocio": "Fundo imobiliário voltado para galpões logísticos e industriais.",
    },
    "Cotacao_e_Mercado": {
        "Preco_Atual": "R$ 160,00",
        "Variacao_Dia": "-0,20%",
        "Abertura_Dia": "R$ 160,30",
        "Fechamento_Anterior": "R$ 160,32",
        "Faixa_Dia": "R$ 159,50 - R$ 160,80",
        "Minima_52_Semanas": "R$ 152,00",
        "Maxima_52_Semanas": "R$ 168,00",
        "Valor_de_Mercado": "R$ 5,40 Bi",
        "Enterprise_Value_EV": "N/D",
    },
    "Fluxo_e_Volume": {
        "Volume_Financeiro_Dia": "R$ 8,00 Mi",
        "Volume_Acoes_Dia": "50.000",
        "Volume_Medio_10d": "48.000",
        "Volume_Medio_3m": "45.000",
        "Status_Fluxo": "Volume Normal (111% da média 3m)",
        "Beta_Volatilidade": 0.15,
        "Total_Acoes_Emitidas": "33,75 Mi",
        "Pregoes_Recentes": [
            {
                "data": "20/10/2026",
                "fechamento": "R$ 160,00",
                "variacao_pct": -0.20,
                "volume_acoes": "50.000",
                "volume_financeiro": "R$ 8,00 Mi",
                "fluxo": "Saída Vendedora",
            }
        ],
    },
    "Valuation": {
        "P_L": "N/D",
        "P_L_Projetado": "N/D",
        "P_VP": 1.02,
        "EV_EBITDA": "N/D",
        "LPA": "N/D",
        "VPA": 156.86,
        "Valor_Intrinseco_Graham": "N/D (Não se aplica a FII/ETF)",
        "Preco_Teto_Bazin_6pct": "R$ 220,00",
    },
    "Rentabilidade_e_Eficiencia": {
        "ROE": "N/D",
        "ROA": "N/D",
        "Margem_Bruta": "N/D",
        "Margem_EBITDA": "N/D",
        "Margem_Liquida": "N/D",
        "Crescimento_Receita_YoY": "N/D",
        "Crescimento_Lucros_YoY": "N/D",
    },
    "Endividamento_e_Caixa": {
        "Receita_Total_12m": "N/D",
        "EBITDA": "N/D",
        "Caixa_Total": "N/D",
        "Divida_Total": "N/D",
        "Divida_Liquida": "N/D",
        "Divida_Liquida_sobre_EBITDA": "N/D",
        "Liquidez_Corrente": "N/D",
        "Fluxo_Caixa_Livre": "N/D",
    },
    "Proventos": {
        "Dividend_Yield_Real_12m": "8,25%",
        "Total_Pago_12m_por_Cota": "R$ 13,20",
        "Frequencia_Pagamentos_12m": "12 pagamentos nos últimos 12 meses",
        "Payout_Ratio": "N/D",
    },
    "Numeros": {
        "preco_atual": 160.0,
        "variacao_dia_pct": -0.20,
        "vol_financeiro_dia": 8_000_000.0,
        "min_52s": 152.0,
        "max_52s": 168.0,
        "pos_52s": 50.0,
        "dividendos_12m": 13.20,
        "dy_12m": 8.25,
        "graham": None,
        "obs_graham": "Não se aplica a FII/ETF",
        "bazin": 220.0,
        "obs_bazin": "",
        "tipo": "FII",
    },
}


def obter_fixture_ativo(ticker: str) -> dict:
    t = (ticker or "").strip().upper()
    if t.endswith("11"):
        dados = copy.deepcopy(FIXTURE_FII_HGLG11)
    else:
        dados = copy.deepcopy(FIXTURE_ACAO_PETR4)
    dados["Identificacao"]["Ticker"] = t
    return dados
