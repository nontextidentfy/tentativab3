"""
Serviço central do Investidor IA.
Extrai fundamentos, documentação contábil e fluxo de volume da B3 (via Yahoo Finance)
e gera parecer com o Gemini (com fallback algorítmico caso a cota da API esgote).
"""
import copy
import datetime
import json
import math
import os
import re
import time
import unicodedata
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from google import genai

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PASTA_PROJETO, ".env"))

CHAVE_API = (os.getenv("GEMINI_API_KEY") or "").strip()
MODELO_GEMINI = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
CHAVE_EXEMPLO = "sua_chave_aqui_gerada_no_google_ai_studio"

client = genai.Client(
    api_key=CHAVE_API,
    http_options={"timeout": 15000}
) if CHAVE_API and CHAVE_API != CHAVE_EXEMPLO else None

TAXA_BAZIN = 0.06
FATOR_GRAHAM = 22.5
# Aceita tickers padrão B3 (ex: PETR4, B3SA3, TAEE11, G2DI33, AAPL34, BOVA11)
PADRAO_TICKER = re.compile(r"^[A-Z0-9]{4}\d{1,2}$")
SIMBOLOS_MOEDA = {"BRL": "R$", "USD": "US$", "EUR": "€"}
DESCRICAO_TIPO = {
    "Acao": "Ação Corporativa",
    "Unit": "Unit",
    "FII": "Fundo Imobiliário (FII)",
    "ETF": "ETF",
    "BDR": "BDR",
}


class ErroAtivo(Exception):
    """Ticker inválido, inexistente ou sem dados públicos."""


class ErroIA(Exception):
    """Falha ou limite de cota ao consultar a API de IA."""


def numero(valor):
    if valor is None or isinstance(valor, bool):
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def br(valor, casas=2):
    v = numero(valor)
    if v is None:
        return "N/D"
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def inteiro_br(valor):
    v = numero(valor)
    if v is None:
        return "N/D"
    return f"{int(v):,}".replace(",", ".")


def arredondar(valor, casas=2):
    v = numero(valor)
    return round(v, casas) if v is not None else None


def formatar_moeda_grande(valor, moeda="BRL"):
    v = numero(valor)
    if v is None:
        return "N/D"
    simbolo = SIMBOLOS_MOEDA.get(moeda, moeda)
    prefixo = f"{simbolo} " if simbolo else ""
    if abs(v) >= 1_000_000_000:
        return f"{prefixo}{br(v / 1_000_000_000)} Bi"
    if abs(v) >= 1_000_000:
        return f"{prefixo}{br(v / 1_000_000)} Mi"
    if abs(v) >= 1_000:
        return f"{prefixo}{br(v / 1_000)} Mil"
    return f"{prefixo}{br(v)}"


def formatar_percentual(valor, casas=2, ja_percentual=False):
    v = numero(valor)
    if v is None:
        return "N/D"
    pct = v if ja_percentual else (v * 100.0)
    return f"{br(pct, casas)}%"


def normalizar_ticker(ticker_usuario: str) -> str:
    t = (ticker_usuario or "").strip().upper().replace(".SA", "")
    if t.endswith("F") and PADRAO_TICKER.match(t[:-1]):
        t = t[:-1]
    if not PADRAO_TICKER.match(t):
        raise ErroAtivo(
            f"'{ticker_usuario}' não parece um ticker válido da B3. "
            "Use o formato PETR4, B3SA3, VALE3, TAEE11 ou HGLG11."
        )
    return t


# ---------------------------------------------------------------------------
# Busca de ativos por nome (ex.: "Sanepar" -> SAPR4) com catálogo local + cache
# ---------------------------------------------------------------------------
CATALOGO_B3: list[tuple[str, str]] = [
    ("PETR4", "Petrobras PN"),
    ("PETR3", "Petrobras ON"),
    ("VALE3", "Vale ON"),
    ("ITUB4", "Itaú Unibanco PN"),
    ("ITUB3", "Itaú Unibanco ON"),
    ("ITSA4", "Itaúsa PN"),
    ("BBAS3", "Banco do Brasil ON"),
    ("BBDC4", "Bradesco PN"),
    ("BBDC3", "Bradesco ON"),
    ("SANB11", "Santander Brasil Unit"),
    ("BPAC11", "BTG Pactual Unit"),
    ("BBSE3", "BB Seguridade ON"),
    ("CXSE3", "Caixa Seguridade ON"),
    ("B3SA3", "B3 - Brasil, Bolsa, Balcão ON"),
    ("WEGE3", "WEG ON"),
    ("SAPR4", "Sanepar PN"),
    ("SAPR11", "Sanepar Unit"),
    ("SAPR3", "Sanepar ON"),
    ("SBSP3", "Sabesp ON"),
    ("CSMG3", "Copasa ON"),
    ("TAEE11", "Taesa Unit"),
    ("TRPL4", "ISA CTEEP (Transmissão Paulista) PN"),
    ("EGIE3", "Engie Brasil ON"),
    ("EQTL3", "Equatorial Energia ON"),
    ("CPFE3", "CPFL Energia ON"),
    ("CMIG4", "Cemig PN"),
    ("CPLE6", "Copel PNB"),
    ("ELET3", "Eletrobras ON"),
    ("ELET6", "Eletrobras PNB"),
    ("ENEV3", "Eneva ON"),
    ("AURE3", "Auren Energia ON"),
    ("ABEV3", "Ambev ON"),
    ("SUZB3", "Suzano ON"),
    ("KLBN11", "Klabin Unit"),
    ("KLBN4", "Klabin PN"),
    ("GGBR4", "Gerdau PN"),
    ("GOAU4", "Metalúrgica Gerdau PN"),
    ("CSNA3", "CSN Siderúrgica Nacional ON"),
    ("USIM5", "Usiminas PNA"),
    ("CMIN3", "CSN Mineração ON"),
    ("PRIO3", "PRIO (PetroRio) ON"),
    ("RECV3", "PetroReconcavo ON"),
    ("BRAV3", "Brava Energia ON"),
    ("VBBR3", "Vibra Energia ON"),
    ("UGPA3", "Ultrapar ON"),
    ("CSAN3", "Cosan ON"),
    ("RAIZ4", "Raízen PN"),
    ("RADL3", "Raia Drogasil ON"),
    ("RENT3", "Localiza ON"),
    ("RAIL3", "Rumo Logística ON"),
    ("EMBR3", "Embraer ON"),
    ("TOTS3", "TOTVS ON"),
    ("VIVT3", "Telefônica Brasil (Vivo) ON"),
    ("TIMS3", "TIM Brasil ON"),
    ("LREN3", "Lojas Renner ON"),
    ("MGLU3", "Magazine Luiza ON"),
    ("ASAI3", "Assaí Atacadista ON"),
    ("CRFB3", "Carrefour Brasil ON"),
    ("JBSS3", "JBS ON"),
    ("BRFS3", "BRF ON"),
    ("MRFG3", "Marfrig ON"),
    ("BEEF3", "Minerva Foods ON"),
    ("SLCE3", "SLC Agrícola ON"),
    ("SMTO3", "São Martinho ON"),
    ("RDOR3", "Rede D'Or ON"),
    ("HAPV3", "Hapvida ON"),
    ("FLRY3", "Fleury ON"),
    ("CYRE3", "Cyrela ON"),
    ("EZTC3", "EZTEC ON"),
    ("MRVE3", "MRV Engenharia ON"),
    ("MULT3", "Multiplan ON"),
    ("IGTI11", "Iguatemi Unit"),
    ("ALOS3", "Allos ON"),
    ("HGLG11", "CSHG Logística FII"),
    ("MXRF11", "Maxi Renda FII"),
    ("XPML11", "XP Malls FII"),
    ("KNRI11", "Kinea Renda Imobiliária FII"),
    ("VISC11", "Vinci Shopping Centers FII"),
    ("BTLG11", "BTG Pactual Logística FII"),
    ("XPLG11", "XP Log FII"),
    ("KNCR11", "Kinea Rendimentos Imobiliários FII"),
    ("KNIP11", "Kinea Índices de Preços FII"),
    ("HGRU11", "CSHG Renda Urbana FII"),
    ("BOVA11", "iShares Ibovespa ETF"),
    ("IVVB11", "iShares S&P 500 ETF"),
    ("SMAL11", "iShares Small Cap ETF"),
    ("DIVO11", "It Now IDIV Dividendos ETF"),
    ("ROXO34", "Nu Holdings (Nubank) BDR"),
    ("AAPL34", "Apple BDR"),
    ("MSFT34", "Microsoft BDR"),
    ("NVDC34", "NVIDIA BDR"),
    ("GOGL34", "Alphabet (Google) BDR"),
    ("AMZO34", "Amazon BDR"),
]

_CACHE_BUSCA: dict[str, tuple[float, list[dict]]] = {}
_CACHE_BUSCA_TTL_SEGUNDOS = 60 * 60 * 6  # 6 horas
_CACHE_BUSCA_MAX_ITENS = 500


def _sem_acentos(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _limpar_nome_yahoo(nome: str) -> str:
    """Remove espaços duplicados dos nomes curtos do Yahoo (ex.: 'SANEPAR     PN      N2')."""
    return re.sub(r"\s+", " ", (nome or "").strip())


def buscar_ativos(termo: str, limite: int = 8) -> list[dict]:
    """
    Procura ativos da B3 pelo nome da empresa ou parte do ticker.
    Retorna uma lista de {"ticker": "SAPR4", "nome": "Sanepar PN"}.
    Nunca levanta exceção: em caso de falha de rede, retorna os resultados locais.
    """
    termo_limpo = re.sub(r"\s+", " ", (termo or "").strip())
    if len(termo_limpo) < 2:
        return []
    chave = _sem_acentos(termo_limpo)

    agora = time.monotonic()
    em_cache = _CACHE_BUSCA.get(chave)
    if em_cache and agora - em_cache[0] < _CACHE_BUSCA_TTL_SEGUNDOS:
        return em_cache[1][:limite]

    resultados: list[dict] = []
    vistos: set[str] = set()

    # 1. Busca instantânea no catálogo local da B3
    for tk, nome in CATALOGO_B3:
        if chave in tk.lower() or chave in _sem_acentos(nome):
            vistos.add(tk)
            resultados.append({"ticker": tk, "nome": nome})

    # 2. Complementa com o Yahoo Finance se ainda houver espaço
    if len(resultados) < limite:
        try:
            cotacoes = yf.Search(termo_limpo, max_results=20, news_count=0).quotes or []
        except Exception:
            cotacoes = []

        for item in cotacoes:
            simbolo = str(item.get("symbol") or "").upper()
            if not simbolo.endswith(".SA"):
                continue
            ticker = simbolo[:-3]
            if not PADRAO_TICKER.match(ticker) or ticker in vistos:
                continue
            vistos.add(ticker)
            nome = _limpar_nome_yahoo(item.get("longname") or item.get("shortname") or ticker)
            resultados.append({"ticker": ticker, "nome": nome})

    if len(_CACHE_BUSCA) >= _CACHE_BUSCA_MAX_ITENS:
        _CACHE_BUSCA.clear()
    if resultados:
        _CACHE_BUSCA[chave] = (agora, resultados)
    return resultados[:limite]


def resolver_ticker(entrada_usuario: str) -> str:
    """
    Aceita tanto o ticker (PETR4, sapr4, SAPR4F) quanto o nome da empresa (Sanepar, Itaú, Magazine Luiza).
    Retorna sempre o ticker padrão da B3 ou levanta ErroAtivo com uma mensagem amigável.
    """
    entrada = (entrada_usuario or "").strip()
    try:
        return normalizar_ticker(entrada)
    except ErroAtivo:
        pass

    encontrados = buscar_ativos(entrada, limite=1)
    if encontrados:
        return encontrados[0]["ticker"]

    raise ErroAtivo(
        f"Não encontramos nenhum ativo da B3 para '{entrada_usuario}'. "
        "Tente o nome da empresa (ex.: Sanepar, Petrobras, Itaú) ou o ticker (ex.: SAPR4, PETR4, ITUB4)."
    )


def classificar_ativo(ticker: str, info: dict) -> str:
    nome = f"{info.get('shortName') or ''} {info.get('longName') or ''}".upper()
    tipo_yahoo = str(info.get("quoteType") or "").upper()
    setor = info.get("sector")

    if tipo_yahoo == "ETF" or "ISHARES" in nome or " ETF" in f" {nome}":
        return "ETF"
    if ticker[-2:] in ("31", "32", "33", "34", "35", "39"):
        return "BDR"
    if ticker.endswith("11"):
        if any(p in nome for p in ("FII", "IMOB", "FDO", "FUNDO")) or setor in (None, "", "Real Estate"):
            return "FII"
        return "Unit"
    return "Acao"


def dividendos_ultimos_12m(ativo):
    try:
        historico = ativo.dividends
    except Exception:
        return 0.0, 0
    if historico is None or historico.empty:
        return 0.0, 0
    tz = historico.index.tz
    limite = (pd.Timestamp.now(tz=tz) if tz else pd.Timestamp.now()) - pd.Timedelta(days=365)
    recentes = historico[historico.index >= limite]
    return float(recentes.sum()), int(len(recentes))


def extrair_fluxo_pregoes(ativo) -> list[dict]:
    """Retorna os últimos pregões com valores numéricos crus."""
    pregoes = []
    try:
        hist = ativo.history(period="5d")
        if hist is not None and not hist.empty:
            fechamento_ant = None
            for data_idx, linha in hist.iterrows():
                fech = float(linha.get("Close", 0.0))
                abert = float(linha.get("Open", 0.0))
                vol = int(linha.get("Volume", 0))
                vol_fin = fech * vol
                if fechamento_ant and fechamento_ant > 0:
                    var_dia = ((fech / fechamento_ant) - 1) * 100
                elif abert > 0:
                    var_dia = ((fech / abert) - 1) * 100
                else:
                    var_dia = 0.0
                fechamento_ant = fech

                direcao = (
                    "Entrada Compradora"
                    if var_dia > 0.15
                    else ("Saída Vendedora" if var_dia < -0.15 else "Neutro")
                )
                pregoes.append({
                    "data": data_idx.strftime("%d/%m/%Y"),
                    "fechamento": round(fech, 4),
                    "variacao_pct": round(var_dia, 2),
                    "volume_acoes": vol,
                    "volume_financeiro": round(vol_fin, 2),
                    "fluxo": direcao,
                })
            pregoes.reverse()
    except Exception:
        pass
    return pregoes


def extrair_raio_x_b3(ticker_usuario: str) -> dict:
    """
    Extrai dados fundamentalistas e de mercado da B3 devolvendo números crus (float/int/None)
    e metadados de moeda/observação. Nenhuma formatação textual (R$, %, Mi/Bi, N/D) ocorre aqui.
    """
    ticker = resolver_ticker(ticker_usuario)
    ativo = yf.Ticker(f"{ticker}.SA")

    try:
        info = ativo.info or {}
    except Exception as e:
        raise ErroAtivo(
            f"Não foi possível consultar {ticker} na B3 agora ({type(e).__name__})."
        ) from e

    preco = (
        numero(info.get("currentPrice"))
        or numero(info.get("regularMarketPrice"))
        or numero(info.get("previousClose"))
    )
    if not preco or preco <= 0:
        raise ErroAtivo(f"O ticker {ticker} não foi encontrado na B3 ou não possui cotação ativa.")

    tipo = classificar_ativo(ticker, info)
    eh_fundo = tipo in ("FII", "ETF")

    moeda_cotacao = info.get("currency") or "BRL"
    moeda_balanco = info.get("financialCurrency") or moeda_cotacao
    balanco_em_outra_moeda = moeda_balanco != moeda_cotacao
    simbolo = SIMBOLOS_MOEDA.get(moeda_cotacao, moeda_cotacao)
    simbolo_balanco = SIMBOLOS_MOEDA.get(moeda_balanco, moeda_balanco)

    fechamento_anterior = numero(info.get("previousClose")) or preco
    abertura_dia = numero(info.get("open")) or numero(info.get("regularMarketOpen")) or preco
    min_dia = numero(info.get("dayLow")) or numero(info.get("regularMarketDayLow")) or preco
    max_dia = numero(info.get("dayHigh")) or numero(info.get("regularMarketDayHigh")) or preco
    variacao_dia_pct = ((preco / fechamento_anterior) - 1) * 100 if fechamento_anterior > 0 else 0.0

    vol_acoes_dia = int(numero(info.get("volume")) or numero(info.get("regularMarketVolume")) or 0)
    vol_medio_10d = int(numero(info.get("averageDailyVolume10Day")) or numero(info.get("averageVolume10days")) or 0)
    vol_medio_3m = int(numero(info.get("averageVolume")) or 0)
    vol_financeiro_dia = float(preco * vol_acoes_dia)

    relacao_volume_media = (vol_acoes_dia / vol_medio_3m * 100) if vol_medio_3m and vol_medio_3m > 0 else None
    if relacao_volume_media is not None:
        if relacao_volume_media >= 120 and variacao_dia_pct > 0:
            status_fluxo = f"Forte Entrada de Volume ({round(relacao_volume_media)}% da média)"
        elif relacao_volume_media >= 120 and variacao_dia_pct < 0:
            status_fluxo = f"Forte Pressão Vendedora ({round(relacao_volume_media)}% da média)"
        else:
            status_fluxo = f"Volume Normal ({round(relacao_volume_media)}% da média 3m)"
    else:
        status_fluxo = "Volume em linha com o histórico"

    dividendos_12m, qtd_pagamentos = dividendos_ultimos_12m(ativo)
    dy_12m_razao = (dividendos_12m / preco) if preco > 0 else 0.0
    dy_12m_pct = dy_12m_razao * 100.0

    p_l = arredondar(info.get("trailingPE"))
    p_l_proj = arredondar(info.get("forwardPE"))
    p_vp = arredondar(info.get("priceToBook"))
    ev_ebitda = arredondar(info.get("enterpriseToEbitda"))
    lpa = arredondar(info.get("trailingEps"))
    vpa = arredondar(info.get("bookValue"))

    graham = None
    if eh_fundo:
        obs_graham = "Não se aplica a FII/ETF"
    elif balanco_em_outra_moeda:
        obs_graham = f"Balanço em {moeda_balanco}"
    elif lpa and vpa and lpa > 0 and vpa > 0:
        graham = round(math.sqrt(FATOR_GRAHAM * lpa * vpa), 2)
        obs_graham = ""
    else:
        obs_graham = "Lucro ou patrimônio negativo"

    bazin = round(dividendos_12m / TAXA_BAZIN, 2) if dividendos_12m > 0 else None
    obs_bazin = "" if bazin else "Sem proventos em 12 meses"

    margem_graham_pct = round(((graham / preco) - 1) * 100, 2) if graham and preco > 0 else None
    margem_bazin_pct = round(((bazin / preco) - 1) * 100, 2) if bazin and preco > 0 else None

    divida_total = numero(info.get("totalDebt"))
    caixa_total = numero(info.get("totalCash"))
    divida_liquida = (divida_total - caixa_total) if (divida_total is not None and caixa_total is not None) else None
    ebitda = numero(info.get("ebitda"))
    alavancagem = round(divida_liquida / ebitda, 2) if (divida_liquida is not None and ebitda and ebitda > 0) else None

    min_52s = numero(info.get("fiftyTwoWeekLow"))
    max_52s = numero(info.get("fiftyTwoWeekHigh"))
    resumo = (info.get("longBusinessSummary") or "Sem descrição institucional cadastrada.").strip()
    liquidez = arredondar(info.get("currentRatio"))
    roe = numero(info.get("returnOnEquity"))
    beta = arredondar(info.get("beta"))
    acoes_circulacao = numero(info.get("sharesOutstanding"))
    valor_mercado = numero(info.get("marketCap"))
    enterprise_value = numero(info.get("enterpriseValue"))

    historico_5d = extrair_fluxo_pregoes(ativo)

    pos_52s = 50.0
    if min_52s and max_52s and max_52s > min_52s:
        pos_52s = max(0.0, min(100.0, (preco - min_52s) / (max_52s - min_52s) * 100))

    site_oficial = (info.get("website") or "").strip() or None

    return {
        "Metadados": {
            "moeda_cotacao": moeda_cotacao,
            "simbolo": simbolo,
            "moeda_balanco": moeda_balanco,
            "simbolo_balanco": simbolo_balanco,
            "tipo": tipo,
            "obs_graham": obs_graham,
            "obs_bazin": obs_bazin,
        },
        "Identificacao": {
            "Ticker": ticker,
            "Nome_Empresa": (info.get("longName") or info.get("shortName") or ticker)[:200],
            "Tipo_Ativo": DESCRICAO_TIPO[tipo],
            "Tipo_Codigo": tipo,
            "Setor": info.get("sector") or "Não especificado",
            "Industria": info.get("industry") or "Não especificado",
            "Site_Oficial": site_oficial,
            "Moeda_do_Balanco": moeda_balanco,
            "Moeda_Cotacao": moeda_cotacao,
            "Simbolo_Moeda": simbolo,
            "Simbolo_Moeda_Balanco": simbolo_balanco,
            "Data_Consulta": datetime.date.today().strftime("%d/%m/%Y"),
            "Resumo_Negocio": resumo[:1500],
        },
        "Cotacao_e_Mercado": {
            "Preco_Atual": round(preco, 4),
            "Variacao_Dia": round(variacao_dia_pct, 2),
            "Abertura_Dia": round(abertura_dia, 4),
            "Fechamento_Anterior": round(fechamento_anterior, 4),
            "Minima_Dia": round(min_dia, 4),
            "Maxima_Dia": round(max_dia, 4),
            "Minima_52_Semanas": round(min_52s, 4) if min_52s is not None else None,
            "Maxima_52_Semanas": round(max_52s, 4) if max_52s is not None else None,
            "Posicao_52_Semanas": round(pos_52s, 1),
            "Valor_de_Mercado": valor_mercado,
            "Enterprise_Value_EV": enterprise_value,
        },
        "Fluxo_e_Volume": {
            "Volume_Financeiro_Dia": round(vol_financeiro_dia, 2),
            "Volume_Acoes_Dia": vol_acoes_dia,
            "Volume_Medio_10d": vol_medio_10d,
            "Volume_Medio_3m": vol_medio_3m,
            "Relacao_Volume_Media_Pct": round(relacao_volume_media, 1) if relacao_volume_media is not None else None,
            "Status_Fluxo": status_fluxo,
            "Beta_Volatilidade": beta,
            "Total_Acoes_Emitidas": acoes_circulacao,
            "Pregoes_Recentes": historico_5d,
        },
        "Valuation": {
            "P_L": p_l,
            "P_L_Projetado": p_l_proj,
            "P_VP": p_vp,
            "EV_EBITDA": ev_ebitda,
            "LPA": lpa,
            "VPA": vpa,
            "Valor_Intrinseco_Graham": graham,
            "Obs_Graham": obs_graham,
            "Margem_Graham_Pct": margem_graham_pct,
            "Preco_Teto_Bazin_6pct": bazin,
            "Obs_Bazin": obs_bazin,
            "Margem_Bazin_Pct": margem_bazin_pct,
        },
        "Rentabilidade_e_Eficiencia": {
            "ROE": roe,
            "ROA": numero(info.get("returnOnAssets")),
            "Margem_Bruta": numero(info.get("grossMargins")),
            "Margem_EBITDA": numero(info.get("ebitdaMargins")),
            "Margem_Liquida": numero(info.get("profitMargins")),
            "Crescimento_Receita_YoY": numero(info.get("revenueGrowth")),
            "Crescimento_Lucros_YoY": numero(info.get("earningsGrowth")),
        },
        "Endividamento_e_Caixa": {
            "Receita_Total_12m": numero(info.get("totalRevenue")),
            "EBITDA": ebitda,
            "Caixa_Total": caixa_total,
            "Divida_Total": divida_total,
            "Divida_Liquida": divida_liquida,
            "Divida_Liquida_sobre_EBITDA": alavancagem,
            "Liquidez_Corrente": liquidez,
            "Fluxo_Caixa_Livre": numero(info.get("freeCashflow")),
        },
        "Proventos": {
            "Dividend_Yield_Real_12m": round(dy_12m_razao, 6),
            "Dividend_Yield_12m_Pct": round(dy_12m_pct, 2),
            "Total_Pago_12m_por_Cota": round(dividendos_12m, 4),
            "Quantidade_Pagamentos_12m": qtd_pagamentos,
            "Frequencia_Pagamentos_12m": f"{qtd_pagamentos} pagamentos nos últimos 12 meses",
            "Payout_Ratio": numero(info.get("payoutRatio")),
        },
        "Numeros": {
            "preco_atual": round(preco, 4),
            "variacao_dia_pct": round(variacao_dia_pct, 2),
            "vol_financeiro_dia": round(vol_financeiro_dia, 2),
            "min_52s": min_52s,
            "max_52s": max_52s,
            "pos_52s": round(pos_52s, 1),
            "dividendos_12m": round(dividendos_12m, 4),
            "dy_12m": round(dy_12m_pct, 2),
            "graham": graham,
            "obs_graham": obs_graham,
            "bazin": bazin,
            "obs_bazin": obs_bazin,
            "tipo": tipo,
        },
    }


def _moeda_txt(valor, simbolo="R$") -> str:
    v = numero(valor)
    if v is None:
        return "N/D"
    return f"{simbolo} {br(v, 2)}"


def formatar_para_leitura(dados: dict) -> dict:
    """
    Recebe a estrutura de números crus de `extrair_raio_x_b3` e devolve uma cópia
    com textos legíveis (R$, %, Mi/Bi, N/D) para o prompt da IA e para o terminal (`analisador.py`).
    """
    d = copy.deepcopy(dados)
    meta = d.get("Metadados") or {}
    ide = d.get("Identificacao") or {}
    cot = d.get("Cotacao_e_Mercado") or {}
    fluxo = d.get("Fluxo_e_Volume") or {}
    val = d.get("Valuation") or {}
    rent = d.get("Rentabilidade_e_Eficiencia") or {}
    end = d.get("Endividamento_e_Caixa") or {}
    prov = d.get("Proventos") or {}

    moeda_cot = meta.get("moeda_cotacao") or ide.get("Moeda_Cotacao") or "BRL"
    moeda_bal = meta.get("moeda_balanco") or ide.get("Moeda_do_Balanco") or moeda_cot
    simbolo = meta.get("simbolo") or ide.get("Simbolo_Moeda") or SIMBOLOS_MOEDA.get(moeda_cot, "R$")

    preco = cot.get("Preco_Atual")
    var_dia = cot.get("Variacao_Dia")
    min_dia = cot.get("Minima_Dia")
    max_dia = cot.get("Maxima_Dia")
    min_52s = cot.get("Minima_52_Semanas")
    max_52s = cot.get("Maxima_52_Semanas")

    graham = val.get("Valor_Intrinseco_Graham")
    obs_graham = meta.get("obs_graham") or val.get("Obs_Graham") or ""
    bazin = val.get("Preco_Teto_Bazin_6pct")
    obs_bazin = meta.get("obs_bazin") or val.get("Obs_Bazin") or ""

    pregoes_fmt = []
    for p in fluxo.get("Pregoes_Recentes") or []:
        fech_val = p.get("fechamento")
        vol_fin_val = p.get("volume_financeiro")
        pregoes_fmt.append({
            "data": p.get("data"),
            "fechamento": _moeda_txt(fech_val, simbolo) if isinstance(fech_val, (int, float)) else str(fech_val),
            "variacao_pct": p.get("variacao_pct", 0.0),
            "volume_acoes": inteiro_br(p.get("volume_acoes")) if isinstance(p.get("volume_acoes"), (int, float)) else str(p.get("volume_acoes")),
            "volume_financeiro": formatar_moeda_grande(vol_fin_val, moeda_cot) if isinstance(vol_fin_val, (int, float)) else str(vol_fin_val),
            "fluxo": p.get("fluxo", "Neutro"),
        })

    alav = end.get("Divida_Liquida_sobre_EBITDA")
    dy_raw = prov.get("Dividend_Yield_Real_12m")
    dy_txt = (
        formatar_percentual(dy_raw, ja_percentual=False)
        if isinstance(dy_raw, (int, float)) and abs(dy_raw) <= 1.5 and (prov.get("Dividend_Yield_12m_Pct") is not None)
        else (formatar_percentual(prov.get("Dividend_Yield_12m_Pct"), ja_percentual=True) if isinstance(dy_raw, (int, float)) else str(dy_raw or "N/D"))
    )
    if prov.get("Dividend_Yield_12m_Pct") is not None:
        dy_txt = f"{br(prov['Dividend_Yield_12m_Pct'])}%"

    qtd_pag = prov.get("Quantidade_Pagamentos_12m")
    freq_txt = prov.get("Frequencia_Pagamentos_12m") or (
        f"{qtd_pag} pagamentos nos últimos 12 meses" if qtd_pag is not None else "N/D"
    )

    return {
        "Identificacao": {
            "Ticker": ide.get("Ticker"),
            "Nome_Empresa": ide.get("Nome_Empresa"),
            "Tipo_Ativo": ide.get("Tipo_Ativo"),
            "Setor": ide.get("Setor"),
            "Industria": ide.get("Industria"),
            "Site_Oficial": ide.get("Site_Oficial") or "N/D",
            "Moeda_do_Balanco": moeda_bal,
            "Data_Consulta": ide.get("Data_Consulta"),
            "Resumo_Negocio": ide.get("Resumo_Negocio"),
        },
        "Cotacao_e_Mercado": {
            "Preco_Atual": _moeda_txt(preco, simbolo) if isinstance(preco, (int, float)) else str(preco),
            "Variacao_Dia": (
                f"{'+' if var_dia >= 0 else ''}{br(var_dia)}%"
                if isinstance(var_dia, (int, float))
                else str(var_dia)
            ),
            "Abertura_Dia": _moeda_txt(cot.get("Abertura_Dia"), simbolo),
            "Fechamento_Anterior": _moeda_txt(cot.get("Fechamento_Anterior"), simbolo),
            "Faixa_Dia": (
                f"{_moeda_txt(min_dia, simbolo)} - {_moeda_txt(max_dia, simbolo)}"
                if min_dia is not None and max_dia is not None
                else str(cot.get("Faixa_Dia") or "N/D")
            ),
            "Minima_52_Semanas": _moeda_txt(min_52s, simbolo) if isinstance(min_52s, (int, float)) else str(min_52s or "N/D"),
            "Maxima_52_Semanas": _moeda_txt(max_52s, simbolo) if isinstance(max_52s, (int, float)) else str(max_52s or "N/D"),
            "Valor_de_Mercado": formatar_moeda_grande(cot.get("Valor_de_Mercado"), moeda_cot) if not isinstance(cot.get("Valor_de_Mercado"), str) else cot.get("Valor_de_Mercado"),
            "Enterprise_Value_EV": formatar_moeda_grande(cot.get("Enterprise_Value_EV"), moeda_cot) if not isinstance(cot.get("Enterprise_Value_EV"), str) else cot.get("Enterprise_Value_EV"),
        },
        "Fluxo_e_Volume": {
            "Volume_Financeiro_Dia": formatar_moeda_grande(fluxo.get("Volume_Financeiro_Dia"), moeda_cot) if not isinstance(fluxo.get("Volume_Financeiro_Dia"), str) else fluxo.get("Volume_Financeiro_Dia"),
            "Volume_Acoes_Dia": inteiro_br(fluxo.get("Volume_Acoes_Dia")) if not isinstance(fluxo.get("Volume_Acoes_Dia"), str) else fluxo.get("Volume_Acoes_Dia"),
            "Volume_Medio_10d": inteiro_br(fluxo.get("Volume_Medio_10d")) if not isinstance(fluxo.get("Volume_Medio_10d"), str) else fluxo.get("Volume_Medio_10d"),
            "Volume_Medio_3m": inteiro_br(fluxo.get("Volume_Medio_3m")) if not isinstance(fluxo.get("Volume_Medio_3m"), str) else fluxo.get("Volume_Medio_3m"),
            "Status_Fluxo": fluxo.get("Status_Fluxo"),
            "Beta_Volatilidade": br(fluxo.get("Beta_Volatilidade")),
            "Total_Acoes_Emitidas": formatar_moeda_grande(fluxo.get("Total_Acoes_Emitidas"), "").strip() if not isinstance(fluxo.get("Total_Acoes_Emitidas"), str) else fluxo.get("Total_Acoes_Emitidas"),
            "Pregoes_Recentes": pregoes_fmt,
        },
        "Valuation": {
            "P_L": br(val.get("P_L")),
            "P_L_Projetado": br(val.get("P_L_Projetado")),
            "P_VP": br(val.get("P_VP")),
            "EV_EBITDA": br(val.get("EV_EBITDA")),
            "LPA": br(val.get("LPA")),
            "VPA": br(val.get("VPA")),
            "Valor_Intrinseco_Graham": (
                _moeda_txt(graham, simbolo)
                if isinstance(graham, (int, float))
                else (str(graham) if isinstance(graham, str) else f"N/D ({obs_graham})")
            ),
            "Preco_Teto_Bazin_6pct": (
                _moeda_txt(bazin, simbolo)
                if isinstance(bazin, (int, float))
                else (str(bazin) if isinstance(bazin, str) else f"N/D ({obs_bazin})")
            ),
        },
        "Rentabilidade_e_Eficiencia": {
            "ROE": formatar_percentual(rent.get("ROE")) if not isinstance(rent.get("ROE"), str) else rent.get("ROE"),
            "ROA": formatar_percentual(rent.get("ROA")) if not isinstance(rent.get("ROA"), str) else rent.get("ROA"),
            "Margem_Bruta": formatar_percentual(rent.get("Margem_Bruta")) if not isinstance(rent.get("Margem_Bruta"), str) else rent.get("Margem_Bruta"),
            "Margem_EBITDA": formatar_percentual(rent.get("Margem_EBITDA")) if not isinstance(rent.get("Margem_EBITDA"), str) else rent.get("Margem_EBITDA"),
            "Margem_Liquida": formatar_percentual(rent.get("Margem_Liquida")) if not isinstance(rent.get("Margem_Liquida"), str) else rent.get("Margem_Liquida"),
            "Crescimento_Receita_YoY": formatar_percentual(rent.get("Crescimento_Receita_YoY")) if not isinstance(rent.get("Crescimento_Receita_YoY"), str) else rent.get("Crescimento_Receita_YoY"),
            "Crescimento_Lucros_YoY": formatar_percentual(rent.get("Crescimento_Lucros_YoY")) if not isinstance(rent.get("Crescimento_Lucros_YoY"), str) else rent.get("Crescimento_Lucros_YoY"),
        },
        "Endividamento_e_Caixa": {
            "Receita_Total_12m": formatar_moeda_grande(end.get("Receita_Total_12m"), moeda_bal) if not isinstance(end.get("Receita_Total_12m"), str) else end.get("Receita_Total_12m"),
            "EBITDA": formatar_moeda_grande(end.get("EBITDA"), moeda_bal) if not isinstance(end.get("EBITDA"), str) else end.get("EBITDA"),
            "Caixa_Total": formatar_moeda_grande(end.get("Caixa_Total"), moeda_bal) if not isinstance(end.get("Caixa_Total"), str) else end.get("Caixa_Total"),
            "Divida_Total": formatar_moeda_grande(end.get("Divida_Total"), moeda_bal) if not isinstance(end.get("Divida_Total"), str) else end.get("Divida_Total"),
            "Divida_Liquida": formatar_moeda_grande(end.get("Divida_Liquida"), moeda_bal) if not isinstance(end.get("Divida_Liquida"), str) else end.get("Divida_Liquida"),
            "Divida_Liquida_sobre_EBITDA": f"{br(alav)}x" if isinstance(alav, (int, float)) else str(alav or "N/D"),
            "Liquidez_Corrente": br(end.get("Liquidez_Corrente")),
            "Fluxo_Caixa_Livre": formatar_moeda_grande(end.get("Fluxo_Caixa_Livre"), moeda_bal) if not isinstance(end.get("Fluxo_Caixa_Livre"), str) else end.get("Fluxo_Caixa_Livre"),
        },
        "Proventos": {
            "Dividend_Yield_Real_12m": dy_txt,
            "Total_Pago_12m_por_Cota": _moeda_txt(prov.get("Total_Pago_12m_por_Cota"), simbolo) if not isinstance(prov.get("Total_Pago_12m_por_Cota"), str) else prov.get("Total_Pago_12m_por_Cota"),
            "Frequencia_Pagamentos_12m": freq_txt,
            "Payout_Ratio": formatar_percentual(prov.get("Payout_Ratio")) if not isinstance(prov.get("Payout_Ratio"), str) else prov.get("Payout_Ratio"),
        },
    }


def gerar_resumo_tecnico_local(dados: dict, motivo_fallback: str = "") -> str:
    fmt = formatar_para_leitura(dados)
    ide = fmt["Identificacao"]
    val = fmt["Valuation"]
    rent = fmt["Rentabilidade_e_Eficiencia"]
    end = fmt["Endividamento_e_Caixa"]
    fluxo = fmt["Fluxo_e_Volume"]
    prov = fmt["Proventos"]

    cot_raw = dados.get("Cotacao_e_Mercado") or {}
    val_raw = dados.get("Valuation") or {}
    n = dados.get("Numeros") or {}

    preco = numero(cot_raw.get("Preco_Atual")) or numero(n.get("preco_atual"))
    graham = numero(val_raw.get("Valor_Intrinseco_Graham")) or numero(n.get("graham"))
    bazin = numero(val_raw.get("Preco_Teto_Bazin_6pct")) or numero(n.get("bazin"))

    analise_val = (
        f"O ativo **{ide['Ticker']}** negocia a **{fmt['Cotacao_e_Mercado']['Preco_Atual']}**, "
        f"apresentando P/L de **{val['P_L']}** e P/VP de **{val['P_VP']}**. "
    )
    if graham and preco and preco > 0:
        diff_g = ((graham / preco) - 1) * 100
        status_g = f"desconto de {br(diff_g)}%" if diff_g >= 0 else f"prêmio de {br(abs(diff_g))}%"
        analise_val += f"Pela fórmula de Benjamin Graham ({val['Valor_Intrinseco_Graham']}), a cotação opera com **{status_g}**. "
    if bazin:
        analise_val += f"O Preço Teto de Décio Bazin (6% a.a.) situa-se em **{val['Preco_Teto_Bazin_6pct']}**."

    aviso_topo = f"> *Nota Técnica: Diagnóstico gerado pelo Motor Algorítmico Interno ({motivo_fallback}).*\n\n" if motivo_fallback else ""

    return (
        f"{aviso_topo}"
        f"### 1. Valuation e Precificação\n"
        f"{analise_val}\n\n"
        f"### 2. Fluxo de Volume e Liquidez de Mercado\n"
        f"No pregão atual, o ativo registra variação de **{fmt['Cotacao_e_Mercado']['Variacao_Dia']}** com volume financeiro estimado em "
        f"**{fluxo['Volume_Financeiro_Dia']}** ({fluxo['Volume_Acoes_Dia']} papéis negociados vs. média de 3 meses de {fluxo['Volume_Medio_3m']}). "
        f"Diagnóstico de fluxo: **{fluxo['Status_Fluxo']}**.\n\n"
        f"### 3. Qualidade Operacional e Documentação Contábil\n"
        f"A empresa apresenta ROE de **{rent['ROE']}**, Margem Líquida de **{rent['Margem_Liquida']}** e Margem EBITDA de **{rent['Margem_EBITDA']}**. "
        f"Na estrutura de capital, reporta Caixa de **{end['Caixa_Total']}**, Dívida Bruta de **{end['Divida_Total']}** e alavancagem (Dív. Líquida/EBITDA) em **{end['Divida_Liquida_sobre_EBITDA']}**.\n\n"
        f"### 4. Remuneração ao Acionista (Proventos)\n"
        f"Nos últimos 12 meses, acumulou Dividend Yield real de **{prov['Dividend_Yield_Real_12m']}** ({prov['Total_Pago_12m_por_Cota']} por ação/cota em {prov['Frequencia_Pagamentos_12m']}), "
        f"com Payout registrado de **{prov['Payout_Ratio']}**.\n\n"
        f"*Este diagnóstico é estritamente informativo e não constitui recomendação de compra ou venda.*"
    )


def gerar_relatorio_ia(dados: dict) -> tuple[str, str, bool]:
    """
    Gera o diagnóstico fundamentalista e retorna (relatorio_md, modelo_utilizado, veio_da_ia).
    """
    if client is None:
        return gerar_resumo_tecnico_local(dados, "Chave de API não configurada"), "Motor Algorítmico Local", False

    dados_prompt = formatar_para_leitura(dados)

    prompt = f"""Você é um especialista em análise fundamentalista de ativos listados na B3.
Analise de forma crítica, técnica e independente os dados abaixo, extraídos da B3/Yahoo Finance.
Interprete os indicadores conjuntamente (Valuation, Fluxo de Volume, Balanço e Proventos).

REGRAS:
1. Use somente os dados fornecidos. Não invente números ou eventos.
2. Analise também o Fluxo de Volume e a liquidez nos últimos pregões.
3. Para FII, priorize P/VP, Dividend Yield, liquidez e consistência dos rendimentos.
4. Responda em português do Brasil, em Markdown, usando exatamente os 4 títulos '###' abaixo (máximo 450 palavras).
5. Encerre lembrando que não é recomendação formal de compra ou venda.

DADOS DO ATIVO (JSON):
{json.dumps(dados_prompt, ensure_ascii=False, indent=2)}

ESTRUTURA OBRIGATÓRIA:
### 1. Valuation: descontado ou esticado?
### 2. Fluxo de Volume e Dinâmica de Mercado
### 3. Qualidade Operacional e Documentação de Balanço
### 4. Proventos e Principais Riscos Setoriais
"""
    try:
        resposta = client.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        texto = (getattr(resposta, "text", None) or "").strip()
        if not texto:
            return gerar_resumo_tecnico_local(dados, "Resposta vazia da API"), "Motor Algorítmico Local", False
        return texto, MODELO_GEMINI, True
    except Exception as e:
        msg_erro = str(e)
        if "429" in msg_erro or "quota" in msg_erro.lower():
            erro_curto = "Limite de cota da API atingido"
        elif "404" in msg_erro or "not found" in msg_erro.lower():
            erro_curto = f"Modelo '{MODELO_GEMINI}' inválido no .env"
        else:
            erro_curto = type(e).__name__
        return gerar_resumo_tecnico_local(dados, erro_curto), f"Motor Algorítmico ({erro_curto})", False
