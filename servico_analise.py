"""
Serviço central do Investidor IA.
Extrai fundamentos, documentação contábil e fluxo de volume da B3 (via Yahoo Finance)
e gera parecer com o Gemini (com fallback algorítmico caso a cota da API esgote).
"""
from concurrent.futures import ThreadPoolExecutor
import copy
import datetime
import json
import math
import os
import re
import time
import unicodedata
import urllib.request
import zoneinfo
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors

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
FUSO_BRASILIA = zoneinfo.ZoneInfo("America/Sao_Paulo")


def agora_brasilia() -> datetime.datetime:
    """Retorna o datetime atual no fuso horário oficial de Brasília (America/Sao_Paulo)."""
    return datetime.datetime.now(datetime.timezone.utc).astimezone(FUSO_BRASILIA)


def em_horario_pregao_b3(dt_brasilia: datetime.datetime | None = None) -> bool:
    """Retorna True se o instante informado cair em dia útil entre 10h e 17h (horário de Brasília)."""
    dt = dt_brasilia or agora_brasilia()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FUSO_BRASILIA)
    else:
        dt = dt.astimezone(FUSO_BRASILIA)
    return dt.weekday() < 5 and (10 <= dt.hour < 17)
# Aceita tickers padrão B3 (4 caracteres de emissor terminando em letra + 1 ou 2 dígitos: PETR4, B3SA3, TAEE11, G2DI33, AAPL34)
PADRAO_TICKER = re.compile(r"^[A-Z][A-Z0-9]{2}[A-Z]\d{1,2}$")
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


class EntidadeNaoListada(ErroAtivo):
    """Cooperativa de crédito ou empresa pública não listada na B3."""

    def __init__(self, entidade: dict):
        self.entidade = entidade
        self.info = entidade
        msg = (
            f"{entidade.get('nome', 'Esta instituição')} é uma {entidade.get('tipo_entidade', 'entidade não listada')} "
            "e não possui ações na B3. Veja as alternativas do setor."
        )
        super().__init__(msg)


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


# ---------------------------------------------------------------------------
# Mapeamento de Marcas Populares do Cotidiano -> Tickers B3 (R3 / Anti-Guru)
# ---------------------------------------------------------------------------
MAPA_MARCAS_COTIDIANO: dict[str, dict[str, str]] = {
    "magalu": {"ticker": "MGLU3", "nome": "Magazine Luiza (Magalu) ON", "marca": "Magalu"},
    "magazine luiza": {"ticker": "MGLU3", "nome": "Magazine Luiza ON", "marca": "Magazine Luiza"},
    "lojas luiza": {"ticker": "MGLU3", "nome": "Magazine Luiza ON", "marca": "Lojas Luiza"},
    "posto ipiranga": {"ticker": "UGPA3", "nome": "Ultrapar (Postos Ipiranga) ON", "marca": "Posto Ipiranga"},
    "postos ipiranga": {"ticker": "UGPA3", "nome": "Ultrapar (Postos Ipiranga) ON", "marca": "Postos Ipiranga"},
    "ipiranga": {"ticker": "UGPA3", "nome": "Ultrapar (Ipiranga) ON", "marca": "Ipiranga"},
    "ultrapar": {"ticker": "UGPA3", "nome": "Ultrapar ON", "marca": "Ultrapar"},
    "banco do brasil": {"ticker": "BBAS3", "nome": "Banco do Brasil ON", "marca": "Banco do Brasil"},
    "ambev": {"ticker": "ABEV3", "nome": "Ambev ON", "marca": "Ambev"},
    "skol": {"ticker": "ABEV3", "nome": "Ambev (Skol) ON", "marca": "Skol"},
    "brahma": {"ticker": "ABEV3", "nome": "Ambev (Brahma) ON", "marca": "Brahma"},
    "antarctica": {"ticker": "ABEV3", "nome": "Ambev (Antarctica) ON", "marca": "Antarctica"},
    "casas bahia": {"ticker": "BHIA3", "nome": "Casas Bahia ON", "marca": "Casas Bahia"},
    "ponto frio": {"ticker": "BHIA3", "nome": "Casas Bahia (Ponto Frio) ON", "marca": "Ponto Frio"},
    "via varejo": {"ticker": "BHIA3", "nome": "Casas Bahia (Via Varejo) ON", "marca": "Via Varejo"},
    "atacadao": {"ticker": "CRFB3", "nome": "Carrefour Brasil (Atacadão) ON", "marca": "Atacadão"},
    "carrefour": {"ticker": "CRFB3", "nome": "Carrefour Brasil ON", "marca": "Carrefour"},
    "drogasil": {"ticker": "RADL3", "nome": "Raia Drogasil (Drogasil) ON", "marca": "Drogasil"},
    "droga raia": {"ticker": "RADL3", "nome": "Raia Drogasil ON", "marca": "Droga Raia"},
    "raia drogasil": {"ticker": "RADL3", "nome": "Raia Drogasil ON", "marca": "Raia Drogasil"},
    "posto shell": {"ticker": "RAIZ4", "nome": "Raízen (Postos Shell) PN", "marca": "Posto Shell"},
    "postos shell": {"ticker": "RAIZ4", "nome": "Raízen (Postos Shell) PN", "marca": "Postos Shell"},
    "shell": {"ticker": "RAIZ4", "nome": "Raízen (Shell) PN", "marca": "Shell"},
    "shell brasil": {"ticker": "RAIZ4", "nome": "Raízen (Shell Brasil) PN", "marca": "Shell Brasil"},
    "posto br": {"ticker": "VBBR3", "nome": "Vibra Energia (Postos BR) ON", "marca": "Posto BR"},
    "postos br": {"ticker": "VBBR3", "nome": "Vibra Energia (Postos BR) ON", "marca": "Postos BR"},
    "br distribuidora": {"ticker": "VBBR3", "nome": "Vibra Energia (BR Distribuidora) ON", "marca": "BR Distribuidora"},
    "vibra": {"ticker": "VBBR3", "nome": "Vibra Energia ON", "marca": "Vibra"},
    "pao de acucar": {"ticker": "PCAR3", "nome": "GPA (Pão de Açúcar) ON", "marca": "Pão de Açúcar"},
    "extra supermercado": {"ticker": "PCAR3", "nome": "GPA (Extra Supermercado) ON", "marca": "Extra Supermercado"},
    "smart fit": {"ticker": "SMFT3", "nome": "Smart Fit ON", "marca": "Smart Fit"},
    "smartfit": {"ticker": "SMFT3", "nome": "Smart Fit ON", "marca": "SmartFit"},
    "nubank": {"ticker": "ROXO34", "nome": "Nu Holdings (Nubank) BDR", "marca": "Nubank"},
    "roxinho": {"ticker": "ROXO34", "nome": "Nu Holdings (Nubank / Roxinho) BDR", "marca": "Roxinho"},
    "vale": {"ticker": "VALE3", "nome": "Vale ON", "marca": "Vale"},
    "vale do rio doce": {"ticker": "VALE3", "nome": "Vale ON", "marca": "Vale do Rio Doce"},
    "cvrd": {"ticker": "VALE3", "nome": "Vale ON", "marca": "CVRD"},
}

# ---------------------------------------------------------------------------
# Cooperativas de Crédito e Entidades Não Listadas com Tratamento Didático
# ---------------------------------------------------------------------------
MAPA_NAO_LISTADAS_COOPERATIVAS: dict[str, dict] = {
    "sicredi": {
        "termo": "Sicredi",
        "nome": "Sicredi",
        "tipo_entidade": "Cooperativa de Crédito",
        "titulo": "Sicredi é uma Cooperativa de Crédito, não uma S.A. de Capital Aberto",
        "resumo": "Você não encontrará ações do Sicredi na B3 porque seu modelo societário é o cooperativismo financeiro.",
        "explicacao": (
            "No Sicredi, os clientes são cooperados e sócios da instituição local, participando da distribuição "
            "anual de resultados (as chamadas 'sobras') proporcionalmente ao volume de negócios realizados com a cooperativa. "
            "Diferente das Sociedades Anônimas (S.A.), cooperativas de crédito não emitem ações para livre negociação em Bolsa de Valores. "
            "Se você busca investir no setor bancário e do agronegócio com liquidez diária e dividendos em dinheiro, a B3 conta com grandes bancos listados."
        ),
        "alternativas": [
            {
                "ticker": "BBAS3",
                "nome": "Banco do Brasil",
                "setor": "Bancos / Crédito Rural",
                "motivo": "Líder absoluto no crédito ao Agronegócio e produtor rural brasileiro, com alto pagamento histórico de dividendos.",
            },
            {
                "ticker": "ITUB4",
                "nome": "Itaú Unibanco",
                "setor": "Bancos / Varejo e Atacado",
                "motivo": "Maior banco privado da América Latina, com reconhecida gestão de crédito, solidez de balanço e elevado ROE.",
            },
            {
                "ticker": "BBDC4",
                "nome": "Bradesco",
                "setor": "Bancos / Varejo e Interior",
                "motivo": "Enorme capilaridade no interior do Brasil, concorrendo de perto com a rede física e atendimento das cooperativas.",
            },
            {
                "ticker": "SANB11",
                "nome": "Santander Brasil",
                "setor": "Bancos / Crédito Geral e Agro",
                "motivo": "Forte presença em crédito rural no Sul e Centro-Oeste, com foco em médias empresas e crédito consignado.",
            },
        ],
    },
    "sicoob": {
        "termo": "Sicoob",
        "nome": "Sicoob",
        "tipo_entidade": "Cooperativa de Crédito",
        "titulo": "Sicoob é um Sistema de Cooperativas de Crédito, não uma S.A.",
        "resumo": "O Sicoob funciona sob o modelo cooperativo, portanto suas cotas de participação não são ações de Bolsa.",
        "explicacao": (
            "O Sicoob é o maior sistema cooperativo financeiro do Brasil. Seus correntistas são associados com direito a voto "
            "e cotas-partes de capital da cooperativa local, recebendo sobras financeiras anuais. Como entidade cooperativa, "
            "não possui ações negociadas no pregão da B3. Para investir em instituições financeiras com ações em Bolsa, veja as alternativas abaixo."
        ),
        "alternativas": [
            {
                "ticker": "BBAS3",
                "nome": "Banco do Brasil",
                "setor": "Bancos / Crédito Rural",
                "motivo": "Principal concorrente do cooperativismo em financiamento produtivo, infraestrutura regional e crédito rural.",
            },
            {
                "ticker": "ITUB4",
                "nome": "Itaú Unibanco",
                "setor": "Bancos / Varejo e Atacado",
                "motivo": "Referência em eficiência operacional, tecnologia bancária e consistência na entrega de resultados aos acionistas.",
            },
            {
                "ticker": "BBDC4",
                "nome": "Bradesco",
                "setor": "Bancos / Varejo e Interior",
                "motivo": "Ampla rede física de agências em cidades de pequeno e médio porte em todos os estados brasileiros.",
            },
            {
                "ticker": "SANB11",
                "nome": "Santander Brasil",
                "setor": "Bancos / Crédito Geral e Agro",
                "motivo": "Atuação relevante no agronegócio e forte operação em carteiras de crédito comercial e CDC.",
            },
        ],
    },
    "cresol": {
        "termo": "Cresol",
        "nome": "Cresol",
        "tipo_entidade": "Cooperativa de Crédito",
        "titulo": "Cresol é um Sistema Cooperativo Solidário e não negocia ações na B3",
        "resumo": "A Cresol é especializada no cooperativismo de crédito e apoio à agricultura familiar, sem ações em Bolsa.",
        "explicacao": (
            "Nascida com foco no fortalecimento da agricultura familiar e pequenos produtores, a Cresol é regida pela legislação "
            "das cooperativas (Lei nº 5.764/1971). Seus membros são cooperados locais e participam das assembleias e sobras anuais. "
            "Para ter exposição ao setor de crédito e agronegócio com ações líquidas que pagam dividendos em corretoras, consulte os bancos da B3."
        ),
        "alternativas": [
            {
                "ticker": "BBAS3",
                "nome": "Banco do Brasil",
                "setor": "Bancos / Crédito Rural",
                "motivo": "Líder incontestável no Plano Safra e crédito rural direcionado à agricultura e cooperativismo.",
            },
            {
                "ticker": "ITUB4",
                "nome": "Itaú Unibanco",
                "setor": "Bancos / Varejo e Atacado",
                "motivo": "Carteira de crédito robusta e alta rentabilidade com sólida remuneração aos investidores.",
            },
            {
                "ticker": "BBDC4",
                "nome": "Bradesco",
                "setor": "Bancos / Varejo e Interior",
                "motivo": "Pioneiro na bancarização de pequenos municípios e crédito a pessoas físicas.",
            },
            {
                "ticker": "SANB11",
                "nome": "Santander Brasil",
                "setor": "Bancos / Crédito Geral e Agro",
                "motivo": "Forte expansão em lojas agro e atendimento a cooperados e produtores do campo.",
            },
        ],
    },
    "caixa economica": {
        "termo": "Caixa Econômica",
        "nome": "Caixa Econômica Federal",
        "tipo_entidade": "Empresa Pública Federal",
        "titulo": "A Caixa Econômica Federal é 100% estatal, mas sua subsidiária CXSE3 tem ações na B3",
        "resumo": "A Caixa é uma empresa pública do Governo Federal sem ações na Bolsa, porém sua seguradora é listada.",
        "explicacao": (
            "A Caixa Econômica Federal (CEF) é uma instituição financeira pública unipessoal, de propriedade exclusiva da União. "
            "Por lei, seu capital não é aberto a acionistas privados na Bolsa. Contudo, em 2021 a Caixa abriu o capital da sua divisão "
            "de seguridade: a Caixa Seguridade (CXSE3), que hoje é uma das maiores pagadoras de dividendos da B3. "
            "Além disso, o Banco do Brasil (BBAS3) é a grande alternativa de banco comercial de controle público com capital aberto."
        ),
        "alternativas": [
            {
                "ticker": "CXSE3",
                "nome": "Caixa Seguridade",
                "setor": "Seguros e Previdência",
                "motivo": "Subsidiária listada da Caixa que distribui grande parcela de lucros em dividendos aos acionistas na B3.",
            },
            {
                "ticker": "BBAS3",
                "nome": "Banco do Brasil",
                "setor": "Bancos / Comercial e Público",
                "motivo": "Instituição financeira de controle público listada em Bolsa, atuando com liderança em crédito e poupança.",
            },
            {
                "ticker": "ITUB4",
                "nome": "Itaú Unibanco",
                "setor": "Bancos / Varejo e Imobiliário",
                "motivo": "Grande concorrente privado em crédito habitacional e contas correntes no território nacional.",
            },
            {
                "ticker": "BBDC4",
                "nome": "Bradesco",
                "setor": "Bancos / Varejo e Popular",
                "motivo": "Atuação massiva no crédito ao consumidor, cartões e benefícios sociais em parceria governamental.",
            },
        ],
    },
}
MAPA_NAO_LISTADAS_COOPERATIVAS["caixa"] = MAPA_NAO_LISTADAS_COOPERATIVAS["caixa economica"]
MAPA_NAO_LISTADAS_COOPERATIVAS["caixa economica federal"] = MAPA_NAO_LISTADAS_COOPERATIVAS["caixa economica"]
MAPA_NAO_LISTADAS_COOPERATIVAS["cef"] = MAPA_NAO_LISTADAS_COOPERATIVAS["caixa economica"]


def obter_entidade_nao_listada(termo: str) -> dict | None:
    """Retorna dados didáticos caso o termo corresponda a uma cooperativa ou entidade não listada."""
    if not termo:
        return None
    chave = _sem_acentos(termo.strip())
    if chave in MAPA_NAO_LISTADAS_COOPERATIVAS:
        return dict(MAPA_NAO_LISTADAS_COOPERATIVAS[chave])
    for k, v in MAPA_NAO_LISTADAS_COOPERATIVAS.items():
        if k == chave or k in chave.split() or _sem_acentos(v["nome"]) == chave:
            return dict(v)
    return None


def _limpar_nome_yahoo(nome: str) -> str:
    """Remove espaços duplicados dos nomes curtos do Yahoo (ex.: 'SANEPAR     PN      N2')."""
    return re.sub(r"\s+", " ", (nome or "").strip())


def _classificar_prioridade_busca(chave: str, ticker: str, nome: str) -> int | None:
    """
    Classifica o resultado em:
    1 = ticker exato
    2 = ticker começa com o termo
    3 = alguma palavra do nome começa com o termo
    4 = ticker ou nome contém o termo
    None = não corresponde
    """
    tk_low = (ticker or "").lower()
    nome_norm = _sem_acentos(nome)
    if tk_low == chave:
        return 1
    if tk_low.startswith(chave):
        return 2
    palavras = re.findall(r"[a-z0-9]+", nome_norm)
    if nome_norm.startswith(chave) or any(p.startswith(chave) for p in palavras):
        return 3
    if chave in tk_low or chave in nome_norm:
        return 4
    return None


def _buscar_candidatos_ordenados(termo: str, limite: int = 8) -> list[dict]:
    termo_limpo = re.sub(r"\s+", " ", (termo or "").strip())
    if len(termo_limpo) < 2:
        return []
    chave = _sem_acentos(termo_limpo)

    agora = time.monotonic()
    em_cache = _CACHE_BUSCA.get(chave)
    if em_cache and agora - em_cache[0] < _CACHE_BUSCA_TTL_SEGUNDOS:
        return em_cache[1][:limite]

    candidatos_com_ordem: list[tuple[int, int, dict]] = []
    vistos: set[str] = set()
    ordem = 0

    # 1. Verifica cooperativas e entidades não listadas
    for k_nl, v_nl in MAPA_NAO_LISTADAS_COOPERATIVAS.items():
        nome_nl = v_nl["nome"]
        tk_id = nome_nl.upper()
        if tk_id in vistos:
            continue
        if k_nl == chave or _sem_acentos(nome_nl) == chave:
            vistos.add(tk_id)
            candidatos_com_ordem.append((
                1,
                ordem,
                {
                    "ticker": nome_nl,
                    "nome": f"{nome_nl} ({v_nl['tipo_entidade']} - Ver alternativas na B3)",
                    "tipo": "cooperativa" if "cooperativa" in v_nl["tipo_entidade"].lower() else "nao_listada",
                    "_grupo": 1,
                },
            ))
            ordem += 1
        elif k_nl.startswith(chave) or any(p.startswith(chave) for p in k_nl.split()):
            vistos.add(tk_id)
            candidatos_com_ordem.append((
                2,
                ordem,
                {
                    "ticker": nome_nl,
                    "nome": f"{nome_nl} ({v_nl['tipo_entidade']} - Ver alternativas na B3)",
                    "tipo": "cooperativa" if "cooperativa" in v_nl["tipo_entidade"].lower() else "nao_listada",
                    "_grupo": 2,
                },
            ))
            ordem += 1

    # 2. Verifica sinônimos de marcas populares do cotidiano
    for k_marca, v_marca in MAPA_MARCAS_COTIDIANO.items():
        tk = v_marca["ticker"]
        nome_marca = v_marca["nome"]
        if tk in vistos:
            continue
        if k_marca == chave:
            vistos.add(tk)
            candidatos_com_ordem.append((1, ordem, {"ticker": tk, "nome": nome_marca, "_grupo": 1}))
            ordem += 1
        elif k_marca.startswith(chave):
            vistos.add(tk)
            candidatos_com_ordem.append((2, ordem, {"ticker": tk, "nome": nome_marca, "_grupo": 2}))
            ordem += 1
        elif any(p.startswith(chave) for p in k_marca.split()):
            vistos.add(tk)
            candidatos_com_ordem.append((3, ordem, {"ticker": tk, "nome": nome_marca, "_grupo": 3}))
            ordem += 1

    # 3. Busca no catálogo local da B3
    for tk, nome in CATALOGO_B3:
        if tk in vistos:
            continue
        grupo = _classificar_prioridade_busca(chave, tk, nome)
        if grupo is not None:
            vistos.add(tk)
            candidatos_com_ordem.append((grupo, ordem, {"ticker": tk, "nome": nome, "_grupo": grupo}))
            ordem += 1

    # 4. Complementa com o Yahoo Finance se ainda houver espaço
    if len(candidatos_com_ordem) < limite:
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
            grupo = _classificar_prioridade_busca(chave, ticker, nome) or 4
            candidatos_com_ordem.append((grupo, ordem, {"ticker": ticker, "nome": nome, "_grupo": grupo}))
            ordem += 1

    candidatos_com_ordem.sort(key=lambda item: (item[0], item[1]))
    ordenados = [item[2] for item in candidatos_com_ordem]

    if len(_CACHE_BUSCA) >= _CACHE_BUSCA_MAX_ITENS:
        _CACHE_BUSCA.clear()
    if ordenados:
        _CACHE_BUSCA[chave] = (agora, ordenados)
    return ordenados[:limite]


def buscar_ativos(termo: str, limite: int = 8) -> list[dict]:
    """
    Procura ativos da B3 pelo nome da empresa ou parte do ticker, ordenando por:
    (1) ticker exato / marca exata, (2) prefixo de ticker / marca, (3) prefixo de palavra no nome, (4) contém.
    Retorna uma lista de {"ticker": "...", "nome": "...", ...}.
    """
    candidatos = _buscar_candidatos_ordenados(termo, limite=limite)
    resultados = []
    for c in candidatos:
        item = {"ticker": c["ticker"], "nome": c["nome"]}
        if "tipo" in c:
            item["tipo"] = c["tipo"]
        resultados.append(item)
    return resultados


def resolver_ticker(entrada_usuario: str) -> str:
    """
    Aceita tanto o ticker (PETR4, sapr4, SAPR4F) quanto o nome da empresa (Sanepar, Itaú, WEG)
    ou marcas do cotidiano (Magalu, Posto Ipiranga, Casas Bahia).
    Para cooperativas ou entidades não listadas (ex: Sicredi, Sicoob), levanta EntidadeNaoListada.
    """
    entrada = (entrada_usuario or "").strip()
    chave = _sem_acentos(entrada)

    # 1. Intercepta cooperativas e entidades não listadas com tratamento didático
    entidade_nl = obter_entidade_nao_listada(chave)
    if entidade_nl:
        raise EntidadeNaoListada(entidade_nl)

    # 2. Ticker direto válido
    try:
        return normalizar_ticker(entrada)
    except ErroAtivo:
        pass

    # 3. Mapeamento direto de marcas populares do cotidiano
    if chave in MAPA_MARCAS_COTIDIANO:
        return MAPA_MARCAS_COTIDIANO[chave]["ticker"]

    # 4. Busca no catálogo local e Yahoo Finance
    candidatos = _buscar_candidatos_ordenados(entrada, limite=8)
    if not candidatos:
        raise ErroAtivo(
            f"Não encontramos nenhum ativo da B3 para '{entrada_usuario}'. "
            "Tente o nome da empresa (ex.: Sanepar, Petrobras, Itaú) ou o ticker (ex.: SAPR4, PETR4, ITUB4)."
        )

    melhor = candidatos[0]
    if len(candidatos) == 1 or melhor.get("_grupo") in (1, 2):
        return melhor["ticker"]

    mesmo_grupo = [c for c in candidatos if c.get("_grupo") == melhor.get("_grupo")]
    lista_exibicao = mesmo_grupo if len(mesmo_grupo) > 1 else candidatos
    opcoes_txt = ", ".join(f"{c['ticker']} {c['nome']}" for c in lista_exibicao)
    raise ErroAtivo(f"Você quis dizer: {opcoes_txt}")


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


def extrair_fluxo_pregoes(ativo, vol_medio_referencia: int = 0, hist_pre_carregado=None) -> list[dict]:
    """
    Busca 6 dias de histórico e exibe até 5 pregões mais recentes com valores numéricos crus,
    garantindo que a variação do pregão mais antigo exibido também seja calculada sobre o fechamento anterior.
    """
    pregoes = []
    try:
        if hist_pre_carregado is not None:
            hist = hist_pre_carregado
        elif ativo is not None:
            hist = ativo.history(period="6d")
        else:
            hist = None
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

                if var_dia > 0.15:
                    direcao = "Dia de alta"
                elif var_dia < -0.15:
                    direcao = "Dia de queda"
                else:
                    direcao = "Estável"

                if vol_medio_referencia and vol_medio_referencia > 0 and vol > vol_medio_referencia:
                    direcao = f"{direcao} com volume acima da média"

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
    return pregoes[:5]


def _coletar_historico_seguro(ativo):
    try:
        return ativo.history(period="6d")
    except Exception:
        return None


def _coletar_historico_mensal_5a(ativo) -> list[dict]:
    """
    Carrega até 5 anos de histórico mensal (fechamentos e dividendos por mês)
    em um objeto yf.Ticker isolado para alimentar o Simulador Realidade vs. Guru.
    """
    try:
        try:
            hist = ativo.history(period="5y", interval="1mo", auto_adjust=False)
        except TypeError:
            hist = ativo.history(period="5y")
        if hist is None or hist.empty:
            return []

        divs_por_mes: dict[str, float] = {}
        if "Dividends" not in hist.columns:
            try:
                s_div = getattr(ativo, "dividends", None)
                if s_div is not None and not s_div.empty:
                    for dt_idx, val_d in s_div.items():
                        chave_m = dt_idx.strftime("%m/%Y")
                        divs_por_mes[chave_m] = divs_por_mes.get(chave_m, 0.0) + float(val_d or 0.0)
            except Exception:
                pass

        registros = []
        for data_idx, linha in hist.iterrows():
            fech = numero(linha.get("Close"))
            if fech is None or fech <= 0:
                continue
            mes_ano = data_idx.strftime("%m/%Y")
            if "Dividends" in hist.columns:
                div_mes = numero(linha.get("Dividends")) or 0.0
            else:
                div_mes = divs_por_mes.get(mes_ano, 0.0)
            registros.append(
                {
                    "mes_ano": mes_ano,
                    "data_iso": data_idx.strftime("%Y-%m-%d"),
                    "fechamento": round(float(fech), 4),
                    "dividendos": round(float(div_mes), 4),
                }
            )
        return registros
    except Exception:
        return []



# ===========================================================================
# BLOCO 1 (Dossiê Aberto): Catálogo Curado de Perfis Didáticos da B3
# ===========================================================================
MAPA_PERFIL_DIDATICO = {
    "PETR4": {
        "o_que_faz": "Produz, refina e comercializa petróleo, combustíveis (gasolina, diesel), gás natural e derivados de alta energia.",
        "para_quem_vende": "Distribuidoras de combustível em todo o Brasil, refinarias internacionais e indústrias químicas.",
        "como_ganha_dinheiro": "Com a margem entre o baixo custo de extrair o barril de petróleo no pré-sal e a venda de diesel e gasolina a preços de mercado internacional.",
        "estrutura_controle": "Controlada pela União Federal (Governo Brasileiro), com o restante das ações negociado com investidores privados nacionais e globais.",
        "tipo_controle": "Estatal de Capital Misto",
        "contexto_vida_real": "Você consome Petrobras sempre que abastece o carro no posto, viaja de ônibus ou consome mercadorias transportadas por caminhões a diesel.",
        "marcas_cotidiano": "Combustíveis Petrobras, Lubrificantes Lubrax, Gás Liquigás (marca histórica), Plataformas do Pré-Sal.",
    },
    "VALE3": {
        "o_que_faz": "Extrai e exporta minério de ferro de altíssima pureza, pelotas, níquel e cobre para as maiores siderúrgicas do mundo.",
        "para_quem_vende": "Siderúrgicas internacionais (especialmente na China, Japão e Europa) que transformam o minério em aço.",
        "como_ganha_dinheiro": "Extrai o minério em Carajás e Minas Gerais a baixo custo e vende em dólares por navios graneleiros nos portos.",
        "estrutura_controle": "Sem controlador único definido (Novo Mercado), com capital pulverizado entre fundos globais e locais.",
        "tipo_controle": "Corporation Privada Pulverizada",
        "contexto_vida_real": "O minério da Vale está na estrutura de aço do seu prédio, no chassi do seu carro e nos eletrodomésticos da sua cozinha.",
        "marcas_cotidiano": "Minério de Ferro Carajás, Pelotas de Tubarão, Estrada de Ferro Vitória a Minas.",
    },
    "ITUB4": {
        "o_que_faz": "Maior banco privado da América Latina, oferecendo contas, cartões de crédito, empréstimos, investimentos e seguros.",
        "para_quem_vende": "Pessoas físicas de todas as faixas de renda, pequenas e médias empresas e grandes corporações multinacionais.",
        "como_ganha_dinheiro": "Spread bancário (diferença entre o que paga para captar dinheiro e o que cobra em juros de empréstimos) e tarifas de serviços.",
        "estrutura_controle": "Controlado pelas famílias Setubal, Villela e Moreira Salles via Itaúsa (ITSA4).",
        "tipo_controle": "Privado Nacional Familiar",
        "contexto_vida_real": "Você é cliente do Itaú quando passa o cartão na maquininha da Rede, acessa o app do banco ou financia seu veículo pelo iCarros.",
        "marcas_cotidiano": "Itaú, Personnalité, Itaú BBA, Maquininhas Rede, Iti.",
    },
    "BBDC4": {
        "o_que_faz": "Um dos maiores conglomerados financeiros do país, com presença em agências por todo o interior e liderança em seguros.",
        "para_quem_vende": "Famílias de todas as classes sociais, aposentados, servidores públicos, comerciantes e produtores rurais.",
        "como_ganha_dinheiro": "Cobrança de juros em empréstimos e financiamentos, além de grande lucro gerado pelo Grupo Bradesco Seguros e cartões.",
        "estrutura_controle": "Controlado pela Fundação Bradesco (instituição beneficente de educação) e pela holding Cidade de Deus.",
        "tipo_controle": "Privado Institucional / Fundação",
        "contexto_vida_real": "Você interage com o Bradesco quando recebe seu salário, contrata o plano Bradesco Saúde ou utiliza o banco digital Next.",
        "marcas_cotidiano": "Bradesco, Bradesco Seguros, Bradesco Saúde, Next, Ágora Investimentos, Digio.",
    },
    "BBAS3": {
        "o_que_faz": "Banco mais antigo do Brasil, líder absoluto em crédito ao agronegócio (Plano Safra) e principal operador financeiro do setor público.",
        "para_quem_vende": "Produtores rurais (pequenos a gigantes), servidores públicos, aposentados e empresas de comércio e serviços.",
        "como_ganha_dinheiro": "Juros de crédito agro e consignado (baixo risco de inadimplência), prestação de serviços governamentais e seguros via BBSE3.",
        "estrutura_controle": "Controlado pela União Federal (Governo Brasileiro), com ações negociadas livremente no Novo Mercado da B3.",
        "tipo_controle": "Estatal de Capital Misto (Novo Mercado)",
        "contexto_vida_real": "Você vê o Banco do Brasil na agência da sua cidade, no custeio das safras de soja e milho que chegam ao seu prato e no app do BB.",
        "marcas_cotidiano": "Banco do Brasil, Ourocard, BB Américas, Livelo (parceria), BB Investimentos.",
    },
    "SANB11": {
        "o_que_faz": "Filial brasileira do gigante bancário espanhol Santander, com forte operação em crédito ao consumo, cartões e veículos.",
        "para_quem_vende": "Pessoas físicas de classe média e alta, pequenas empresas, frotistas e grandes importadores/exportadores.",
        "como_ganha_dinheiro": "Spread em crédito de veículos e consignado, tarifas de gestão de cartões de crédito e serviços corporativos.",
        "estrutura_controle": "Controlado pelo grupo espanhol Banco Santander S.A.",
        "tipo_controle": "Subsidiária de Grupo Estrangeiro",
        "contexto_vida_real": "Você encontra o Santander nas agências vermelhas de shoppings, no app Santander Way ou financiando veículos pela Santander Financiamentos.",
        "marcas_cotidiano": "Santander, Santander Select, Santander Way, Getnet, Toro Investimentos.",
    },
    "BPAC11": {
        "o_que_faz": "Maior banco de investimentos da América Latina, atuando em fusões, aquisições, assessoria patrimonial e crédito a grandes empresas.",
        "para_quem_vende": "Famílias de altíssima renda (Private Banking), investidores institucionais, fundos e grandes companhias abertas.",
        "como_ganha_dinheiro": "Taxas de administração e performance de fundos de investimento, comissões de IPOs e fusões, e intermediação financeira.",
        "estrutura_controle": "Controlado pelo grupo de sócios seniores (Partnership liderado por André Esteves).",
        "tipo_controle": "Partnership Privado Nacional",
        "contexto_vida_real": "Você se conecta ao BTG através da plataforma de investimentos BTG Pactual Banking, do app BTG ou de assessorias financeiras parceiras.",
        "marcas_cotidiano": "BTG Pactual, BTG Banking, Empiricus (grupo), Vítreo, Banco Pan (controlada).",
    },
    "BBSE3": {
        "o_que_faz": "Holding que concentra todos os negócios de seguros, previdência privada, capitalização e corretagem do Banco do Brasil.",
        "para_quem_vende": "Correntistas e produtores rurais que buscam seguro de vida, previdência, seguro agrícola ou seguro de colheitadeiras.",
        "como_ganha_dinheiro": "Recebe comissões e fatias de lucros das apólices de seguro vendidas nas agências e apps do Banco do Brasil com custo quase zero de infraestrutura.",
        "estrutura_controle": "Controlada pelo Banco do Brasil, com o restante das ações em livre circulação na B3.",
        "tipo_controle": "Subsidiária de Estatal / Capital Misto",
        "contexto_vida_real": "Você contrata a BB Seguridade sempre que faz um seguro de vida no Banco do Brasil ou um seguro agrícola para plantações.",
        "marcas_cotidiano": "Brasilseg, Brasilprev, Brasilcap, Ciclic, BB Corretora.",
    },
    "CXSE3": {
        "o_que_faz": "Holding que consolida as operações de seguros, previdência privada, capitalização e consórcios dentro dos canais da Caixa Econômica Federal.",
        "para_quem_vende": "Milhões de correntistas da Caixa, mutuários de crédito imobiliário habitacional e investidores de previdência.",
        "como_ganha_dinheiro": "Direito exclusivo de vender seguros em todas as agências e lotéricas da Caixa Econômica Federal, sem necessidade de construir lojas próprias.",
        "estrutura_controle": "Controlada pela Caixa Econômica Federal, com ações negociadas com investidores privados na B3.",
        "tipo_controle": "Subsidiária de Empresa Pública Federal",
        "contexto_vida_real": "Você paga à Caixa Seguridade o seguro habitacional obrigatório quando financia a casa própria pelo Minha Casa Minha Vida ou Caixa.",
        "marcas_cotidiano": "Caixa Seguros, Caixa Previdência, Caixa Consórcios, Caixa Capitalização.",
    },
    "PSSA3": {
        "o_que_faz": "Líder histórica em seguros de automóveis e residenciais no Brasil, expandindo para saúde e banco digital.",
        "para_quem_vende": "Proprietários de veículos, famílias protegendo residências e empresas contratando benefícios corporativos.",
        "como_ganha_dinheiro": "Prêmios de seguro recolhidos e resultado financeiro da aplicação das reservas técnicas no mercado de juros.",
        "estrutura_controle": "Controlada conjuntamente pela família Garfinkel e pelo Itaú Unibanco.",
        "tipo_controle": "Privado Nacional Compartilhado",
        "contexto_vida_real": "Você aciona a Porto Seguro quando precisa de guincho na estrada, chaveiro para sua casa ou troca de bateria.",
        "marcas_cotidiano": "Porto Seguro, Azul Seguros, Itaú Auto e Residência, Porto Bank.",
    },
    "B3SA3": {
        "o_que_faz": "A bolsa oficial de valores do Brasil, infraestrutura central de negociação, compensação e custódia de ações, títulos e derivativos.",
        "para_quem_vende": "Corretoras, bancos, gestoras de fundos, empresas emissoras de dívidas e investidores pessoas físicas.",
        "como_ganha_dinheiro": "Taxas de emolumentos por cada ordem executada, tarifas de custódia de ações e registro de financiamentos de veículos.",
        "estrutura_controle": "Sem controlador único (capital pulverizado no Novo Mercado).",
        "tipo_controle": "Corporation Privada Pulverizada",
        "contexto_vida_real": "Toda ação ou FII que você compra através de qualquer corretora brasileira é liquidada e guardada com segurança na B3.",
        "marcas_cotidiano": "B3, Tesouro Direto (parceria operacional), Cetip, Sinacor.",
    },
    "ROXO34": {
        "o_que_faz": "Maior banco digital do Ocidente (Nu Holdings), oferecendo conta corrente gratuita, cartão de crédito sem anuidade, empréstimos e investimentos.",
        "para_quem_vende": "Mais de 100 milhões de clientes no Brasil, México e Colômbia buscando simplicidade financeira pelo celular.",
        "como_ganha_dinheiro": "Tarifas de intercâmbio de cartão de crédito, juros de empréstimos pessoais e financiamento de fatura.",
        "estrutura_controle": "Fundadores David Vélez, Cristina Junqueira e Edward Wible detêm ações de supervoto (Classe B).",
        "tipo_controle": "Fundadores / BDR Listado na B3",
        "contexto_vida_real": "Você usa o roxinho do Nubank para pagar compras por aproximação, guardar dinheiro nas Caixinhas ou pagar boletos no app.",
        "marcas_cotidiano": "Nubank, Cartão Ultravioleta, NuInvest, Nu Limite Garantido, Nu México.",
    },
    "PRIO3": {
        "o_que_faz": "Maior petroleira independente do Brasil, especializada em revitalizar e operar campos maduros de petróleo em águas profundas.",
        "para_quem_vende": "Tradings globais de energia e refinarias internacionais que compram petróleo cru tipo brent.",
        "como_ganha_dinheiro": "Compra campos antigos de grandes petroleiras, reduz o custo de extração (lifting cost) drasticamente e vende em dólares.",
        "estrutura_controle": "Sem controlador único (capital pulverizado no Novo Mercado da B3).",
        "tipo_controle": "Corporation Privada Nacional",
        "contexto_vida_real": "A Prio produz centenas de milhares de barris diários que abastecem o mercado global e aumentam o superávit comercial do Brasil.",
        "marcas_cotidiano": "Prio, Campos de Frade, Wahoo, Albacora Leste.",
    },
    "RECV3": {
        "o_que_faz": "Maior produtora independente de petróleo e gás natural em campos terrestres (onshore) na Bacia do Recôncavo e Potiguar.",
        "para_quem_vende": "Refinarias privadas do Nordeste (Acelen) e distribuidoras estaduais de gás canalizado (Bahiagás, Potigás).",
        "como_ganha_dinheiro": "Extração em terra com custos operacionais muito baixos e contratos de longo prazo de fornecimento de gás.",
        "estrutura_controle": "Companhia listada no Novo Mercado da B3 com capital disperso entre fundos de investimento e fundadores.",
        "tipo_controle": "Privado Nacional (Novo Mercado)",
        "contexto_vida_real": "O gás da PetroReconcavo alimenta indústrias, veículos GNV e termelétricas em todo o Nordeste brasileiro.",
        "marcas_cotidiano": "PetroReconcavo, Polo Miranga, Polo Remanso.",
    },
    "BRAV3": {
        "o_que_faz": "Petroleira independente resultante da fusão entre Enauta e 3R Petroleum, com portfólio equilibrado em terra e mar.",
        "para_quem_vende": "Refinarias globais e distribuidoras de derivados de petróleo e gás natural no Brasil.",
        "como_ganha_dinheiro": "Escala combinada de produção no campo de Atlanta e bacias terrestres com aumento gradual de capacidade.",
        "estrutura_controle": "Sem controlador único (capital disperso no Novo Mercado da B3).",
        "tipo_controle": "Corporation Privada Nacional",
        "contexto_vida_real": "O petróleo extraído pelo navio-plataforma Atlanta da Brava Energia é exportado gerando divisas para o Brasil.",
        "marcas_cotidiano": "Brava Energia, Campo de Atlanta, Polo Macau.",
    },
    "UGPA3": {
        "o_que_faz": "Conglomerado industrial e de distribuição proprietário da rede de Postos Ipiranga e da distribuidora de gás Ultragaz.",
        "para_quem_vende": "Motoristas particulares, frotistas e famílias e indústrias que utilizam gás de cozinha e granel.",
        "como_ganha_dinheiro": "Margens na distribuição de combustíveis por milhares de postos franqueados e revenda de botijões de GLP.",
        "estrutura_controle": "Controlada pelo grupo Ultra (famílias Igel e acionistas de referência) com ações no Novo Mercado.",
        "tipo_controle": "Privado Nacional Tradicional",
        "contexto_vida_real": "Você abastece no Posto Ipiranga, compra lanche na loja AmPm ou troca o botijão de gás azul da Ultragaz.",
        "marcas_cotidiano": "Postos Ipiranga, Ultragaz, Lojas AmPm, Jet Oil, Ultracargo.",
    },
    "VBBR3": {
        "o_que_faz": "Maior distribuidora de combustíveis e lubrificantes do Brasil, antiga BR Distribuidora privatizada em 2019.",
        "para_quem_vende": "Milhares de postos com a bandeira Petrobras licenciada, companhias aéreas nos aeroportos e frotas industriais.",
        "como_ganha_dinheiro": "Giro rápido e escala gigantesca de caminhões-tanque abastecendo o país inteiro com margens por litro.",
        "estrutura_controle": "Sem controlador único (capital pulverizado no Novo Mercado da B3).",
        "tipo_controle": "Corporation Privada Pulverizada",
        "contexto_vida_real": "Você vê a Vibra nos postos Petrobras, nas lojas de conveniência BR Mania e no abastecimento de querosene dos aviões comerciais.",
        "marcas_cotidiano": "Postos Petrobras (marca licenciada), BR Mania, Lubrax, Vibra Aviation.",
    },
    "RAIZ4": {
        "o_que_faz": "Joint venture entre a Cosan e a multinacional Shell, líder mundial em açúcar e etanol de cana e distribuidora Shell no Brasil.",
        "para_quem_vende": "Motoristas nos postos Shell, indústrias globais de alimentos que compram açúcar e importadores de biocombustíveis.",
        "como_ganha_dinheiro": "Moagem de cana-de-açúcar para produzir etanol e açúcar em alta escala, aliada à distribuição nos postos Shell.",
        "estrutura_controle": "Controle compartilhado entre a Cosan S.A. e a Shell (Royal Dutch Shell).",
        "tipo_controle": "Privado Compartilhado Internacional",
        "contexto_vida_real": "Você abastece nos postos Shell com combustíveis Shell V-Power, consome o etanol nos carros flex ou compra nas lojas Shell Select.",
        "marcas_cotidiano": "Postos Shell, Shell V-Power, Lojas Shell Select, Raízen Bioenergia.",
    },
    "CSAN3": {
        "o_que_faz": "Uma das maiores holdings de infraestrutura e energia do Brasil, com participações em Raízen, Compass (gás), Rumo (ferrovias) e Moove.",
        "para_quem_vende": "Consumidores de energia, indústrias que utilizam gás canalizado e exportadores agrícolas de grãos.",
        "como_ganha_dinheiro": "Dividendos e valorização de suas empresas operacionais que lideram seus respectivos setores essenciais.",
        "estrutura_controle": "Controlada pelo empresário Rubens Ometto Silveira Mello.",
        "tipo_controle": "Privado Nacional / Grupo Controlador",
        "contexto_vida_real": "A Cosan está no combustível do seu carro (Raízen/Shell), no gás encanado da sua casa (Comgás) e nos trens de carga de soja (Rumo).",
        "marcas_cotidiano": "Cosan, Comgás, Rumo Logística, Moove (Mobil), Raízen.",
    },
    "TAEE11": {
        "o_que_faz": "Um dos maiores grupos privados de transmissão de energia elétrica do Brasil, operando milhares de quilômetros de linhas de alta tensão.",
        "para_quem_vende": "Concessionárias de geração e distribuidoras de energia que precisam transportar a eletricidade das usinas até as cidades.",
        "como_ganha_dinheiro": "Recebe a Receita Anual Permitida (RAP), uma renda regulada corrigida anualmente pela inflação (IPCA ou IGP-M) independentemente de quanta energia é consumida.",
        "estrutura_controle": "Controlada conjuntamente pela estatal mineira Cemig e pelo grupo colombiano ISA.",
        "tipo_controle": "Controle Compartilhado (Privado / Estatal)",
        "contexto_vida_real": "As linhas de transmissão da Taesa garantem que a energia gerada em usinas distantes chegue com estabilidade à tomada da sua casa.",
        "marcas_cotidiano": "Taesa, Linhas de Transmissão Novatrans, TSN, ETEO.",
    },
    "TRPL4": {
        "o_que_faz": "Principal concessionária de transmissão de energia do estado de São Paulo, responsável por transportar grande parte da energia elétrica do país.",
        "para_quem_vende": "Operador Nacional do Sistema (ONS) e distribuidoras como Enel, CPFL e Neoenergia que entregam energia aos lares paulistas.",
        "como_ganha_dinheiro": "Recebe remuneração regulada (RAP) pelo simples fato de manter suas subestações e torres ligadas e disponíveis 24 horas por dia.",
        "estrutura_controle": "Controlada pela ISA (Interconexión Eléctrica S.A., grupo Ecopetrol), com participação da Eletrobras.",
        "tipo_controle": "Privado Internacional",
        "contexto_vida_real": "Uma parcela relevante das lâmpadas acesas no estado de São Paulo é alimentada pelas torres e subestações da ISA Cteep.",
        "marcas_cotidiano": "ISA Cteep, Cteep, Linhas de Transmissão de São Paulo.",
    },
    "EGIE3": {
        "o_que_faz": "Maior geradora privada de energia renovável do Brasil, operando usinas hidrelétricas, solares, eólicas e linhas de transmissão.",
        "para_quem_vende": "Distribuidoras de energia pelo mercado regulado e indústrias livres que compram energia limpa por contratos de longo prazo.",
        "como_ganha_dinheiro": "Venda de contratos de energia com preços previsíveis e transporte de eletricidade em linhas próprias.",
        "estrutura_controle": "Controlada pelo grupo multinacional francês Engie S.A.",
        "tipo_controle": "Subsidiária de Grupo Estrangeiro",
        "contexto_vida_real": "A eletricidade limpa que liga as fábricas e ilumina residências é gerada por hidrelétricas e parques eólicos da Engie.",
        "marcas_cotidiano": "Engie Brasil, Usina de Itá, Usina Machadinho, Parque Eólico Umburanas.",
    },
    "CPFE3": {
        "o_que_faz": "Maior distribuidora de energia elétrica do Brasil em volume vendido, além de atuar em geração renovável e transmissão.",
        "para_quem_vende": "Mais de 10 milhões de clientes residenciais e industriais em cidades do interior de São Paulo, Rio Grande do Sul e Paraná.",
        "como_ganha_dinheiro": "Tarifa de distribuição de energia na conta de luz reajustada pela Aneel e contratos de venda de geração limpa.",
        "estrutura_controle": "Controlada pelo grupo estatal chinês State Grid.",
        "tipo_controle": "Subsidiária de Grupo Estrangeiro",
        "contexto_vida_real": "Se você mora em Campinas, Ribeirão Preto ou Serra Gaúcha, a CPFL é quem envia sua fatura de luz e conserta os fios elétricos.",
        "marcas_cotidiano": "CPFL Paulista, CPFL Piratininga, CPFL Santa Cruz, RGE Sul.",
    },
    "EQTL3": {
        "o_que_faz": "Grupo líder em recuperação operacional de distribuidoras de energia e saneamento, atuando em diversos estados brasileiros.",
        "para_quem_vende": "Milhões de consumidores no Maranhão, Pará, Piauí, Alagoas, Rio Grande do Sul, Goiás e Amapá.",
        "como_ganha_dinheiro": "Assume distribuidoras estatais deficitárias, corta perdas de energia, reduz custos e transforma em negócios eficientes.",
        "estrutura_controle": "Sem controlador único, com ações pulverizadas no Novo Mercado da B3.",
        "tipo_controle": "Corporation Privada Pulverizada",
        "contexto_vida_real": "A Equatorial leva energia a regiões do Norte, Nordeste, Centro-Oeste e Sul e também é acionista de referência da Sabesp.",
        "marcas_cotidiano": "Equatorial Maranhão, Equatorial Pará, Equatorial Goiás, CEEE-D, Sabesp (acionista de referência).",
    },
    "CMIG4": {
        "o_que_faz": "Concessionária integrada de energia de Minas Gerais, responsável pela geração, transmissão e distribuição de eletricidade.",
        "para_quem_vende": "Milhões de consumidores residenciais e indústrias em praticamente todos os municípios mineiros.",
        "como_ganha_dinheiro": "Contas de energia elétrica reguladas pela Aneel e dividendos de fatias societárias estratégicas (como a Taesa).",
        "estrutura_controle": "Controlada pelo Governo do Estado de Minas Gerais.",
        "tipo_controle": "Estatal Estadual de Capital Misto",
        "contexto_vida_real": "A Cemig ilumina cidades inteiras de Minas Gerais e garante a infraestrutura elétrica para a indústria e o agronegócio mineiro.",
        "marcas_cotidiano": "Cemig Distribuição, Cemig GT, Gasmig, Conta de Luz Cemig.",
    },
    "ELET3": {
        "o_que_faz": "Maior empresa de geração e transmissão de energia elétrica da América Latina, privatizada em 2022.",
        "para_quem_vende": "Distribuidoras de todo o Brasil e grandes indústrias que compram grandes lotes de energia no mercado livre.",
        "como_ganha_dinheiro": "Opera gigantes hidrelétricas (Furnas, Chesf, Eletronorte) e milhares de quilômetros de linhas de transmissão.",
        "estrutura_controle": "Sem controlador único (capital pulverizado com limite estatutário do poder de voto individual).",
        "tipo_controle": "Corporation Privada Pulverizada",
        "contexto_vida_real": "Cerca de um terço de toda a energia elétrica consumida no Brasil é gerada nas usinas históricas da Eletrobras.",
        "marcas_cotidiano": "Eletrobras, Furnas, Chesf, Eletronorte, Eletrosul.",
    },
    "SBSP3": {
        "o_que_faz": "Maior empresa de saneamento básico das Américas, responsável pelo abastecimento de água potável e coleta/tratamento de esgoto em centenas de municípios de São Paulo.",
        "para_quem_vende": "Milhões de moradores e estabelecimentos comerciais do estado de São Paulo que consomem água encanada.",
        "como_ganha_dinheiro": "Cobrança de tarifa mensal de água e esgoto nas contas das residências e empresas com reajustes periódicos regulados pela Arsesp.",
        "estrutura_controle": "Privatizada em 2024, tem o grupo Equatorial Energia como acionista de referência estratégico e capital pulverizado na Bolsa.",
        "tipo_controle": "Privado com Acionista Estratégico (Novo Mercado)",
        "contexto_vida_real": "A Sabesp é a responsável pela água que sai da sua torneira e do seu chuveiro em São Paulo e pela coleta do esgoto da sua rua.",
        "marcas_cotidiano": "Sabesp, Conta de Água Sabesp, Sistema Cantareira, Guarapiranga.",
    },
    "SAPR4": {
        "o_que_faz": "Concessionária estadual de saneamento responsável pela captação, tratamento e distribuição de água e esgoto em centenas de cidades do Paraná.",
        "para_quem_vende": "Famílias, indústrias e comércios em praticamente todo o estado do Paraná.",
        "como_ganha_dinheiro": "Tarifa de água e esgoto cobrada mensalmente nas faturas residenciais e comerciais, reajustada pela agência reguladora estadual (Agepar).",
        "estrutura_controle": "Controlada pelo Governo do Estado do Paraná.",
        "tipo_controle": "Estatal Estadual de Capital Misto",
        "contexto_vida_real": "Se você mora ou viaja pelo Paraná, toda água que bebe e utiliza para higiene é tratada e fornecida pela Sanepar.",
        "marcas_cotidiano": "Sanepar, Fatura Sanepar, Água Sanepar.",
    },
    "CSMG3": {
        "o_que_faz": "Companhia de saneamento do estado de Minas Gerais, prestando serviços de abastecimento de água e esgotamento sanitário em centenas de municípios.",
        "para_quem_vende": "Milhões de residências, hospitais, comércios e escolas no estado mineiro.",
        "como_ganha_dinheiro": "Faturamento regulado mensal de tarifas de saneamento básico com margens operacionais consistentes.",
        "estrutura_controle": "Controlada pelo Governo do Estado de Minas Gerais.",
        "tipo_controle": "Estatal Estadual de Capital Misto",
        "contexto_vida_real": "A Copasa trata e abastece a água potável que chega às residências e estabelecimentos de Belo Horizonte e do interior de Minas.",
        "marcas_cotidiano": "Copasa, Copanor, Conta de Água Copasa.",
    },
    "CSNA3": {
        "o_que_faz": "Conglomerado industrial integrado atuando em siderurgia (aço plano), mineração de ferro, cimento e logística portuária e ferroviária.",
        "para_quem_vende": "Indústria automotiva, fabricantes de eletrodomésticos, construtoras civis e importadores de minério de ferro.",
        "como_ganha_dinheiro": "Venda de bobinas de aço galvanizado de alto valor e exportação de minério de ferro pela subsidiária CSN Mineração.",
        "estrutura_controle": "Controlada pela família Steinbruch (via holding Vicunha).",
        "tipo_controle": "Privado Nacional Familiar",
        "contexto_vida_real": "O aço da CSN está nas chapas dos automóveis fabricados no Brasil, nas latas de aço de conservas e no cimento das obras civis.",
        "marcas_cotidiano": "CSN Siderurgia, CSN Mineração, Cimentos CSN, Usina Presidente Vargas.",
    },
    "GGBR4": {
        "o_que_faz": "Maior produtora brasileira de aços longos (vergalhões para construção) e uma das maiores recicladoras de sucata metálica do mundo.",
        "para_quem_vende": "Construtoras civis, montadoras de automóveis, implementos agrícolas e indústrias em todas as Américas.",
        "como_ganha_dinheiro": "Transforma sucata de ferro-velho e minério em vigas e vergalhões essenciais para obras, lucrando com a forte demanda de infraestrutura.",
        "estrutura_controle": "Controlada pela tradicional família Gerdau Johannpeter.",
        "tipo_controle": "Privado Nacional Familiar",
        "contexto_vida_real": "O aço da Gerdau forma o esqueleto de concreto armado de pontes, viadutos, edifícios e casas em todo o Brasil.",
        "marcas_cotidiano": "Gerdau, Aço Gerdau, Vergalhão GG 50, Gerdau Corsa.",
    },
    "USIM5": {
        "o_que_faz": "Líder na produção de aços planos no Brasil (chapas laminadas a frio e a quente), essencial para a indústria automobilística nacional.",
        "para_quem_vende": "Montadoras de carros, fabricantes de linha branca (fogões, geladeiras), tubos e estruturas metálicas pesadas.",
        "como_ganha_dinheiro": "Venda de aço de alta tecnologia sob medida com contratos anuais firmados diretamente com montadoras de veículos.",
        "estrutura_controle": "Controlada pelo grupo ítalo-argentino Ternium em acordo de acionistas com a japonesa Nippon Steel.",
        "tipo_controle": "Privado Multinacional / Grupo Controlador",
        "contexto_vida_real": "A carroceria do seu carro e as laterais metálicas da sua geladeira foram laminadas nas usinas da Usiminas em Ipatinga e Cubatão.",
        "marcas_cotidiano": "Usiminas, Soluções Usiminas, Mineração Usiminas, Unigal.",
    },
    "MGLU3": {
        "o_que_faz": "Uma das maiores redes varejistas do Brasil, combinando lojas físicas com marketplace digital, logística e serviços financeiros.",
        "para_quem_vende": "Consumidores finais de todo o Brasil comprando eletrodomésticos, celulares, móveis e produtos de beleza e moda.",
        "como_ganha_dinheiro": "Margem sobre a venda de mercadorias próprias (1P), comissões de lojistas parceiros no marketplace (3P) e juros do cartão Luiza.",
        "estrutura_controle": "Controlada pela família Trajano Rodrigues (Luiza Helena Trajano e Frederico Trajano).",
        "tipo_controle": "Privado Nacional Familiar (Novo Mercado)",
        "contexto_vida_real": "Você compra no Magalu pelo app, retira produtos na loja física do seu bairro ou adquire eletrônicos pelo site do KaBuM! e Netshoes.",
        "marcas_cotidiano": "Magazine Luiza, Magalu, KaBuM!, Netshoes, Época Cosméticos, Consórcio Magalu.",
    },
    "LREN3": {
        "o_que_faz": "Líder em moda e vestuário no Brasil, operando lojas de departamento de moda, beleza, artigos para casa e soluções financeiras.",
        "para_quem_vende": "Mulheres, homens e famílias de classe média que buscam roupas modernas, calçados e decoração com preços acessíveis.",
        "como_ganha_dinheiro": "Venda de peças de confecção com alta margem de marca própria e receitas de crédito da Realize CFI (cartão Renner).",
        "estrutura_controle": "Sem controlador único (capital pulverizado no Novo Mercado da B3).",
        "tipo_controle": "Corporation Privada Pulverizada (Novo Mercado)",
        "contexto_vida_real": "Você compra roupas na Renner nos principais shoppings, decora sua casa com itens da Camicado ou adquire moda jovem na Youcom.",
        "marcas_cotidiano": "Lojas Renner, Camicado, Youcom, Ashua Curve & Plus Size, Cartão Renner.",
    },
    "ARZZ3": {
        "o_que_faz": "Maior 'house of brands' de calçados, bolsas e moda do Brasil (Azzas 2154), dona de marcas líderes do mercado feminino e masculino.",
        "para_quem_vende": "Consumidores de moda das classes A, B e C através de franquias, lojas próprias, multimarcas e e-commerce.",
        "como_ganha_dinheiro": "Design, fabricação e venda de sapatos, bolsas e roupas com alto apelo de estilo e fidelidade de marca.",
        "estrutura_controle": "Controlada pelas famílias Birman (Arezzo) e Jereissati (Grupo Soma).",
        "tipo_controle": "Privado Nacional / Novo Mercado",
        "contexto_vida_real": "Você consome Azzas 2154 ao comprar calçados na Arezzo, Schutz e Anacapri, ou roupas na Farm, Hering e Animale.",
        "marcas_cotidiano": "Arezzo, Schutz, Anacapri, Farm, Hering, Animale, Reserva, Vans (licença).",
    },
    "ASAI3": {
        "o_que_faz": "Segunda maior rede de atacarejo (cash & carry) do Brasil, vendendo alimentos e mercadorias a preços de atacado em lojas amplas.",
        "para_quem_vende": "Pequenos comerciantes (donos de bares, lanchonetes e mercearias) e famílias que fazem compras mensais em grandes volumes.",
        "como_ganha_dinheiro": "Giro ultrarrápido de estoque com margem operacional enxuta e alta eficiência por metro quadrado.",
        "estrutura_controle": "Sem controlador único (capital pulverizado no Novo Mercado da B3).",
        "tipo_controle": "Corporation Privada Pulverizada",
        "contexto_vida_real": "Você economiza comprando no Assaí atacadista para estocar alimentos da sua casa ou para abastecer o seu restaurante.",
        "marcas_cotidiano": "Assaí Atacadista, Cartão Passaí, Meu Assaí.",
    },
    "CRFB3": {
        "o_que_faz": "Maior varejista alimentar do Brasil, operando hipermercados Carrefour, lojas de proximidade, o gigante Atacadão e banco digital.",
        "para_quem_vende": "Milhões de famílias brasileiras em todas as faixas de renda e pequenos comerciantes que compram no Atacadão.",
        "como_ganha_dinheiro": "Escala massiva de compras junto à indústria de alimentos com margem de varejo e juros do Banco Carrefour.",
        "estrutura_controle": "Controlado pelo grupo francês Carrefour S.A., com participação relevante da família Diniz (holding Península).",
        "tipo_controle": "Subsidiária de Grupo Estrangeiro / B3",
        "contexto_vida_real": "Você faz compras na loja do Atacadão do seu bairro, no Hipermercado Carrefour ou utiliza o Cartão Carrefour no dia a dia.",
        "marcas_cotidiano": "Atacadão, Hipermercados Carrefour, Carrefour Express, Sam's Club, Banco Carrefour.",
    },
    "ABEV3": {
        "o_que_faz": "Maior cervejaria do mundo e líder em bebidas na América Latina, fabricante de cervejas consagradas, refrigerantes, águas e energéticos.",
        "para_quem_vende": "Supermercados, bares, restaurantes, distribuidores de bebidas e consumidores finais de todo o continente.",
        "como_ganha_dinheiro": "Escala gigantesca de fabricação e distribuição com custo por litro muito baixo e forte poder de precificação de suas marcas consagradas.",
        "estrutura_controle": "Controlada pelo conglomerado global Anheuser-Busch InBev (famílias Lemann, Telles e Sicupira via 3G Capital).",
        "tipo_controle": "Multinacional Privada / Grupo Controlador",
        "contexto_vida_real": "Você bebe Ambev ao pedir uma Skol, Brahma, Spaten ou Stella no bar, ao tomar um Guaraná Antarctica ou ao pedir bebidas pelo app Zé Delivery.",
        "marcas_cotidiano": "Brahma, Skol, Amstel, Spaten, Corona, Stella Artois, Guaraná Antarctica, Zé Delivery, Bees.",
    },
    "RADL3": {
        "o_que_faz": "Maior rede de farmácias do Brasil (mais de 3.000 lojas), resultado da fusão entre Drogasil e Droga Raia.",
        "para_quem_vende": "Consumidores que necessitam de medicamentos com prescrição, produtos de higiene pessoal, beleza e bem-estar.",
        "como_ganha_dinheiro": "Margens na venda de remédios de marca e genéricos e produtos de beleza de alto valor agregado com grande fidelidade em lojas de bairro.",
        "estrutura_controle": "Controlada pelas famílias fundadoras Pires de Oliveira Dias e Pipponzi com ações no Novo Mercado.",
        "tipo_controle": "Privado Nacional Familiar (Novo Mercado)",
        "contexto_vida_real": "Em praticamente qualquer esquina nobre das cidades brasileiras você encontra uma farmácia Droga Raia ou Drogasil para comprar remédios.",
        "marcas_cotidiano": "Droga Raia, Drogasil, Farmácias 4Bio, Marcas Próprias Needs, Triss, Nutlês.",
    },
    "BHIA3": {
        "o_que_faz": "Rede varejista histórica de móveis, eletrodomésticos e eletrônicos, dona das marcas Casas Bahia e Ponto (antigo Ponto Frio).",
        "para_quem_vende": "População brasileira de média e baixa renda que necessita parcelar bens duráveis essenciais para o lar.",
        "como_ganha_dinheiro": "Venda de eletrodomésticos em lojas físicas e internet, complementada pelos juros do famoso carnê de crediário.",
        "estrutura_controle": "Empresa em plano de reestruturação operacional com ações negociadas no Novo Mercado da B3.",
        "tipo_controle": "Capital Aberto B3",
        "contexto_vida_real": "Você compra eletrodomésticos na Casas Bahia do calçadão da sua cidade pelo clássico carnê ou pelo aplicativo da marca.",
        "marcas_cotidiano": "Casas Bahia, Ponto, Carnê Casas Bahia, Bartira (móveis).",
    },
    "WEGE3": {
        "o_que_faz": "Multinacional brasileira de bens de capital que fabrica motores elétricos industriais, geradores, transformadores e sistemas de energia solar e eólica.",
        "para_quem_vende": "Indústrias automotivas, mineradoras, refinarias, concessionárias de energia e usinas solares em mais de 135 países.",
        "como_ganha_dinheiro": "Venda de motores elétricos ultraeficientes e projetos complexos de engenharia elétrica de alto valor agregado.",
        "estrutura_controle": "Controlada pelas famílias dos fundadores (Werner Voigt, Eggon da Silva e Geraldo Werninghaus).",
        "tipo_controle": "Privado Nacional Familiar",
        "contexto_vida_real": "Motores da WEG acionam elevadores de prédios, bombas de água tratada, fábricas de refrigerante e geradores de usinas eólicas.",
        "marcas_cotidiano": "WEG Motores, Tintas WEG, Transformadores WEG, WEG Solar.",
    },
    "EMBR3": {
        "o_que_faz": "Terceira maior fabricante de aviões comerciais do planeta, líder global incontestável no segmento de jatos regionais de até 150 assentos.",
        "para_quem_vende": "Companhias aéreas do mundo todo (United, Delta, KLM, Azul), forças aéreas militares e frotas de aviação executiva privada.",
        "como_ganha_dinheiro": "Venda de aeronaves de alta tecnologia em dólares e contratos plurianuais de manutenção de turbinas e peças de reposição.",
        "estrutura_controle": "Sem controlador único (capital pulverizado), com a União Federal mantendo uma 'Golden Share' com poder de veto em temas estratégicos de defesa.",
        "tipo_controle": "Corporation Privada com Golden Share",
        "contexto_vida_real": "Você viaja em um avião da Embraer sempre que voa nos jatos E-Jets da Azul Linhas Aéreas ou em conexões regionais nos EUA e Europa.",
        "marcas_cotidiano": "Embraer, E-Jets E2, Cargueiro Militar KC-390 Millennium, Jatos Executivos Phenom e Praetor, Eve Air Mobility.",
    },
    "TUPY3": {
        "o_que_faz": "Multinacional brasileira de engenharia metalúrgica que desenvolve e fabrica blocos e cabeçotes de motor em ferro fundido para veículos pesados.",
        "para_quem_vende": "Montadoras globais de caminhões, tratores agrícolas, máquinas de construção e navios (Ford, Mercedes-Benz, Volvo, Cummins).",
        "como_ganha_dinheiro": "Fundição de alta precisão de peças estruturais pesadas que exigem resistência extrema a altas temperaturas e pressão.",
        "estrutura_controle": "Acionistas institucionais de referência (BNDESPar e Previ) com ações negociadas no Novo Mercado da B3.",
        "tipo_controle": "Privado Institucional",
        "contexto_vida_real": "O motor dos caminhões que transportam as cargas e dos tratores que colhem grãos no Brasil é fundido pela Tupy em Joinville.",
        "marcas_cotidiano": "Tupy, Conexões Tupy, Blocos de Motor Tupy, MWM Motores.",
    },
    "POMO4": {
        "o_que_faz": "Líder brasileira e uma das maiores fabricantes mundiais de carrocerias de ônibus urbanos, rodoviários e micro-ônibus.",
        "para_quem_vende": "Empresas de transporte coletivo urbano em capitais, companhias de viação rodoviária interestadual e frotas escolares.",
        "como_ganha_dinheiro": "Montagem e venda de carrocerias completas sobre chassis de grandes montadoras com peças personalizadas.",
        "estrutura_controle": "Controlada pela família Bellini e sócios fundadores no Rio Grande do Sul.",
        "tipo_controle": "Privado Nacional Tradicional",
        "contexto_vida_real": "O ônibus circular que você pega para ir ao trabalho ou o ônibus de viagem que te leva à praia na rodoviária foi encarroçado pela Marcopolo.",
        "marcas_cotidiano": "Marcopolo, Marcopolo Paradiso, Torino, Volare (micro-ônibus), Neobus.",
    },
    "JBSS3": {
        "o_que_faz": "Maior produtora global de carnes e proteína animal do planeta, operando frigoríficos de carne bovina, suína, de aves e alimentos prontos.",
        "para_quem_vende": "Supermercados, redes de fast-food (McDonald's, Burger King) e importadores em mais de 190 países no mundo.",
        "como_ganha_dinheiro": "Escala global incomparável de abate e processamento, diversificada entre operações em dólares nos EUA e no Brasil.",
        "estrutura_controle": "Controlada pela família Batista (holding J&F Investimentos).",
        "tipo_controle": "Privado Nacional / Grupo Controlador",
        "contexto_vida_real": "Você consome produtos da JBS sempre que compra carnes Friboi, frios e hambúrgueres Seara ou cortes Swift.",
        "marcas_cotidiano": "Friboi, Seara, Swift, Maturatta, Doriana, Pilgrim's Pride (EUA).",
    },
    "BRFS3": {
        "o_que_faz": "Gigante global de carnes processadas de frango e suínos, resultado da união histórica entre Sadia e Perdigão.",
        "para_quem_vende": "Varejo alimentar brasileiro e grande exportadora para os países árabes e muçulmanos (mercado Halal).",
        "como_ganha_dinheiro": "Venda de produtos processados congelados e embutidos de alto valor agregado e margens superiores às da carne crua.",
        "estrutura_controle": "Controlada pelo grupo Marfrig Global Foods (família Molina).",
        "tipo_controle": "Subsidiária de Grupo Privado Nacional",
        "contexto_vida_real": "Você come BRF no café da manhã com presunto Sadia, no lanche com mortadela Perdigão ou no almoço com linguiças e nuggets.",
        "marcas_cotidiano": "Sadia, Perdigão, Qualy (margarina), Deline, Chester, Sadia Bio.",
    },
    "MRFG3": {
        "o_que_faz": "Segunda maior produtora de carne bovina do mundo e líder absoluta na produção de hambúrgueres para grandes cadeias de fast-food.",
        "para_quem_vende": "Redes globais de alimentação rápida (fast-food), distribuidores de carne nos Estados Unidos e varejo sul-americano.",
        "como_ganha_dinheiro": "Operação de abate de gado de alta qualidade nos EUA (National Beef) somada ao controle da BRF no setor de frangos e embutidos.",
        "estrutura_controle": "Controlada pelo empresário Marcos Molina dos Santos.",
        "tipo_controle": "Privado Nacional / Grupo Controlador",
        "contexto_vida_real": "O hambúrguer que você consome nas maiores redes de lanchonetes de fast-food é produzido e fornecido pela Marfrig.",
        "marcas_cotidiano": "Marfrig, Montana, Bassi, National Beef (EUA), BRF (controlada).",
    },
    "BEEF3": {
        "o_que_faz": "Líder na exportação de carne bovina na América do Sul, com plantas de abate estrategicamente distribuídas em Brasil, Argentina, Uruguai e Paraguai.",
        "para_quem_vende": "Grandes importadores de carne na China, Oriente Médio, Estados Unidos e Europa que buscam proteína de qualidade a pasto.",
        "como_ganha_dinheiro": "Arbitragem geográfica (produz no país do Mercosul com custo mais baixo no momento) e venda em moeda forte com hedge de câmbio.",
        "estrutura_controle": "Controlada pela família Vilela de Queiroz, com participação acionária estratégica do grupo saudita SALIC.",
        "tipo_controle": "Privado Nacional com Sócio Estratégico Global",
        "contexto_vida_real": "A carne de alta qualidade da Minerva abastece churrascarias nobres e chega às mesas de consumidores nos cinco continentes.",
        "marcas_cotidiano": "Minerva Foods, Cabaña Las Lilas, Estância 92, Pul, Frigomerc.",
    },
    "SMTO3": {
        "o_que_faz": "Um dos maiores e mais eficientes grupos sucroenergéticos do Brasil, processando cana para produzir açúcar, etanol e energia limpa.",
        "para_quem_vende": "Grandes tradings internacionais de commodities agrícolas e distribuidoras nacionais de combustíveis automotivos.",
        "como_ganha_dinheiro": "Colheita mecanizada com baixíssimo custo de produção de cana por hectare e usinas de cogeração de eletricidade.",
        "estrutura_controle": "Controlada pela tradicional família Ometto Penteado.",
        "tipo_controle": "Privado Nacional Familiar (Novo Mercado)",
        "contexto_vida_real": "O açúcar que você usa para adoçar seu café e o etanol hidratado que abastece carros flex saem dos canaviais da São Martinho.",
        "marcas_cotidiano": "São Martinho, Usina São Martinho, Usina Iracema, Usina Santa Cruz.",
    },
    "SLCE3": {
        "o_que_faz": "Uma das maiores produtoras agrícolas do planeta em área plantada, líder no cultivo em larga escala de soja, milho e algodão no Cerrado.",
        "para_quem_vende": "Tradings multinacionais agrícolas (Cargill, Bunge, ADM, Louis Dreyfus) que exportam os grãos para a alimentação mundial.",
        "como_ganha_dinheiro": "Agricultura de altíssima precisão com sistema de plantio direto de safra e safrinha, além de valorização das terras próprias.",
        "estrutura_controle": "Controlada pela família Logemann (Grupo SLC) do Rio Grande do Sul.",
        "tipo_controle": "Privado Nacional Familiar (Novo Mercado)",
        "contexto_vida_real": "O tecido de algodão das suas roupas e o óleo de soja usado no preparo de refeições começaram nas fazendas de alta tecnologia da SLC.",
        "marcas_cotidiano": "SLC Agrícola, Fazenda Pamplona, Sementes SLC.",
    },
    "RDOR3": {
        "o_que_faz": "Maior rede integrada de saúde privada do Brasil, proprietária de dezenas de hospitais de excelência e da seguradora SulAmérica.",
        "para_quem_vende": "Pacientes de planos de saúde de padrão premium e beneficiários de planos médicos e odontológicos corporativos.",
        "como_ganha_dinheiro": "Diárias hospitalares, procedimentos cirúrgicos complexos e prêmios de planos de saúde arrecadados pela SulAmérica.",
        "estrutura_controle": "Controlada pela família Moll (fundadores liderados pelo Dr. Jorge Moll) com ações no Novo Mercado.",
        "tipo_controle": "Privado Nacional Familiar (Novo Mercado)",
        "contexto_vida_real": "Você é atendido pela Rede D'Or quando consulta médicos nos hospitais Copa D'Or, São Luiz, Quinta D'Or ou tem o convênio SulAmérica Saúde.",
        "marcas_cotidiano": "Hospital São Luiz, Copa D'Or, Hospital Vila Nova Star, SulAmérica Saúde, Richet Medicina Diagnóstica.",
    },
    "HYPE3": {
        "o_que_faz": "Maior empresa farmacêutica de capital nacional, líder absoluta em medicamentos isentos de prescrição (OTC), genéricos e dermocosméticos.",
        "para_quem_vende": "Grandes redes de farmácias (Drogasil, Raia, Pague Menos), hospitais e clínicas médicas de todo o país.",
        "como_ganha_dinheiro": "Portfólio de marcas líderes históricas com altíssima margem de lucro e fábrica gigantesca de medicamentos em Anápolis (GO).",
        "estrutura_controle": "Controlada pelo empresário João Alves de Queiroz Filho (Junior) e bloco de referência no Novo Mercado.",
        "tipo_controle": "Privado Nacional (Novo Mercado)",
        "contexto_vida_real": "Você consome Hypera quando toma um comprimido de Neosaldina para dor de cabeça, um Benegrip para gripe ou um antiácido Engov ou Estomazil.",
        "marcas_cotidiano": "Neosaldina, Benegrip, Engov, Doril, Coristina d, Buscopan, Rinosoro, Epocler, Albel, Mantecorp Skincare.",
    },
    "VIVT3": {
        "o_que_faz": "Líder em telecomunicações no Brasil sob a marca comercial Vivo, fornecendo telefonia móvel 5G, internet de fibra ótica residencial e serviços digitais.",
        "para_quem_vende": "Mais de 100 milhões de clientes pessoas físicas em planos pós-pago e pré-pago e corporações com conectividade empresarial.",
        "como_ganha_dinheiro": "Receita recorrente e previsível de assinaturas mensais de internet fixa por fibra ótica de alta velocidade e planos de celular.",
        "estrutura_controle": "Controlada pelo grupo multinacional espanhol Telefónica S.A.",
        "tipo_controle": "Subsidiária de Grupo Estrangeiro",
        "contexto_vida_real": "A Vivo é quem fornece o sinal 4G/5G do seu celular e a conexão Wi-Fi de fibra ótica de alta velocidade da sua residência.",
        "marcas_cotidiano": "Vivo, Vivo Fibra, Vivo Total, Vivo Empresas, Telefônica Brasil.",
    },
    "TIMS3": {
        "o_que_faz": "Segunda maior operadora de telefonia celular do Brasil, pioneira na cobertura nacional 4G/5G em todos os municípios brasileiros.",
        "para_quem_vende": "Milhões de usuários de smartphones buscando planos móveis com benefícios digitais e empresas com conectividade corporativa.",
        "como_ganha_dinheiro": "Cobrança mensal recorrente de planos de dados móveis e expansão da base de internet banda larga fixa TIM UltraFibra.",
        "estrutura_controle": "Controlada pelo grupo italiano Telecom Italia.",
        "tipo_controle": "Subsidiária de Grupo Estrangeiro (Novo Mercado)",
        "contexto_vida_real": "Você utiliza a rede da TIM quando navega na internet pelo celular na rua, utiliza redes sociais com franquia inclusa ou assiste a vídeos pelo 5G.",
        "marcas_cotidiano": "TIM, TIM Black, TIM Beta, TIM Pré Top, TIM UltraFibra.",
    },
    "HGLG11": {
        "o_que_faz": "Um dos maiores e mais tradicionais fundos imobiliários de tijolo da B3, proprietário de dezenas de galpões logísticos e industriais de padrão Triple A (A+).",
        "para_quem_vende": "Grandes gigantes do e-commerce, operadores logísticos e multinacionais (Mercado Livre, Volkswagen, Ambev) que alugam os galpões.",
        "como_ganha_dinheiro": "Recebimento mensal de aluguéis de longo prazo com reajuste por índices de inflação (IPCA e IGP-M), distribuindo a maior parte do lucro caixa em proventos isentos aos cotistas.",
        "estrutura_controle": "Fundo Imobiliário gerido pelo Patria Investimentos (antigo CSHG) e administrado pelo Itaú Unibanco.",
        "tipo_controle": "Fundo de Investimento Imobiliário (FII)",
        "contexto_vida_real": "Quando você compra algo pela internet e recebe no dia seguinte, a mercadoria estava armazenada em um galpão logístico do HGLG11 à beira da rodovia.",
        "marcas_cotidiano": "CSHG Logística, Galpões Logísticos HGLG, Condomínio Logístico Betim/Louveira.",
    },
    "MXRF11": {
        "o_que_faz": "Maior fundo imobiliário de papel da Bolsa brasileira por número de cotistas (mais de 1 milhão de investidores), focado em Certificados de Recebíveis Imobiliários (CRIs).",
        "para_quem_vende": "Financia loteamentos residenciais, edifícios comerciais e empreendimentos imobiliários que precisam de capital de dívida.",
        "como_ganha_dinheiro": "Recebe juros mensais das dívidas imobiliárias (geralmente indexadas ao CDI + spread ou IPCA + juros), pingando dividendos todos os meses aos cotistas.",
        "estrutura_controle": "Gerido pela XP Vista Asset Management e administrado pelo Banco BTG Pactual Serviços Financeiros.",
        "tipo_controle": "Fundo de Investimento Imobiliário (FII de Papel)",
        "contexto_vida_real": "Ao comprar cotas do MXRF11 por cerca de R$ 10, você passa a ser credor de grandes obras imobiliárias e recebe proventos mensais em sua conta.",
        "marcas_cotidiano": "Maxi Renda FII, XP Asset, Títulos de Dívida CRI.",
    },
    "XPML11": {
        "o_que_faz": "Fundo imobiliário proprietário de participações em dezenas dos melhores shoppings centers do Brasil em grandes capitais.",
        "para_quem_vende": "Lojas âncoras (Renner, Zara, C&A), restaurantes, cinemas e franquias que alugam espaços nos shoppings.",
        "como_ganha_dinheiro": "Cobrança de aluguel fixo e variável sobre as vendas das lojas do shopping, mais receita do estacionamento.",
        "estrutura_controle": "Gerido pela XP Asset Management e administrado pelo Banco BTG Pactual.",
        "tipo_controle": "Fundo de Investimento Imobiliário (FII de Shoppings)",
        "contexto_vida_real": "Você gera lucro para o XPML11 sempre que passeia no shopping, almoça na praça de alimentação ou paga o tíquete do estacionamento.",
        "marcas_cotidiano": "XP Malls, Shopping Cidade São Paulo, Catarina Fashion Outlet, Shopping Tijuca.",
    },
    "KNCR11": {
        "o_que_faz": "Fundo imobiliário de papel da gestora Kinea (Itaú), focado exclusivamente em títulos de crédito imobiliário atrelados à taxa CDI.",
        "para_quem_vende": "Financiamento de grandes incorporadoras e grupos corporativos de primeira linha, com histórico de inadimplência menor.",
        "como_ganha_dinheiro": "Rende a taxa CDI do Brasil mais um prêmio de juros, distribuindo proventos mensais com alta liquidez na B3.",
        "estrutura_controle": "Gerido pela Kinea Investimentos (holding do Grupo Itaú Unibanco) e administrado pela Intrag DTVM.",
        "tipo_controle": "Fundo de Investimento Imobiliário (FII Pós-Fixado)",
        "contexto_vida_real": "Busca pagar rendimentos mensais atrelados ao CDI; o valor sobe e desce com a Selic e com a inadimplência dos CRIs.",
        "marcas_cotidiano": "Kinea Rendimentos Imobiliários, Itaú Asset, CRIs Corporativos.",
    },
    "KNIP11": {
        "o_que_faz": "Fundo imobiliário de papel da Kinea focado em títulos de dívida imobiliária indexados à inflação oficial brasileira (IPCA + juro real).",
        "para_quem_vende": "Grandes construtoras de hospitais, centros de distribuição e torres comerciais de alto padrão.",
        "como_ganha_dinheiro": "Repassa integralmente a inflação acumulada do mês mais uma taxa de juros real atrativa em forma de dividendos regulares.",
        "estrutura_controle": "Gerido pela Kinea Investimentos (Itaú) e administrado pela Intrag DTVM.",
        "tipo_controle": "Fundo de Investimento Imobiliário (FII Inflação)",
        "contexto_vida_real": "Protege o poder de compra do investidor leigo contra o aumento dos preços do supermercado com renda isenta de IR.",
        "marcas_cotidiano": "Kinea Índices de Preços, Títulos IPCA Imobiliário, Kinea Itaú.",
    },
    "BTLG11": {
        "o_que_faz": "Fundo imobiliário de logística do BTG Pactual com galpões modernos situados prioritariamente no estado de São Paulo (raio de 30 km da capital).",
        "para_quem_vende": "Empresas multinacionais de comércio eletrônico, logística farmacêutica e bens de consumo rápido.",
        "como_ganha_dinheiro": "Aluguéis mensais com baixa vacância graças à localização privilegiada perto dos maiores centros de consumo do país.",
        "estrutura_controle": "Gerido e administrado pelo Banco BTG Pactual Serviços Financeiros.",
        "tipo_controle": "Fundo de Investimento Imobiliário (FII Logístico)",
        "contexto_vida_real": "Os galpões do BTLG11 garantem que entregas rápidas cheguem em poucas horas na casa dos consumidores da Grande São Paulo.",
        "marcas_cotidiano": "BTG Pactual Logística, Galpões BTLG Embu, Louveira e Guarulhos.",
    },
    "VISC11": {
        "o_que_faz": "Fundo imobiliário de shopping centers administrado pela Vinci Partners, com portfólio distribuído em diversos estados brasileiros.",
        "para_quem_vende": "Milhares de lojistas de shoppings centers consolidados em capitais e grandes polos do interior do Brasil.",
        "como_ganha_dinheiro": "Aluguel pago pelos lojistas e receita de estacionamento e eventos promocionais nos shoppings.",
        "estrutura_controle": "Gerido pela Vinci Real Estate e administrado pela BRL Trust.",
        "tipo_controle": "Fundo de Investimento Imobiliário (FII Shoppings)",
        "contexto_vida_real": "Você visita ativos do VISC11 ao passear ou fazer compras nos shoppings centers da rede Vinci pelo país.",
        "marcas_cotidiano": "Vinci Shopping Centers, Shopping Granja Vianna, Shopping Iguatemi Fortaleza (fatia).",
    },
    "XPLG11": {
        "o_que_faz": "Fundo imobiliário de galpões logísticos da XP Asset com portfólio focado na cadeia de suprimentos e e-commerce moderno.",
        "para_quem_vende": "Operadores como Mercado Livre, Via Varejo, Leroy Merlin e Renner.",
        "como_ganha_dinheiro": "Aluguéis com contratos atípicos de longo prazo atrelados à inflação que geram fluxo de dividendos todo mês.",
        "estrutura_controle": "Gerido pela XP Vista Asset e administrado pela Vórtx DTVM.",
        "tipo_controle": "Fundo de Investimento Imobiliário (FII Logístico)",
        "contexto_vida_real": "Os condomínios logísticos do XPLG armazenam produtos e facilitam a entrega do comércio eletrônico nacional.",
        "marcas_cotidiano": "XP Log FII, Syslog São Paulo, Galpão Leroy Merlin Cajamar.",
    },
}
for _tk_curado, _dados_curado in MAPA_PERFIL_DIDATICO.items():
    _dados_curado["revisado_em"] = "2026-10"
    _dados_curado["revisado_em_texto"] = "Perfil revisado em outubro de 2026"
    _dados_curado["curado"] = True

# Copia mapeamentos para ações ON/PN/Units correspondentes
for _origem, _destinos in [
    ("PETR4", ["PETR3"]),
    ("ITUB4", ["ITUB3"]),
    ("BBDC4", ["BBDC3"]),
    ("ELET3", ["ELET6"]),
    ("SAPR4", ["SAPR11", "SAPR3"]),
]:
    if _origem in MAPA_PERFIL_DIDATICO:
        for _dst in _destinos:
            MAPA_PERFIL_DIDATICO[_dst] = copy.deepcopy(MAPA_PERFIL_DIDATICO[_origem])


def obter_dossie_didatico(ticker: str, info: dict | None = None, dados: dict | None = None) -> dict:
    tk = (ticker or "").upper().strip()
    if tk not in MAPA_PERFIL_DIDATICO:
        raiz = re.sub(r"\d+$", "", tk)
        for cand in [f"{raiz}4", f"{raiz}3", f"{raiz}11"]:
            if cand in MAPA_PERFIL_DIDATICO:
                tk = cand
                break

    if tk in MAPA_PERFIL_DIDATICO:
        perfil = copy.deepcopy(MAPA_PERFIL_DIDATICO[tk])
        perfil["curado"] = True
        perfil["revisado_em"] = perfil.get("revisado_em") or "2026-10"
        perfil["revisado_em_texto"] = perfil.get("revisado_em_texto") or "Perfil revisado em outubro de 2026"
        return perfil

    info = info or {}
    resumo_oficial = (
        info.get("longBusinessSummary")
        or (dados or {}).get("Identificacao", {}).get("Resumo_Negocio")
        or ""
    ).strip()

    return {
        "curado": False,
        "revisado_em": None,
        "revisado_em_texto": None,
        "aviso_nao_curado": "Ainda não escrevemos o perfil em linguagem simples desta empresa.",
        "rotulo_resumo_oficial": "Descrição oficial da empresa (em inglês, vinda do Yahoo Finance)",
        "resumo_oficial": resumo_oficial or "Descrição oficial indisponível no momento.",
        "o_que_faz": None,
        "para_quem_vende": None,
        "como_ganha_dinheiro": None,
        "estrutura_controle": None,
        "tipo_controle": None,
        "contexto_vida_real": None,
        "marcas_cotidiano": None,
    }


# ===========================================================================
# BLOCO 2 (Dossiê Aberto): Simulador Realidade vs. Guru (Histórico Factual + SGS BCB)
# ===========================================================================
_CACHE_SGS_BCB: dict[str, tuple[float, list[dict]]] = {}
_CACHE_SGS_TTL_SEGUNDOS = 24 * 3600
TAXA_FIXA_CDI_AA = 0.105
TAXA_FIXA_POUPANCA_AA = 0.065


def _consultar_sgs_bcb_http(
    codigo_serie: int, data_inicial: str, data_final: str, timeout: float = 5.0
) -> list[dict]:
    url = (
        f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{int(codigo_serie)}/dados"
        f"?formato=json&dataInicial={data_inicial}&dataFinal={data_final}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "InvestidorIA/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        corpo = resp.read().decode("utf-8")
        dados = json.loads(corpo)
        return dados if isinstance(dados, list) else []


def obter_serie_sgs_bcb(codigo_serie: int, anos: int = 5, timeout: float = 5.0) -> list[dict] | None:
    """
    Consulta a API gratuita do Banco Central (SGS) para a série informada
    (12 = CDI diário, 195 = Poupança), com cache de 24h em memória e timeout de 5s.
    """
    agora_ts = time.time()
    chave = f"{codigo_serie}:{anos}"
    em_cache = _CACHE_SGS_BCB.get(chave)
    if em_cache and (agora_ts - em_cache[0]) < _CACHE_SGS_TTL_SEGUNDOS:
        return em_cache[1]

    hoje = agora_brasilia().date()
    dt_ini = hoje - datetime.timedelta(days=int(anos * 365.25) + 15)
    try:
        dados = _consultar_sgs_bcb_http(
            codigo_serie,
            dt_ini.strftime("%d/%m/%Y"),
            hoje.strftime("%d/%m/%Y"),
            timeout=timeout,
        )
        if not dados:
            return None
        _CACHE_SGS_BCB[chave] = (agora_ts, dados)
        return dados
    except Exception:
        return None


def _acumular_cdi_sgs_ou_fixo(c0: float, anos: int, serie_cdi_5a: list[dict] | None) -> dict:
    aliquota_ir = 0.175 if anos == 1 else 0.150
    if serie_cdi_5a:
        try:
            qtd_dias = anos * 252
            fatia = serie_cdi_5a[-qtd_dias:] if len(serie_cdi_5a) >= qtd_dias else serie_cdi_5a
            fator_bruto = 1.0
            for item in fatia:
                v_str = str(item.get("valor", "0")).replace(",", ".")
                taxa_dia = float(v_str) / 100.0
                fator_bruto *= 1.0 + taxa_dia
            ganho_bruto = c0 * (fator_bruto - 1.0)
            ganho_liq = ganho_bruto * (1.0 - aliquota_ir)
            total_cdi = c0 + ganho_liq
            rent_cdi = ((total_cdi - c0) / c0) * 100.0 if c0 > 0 else 0.0
            return {
                "total": round(total_cdi, 2),
                "rent_pct": round(rent_cdi, 2),
                "aproximacao": False,
                "fonte": "SGS/BCB (série 12)",
                "aviso": f"Série 12 do Banco Central (IR regressivo de {br(aliquota_ir * 100, 1)}% deduzido)",
            }
        except Exception:
            pass

    ganho_bruto_cdi = c0 * (((1.0 + TAXA_FIXA_CDI_AA) ** anos) - 1.0)
    ganho_liq_cdi = ganho_bruto_cdi * (1.0 - aliquota_ir)
    total_cdi = c0 + ganho_liq_cdi
    rent_cdi = ((total_cdi - c0) / c0) * 100.0 if c0 > 0 else 0.0
    aviso_fixo = f"aproximação com taxa fixa de {br(TAXA_FIXA_CDI_AA * 100, 1)}% a.a."
    return {
        "total": round(total_cdi, 2),
        "rent_pct": round(rent_cdi, 2),
        "aproximacao": True,
        "fonte": "taxa_fixa",
        "aviso": aviso_fixo,
    }


def _acumular_poupanca_sgs_ou_fixo(c0: float, anos: int, serie_poup_5a: list[dict] | None) -> dict:
    if serie_poup_5a:
        try:
            por_mes: dict[str, float] = {}
            ordem_meses: list[str] = []
            for item in serie_poup_5a:
                dt_txt = str(item.get("data", ""))
                partes = dt_txt.split("/")
                chave_m = f"{partes[1]}/{partes[2]}" if len(partes) == 3 else dt_txt
                v_str = str(item.get("valor", "0")).replace(",", ".")
                if chave_m not in por_mes:
                    ordem_meses.append(chave_m)
                por_mes[chave_m] = float(v_str) / 100.0
            meses_alvo = anos * 12
            chaves_recorte = ordem_meses[-meses_alvo:]
            if chaves_recorte:
                fator_poup = 1.0
                for ch in chaves_recorte:
                    fator_poup *= 1.0 + por_mes[ch]
                total_poup = c0 * fator_poup
                rent_poup = ((total_poup - c0) / c0) * 100.0 if c0 > 0 else 0.0
                return {
                    "total": round(total_poup, 2),
                    "rent_pct": round(rent_poup, 2),
                    "aproximacao": False,
                    "fonte": "SGS/BCB (série 195)",
                    "aviso": "Série 195 do Banco Central (isento de IR)",
                }
        except Exception:
            pass

    total_poup = c0 * ((1.0 + TAXA_FIXA_POUPANCA_AA) ** anos)
    rent_poup = ((total_poup - c0) / c0) * 100.0 if c0 > 0 else 0.0
    aviso_fixo = f"aproximação com taxa fixa de {br(TAXA_FIXA_POUPANCA_AA * 100, 1)}% a.a."
    return {
        "total": round(total_poup, 2),
        "rent_pct": round(rent_poup, 2),
        "aproximacao": True,
        "fonte": "taxa_fixa",
        "aviso": aviso_fixo,
    }


def calcular_simulador_realidade_vs_guru(
    ticker: str,
    preco_atual: float,
    div_12m: float = 0.0,
    historico_mensal: list[dict] | None = None,
) -> dict:
    try:
        p0 = float(preco_atual) if preco_atual is not None else 0.0
    except (TypeError, ValueError):
        p0 = 0.0
    try:
        d12 = float(div_12m) if div_12m is not None else 0.0
    except (TypeError, ValueError):
        d12 = 0.0

    hist_valido = [
        m
        for m in (historico_mensal or [])
        if isinstance(m, dict) and (numero(m.get("fechamento")) or 0) > 0
    ]

    serie_cdi_5a = obter_serie_sgs_bcb(12, anos=5, timeout=5.0)
    serie_poup_5a = obter_serie_sgs_bcb(195, anos=5, timeout=5.0)

    opcoes_aporte = [1000, 5000, 10000]
    prazos = ["1y", "2y", "5y"]
    prazos_anos = [1, 2, 5]

    matriz = {}
    for cap in opcoes_aporte:
        c0 = float(cap)
        sub = {}
        for t in prazos_anos:
            prazo_key = f"{t}y"
            meses_necessarios = t * 12

            if len(hist_valido) >= meses_necessarios:
                fatia = hist_valido[-meses_necessarios:]
                ponto_ini = fatia[0]
                cotacao_ini = float(ponto_ini["fechamento"])
                mes_ano_ini = str(ponto_ini.get("mes_ano") or "")
                cotacao_fim = p0 if p0 > 0 else float(fatia[-1]["fechamento"])
                cotas = c0 / cotacao_ini
                div_por_cota_periodo = sum(float(numero(m.get("dividendos")) or 0.0) for m in fatia)
                proventos_total = round(cotas * div_por_cota_periodo, 2)
                patrimonio_acoes = round(cotas * cotacao_fim, 2)
                total_acao = round(patrimonio_acoes + proventos_total, 2)
                rent_acao = round(((total_acao - c0) / c0) * 100.0, 2)
                frase = (
                    f"Se você tivesse investido R$ {inteiro_br(c0)} em {mes_ano_ini}, "
                    f"hoje teria R$ {br(total_acao, 2)}, sendo R$ {br(proventos_total, 2)} em proventos."
                )
                bloco_acao = {
                    "disponivel": True,
                    "total": total_acao,
                    "rent_pct": rent_acao,
                    "dividendos": proventos_total,
                    "patrimonio": patrimonio_acoes,
                    "cotas": round(cotas, 4),
                    "cotacao_inicial": round(cotacao_ini, 2),
                    "mes_ano_inicio": mes_ano_ini,
                    "mensagem": "",
                    "frase": frase,
                }
            else:
                msg_curto = f"listada há menos de {t} anos"
                bloco_acao = {
                    "disponivel": False,
                    "total": None,
                    "rent_pct": None,
                    "dividendos": None,
                    "patrimonio": None,
                    "cotas": None,
                    "cotacao_inicial": None,
                    "mes_ano_inicio": None,
                    "mensagem": msg_curto,
                    "frase": msg_curto,
                }

            bloco_cdi = _acumular_cdi_sgs_ou_fixo(c0, t, serie_cdi_5a)
            bloco_poup = _acumular_poupanca_sgs_ou_fixo(c0, t, serie_poup_5a)

            meses = 12 * t
            fator_guru = (1.05) ** meses
            total_guru = c0 * fator_guru
            rent_guru = (fator_guru - 1.0) * 100.0

            bloco_prazo = {
                "acao": bloco_acao,
                "cdi": bloco_cdi,
                "poupanca": bloco_poup,
                "guru": {
                    "total": round(total_guru, 2),
                    "rent_pct": round(rent_guru, 2),
                    "status": "ILUSÃO MATEMÁTICA",
                },
            }
            sub[prazo_key] = bloco_prazo
            sub[str(t)] = bloco_prazo
        matriz[str(cap)] = sub

    veredito_texto = (
        "🚨 DESMISTIFICAÇÃO MATEMÁTICA DO PROFESSOR DA B3:\n"
        "Se a promessa de '5% ao mês garantido' fosse verdadeira, você transformaria R$ 5.000 em R$ 93.395 em apenas 5 anos "
        "e em mais de R$ 1,7 milhão em 10 anos! Se isso fosse sustentável, nenhum banco emprestaria a 1% ao mês e em 20 anos esse "
        "método concentraria mais dinheiro do que o PIB de todo o planeta Terra. "
        "Na vida real, a riqueza sustentável vem de empresas reais que lucram, geram empregos e pagam proventos factuais auditados."
    )

    padrao_2y = matriz["5000"]["2y"]
    return {
        "ticker": ticker,
        "preco_referencia": p0,
        "div_12m_referencia": d12,
        "opcoes_aporte": opcoes_aporte,
        "prazos": prazos,
        "prazos_anos": prazos_anos,
        "padrao": {"aporte": 5000, "prazo": "2y"},
        "aviso_cdi": padrao_2y["cdi"]["aviso"],
        "aviso_poupanca": padrao_2y["poupanca"]["aviso"],
        "aproximacao_taxa_fixa": bool(padrao_2y["cdi"]["aproximacao"] or padrao_2y["poupanca"]["aproximacao"]),
        "matriz": matriz,
        "1000": matriz["1000"],
        "5000": matriz["5000"],
        "10000": matriz["10000"],
        1000: matriz["1000"],
        5000: matriz["5000"],
        10000: matriz["10000"],
        "veredito_anti_guru": veredito_texto,
        "veredito_guru": veredito_texto,
    }


# ===========================================================================
# BLOCO 3 (Camada Pro): Pares Setoriais & Termômetro Macroeconômico
# ===========================================================================
PARES_SETORIAIS = {
    # Bancos e Financeiro
    "BBAS3": ["ITUB4", "BBDC4", "SANB11"],
    "ITUB4": ["BBDC4", "BBAS3", "SANB11"],
    "BBDC4": ["ITUB4", "BBAS3", "SANB11"],
    "SANB11": ["ITUB4", "BBDC4", "BBAS3"],
    "BPAC11": ["ITUB4", "BBAS3", "ROXO34"],
    "BBSE3": ["CXSE3", "PSSA3"],
    "CXSE3": ["BBSE3", "PSSA3"],
    "PSSA3": ["BBSE3", "CXSE3"],
    "B3SA3": ["BPAC11", "ITUB4"],
    "ROXO34": ["ITUB4", "BBDC4", "BPAC11"],

    # Petróleo, Gás & Energia
    "PETR4": ["PRIO3", "VBBR3", "RECV3"],
    "PETR3": ["PETR4", "PRIO3", "RECV3"],
    "PRIO3": ["PETR4", "RECV3", "BRAV3"],
    "RECV3": ["PRIO3", "BRAV3", "PETR4"],
    "BRAV3": ["PRIO3", "RECV3", "PETR4"],
    "UGPA3": ["VBBR3", "RAIZ4"],
    "VBBR3": ["UGPA3", "RAIZ4"],
    "RAIZ4": ["UGPA3", "VBBR3", "CSAN3"],
    "CSAN3": ["RAIZ4", "UGPA3"],

    # Mineração & Siderurgia
    "VALE3": ["CSNA3", "GGBR4", "USIM5"],
    "CSNA3": ["VALE3", "GGBR4", "USIM5"],
    "GGBR4": ["CSNA3", "USIM5", "VALE3"],
    "USIM5": ["CSNA3", "GGBR4", "VALE3"],

    # Elétricas e Saneamento
    "TAEE11": ["TRPL4", "CPLE6", "EGIE3"],
    "TRPL4": ["TAEE11", "EGIE3", "CPFE3"],
    "EGIE3": ["TAEE11", "CPFE3", "AURE3"],
    "CPFE3": ["EQTL3", "EGIE3", "CMIG4"],
    "EQTL3": ["CPFE3", "CMIG4", "EGIE3"],
    "CMIG4": ["CPFE3", "EQTL3", "ELET3"],
    "ELET3": ["CPFE3", "EQTL3", "EGIE3"],
    "SBSP3": ["SAPR4", "CSMG3"],
    "SAPR4": ["SBSP3", "CSMG3"],
    "SAPR11": ["SBSP3", "CSMG3"],
    "CSMG3": ["SBSP3", "SAPR4"],

    # Varejo e Consumo
    "MGLU3": ["LREN3", "ASAI3", "CRFB3"],
    "LREN3": ["MGLU3", "ARZZ3"],
    "ARZZ3": ["LREN3", "MGLU3"],
    "ASAI3": ["CRFB3", "GMAT3"],
    "CRFB3": ["ASAI3", "GMAT3"],
    "ABEV3": ["MDIA3", "JBSS3"],
    "RADL3": ["PNVL3", "HYPE3"],
    "BHIA3": ["MGLU3", "LREN3"],

    # Indústria & Máquinas
    "WEGE3": ["EMBR3", "TUPY3"],
    "EMBR3": ["WEGE3", "TUPY3"],
    "TUPY3": ["WEGE3", "EMBR3"],
    "POMO4": ["RAPT4", "TUPY3"],

    # Frigoríficos & Agro
    "JBSS3": ["BRFS3", "MRFG3", "BEEF3"],
    "BRFS3": ["JBSS3", "MRFG3", "BEEF3"],
    "MRFG3": ["JBSS3", "BRFS3", "BEEF3"],
    "BEEF3": ["JBSS3", "MRFG3", "BRFS3"],
    "SMTO3": ["SLCE3", "RAIZ4"],
    "SLCE3": ["SMTO3", "AGRO3"],

    # Saúde & Telecom
    "RDOR3": ["HAPV3", "FLRY3"],
    "HYPE3": ["RADL3", "BLAU3"],
    "VIVT3": ["TIMS3"],
    "TIMS3": ["VIVT3"],

    # FIIs
    "HGLG11": ["BTLG11", "XPLG11"],
    "BTLG11": ["HGLG11", "XPLG11"],
    "XPLG11": ["HGLG11", "BTLG11"],
    "MXRF11": ["KNCR11", "KNIP11"],
    "KNCR11": ["MXRF11", "KNIP11"],
    "KNIP11": ["MXRF11", "KNCR11"],
    "XPML11": ["VISC11"],
    "VISC11": ["XPML11"],
}

def _nome_catalogo_ou_ticker(ticker: str) -> str:
    tk = (ticker or "").upper().strip()
    for cod, nome in CATALOGO_B3:
        if cod == tk:
            return nome
    return tk


def _coletar_rival_yfinance(rival: str) -> dict:
    """
    Coleta indicadores reais do rival via yfinance em instância própria de yf.Ticker (Regra R8).
    Em caso de falha ou ausência de cotação, marca a linha como 'indisponível no momento'.
    """
    simbolo_sa = f"{rival}.SA"
    nome_padrao = _nome_catalogo_ou_ticker(rival)
    indisponivel = {
        "ticker": rival,
        "nome": nome_padrao,
        "disponivel": False,
        "status": "indisponível no momento",
        "preco": None,
        "p_l": None,
        "p_vp": None,
        "dy": None,
        "roe": None,
        "margem_liquida": None,
        "divida_ebitda": None,
    }
    try:
        info = yf.Ticker(simbolo_sa).info or {}
        preco = (
            numero(info.get("currentPrice"))
            or numero(info.get("regularMarketPrice"))
            or numero(info.get("previousClose"))
        )
        if not preco or preco <= 0:
            return indisponivel

        nome = (info.get("shortName") or info.get("longName") or nome_padrao)[:120]
        p_l = arredondar(info.get("trailingPE"))
        p_vp = arredondar(info.get("priceToBook"))

        div_rate = numero(info.get("trailingAnnualDividendRate")) or numero(info.get("dividendRate"))
        dy_raw = numero(info.get("trailingAnnualDividendYield")) or numero(info.get("dividendYield"))
        if div_rate is not None and div_rate >= 0:
            dy_pct = round((div_rate / preco) * 100.0, 2)
        elif dy_raw is not None and dy_raw >= 0:
            dy_pct = round(dy_raw * 100.0 if dy_raw <= 1.0 else dy_raw, 2)
        else:
            dy_pct = None

        roe_raw = numero(info.get("returnOnEquity"))
        roe_pct = round(roe_raw * 100.0, 2) if roe_raw is not None else None

        margem_raw = numero(info.get("profitMargins"))
        margem_pct = round(margem_raw * 100.0, 2) if margem_raw is not None else None

        div_total = numero(info.get("totalDebt"))
        caixa_total = numero(info.get("totalCash"))
        ebitda = numero(info.get("ebitda"))
        div_liq = (div_total - caixa_total) if (div_total is not None and caixa_total is not None) else None
        div_ebitda = round(div_liq / ebitda, 2) if (div_liq is not None and ebitda and ebitda > 0) else None

        return {
            "ticker": rival,
            "nome": nome,
            "disponivel": True,
            "status": "ok",
            "preco": round(float(preco), 2),
            "p_l": p_l,
            "p_vp": p_vp,
            "dy": dy_pct,
            "roe": roe_pct,
            "margem_liquida": margem_pct,
            "divida_ebitda": div_ebitda,
        }
    except Exception:
        return indisponivel


def obter_comparativo_setorial(ticker: str, setor: str = "", timeout: float = 5.0) -> list[dict]:
    tk = (ticker or "").upper().strip()
    lista_pares = PARES_SETORIAIS.get(tk)
    if not lista_pares:
        raiz = re.sub(r"\d+$", "", tk)
        for cand in [f"{raiz}4", f"{raiz}3", f"{raiz}11"]:
            if cand in PARES_SETORIAIS:
                lista_pares = PARES_SETORIAIS[cand]
                break

    if not lista_pares:
        return []

    rivais = [r for r in lista_pares if r != tk][:3]
    if not rivais:
        return []

    resultado: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(rivais)) as executor:
        futuros = [(rival, executor.submit(_coletar_rival_yfinance, rival)) for rival in rivais]
        for rival, fut in futuros:
            try:
                resultado.append(fut.result(timeout=timeout))
            except Exception:
                resultado.append(
                    {
                        "ticker": rival,
                        "nome": _nome_catalogo_ou_ticker(rival),
                        "disponivel": False,
                        "status": "indisponível no momento",
                        "preco": None,
                        "p_l": None,
                        "p_vp": None,
                        "dy": None,
                        "roe": None,
                        "margem_liquida": None,
                        "divida_ebitda": None,
                    }
                )
    return resultado


def calcular_termometro_macro(ticker: str, setor: str = "") -> dict:
    tk = (ticker or "").upper().strip()
    setor_norm = (setor or "").lower()

    is_petroleo = "petr" in tk.lower() or "prio" in tk.lower() or "energy" in setor_norm or "petr" in setor_norm
    is_banco = "bbas" in tk.lower() or "itub" in tk.lower() or "bbdc" in tk.lower() or "sanb" in tk.lower() or "financial" in setor_norm or "banc" in setor_norm
    is_mineracao = "vale" in tk.lower() or "csna" in tk.lower() or "ggbr" in tk.lower() or "basic materials" in setor_norm or "miner" in setor_norm
    is_eletrica = "taee" in tk.lower() or "trpl" in tk.lower() or "egie" in tk.lower() or "utilities" in setor_norm or "eletric" in setor_norm or "sane" in setor_norm
    is_varejo = "mglu" in tk.lower() or "lren" in tk.lower() or "consumer" in setor_norm or "varejo" in setor_norm

    if is_petroleo:
        res = {
            "commodities": {"sensibilidade": "Alta", "direcao": "Positiva", "impacto": "Cotação do barril de petróleo Brent no mercado internacional dita diretamente as receitas e rentabilidade da produção."},
            "selic": {"sensibilidade": "Média", "direcao": "Neutra", "impacto": "Afeta custos locais de financiamento, mas receitas são fortemente atreladas à moeda forte."},
            "cambio": {"sensibilidade": "Alta", "direcao": "Positiva", "impacto": "Dólar valorizado frente ao Real amplia a conversão das receitas de exportação e paridade interna de combustíveis."},
            "ipca": {"sensibilidade": "Baixa", "direcao": "Neutra", "impacto": "Pouco sensível à inflação brasileira doméstica, pois os preços seguem cotações globais."},
            "risco_brasil": {"sensibilidade": "Alta", "direcao": "Negativa", "impacto": "Incertezas políticas ou de intervenção estatal elevam o desconto exigido pelo mercado."},
        }
    elif is_banco:
        res = {
            "selic": {"sensibilidade": "Alta", "direcao": "Positiva", "impacto": "Taxas de juros elevadas sustentam spreads bancários robustos e maior remuneração sobre o capital de giro."},
            "ipca": {"sensibilidade": "Média", "direcao": "Neutra", "impacto": "Inflação moderada eleva demanda por crédito, mas picos de inflação podem pressionar inadimplência."},
            "cambio": {"sensibilidade": "Baixa", "direcao": "Neutra", "impacto": "Operações concentradas no mercado doméstico em moeda local com proteções de hedge."},
            "commodities": {"sensibilidade": "Média", "direcao": "Positiva", "impacto": "Commodities em alta fortalecem o agronegócio e a carteira de crédito rural."},
            "risco_brasil": {"sensibilidade": "Alta", "direcao": "Negativa", "impacto": "Aumento do prêmio de risco do país encarece captação e eleva provisões contra perdas (PDD)."},
        }
    elif is_mineracao:
        res = {
            "commodities": {"sensibilidade": "Alta", "direcao": "Positiva", "impacto": "Preço internacional do minério de ferro em Qingdao dita a geração de caixa e dividendos."},
            "cambio": {"sensibilidade": "Alta", "direcao": "Positiva", "impacto": "Receitas 100% dolarizadas enquanto a maior parte dos custos operacionais de lavra é em reais."},
            "selic": {"sensibilidade": "Baixa", "direcao": "Neutra", "impacto": "Dívidas corporativas contratadas prioritariamente no mercado internacional em dólares."},
            "ipca": {"sensibilidade": "Baixa", "direcao": "Neutra", "impacto": "Demanda praticamente toda externa (China/Ásia/Europa) desacoplada do consumo doméstico."},
            "risco_brasil": {"sensibilidade": "Média", "direcao": "Negativa", "impacto": "Afeta o fluxo de capital estrangeiro em ações da B3."},
        }
    elif is_eletrica:
        res = {
            "ipca": {"sensibilidade": "Alta", "direcao": "Positiva", "impacto": "Contratos de concessão reajustados anualmente por IPCA ou IGP-M blindam o poder de compra."},
            "selic": {"sensibilidade": "Alta", "direcao": "Negativa", "impacto": "Setor intensivo em capital com dívidas atreladas ao CDI; Selic alta aumenta despesas financeiras."},
            "commodities": {"sensibilidade": "Baixa", "direcao": "Neutra", "impacto": "Demanda essencial de energia com receita permitida regulada pelo governo."},
            "cambio": {"sensibilidade": "Baixa", "direcao": "Neutra", "impacto": "Contratos e custos predominantemente em moeda local."},
            "risco_brasil": {"sensibilidade": "Média", "direcao": "Negativa", "impacto": "Segurança jurídica e estabilidade das agências reguladoras (Aneel) são fundamentais."},
        }
    elif is_varejo:
        res = {
            "selic": {"sensibilidade": "Alta", "direcao": "Negativa", "impacto": "Juros altos encarecem o crediário para o consumidor e encarecem o desconto de recebíveis de cartões."},
            "ipca": {"sensibilidade": "Alta", "direcao": "Negativa", "impacto": "Inflação corrói a renda disponível das famílias e reduz o poder de compra de bens duráveis."},
            "cambio": {"sensibilidade": "Média", "direcao": "Negativa", "impacto": "Dólar alto encarece produtos eletrônicos importados e componentes."},
            "commodities": {"sensibilidade": "Baixa", "direcao": "Neutra", "impacto": "Impacto indireto através do custo de embalagens e logística."},
            "risco_brasil": {"sensibilidade": "Média", "direcao": "Negativa", "impacto": "Confiança do consumidor e nível de emprego são os motores do varejo."},
        }
    else:
        res = {
            "selic": {"sensibilidade": "Média", "direcao": "Neutra", "impacto": "Influencia o custo de financiamento de capital de giro e investimentos."},
            "ipca": {"sensibilidade": "Média", "direcao": "Neutra", "impacto": "Capacidade da empresa de repassar custos de inflação aos clientes finais."},
            "cambio": {"sensibilidade": "Média", "direcao": "Neutra", "impacto": "Equilíbrio entre insumos importados e eventuais receitas de exportação."},
            "commodities": {"sensibilidade": "Média", "direcao": "Neutra", "impacto": "Preços de insumos energéticos, agrícolas ou minerais utilizados na operação."},
            "risco_brasil": {"sensibilidade": "Média", "direcao": "Negativa", "impacto": "Condições gerais de liquidez e confiança no mercado de capitais."},
        }

    for k in list(res.keys()):
        res[k.upper()] = res[k]
    return res


# ===========================================================================
# BLOCO 4: Simulador de Premissas do Usuário & Tradução Absoluta R1
# ===========================================================================
AVISO_PREMISSAS_USUARIO = (
    "Isto não é previsão nem recomendação. É só a conta das premissas que você escolheu."
)


def simular_premissas_usuario(
    dados: dict,
    crescimento_lucro: float = 0.0,
    pl_futuro: float = 10.0,
    anos: int = 1,
) -> dict:
    """
    Realiza exclusivamente a aritmética das premissas escolhidas pelo usuário:
    - LPA futuro = LPA atual * (1 + crescimento_lucro) ** anos
    - Valor da cota nessa hipótese = LPA futuro * pl_futuro
    - Proventos = DY atual aplicado ano a ano (preco_atual * dy_atual * anos)
    Sem valores inventados quando não há cotação ou lucro positivo nos últimos 12 meses.
    """
    dados = dados or {}
    cot = dados.get("Cotacao_e_Mercado") or {}
    val = dados.get("Valuation") or {}
    prov = dados.get("Proventos") or {}
    num = dados.get("Numeros") or {}

    preco_atual = numero(cot.get("Preco_Atual"))
    if preco_atual is None:
        preco_atual = numero(num.get("preco_atual"))

    lpa_atual = numero(val.get("LPA"))

    val_dy = prov.get("Dividend_Yield_Real_12m")
    if val_dy is not None:
        dy_atual = float(numero(val_dy) or 0.0)
    elif prov.get("dividend_yield_12m") is not None:
        dy_atual = float(numero(prov.get("dividend_yield_12m")) or 0.0)
    elif num.get("dy_12m") is not None:
        dy_atual = (float(numero(num.get("dy_12m")) or 0.0)) / 100.0
    else:
        dy_atual = 0.0
    if dy_atual < 0:
        dy_atual = 0.0

    if preco_atual is None or preco_atual <= 0:
        return {
            "disponivel": False,
            "motivo": "não dá para simular: sem cotação",
            "preco_atual": None,
            "lpa_atual": lpa_atual,
            "dy_atual": round(dy_atual, 6),
            "aviso": AVISO_PREMISSAS_USUARIO,
        }

    if lpa_atual is None or lpa_atual <= 0:
        return {
            "disponivel": False,
            "motivo": "não dá para simular: a empresa não teve lucro nos últimos 12 meses",
            "preco_atual": round(float(preco_atual), 2),
            "lpa_atual": lpa_atual,
            "dy_atual": round(dy_atual, 6),
            "aviso": AVISO_PREMISSAS_USUARIO,
        }

    g_raw = float(numero(crescimento_lucro) or 0.0)
    g = (g_raw / 100.0) if abs(g_raw) > 1.0 else g_raw
    pl_esc = float(numero(pl_futuro) or 10.0)
    n_anos = max(1, int(numero(anos) or 1))

    lpa_futuro = float(lpa_atual) * ((1.0 + g) ** n_anos)
    valor_cota_hipotese = lpa_futuro * pl_esc
    proventos_por_ano = float(preco_atual) * dy_atual
    proventos_acumulados = proventos_por_ano * n_anos
    valor_total_hipotese = valor_cota_hipotese + proventos_acumulados
    variacao_total_pct = ((valor_total_hipotese - float(preco_atual)) / float(preco_atual)) * 100.0

    return {
        "disponivel": True,
        "motivo": "",
        "preco_atual": round(float(preco_atual), 2),
        "lpa_atual": round(float(lpa_atual), 4),
        "dy_atual": round(dy_atual, 6),
        "crescimento_lucro": round(g, 4),
        "pl_futuro": round(pl_esc, 2),
        "anos": n_anos,
        "lpa_futuro": round(lpa_futuro, 4),
        "valor_cota_hipotese": round(valor_cota_hipotese, 2),
        "proventos_acumulados": round(proventos_acumulados, 2),
        "valor_total_hipotese": round(valor_total_hipotese, 2),
        "variacao_total_pct": round(variacao_total_pct, 2),
        "aviso": AVISO_PREMISSAS_USUARIO,
    }


TRADUCAO_MULTIPLOS_R1 = {
    "P_L": {
        "nome": "Preço sobre Lucro (P/L)",
        "analogia": "Tempo para o investimento se pagar sozinho. Se uma padaria custa R$ 100 mil e gera R$ 20 mil de lucro limpo ao ano, o P/L é 5 (em 5 anos você recupera o valor investido).",
        "o_que_e": "Tempo para o investimento se pagar sozinho através dos lucros gerados pela operação da empresa.",
        "status_empresa": "P/L abaixo de 8x indica ação descontada ou sob desconfiança do mercado; acima de 15x indica que o mercado precifica forte crescimento futuro.",
        "como_avaliar": "Comparar com a média histórica da própria empresa e com os principais rivais do mesmo setor na B3.",
        "impacto_bolso": "Mostra em quantos anos o lucro da empresa devolve 100% do dinheiro que saiu do seu bolso para comprar a ação.",
        "bolso": "Mostra em quantos anos o lucro da empresa devolve 100% do dinheiro investido.",
        "impacto": "Mostra em quantos anos o lucro da empresa devolve 100% do dinheiro investido.",
    },
    "P_VP": {
        "nome": "Preço sobre Valor Patrimonial (P/VP)",
        "analogia": "Comprar patrimônio físico por menos do que ele custa para construir. P/VP de 0,80 significa comprar R$ 1,00 em fábricas, máquinas e prédios por 80 centavos.",
        "o_que_e": "Relação entre a cotação de mercado da empresa e o valor contábil líquido de todos os seus bens e patrimônio.",
        "status_empresa": "P/VP abaixo de 1,0x indica desconto patrimonial em relação ao balanço; acima de 2,0x reflete intangíveis, marcas fortes ou rentabilidade diferenciada.",
        "como_avaliar": "Essencial para bancos, indústrias de capital intensivo e Fundos Imobiliários.",
        "impacto_bolso": "Oferece uma margem de segurança física: você está pagando menos do que o patrimônio da empresa valeria caso fosse liquidado.",
        "bolso": "Oferece uma margem de segurança física: você compra bens reais com desconto.",
        "impacto": "Oferece uma margem de segurança física: você compra bens reais com desconto.",
    },
    "ROE": {
        "nome": "Retorno sobre Patrimônio Líquido (ROE)",
        "analogia": "A velocidade da máquina de multiplicar dinheiro. Se você coloca R$ 100 mil no seu negócio e ele lucra R$ 20 mil no ano, seu ROE foi de 20%.",
        "o_que_e": "Percentual de lucro líquido que a empresa gera para cada real de patrimônio próprio investido.",
        "status_empresa": "ROE acima de 15% indica uma máquina excelente de capital; acima de 20% coloca a empresa entre as mais rentáveis do país.",
        "como_avaliar": "Empresas com alto ROE sustentável conseguem reinvestir em si mesmas e crescer sem precisar se endividar excessivamente.",
        "impacto_bolso": "ROE alto mostra que a empresa gera mais lucro por real de patrimônio; isso não garante valorização da ação nem dividendos crescentes.",
        "bolso": "ROE alto significa mais lucro por real de patrimônio, sem garantia de valorização ou de proventos crescentes.",
        "impacto": "ROE alto significa mais lucro por real de patrimônio, sem garantia de valorização ou de proventos crescentes.",
    },
    "GRAHAM": {
        "nome": "Preço Justo de Benjamin Graham",
        "analogia": "Uma âncora de bom senso contra a euforia e a depressão da Bolsa. É o cálculo do valor intrínseco que equilibra lucros e patrimônio sem otimismo exagerado.",
        "o_que_e": "Fórmula matemática clássica √(22,5 × LPA × VPA) desenvolvida pelo mentor de Warren Buffett para encontrar ações com margem de segurança.",
        "status_empresa": "Cotação abaixo do Preço Graham indica margem de segurança; cotação acima indica ausência de desconto pela métrica clássica.",
        "como_avaliar": "Não se aplica a Fundos Imobiliários nem a empresas com prejuízo contábil recente.",
        "impacto_bolso": "Serve como referência para comparar o preço com lucro e patrimônio contábil; não impede perdas.",
        "bolso": "Referência de preço que exige margem de desconto; não impede perdas.",
        "impacto": "Referência de preço que exige margem de desconto; não impede perdas.",
    },
    "BAZIN": {
        "nome": "Preço Teto de Décio Bazin",
        "analogia": "O limite prudente para viver de renda. É o valor máximo a pagar por uma ação em que o dividendo dos últimos 12 meses ainda representa 6% ao ano sobre o valor pago.",
        "o_que_e": "Cálculo baseado na regra de Décio Bazin que divide os proventos dos últimos 12 meses por 0,06 (retorno mínimo esperado em dividendos).",
        "status_empresa": "Ação abaixo do teto de Bazin significa que os proventos dos últimos 12 meses superaram 6% do preço atual, sem garantia para o próximo ano; acima do teto, a rentabilidade de proventos fica diluída.",
        "como_avaliar": "Métrica de ouro para estratégias previdenciárias de aposentadoria com ações pagadoras de proventos.",
        "impacto_bolso": "Indica se os proventos dos últimos 12 meses equivalem a 6% do preço atual. Proventos futuros dependem dos lucros da empresa e podem cair ou cessar.",
        "bolso": "Referência de 6% ao ano calculada sobre proventos passados, sem garantia de repetição.",
        "impacto": "Referência de 6% ao ano calculada sobre proventos passados, sem garantia de repetição.",
    },
    "DIVIDA_EBITDA": {
        "nome": "Alavancagem Financeira (Dívida Líquida / EBITDA)",
        "analogia": "Quantos anos de salário seriam necessários para quitar todas as dívidas do cartão e empréstimos se você parasse de gastar com qualquer outra coisa.",
        "o_que_e": "Mede o endividamento líquido da empresa em relação ao seu lucro operacional anual em caixa.",
        "status_empresa": "Abaixo de 2,0x é considerada saudável; entre 2,0x e 3,5x exige atenção; acima de 3,5x indica endividamento de risco que consome lucros em juros.",
        "como_avaliar": "Empresas com concessões reguladas previsíveis (elétricas) toleram dívidas maiores do que empresas de varejo e consumo cíclico.",
        "impacto_bolso": "Dívida controlada deixa mais espaço para o lucro virar dividendos, em vez de pagar juros aos bancos.",
        "bolso": "Dívida controlada ajuda o lucro a virar dividendos para você, em vez de juros bancários.",
        "impacto": "Dívida controlada ajuda o lucro a virar dividendos para você, em vez de juros bancários.",
    },
}
TRADUCAO_MULTIPLOS_R1["p_l"] = TRADUCAO_MULTIPLOS_R1["P_L"]
TRADUCAO_MULTIPLOS_R1["p_vp"] = TRADUCAO_MULTIPLOS_R1["P_VP"]
TRADUCAO_MULTIPLOS_R1["roe"] = TRADUCAO_MULTIPLOS_R1["ROE"]
TRADUCAO_MULTIPLOS_R1["graham"] = TRADUCAO_MULTIPLOS_R1["GRAHAM"]
TRADUCAO_MULTIPLOS_R1["bazin"] = TRADUCAO_MULTIPLOS_R1["BAZIN"]
TRADUCAO_MULTIPLOS_R1["divida_ebitda"] = TRADUCAO_MULTIPLOS_R1["DIVIDA_EBITDA"]


def traduzir_multiplos_ativo(dados: dict) -> dict:
    val = (dados or {}).get("Valuation") or {}
    rent = (dados or {}).get("Rentabilidade_e_Eficiencia") or {}
    end = (dados or {}).get("Endividamento_e_Caixa") or {}
    meta = (dados or {}).get("Metadados") or {}
    eh_fii = meta.get("tipo") == "FII" or (dados or {}).get("Identificacao", {}).get("Tipo_Codigo") == "FII"

    pl_val = val.get("P_L")
    pvp_val = val.get("P_VP")
    roe_val = rent.get("ROE")
    graham_val = val.get("Valor_Intrinseco_Graham")
    bazin_val = val.get("Preco_Teto_Bazin_6pct")
    alav_val = end.get("Divida_Liquida_sobre_EBITDA")

    resultado = {}

    # P/L
    item_pl = dict(TRADUCAO_MULTIPLOS_R1["P_L"])
    item_pl["valor"] = pl_val
    if eh_fii:
        item_pl["situacao"] = "Não se aplica a FIIs (utiliza-se P/VP e Dividend Yield)"
        item_pl["classe_css"] = ""
    elif pl_val is None or pl_val <= 0:
        item_pl["situacao"] = "Prejuízo recente ou sem dados de lucro"
        item_pl["classe_css"] = "neg"
    elif pl_val < 8:
        item_pl["situacao"] = f"Descontado ({pl_val}x lucros)"
        item_pl["classe_css"] = "pos"
    elif pl_val <= 14:
        item_pl["situacao"] = f"Justo / Em linha ({pl_val}x lucros)"
        item_pl["classe_css"] = ""
    else:
        item_pl["situacao"] = f"Esticado / Precificado com otimismo ({pl_val}x lucros)"
        item_pl["classe_css"] = "neg"
    resultado["P_L"] = item_pl

    # P/VP
    item_pvp = dict(TRADUCAO_MULTIPLOS_R1["P_VP"])
    item_pvp["valor"] = pvp_val
    if pvp_val is None:
        item_pvp["situacao"] = "N/D"
        item_pvp["classe_css"] = ""
    elif pvp_val < 0:
        item_pvp["situacao"] = f"Patrimônio Líquido Negativo (Alto Risco) ({pvp_val}x)"
        item_pvp["status"] = "Patrimônio Líquido Negativo (Alto Risco)"
        item_pvp["status_empresa"] = "Patrimônio Líquido Negativo (Alto Risco)"
        item_pvp["classe_css"] = "neg"
        item_pvp["impacto_bolso"] = (
            "Atenção: a empresa deve mais do que tem em bens (passivo a descoberto). "
            "Não há proteção patrimonial para o investidor."
        )
        item_pvp["bolso"] = item_pvp["impacto_bolso"]
        item_pvp["impacto"] = item_pvp["impacto_bolso"]
    elif pvp_val < 1.0:
        item_pvp["situacao"] = f"Desconto patrimonial ({pvp_val}x do patrimônio)"
        item_pvp["classe_css"] = "pos"
    elif pvp_val <= 1.5:
        item_pvp["situacao"] = f"Alinhado ao patrimônio ({pvp_val}x)"
        item_pvp["classe_css"] = ""
    else:
        item_pvp["situacao"] = f"Prêmio sobre patrimônio ({pvp_val}x)"
        item_pvp["classe_css"] = ""
    resultado["P_VP"] = item_pvp

    # ROE
    item_roe = dict(TRADUCAO_MULTIPLOS_R1["ROE"])
    item_roe["valor"] = roe_val
    if eh_fii or roe_val is None:
        item_roe["situacao"] = "Não aplicável a FIIs" if eh_fii else "N/D"
        item_roe["classe_css"] = ""
    elif roe_val >= 0.20:
        item_roe["situacao"] = f"Altamente Eficiente ({round(roe_val * 100, 1)}%)"
        item_roe["classe_css"] = "pos"
    elif roe_val >= 0.12:
        item_roe["situacao"] = f"Rentabilidade Saudável ({round(roe_val * 100, 1)}%)"
        item_roe["classe_css"] = ""
    else:
        item_roe["situacao"] = f"Rentabilidade Baixa ({round(roe_val * 100, 1)}%)"
        item_roe["classe_css"] = "neg"
    resultado["ROE"] = item_roe

    # Graham
    item_graham = dict(TRADUCAO_MULTIPLOS_R1["GRAHAM"])
    item_graham["valor"] = graham_val
    margem_g = val.get("Margem_Graham_Pct")
    if eh_fii:
        item_graham["situacao"] = "Não se aplica a FII/ETF"
        item_graham["classe_css"] = ""
    elif graham_val is None:
        item_graham["situacao"] = meta.get("obs_graham") or "Lucro ou patrimônio negativo"
        item_graham["classe_css"] = ""
    elif margem_g is not None and margem_g >= 0:
        item_graham["situacao"] = f"Com Margem de Segurança (+{margem_g}%)"
        item_graham["classe_css"] = "pos"
    else:
        item_graham["situacao"] = f"Sem Margem de Segurança ({margem_g}%)"
        item_graham["classe_css"] = "neg"
    resultado["GRAHAM"] = item_graham

    # Bazin
    item_bazin = dict(TRADUCAO_MULTIPLOS_R1["BAZIN"])
    item_bazin["valor"] = bazin_val
    margem_b = val.get("Margem_Bazin_Pct")
    if bazin_val is None:
        item_bazin["situacao"] = "Sem proventos nos últimos 12 meses"
        item_bazin["classe_css"] = ""
    elif margem_b is not None and margem_b >= 0:
        item_bazin["situacao"] = f"Abaixo do Preço Teto (+{margem_b}% de margem)"
        item_bazin["classe_css"] = "pos"
    else:
        item_bazin["situacao"] = f"Acima do Preço Teto ({margem_b}%)"
        item_bazin["classe_css"] = "neg"
    resultado["BAZIN"] = item_bazin

    # Dívida / EBITDA
    item_div = dict(TRADUCAO_MULTIPLOS_R1["DIVIDA_EBITDA"])
    item_div["valor"] = alav_val
    if eh_fii or alav_val is None:
        item_div["situacao"] = "Não se aplica / Sem alavancagem de dívida corporativa" if eh_fii else "N/D"
        item_div["classe_css"] = ""
    elif alav_val < 0:
        item_div["situacao"] = "Caixa Líquido Positivo (Mais dinheiro em caixa do que dívidas)"
        item_div["classe_css"] = "pos"
    elif alav_val <= 2.0:
        item_div["situacao"] = f"Endividamento Saudável ({alav_val}x EBITDA)"
        item_div["classe_css"] = "pos"
    elif alav_val <= 3.5:
        item_div["situacao"] = f"Endividamento Moderado ({alav_val}x EBITDA)"
        item_div["classe_css"] = ""
    else:
        item_div["situacao"] = f"Endividamento Alto / Alerta de Risco ({alav_val}x EBITDA)"
        item_div["classe_css"] = "neg"
    resultado["DIVIDA_EBITDA"] = item_div

    for ch_m, val_m in resultado.items():
        val_m["leitura"] = val_m.get("situacao", "")
    return resultado


def checar_coerencia_indicadores(dados: dict) -> list[dict]:
    """
    Quando um indicador chama atenção sozinho, confere o indicador que costuma desmenti-lo:
    P/L baixo -> lucro recorrente; ROE alto -> ROIC (aproximado por ROA) + alavancagem;
    lucro crescendo -> caixa operacional; yield alto -> payout + FCL; crescimento -> dívida líquida/EBITDA.
    Só entra na lista a checagem cujo gatilho disparou. Sem dado na fonte, o status é "sem_dado", nunca um chute.
    """
    d = dados or {}
    if (d.get("Metadados") or {}).get("tipo") == "FII" or (d.get("Identificacao") or {}).get("Tipo_Codigo") == "FII":
        return []
    val = d.get("Valuation") or {}
    rent = d.get("Rentabilidade_e_Eficiencia") or {}
    end = d.get("Endividamento_e_Caixa") or {}
    prov = d.get("Proventos") or {}
    ide = d.get("Identificacao") or {}
    # Bancos e seguradoras operam com ROA de 1-2% por natureza; ROE/ROA alto ali não é sinal de alavancagem anormal
    setor_financeiro = "financ" in str(ide.get("Setor") or "").lower() or any(t in str(ide.get("Industria") or "").lower() for t in ("bank", "banc", "insur", "segur"))

    p_l, p_l_proj = numero(val.get("P_L")), numero(val.get("P_L_Projetado"))
    roe, roa = numero(rent.get("ROE")), numero(rent.get("ROA"))
    cresc_lucro, cresc_receita = numero(rent.get("Crescimento_Lucros_YoY")), numero(rent.get("Crescimento_Receita_YoY"))
    margem_liq, receita = numero(rent.get("Margem_Liquida")), numero(end.get("Receita_Total_12m"))
    fcl, fco = numero(end.get("Fluxo_Caixa_Livre")), numero(end.get("Fluxo_Caixa_Operacional"))
    alav = numero(end.get("Divida_Liquida_sobre_EBITDA"))
    dy, payout = numero(prov.get("Dividend_Yield_Real_12m")), numero(prov.get("Payout_Ratio"))
    lucro = (margem_liq * receita) if (margem_liq is not None and receita) else None

    ROTULO = {"alerta": "Atenção", "ok": "Confere", "sem_dado": "Sem dado na fonte"}
    CLASSE = {"alerta": "badge-neg", "ok": "badge-pos", "sem_dado": ""}

    def item(gatilho, pergunta, alertas, dados_lidos):
        if alertas:
            status, leitura = "alerta", " ".join(alertas)
        elif dados_lidos:
            status, leitura = "ok", "O indicador de contraprova não desmente o número bom."
        else:
            status, leitura = "sem_dado", "A fonte (Yahoo Finance) não trouxe o dado de contraprova. Confira no release de resultados da empresa antes de confiar no número."
        return {"gatilho": gatilho, "pergunta": pergunta, "status": status, "rotulo": ROTULO[status], "classe_css": CLASSE[status], "leitura": leitura}

    checks = []
    if p_l is not None and 0 < p_l < 8:
        alertas, lidos = [], False
        if p_l_proj is not None:
            lidos = True
            if p_l_proj <= 0:
                alertas.append("O mercado projeta prejuízo à frente (P/L projetado negativo): o lucro recente tende a não se repetir.")
            elif p_l_proj > p_l * 1.3:
                alertas.append(f"O P/L projetado ({br(p_l_proj, 1)}x) é bem maior que o atual ({br(p_l, 1)}x): o mercado espera lucro menor à frente, sinal de que parte do lucro recente pode não se repetir.")
        if fcl is not None and lucro and lucro > 0:
            lidos = True
            if fcl < 0.5 * lucro:
                alertas.append("O caixa livre gerado é menos da metade do lucro contábil: o lucro no papel não está virando dinheiro no caixa.")
        checks.append(item(f"P/L baixo ({br(p_l, 1)}x)", "O lucro é recorrente ou veio de um evento único?", alertas, lidos))

    if roe is not None and roe >= 0.20:
        alertas, lidos = [], False
        if roa is not None and roa > 0 and not setor_financeiro:
            lidos = True
            if roe / roa >= 3:
                alertas.append(f"O retorno sobre os ativos (ROA {br(roa * 100, 1)}%) é bem menor que o ROE: o retorno alto vem de patrimônio pequeno e muita dívida, não da operação.")
        if alav is not None:
            lidos = True
            if alav > 3.5:
                alertas.append(f"Alavancagem de {br(alav, 1)}x EBITDA: parte do lucro vai para juros antes de chegar ao acionista.")
        # shortcut: ROIC nao existe no yfinance.info; ROA entra como aproximacao, trocar quando houver fonte de EBIT/capital investido
        checks.append(item(f"ROE alto ({br(roe * 100, 1)}%)", "O retorno vem da operação (ROIC, aproximado pelo ROA) ou de alavancagem?", alertas, lidos))

    if cresc_lucro is not None and cresc_lucro >= 0.10:
        alertas, lidos = [], False
        if fco is not None:
            lidos = True
            if fco < 0:
                alertas.append("O caixa operacional é negativo: o lucro cresce no papel, mas a operação consome dinheiro.")
            elif lucro and lucro > 0 and fco < 0.7 * lucro:
                alertas.append("O caixa operacional cobre menos de 70% do lucro: o crescimento pode estar vindo de vendas a prazo ou de ajustes contábeis.")
        checks.append(item(f"Lucro crescendo ({br(cresc_lucro * 100, 1)}% ao ano)", "O fluxo de caixa operacional acompanha o lucro?", alertas, lidos))

    if dy is not None and dy >= 0.08:
        alertas, lidos = [], False
        if payout is not None:
            lidos = True
            if payout > 1.0:
                alertas.append(f"Payout de {br(payout * 100, 0)}%: a empresa distribuiu mais do que lucrou, o que não se sustenta sem queimar caixa ou tomar dívida.")
        if fcl is not None:
            lidos = True
            if fcl < 0:
                alertas.append("O caixa livre é negativo: os dividendos de hoje saem de dívida ou de reservas, não da geração de caixa.")
        checks.append(item(f"Yield alto ({br(dy * 100, 1)}% em 12 meses)", "O payout e o caixa livre sustentam esse dividendo?", alertas, lidos))

    cresc = max([c for c in (cresc_receita, cresc_lucro) if c is not None], default=None)
    if cresc is not None and cresc >= 0.15:
        alertas, lidos = [], False
        if alav is not None:
            lidos = True
            if alav > 3.5:
                alertas.append(f"Dívida líquida de {br(alav, 1)}x EBITDA: o crescimento está sendo financiado com dívida cara, e os juros podem comer o lucro futuro.")
        checks.append(item(f"Crescimento forte ({br(cresc * 100, 1)}% ao ano)", "A dívida líquida/EBITDA está sob controle?", alertas, lidos))
    return checks


def garantir_blocos_didaticos(dados: dict) -> dict:
    if not isinstance(dados, dict):
        return dados
    ticker = dados.get("Identificacao", {}).get("Ticker") or "B3"
    info = {
        "sector": dados.get("Identificacao", {}).get("Setor"),
        "industry": dados.get("Identificacao", {}).get("Industria"),
        "longName": dados.get("Identificacao", {}).get("Nome_Empresa"),
        "longBusinessSummary": dados.get("Identificacao", {}).get("Resumo_Negocio"),
    }
    preco = dados.get("Cotacao_e_Mercado", {}).get("Preco_Atual") or dados.get("Numeros", {}).get("preco_atual") or 0.0
    div_12m = dados.get("Proventos", {}).get("Total_Pago_12m_por_Cota") or dados.get("Numeros", {}).get("dividendos_12m") or 0.0

    if "Dossie_Didatico" not in dados or not dados["Dossie_Didatico"]:
        dados["Dossie_Didatico"] = obter_dossie_didatico(ticker, info, dados)
    if "Simulador_Realidade" not in dados or not dados["Simulador_Realidade"]:
        dados["Simulador_Realidade"] = calcular_simulador_realidade_vs_guru(
            ticker,
            preco,
            div_12m,
            historico_mensal=dados.get("Historico_Mensal_5a"),
        )
    if "Pares_Setoriais" not in dados or dados["Pares_Setoriais"] is None:
        dados["Pares_Setoriais"] = obter_comparativo_setorial(ticker, str(info.get("sector") or ""))
    if "Termometro_Macro" not in dados or not dados["Termometro_Macro"]:
        dados["Termometro_Macro"] = calcular_termometro_macro(ticker, str(info.get("sector") or ""))
    dados["Simulador_Premissas"] = simular_premissas_usuario(dados, crescimento_lucro=0.0, pl_futuro=10.0, anos=1)
    dados.pop("Cenarios_Projetados", None)
    if "Multiplos_Traduzidos" not in dados or not dados["Multiplos_Traduzidos"]:
        dados["Multiplos_Traduzidos"] = traduzir_multiplos_ativo(dados)
    dados["Checagens_Cruzadas"] = checar_coerencia_indicadores(dados)
    return dados

def extrair_raio_x_b3(ticker_usuario: str) -> dict:
    """
    Extrai dados fundamentalistas e de mercado da B3 devolvendo números crus (float/int/None)
    e metadados de moeda/observação. Nenhuma formatação textual (R$, %, Mi/Bi, N/D) ocorre aqui.
    """
    ticker = resolver_ticker(ticker_usuario)
    simbolo_sa = f"{ticker}.SA"

    with ThreadPoolExecutor(max_workers=4) as executor:
        fut_info = executor.submit(lambda: yf.Ticker(simbolo_sa).info or {})
        fut_div = executor.submit(dividendos_ultimos_12m, yf.Ticker(simbolo_sa))
        fut_hist = executor.submit(_coletar_historico_seguro, yf.Ticker(simbolo_sa))
        fut_hist_5a = executor.submit(_coletar_historico_mensal_5a, yf.Ticker(simbolo_sa))

        try:
            info = fut_info.result()
        except Exception as e:
            raise ErroAtivo(
                f"Não foi possível consultar {ticker} na B3 agora ({type(e).__name__})."
            ) from e

        dividendos_12m, qtd_pagamentos = fut_div.result()
        hist_6d = fut_hist.result()
        historico_mensal_5a = fut_hist_5a.result()

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

    dt_consulta_br = agora_brasilia()
    em_pregao = em_horario_pregao_b3(dt_consulta_br)
    relacao_volume_media = (vol_acoes_dia / vol_medio_3m * 100) if vol_medio_3m and vol_medio_3m > 0 else None
    if relacao_volume_media is not None:
        if relacao_volume_media >= 120 and variacao_dia_pct > 0:
            suf = "volume até agora, " if em_pregao else ""
            status_fluxo = f"Dia de alta com volume acima da média ({suf}{round(relacao_volume_media)}% da média 3m)"
        elif relacao_volume_media >= 120 and variacao_dia_pct < 0:
            suf = "volume até agora, " if em_pregao else ""
            status_fluxo = f"Dia de queda com volume acima da média ({suf}{round(relacao_volume_media)}% da média 3m)"
        elif relacao_volume_media >= 120:
            suf = "volume até agora, " if em_pregao else ""
            status_fluxo = f"Estável com volume acima da média ({suf}{round(relacao_volume_media)}% da média 3m)"
        elif em_pregao:
            status_fluxo = f"Pregão em andamento: volume até agora ({round(relacao_volume_media)}% da média 3m)"
        else:
            status_fluxo = f"Volume Normal ({round(relacao_volume_media)}% da média 3m)"
    else:
        status_fluxo = "Pregão em andamento: volume até agora" if em_pregao else "Volume em linha com o histórico"

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
    barra_graham_pct = round(max(10.0, min(100.0, 50.0 + (margem_graham_pct / 2.0))), 1) if margem_graham_pct is not None else 0.0
    barra_bazin_pct = round(max(10.0, min(100.0, 50.0 + (margem_bazin_pct / 2.0))), 1) if margem_bazin_pct is not None else 0.0

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

    historico_5d = extrair_fluxo_pregoes(
        None,
        vol_medio_referencia=vol_medio_3m or vol_medio_10d,
        hist_pre_carregado=hist_6d,
    )

    pos_52s = 50.0
    if min_52s and max_52s and max_52s > min_52s:
        pos_52s = max(0.0, min(100.0, (preco - min_52s) / (max_52s - min_52s) * 100))

    site_oficial = (info.get("website") or "").strip() or None

    resultado = {
        "Historico_Mensal_5a": historico_mensal_5a,
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
            "Data_Consulta": dt_consulta_br.strftime("%d/%m/%Y"),
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
            "Barra_Graham_Pct": barra_graham_pct,
            "Preco_Teto_Bazin_6pct": bazin,
            "Obs_Bazin": obs_bazin,
            "Margem_Bazin_Pct": margem_bazin_pct,
            "Barra_Bazin_Pct": barra_bazin_pct,
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
            "Fluxo_Caixa_Operacional": numero(info.get("operatingCashflow")),
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
    garantir_blocos_didaticos(resultado)
    return resultado


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
6. Não use travessões (—); separe ideias com vírgula, ponto ou dois-pontos.

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
    except genai_errors.APIError as e:
        codigo_http = getattr(e, "code", None) or getattr(e, "status_code", None)
        if codigo_http == 429:
            erro_curto = "Limite de cota da API atingido"
        elif codigo_http == 404:
            erro_curto = f"Modelo '{MODELO_GEMINI}' inválido no .env"
        else:
            erro_curto = f"APIError({codigo_http})" if codigo_http else type(e).__name__
        return gerar_resumo_tecnico_local(dados, erro_curto), f"Motor Algorítmico ({erro_curto})", False
    except Exception as e:
        codigo_http = getattr(e, "code", None) or getattr(e, "status_code", None)
        if codigo_http == 429:
            erro_curto = "Limite de cota da API atingido"
        elif codigo_http == 404:
            erro_curto = f"Modelo '{MODELO_GEMINI}' inválido no .env"
        else:
            erro_curto = type(e).__name__
        return gerar_resumo_tecnico_local(dados, erro_curto), f"Motor Algorítmico ({erro_curto})", False
