"""
Serviço central do Investidor IA.
Extrai fundamentos, documentação contábil e fluxo de volume da B3 (via Yahoo Finance)
e gera parecer com o Gemini (com fallback algorítmico caso a cota da API esgote).
"""
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
    return round(v, casas) if v is not None else "N/D"


def formatar_moeda_grande(valor, moeda="BRL"):
    v = numero(valor)
    if v is None:
        return "N/D"
    simbolo = SIMBOLOS_MOEDA.get(moeda, moeda)
    if abs(v) >= 1_000_000_000:
        return f"{simbolo} {br(v / 1_000_000_000)} Bi"
    if abs(v) >= 1_000_000:
        return f"{simbolo} {br(v / 1_000_000)} Mi"
    if abs(v) >= 1_000:
        return f"{simbolo} {br(v / 1_000)} Mil"
    return f"{simbolo} {br(v)}"


def formatar_percentual(valor):
    v = numero(valor)
    return "N/D" if v is None else f"{br(v * 100)}%"


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


def extrair_fluxo_pregoes(ativo, simbolo="R$"):
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

                direcao = "Entrada Compradora" if var_dia > 0.15 else ("Saída Vendedora" if var_dia < -0.15 else "Neutro")
                pregoes.append({
                    "data": data_idx.strftime("%d/%m/%Y"),
                    "fechamento": f"{simbolo} {br(fech)}",
                    "variacao_pct": round(var_dia, 2),
                    "volume_acoes": inteiro_br(vol),
                    "volume_financeiro": formatar_moeda_grande(vol_fin),
                    "fluxo": direcao,
                })
            pregoes.reverse()
    except Exception:
        pass
    return pregoes


def extrair_raio_x_b3(ticker_usuario: str) -> dict:
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

    fechamento_anterior = numero(info.get("previousClose")) or preco
    abertura_dia = numero(info.get("open")) or numero(info.get("regularMarketOpen")) or preco
    min_dia = numero(info.get("dayLow")) or numero(info.get("regularMarketDayLow")) or preco
    max_dia = numero(info.get("dayHigh")) or numero(info.get("regularMarketDayHigh")) or preco
    variacao_dia_pct = ((preco / fechamento_anterior) - 1) * 100 if fechamento_anterior > 0 else 0.0

    vol_acoes_dia = numero(info.get("volume")) or numero(info.get("regularMarketVolume")) or 0
    vol_medio_10d = numero(info.get("averageDailyVolume10Day")) or numero(info.get("averageVolume10days")) or 0
    vol_medio_3m = numero(info.get("averageVolume")) or 0
    vol_financeiro_dia = preco * vol_acoes_dia

    relacao_volume_media = (vol_acoes_dia / vol_medio_3m * 100) if vol_medio_3m and vol_medio_3m > 0 else None
    if relacao_volume_media is not None:
        if relacao_volume_media >= 120 and variacao_dia_pct > 0:
            status_fluxo = f"Forte Entrada de Volume ({br(relacao_volume_media, 0)}% da média)"
        elif relacao_volume_media >= 120 and variacao_dia_pct < 0:
            status_fluxo = f"Forte Pressão Vendedora ({br(relacao_volume_media, 0)}% da média)"
        else:
            status_fluxo = f"Volume Normal ({br(relacao_volume_media, 0)}% da média 3m)"
    else:
        status_fluxo = "Volume em linha com o histórico"

    dividendos_12m, qtd_pagamentos = dividendos_ultimos_12m(ativo)
    dy_12m = (dividendos_12m / preco) * 100 if preco > 0 else 0.0

    p_l = numero(info.get("trailingPE"))
    p_l_proj = numero(info.get("forwardPE"))
    p_vp = numero(info.get("priceToBook"))
    ev_ebitda = numero(info.get("enterpriseToEbitda"))
    lpa = numero(info.get("trailingEps"))
    vpa = numero(info.get("bookValue"))

    graham = None
    if eh_fundo:
        obs_graham = "Não se aplica a FII/ETF"
    elif balanco_em_outra_moeda:
        obs_graham = f"Balanço em {moeda_balanco}"
    elif lpa and vpa and lpa > 0 and vpa > 0:
        graham = math.sqrt(FATOR_GRAHAM * lpa * vpa)
        obs_graham = ""
    else:
        obs_graham = "Lucro ou patrimônio negativo"

    bazin = dividendos_12m / TAXA_BAZIN if dividendos_12m > 0 else None
    obs_bazin = "" if bazin else "Sem proventos em 12 meses"

    divida_total = numero(info.get("totalDebt"))
    caixa_total = numero(info.get("totalCash"))
    divida_liquida = (divida_total - caixa_total) if (divida_total is not None and caixa_total is not None) else None
    ebitda = numero(info.get("ebitda"))
    alavancagem = (divida_liquida / ebitda) if (divida_liquida is not None and ebitda and ebitda > 0) else None

    min_52s = numero(info.get("fiftyTwoWeekLow"))
    max_52s = numero(info.get("fiftyTwoWeekHigh"))
    resumo = (info.get("longBusinessSummary") or "Sem descrição institucional cadastrada.").strip()
    liquidez = numero(info.get("currentRatio"))
    roe = numero(info.get("returnOnEquity"))
    beta = numero(info.get("beta"))
    acoes_circulacao = numero(info.get("sharesOutstanding"))

    historico_5d = extrair_fluxo_pregoes(ativo, simbolo)

    # Cálculo percentual da posição na faixa de 52 semanas
    pos_52s = 50.0
    if min_52s and max_52s and max_52s > min_52s:
        pos_52s = max(0.0, min(100.0, (preco - min_52s) / (max_52s - min_52s) * 100))

    return {
        "Identificacao": {
            "Ticker": ticker,
            "Nome_Empresa": (info.get("longName") or info.get("shortName") or ticker)[:200],
            "Tipo_Ativo": DESCRICAO_TIPO[tipo],
            "Setor": info.get("sector") or "Não especificado",
            "Industria": info.get("industry") or "Não especificado",
            "Site_Oficial": info.get("website") or "N/D",
            "Moeda_do_Balanco": moeda_balanco,
            "Data_Consulta": datetime.date.today().strftime("%d/%m/%Y"),
            "Resumo_Negocio": resumo[:1500],
        },
        "Cotacao_e_Mercado": {
            "Preco_Atual": f"{simbolo} {br(preco)}",
            "Variacao_Dia": f"{'+' if variacao_dia_pct >= 0 else ''}{br(variacao_dia_pct)}%",
            "Abertura_Dia": f"{simbolo} {br(abertura_dia)}",
            "Fechamento_Anterior": f"{simbolo} {br(fechamento_anterior)}",
            "Faixa_Dia": f"{simbolo} {br(min_dia)} - {simbolo} {br(max_dia)}",
            "Minima_52_Semanas": f"{simbolo} {br(min_52s)}" if min_52s else "N/D",
            "Maxima_52_Semanas": f"{simbolo} {br(max_52s)}" if max_52s else "N/D",
            "Valor_de_Mercado": formatar_moeda_grande(info.get("marketCap"), moeda_cotacao),
            "Enterprise_Value_EV": formatar_moeda_grande(info.get("enterpriseValue"), moeda_cotacao),
        },
        "Fluxo_e_Volume": {
            "Volume_Financeiro_Dia": formatar_moeda_grande(vol_financeiro_dia, moeda_cotacao),
            "Volume_Acoes_Dia": inteiro_br(vol_acoes_dia),
            "Volume_Medio_10d": inteiro_br(vol_medio_10d),
            "Volume_Medio_3m": inteiro_br(vol_medio_3m),
            "Status_Fluxo": status_fluxo,
            "Beta_Volatilidade": arredondar(beta),
            "Total_Acoes_Emitidas": formatar_moeda_grande(acoes_circulacao, "").strip(),
            "Pregoes_Recentes": historico_5d,
        },
        "Valuation": {
            "P_L": arredondar(p_l),
            "P_L_Projetado": arredondar(p_l_proj),
            "P_VP": arredondar(p_vp),
            "EV_EBITDA": arredondar(ev_ebitda),
            "LPA": arredondar(lpa),
            "VPA": arredondar(vpa),
            "Valor_Intrinseco_Graham": f"{simbolo} {br(graham)}" if graham else f"N/D ({obs_graham})",
            "Preco_Teto_Bazin_6pct": f"{simbolo} {br(bazin)}" if bazin else f"N/D ({obs_bazin})",
        },
        "Rentabilidade_e_Eficiencia": {
            "ROE": formatar_percentual(roe),
            "ROA": formatar_percentual(info.get("returnOnAssets")),
            "Margem_Bruta": formatar_percentual(info.get("grossMargins")),
            "Margem_EBITDA": formatar_percentual(info.get("ebitdaMargins")),
            "Margem_Liquida": formatar_percentual(info.get("profitMargins")),
            "Crescimento_Receita_YoY": formatar_percentual(info.get("revenueGrowth")),
            "Crescimento_Lucros_YoY": formatar_percentual(info.get("earningsGrowth")),
        },
        "Endividamento_e_Caixa": {
            "Receita_Total_12m": formatar_moeda_grande(info.get("totalRevenue"), moeda_balanco),
            "EBITDA": formatar_moeda_grande(ebitda, moeda_balanco),
            "Caixa_Total": formatar_moeda_grande(caixa_total, moeda_balanco),
            "Divida_Total": formatar_moeda_grande(divida_total, moeda_balanco),
            "Divida_Liquida": formatar_moeda_grande(divida_liquida, moeda_balanco),
            "Divida_Liquida_sobre_EBITDA": f"{br(alavancagem)}x" if alavancagem is not None else "N/D",
            "Liquidez_Corrente": arredondar(liquidez),
            "Fluxo_Caixa_Livre": formatar_moeda_grande(info.get("freeCashflow"), moeda_balanco),
        },
        "Proventos": {
            "Dividend_Yield_Real_12m": f"{br(dy_12m)}%",
            "Total_Pago_12m_por_Cota": f"{simbolo} {br(dividendos_12m)}",
            "Frequencia_Pagamentos_12m": f"{qtd_pagamentos} pagamentos nos últimos 12 meses",
            "Payout_Ratio": formatar_percentual(info.get("payoutRatio")),
        },
        "Numeros": {
            "preco_atual": preco,
            "variacao_dia_pct": round(variacao_dia_pct, 2),
            "vol_financeiro_dia": vol_financeiro_dia,
            "min_52s": min_52s,
            "max_52s": max_52s,
            "pos_52s": round(pos_52s, 1),
            "dividendos_12m": dividendos_12m,
            "dy_12m": dy_12m,
            "graham": graham,
            "obs_graham": obs_graham,
            "bazin": bazin,
            "obs_bazin": obs_bazin,
            "tipo": tipo,
        },
    }


def gerar_resumo_tecnico_local(dados: dict, motivo_fallback: str = "") -> str:
    ide = dados["Identificacao"]
    val = dados["Valuation"]
    rent = dados["Rentabilidade_e_Eficiencia"]
    end = dados["Endividamento_e_Caixa"]
    fluxo = dados["Fluxo_e_Volume"]
    prov = dados["Proventos"]
    n = dados["Numeros"]

    preco = n["preco_atual"]
    graham = n["graham"]
    bazin = n["bazin"]

    analise_val = f"O ativo **{ide['Ticker']}** negocia a **{dados['Cotacao_e_Mercado']['Preco_Atual']}**, apresentando P/L de **{val['P_L']}** e P/VP de **{val['P_VP']}**. "
    if graham:
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
        f"No pregão atual, o ativo registra variação de **{dados['Cotacao_e_Mercado']['Variacao_Dia']}** com volume financeiro estimado em "
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


def gerar_relatorio_ia(dados: dict) -> tuple[str, str]:
    if client is None:
        return gerar_resumo_tecnico_local(dados, "Chave de API não configurada"), "Motor Algorítmico Local"

    dados_prompt = {k: v for k, v in dados.items() if k != "Numeros"}

    prompt = f"""Você é um analista fundamentalista sênior (CNPI) especializado na B3.
Analise de forma crítica, técnica e independente os dados abaixo, extraídos da B3/Yahoo Finance.
Interprete os indicadores conjuntamente (Valuation, Fluxo de Volume de Entrada/Saída, Balanço e Proventos).

REGRAS:
1. Use somente os dados fornecidos. Não invente números ou eventos.
2. Analise também o Fluxo de Volume (se há pressão compradora/vendedora e liquidez nos últimos pregões).
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
            return gerar_resumo_tecnico_local(dados, "Resposta vazia da API"), "Motor Algorítmico Local"
        return texto, MODELO_GEMINI
    except Exception as e:
        msg_erro = str(e)
        if "429" in msg_erro or "quota" in msg_erro.lower():
            erro_curto = "Limite de cota da API atingido"
        elif "404" in msg_erro or "not found" in msg_erro.lower():
            erro_curto = f"Modelo '{MODELO_GEMINI}' inválido no .env"
        else:
            erro_curto = type(e).__name__
        return gerar_resumo_tecnico_local(dados, erro_curto), f"Motor Algorítmico ({erro_curto})"