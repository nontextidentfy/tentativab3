"""Dados fixos de ativos da B3 (uma ação e um FII) com números crus para testes sem acesso à rede."""
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
    "Metadados": {
        "moeda_cotacao": "BRL",
        "simbolo": "R$",
        "moeda_balanco": "BRL",
        "simbolo_balanco": "R$",
        "tipo": "Acao",
        "obs_graham": "",
        "obs_bazin": "",
    },
    "Identificacao": {
        "Ticker": "PETR4",
        "Nome_Empresa": "Petróleo Brasileiro S.A. - Petrobras",
        "Tipo_Ativo": "Ação Corporativa",
        "Tipo_Codigo": "Acao",
        "Setor": "Energy",
        "Industria": "Oil & Gas Integrated",
        "Site_Oficial": "https://petrobras.com.br",
        "Moeda_do_Balanco": "BRL",
        "Moeda_Cotacao": "BRL",
        "Simbolo_Moeda": "R$",
        "Simbolo_Moeda_Balanco": "R$",
        "Data_Consulta": "20/10/2026",
        "Resumo_Negocio": "Empresa integrada de energia com foco em exploração, produção e refino de petróleo e gás.",
    },
    "Cotacao_e_Mercado": {
        "Preco_Atual": 36.50,
        "Variacao_Dia": 1.25,
        "Abertura_Dia": 36.10,
        "Fechamento_Anterior": 36.05,
        "Minima_Dia": 35.90,
        "Maxima_Dia": 36.80,
        "Minima_52_Semanas": 31.00,
        "Maxima_52_Semanas": 42.00,
        "Posicao_52_Semanas": 50.0,
        "Valor_de_Mercado": 470_000_000_000.0,
        "Enterprise_Value_EV": 610_000_000_000.0,
    },
    "Fluxo_e_Volume": {
        "Volume_Financeiro_Dia": 1_277_500_000.0,
        "Volume_Acoes_Dia": 35_000_000,
        "Volume_Medio_10d": 32_000_000,
        "Volume_Medio_3m": 30_000_000,
        "Relacao_Volume_Media_Pct": 116.7,
        "Status_Fluxo": "Volume Normal (117% da média 3m)",
        "Beta_Volatilidade": 0.85,
        "Total_Acoes_Emitidas": 13_040_000_000.0,
        "Pregoes_Recentes": [
            {
                "data": "20/10/2026",
                "fechamento": 36.50,
                "variacao_pct": 1.25,
                "volume_acoes": 35_000_000,
                "volume_financeiro": 1_277_500_000.0,
                "fluxo": "Dia de alta",
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
        "Valor_Intrinseco_Graham": 74.50,
        "Obs_Graham": "",
        "Margem_Graham_Pct": 104.11,
        "Preco_Teto_Bazin_6pct": 85.00,
        "Obs_Bazin": "",
        "Margem_Bazin_Pct": 132.88,
    },
    "Rentabilidade_e_Eficiencia": {
        "ROE": 0.265,
        "ROA": 0.112,
        "Margem_Bruta": 0.501,
        "Margem_EBITDA": 0.443,
        "Margem_Liquida": 0.214,
        "Crescimento_Receita_YoY": 0.045,
        "Crescimento_Lucros_YoY": 0.081,
    },
    "Endividamento_e_Caixa": {
        "Receita_Total_12m": 510_000_000_000.0,
        "EBITDA": 225_000_000_000.0,
        "Caixa_Total": 65_000_000_000.0,
        "Divida_Total": 205_000_000_000.0,
        "Divida_Liquida": 140_000_000_000.0,
        "Divida_Liquida_sobre_EBITDA": 0.62,
        "Liquidez_Corrente": 1.15,
        "Fluxo_Caixa_Livre": 150_000_000_000.0,
    },
    "Proventos": {
        "Dividend_Yield_Real_12m": 0.139726,
        "Dividend_Yield_12m_Pct": 13.97,
        "Total_Pago_12m_por_Cota": 5.10,
        "Quantidade_Pagamentos_12m": 4,
        "Frequencia_Pagamentos_12m": "4 pagamentos nos últimos 12 meses",
        "Payout_Ratio": 0.55,
    },
    "Numeros": {
        "preco_atual": 36.50,
        "variacao_dia_pct": 1.25,
        "vol_financeiro_dia": 1_277_500_000.0,
        "min_52s": 31.0,
        "max_52s": 42.0,
        "pos_52s": 50.0,
        "dividendos_12m": 5.10,
        "dy_12m": 13.97,
        "graham": 74.50,
        "obs_graham": "",
        "bazin": 85.0,
        "obs_bazin": "",
        "tipo": "Acao",
    },
}

FIXTURE_FII_HGLG11 = {
    "Metadados": {
        "moeda_cotacao": "BRL",
        "simbolo": "R$",
        "moeda_balanco": "BRL",
        "simbolo_balanco": "R$",
        "tipo": "FII",
        "obs_graham": "Não se aplica a FII/ETF",
        "obs_bazin": "",
    },
    "Identificacao": {
        "Ticker": "HGLG11",
        "Nome_Empresa": "CSHG Logística FII",
        "Tipo_Ativo": "Fundo Imobiliário (FII)",
        "Tipo_Codigo": "FII",
        "Setor": "Real Estate",
        "Industria": "REIT - Industrial",
        "Site_Oficial": "https://imobiliario.cshg.com.br",
        "Moeda_do_Balanco": "BRL",
        "Moeda_Cotacao": "BRL",
        "Simbolo_Moeda": "R$",
        "Simbolo_Moeda_Balanco": "R$",
        "Data_Consulta": "20/10/2026",
        "Resumo_Negocio": "Fundo imobiliário voltado para galpões logísticos e industriais.",
    },
    "Cotacao_e_Mercado": {
        "Preco_Atual": 160.00,
        "Variacao_Dia": -0.20,
        "Abertura_Dia": 160.30,
        "Fechamento_Anterior": 160.32,
        "Minima_Dia": 159.50,
        "Maxima_Dia": 160.80,
        "Minima_52_Semanas": 152.00,
        "Maxima_52_Semanas": 168.00,
        "Posicao_52_Semanas": 50.0,
        "Valor_de_Mercado": 5_400_000_000.0,
        "Enterprise_Value_EV": None,
    },
    "Fluxo_e_Volume": {
        "Volume_Financeiro_Dia": 8_000_000.0,
        "Volume_Acoes_Dia": 50_000,
        "Volume_Medio_10d": 48_000,
        "Volume_Medio_3m": 45_000,
        "Relacao_Volume_Media_Pct": 111.1,
        "Status_Fluxo": "Volume Normal (111% da média 3m)",
        "Beta_Volatilidade": 0.15,
        "Total_Acoes_Emitidas": 33_750_000.0,
        "Pregoes_Recentes": [
            {
                "data": "20/10/2026",
                "fechamento": 160.00,
                "variacao_pct": -0.20,
                "volume_acoes": 50_000,
                "volume_financeiro": 8_000_000.0,
                "fluxo": "Dia de queda",
            }
        ],
    },
    "Valuation": {
        "P_L": None,
        "P_L_Projetado": None,
        "P_VP": 1.02,
        "EV_EBITDA": None,
        "LPA": None,
        "VPA": 156.86,
        "Valor_Intrinseco_Graham": None,
        "Obs_Graham": "Não se aplica a FII/ETF",
        "Margem_Graham_Pct": None,
        "Preco_Teto_Bazin_6pct": 220.00,
        "Obs_Bazin": "",
        "Margem_Bazin_Pct": 37.50,
    },
    "Rentabilidade_e_Eficiencia": {
        "ROE": None,
        "ROA": None,
        "Margem_Bruta": None,
        "Margem_EBITDA": None,
        "Margem_Liquida": None,
        "Crescimento_Receita_YoY": None,
        "Crescimento_Lucros_YoY": None,
    },
    "Endividamento_e_Caixa": {
        "Receita_Total_12m": None,
        "EBITDA": None,
        "Caixa_Total": None,
        "Divida_Total": None,
        "Divida_Liquida": None,
        "Divida_Liquida_sobre_EBITDA": None,
        "Liquidez_Corrente": None,
        "Fluxo_Caixa_Livre": None,
    },
    "Proventos": {
        "Dividend_Yield_Real_12m": 0.0825,
        "Dividend_Yield_12m_Pct": 8.25,
        "Total_Pago_12m_por_Cota": 13.20,
        "Quantidade_Pagamentos_12m": 12,
        "Frequencia_Pagamentos_12m": "12 pagamentos nos últimos 12 meses",
        "Payout_Ratio": None,
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
