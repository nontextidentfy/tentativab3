import datetime
import json
import logging
import os
import re
import threading
from typing import Optional
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
import markdown as md
from fastapi import FastAPI, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler as http_exception_handler_padrao
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import servico_analise as svc
import seguranca as auth
from banco import (
    AnaliseIA,
    Ativo,
    Cotacao,
    MonitoramentoUsuario,
    Usuario,
    agora_utc,
    obter_sessao_banco,
    SessionLocal,
)

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
RATE_LIMIT_STORAGE_URI = (os.getenv("RATE_LIMIT_STORAGE_URI") or "memory://").strip()

PERFIS_OPCOES: tuple[tuple[str, str], ...] = (
    ("Fundamentalista", "Fundamentalista / Valuation"),
    ("Dividendos", "Dividendos & Renda Passiva"),
    ("FIIs", "Fundos Imobiliários (FIIs)"),
    ("Fluxo", "Fluxo de Volume & Liquidez"),
)
PERFIS_PERMITIDOS: set[str] = {valor for valor, _ in PERFIS_OPCOES}


def obter_ip_cliente(request: Request) -> str:
    """Retorna o IP real do cliente. Lê X-Forwarded-For apenas quando CONFIAR_PROXY=1."""
    if os.getenv("CONFIAR_PROXY", "").strip() == "1":
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            primeiro_ip = xff.split(",")[0].strip()
            if primeiro_ip:
                return primeiro_ip
    return get_remote_address(request)


limiter = Limiter(key_func=obter_ip_cliente, storage_uri=RATE_LIMIT_STORAGE_URI)

app = FastAPI(title="Investidor IA - Análise Fundamentalista B3")
app.state.limiter = limiter

# Middleware de compressão GZip para respostas volumosas (> 1KB)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def adicionar_cabecalhos_seguranca(request: Request, call_next):
    """Aplica cabeçalhos de segurança essenciais contra Clickjacking, MIME-sniffing e XSS."""
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    if auth.EM_PRODUCAO:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


templates = Jinja2Templates(directory=os.path.join(PASTA_PROJETO, "templates"))
templates.env.globals["PERFIS_OPCOES"] = PERFIS_OPCOES
templates.env.globals["DESCRICAO_TIPO"] = svc.DESCRICAO_TIPO
log = logging.getLogger("investidor_ia")

HORAS_CACHE = 24
COOLDOWN_ATUALIZACAO_MINUTOS = 15
_LOCKS_TICKER_GUARD = threading.Lock()
_LOCKS_POR_TICKER: dict[str, threading.Lock] = {}

# Limite de consultas de análise por IP (protege a cota do Gemini). Pode ser ajustado no .env.
LIMITE_ANALISE = (os.getenv("LIMITE_ANALISE") or "5/minute;10/day").strip()


def _obter_lock_ticker(ticker: str) -> threading.Lock:
    with _LOCKS_TICKER_GUARD:
        lock = _LOCKS_POR_TICKER.get(ticker)
        if lock is None:
            lock = threading.Lock()
            _LOCKS_POR_TICKER[ticker] = lock
        return lock


ESQUEMAS_URL_PERMITIDOS = ("http://", "https://", "mailto:", "/", "#")


def renderizar_markdown(texto: str) -> str:
    """Converte o relatório (Markdown) em HTML, neutralizando tags brutas e protocolos inseguros como javascript:."""
    # Sem travessões no texto exibido (regra editorial): "a — b" vira "a, b"
    texto_seguro = re.sub(r"[ \t]*—[ \t]*", ", ", texto or "").replace("<", "&lt;")
    html_bruto = md.markdown(texto_seguro, extensions=["extra", "sane_lists"])
    try:
        soup = BeautifulSoup(html_bruto, "html.parser")
        for a in soup.find_all("a"):
            href = a.get("href", "").strip().lower()
            if not any(href.startswith(esq) for esq in ESQUEMAS_URL_PERMITIDOS):
                a["href"] = "#"
            a["rel"] = "noopener noreferrer"
        for tag in soup.find_all(True):
            for attr in list(tag.attrs.keys()):
                if attr.lower().startswith("on"):
                    del tag[attr]
        return str(soup)
    except Exception:
        return html_bruto


def filtro_moeda_br(valor, simbolo: str = "R$") -> str:
    v = svc.numero(valor)
    if v is None:
        return "N/D"
    simb = svc.SIMBOLOS_MOEDA.get(simbolo, simbolo) if simbolo else ""
    prefixo = f"{simb} " if simb else ""
    return f"{prefixo}{svc.br(v, 2)}"


def filtro_numero_br(valor, casas: int = 2) -> str:
    return svc.br(valor, casas)


def filtro_percentual_br(valor, casas: int = 2, ja_percentual: bool = False) -> str:
    return svc.formatar_percentual(valor, casas=casas, ja_percentual=ja_percentual)


def filtro_moeda_grande_br(valor, moeda: str = "BRL") -> str:
    return svc.formatar_moeda_grande(valor, moeda=moeda)


def filtro_inteiro_br(valor) -> str:
    return svc.inteiro_br(valor)


def filtro_horario_br(data_utc) -> str:
    if not data_utc:
        return "N/D"
    if data_utc.tzinfo is None:
        data_utc = data_utc.replace(tzinfo=datetime.timezone.utc)
    return data_utc.astimezone(svc.FUSO_BRASILIA).strftime("%d/%m/%Y às %H:%M")


templates.env.globals["empresa_rodape"] = (os.getenv("EMPRESA_RODAPE") or "").strip()
templates.env.filters["moeda_br"] = filtro_moeda_br
templates.env.filters["numero_br"] = filtro_numero_br
templates.env.filters["percentual_br"] = filtro_percentual_br
templates.env.filters["moeda_grande_br"] = filtro_moeda_grande_br
templates.env.filters["inteiro_br"] = filtro_inteiro_br
templates.env.filters["horario_br"] = filtro_horario_br


def obter_recentes_seguro(db: Session):
    """
    Busca até 12 ativos mais recentes em uma única consulta JOIN (ativos -> cotacoes),
    sem carregar o JSON pesado de dados nem o texto de relatório.
    """
    try:
        linhas = (
            db.query(
                Ativo.ticker.label("ticker"),
                Ativo.nome.label("nome_empresa"),
                Ativo.tipo.label("tipo_ativo"),
                Ativo.moeda_cotacao.label("moeda_cotacao"),
                Cotacao.preco.label("preco_atual"),
                Cotacao.variacao_dia_pct.label("variacao_dia_pct"),
                Cotacao.volume_financeiro.label("volume_financeiro"),
                Cotacao.p_l.label("p_l"),
                Cotacao.p_vp.label("p_vp"),
                Cotacao.dy_12m_pct.label("dy_12m_pct"),
                Cotacao.coletado_em.label("coletado_em"),
            )
            .join(Cotacao, Ativo.ultima_cotacao_id == Cotacao.id)
            .order_by(Cotacao.coletado_em.desc())
            .limit(12)
            .all()
        )
        return linhas
    except Exception:
        log.exception("Erro ao buscar ativos recentes")
        return []


def _salvar_coleta_no_banco(
    db: Session,
    ticker_ok: str,
    dados: dict,
    relatorio_md: str,
    motor_usado: str,
    veio_da_ia: bool,
    data_atual: datetime.datetime,
) -> None:
    ide = dados.get("Identificacao") or {}
    cot = dados.get("Cotacao_e_Mercado") or {}
    fluxo = dados.get("Fluxo_e_Volume") or {}
    val = dados.get("Valuation") or {}
    rent = dados.get("Rentabilidade_e_Eficiencia") or {}
    end = dados.get("Endividamento_e_Caixa") or {}
    prov = dados.get("Proventos") or {}
    num = dados.get("Numeros") or {}
    meta = dados.get("Metadados") or {}

    tipo_cod = meta.get("tipo") or num.get("tipo") or ide.get("Tipo_Codigo") or "Acao"
    moeda_cot = meta.get("moeda_cotacao") or ide.get("Moeda_Cotacao") or "BRL"
    site_val = ide.get("Site_Oficial")
    if site_val and str(site_val).strip().upper() == "N/D":
        site_val = None

    ativo = db.query(Ativo).filter(Ativo.ticker == ticker_ok).first()
    if not ativo:
        ativo = Ativo(
            ticker=ticker_ok,
            nome=ide.get("Nome_Empresa") or ticker_ok,
            tipo=tipo_cod,
            setor=ide.get("Setor"),
            industria=ide.get("Industria"),
            site=site_val,
            resumo=ide.get("Resumo_Negocio"),
            moeda_cotacao=moeda_cot,
            atualizado_em=data_atual,
        )
        db.add(ativo)
        db.flush()
    else:
        ativo.nome = ide.get("Nome_Empresa") or ativo.nome
        ativo.tipo = tipo_cod
        ativo.setor = ide.get("Setor")
        ativo.industria = ide.get("Industria")
        ativo.site = site_val
        ativo.resumo = ide.get("Resumo_Negocio")
        ativo.moeda_cotacao = moeda_cot
        ativo.atualizado_em = data_atual

    preco_val = svc.numero(cot.get("Preco_Atual"))
    if preco_val is None:
        preco_val = svc.numero(num.get("preco_atual"))

    var_dia_val = svc.numero(cot.get("Variacao_Dia"))
    if var_dia_val is None:
        var_dia_val = svc.numero(num.get("variacao_dia_pct"))
    if var_dia_val is None:
        var_dia_val = 0.0

    vol_fin_val = svc.numero(fluxo.get("Volume_Financeiro_Dia"))
    if vol_fin_val is None:
        vol_fin_val = svc.numero(num.get("vol_financeiro_dia"))

    dy_pct_val = svc.numero(prov.get("Dividend_Yield_12m_Pct"))
    if dy_pct_val is None:
        dy_pct_val = svc.numero(num.get("dy_12m"))

    div_12m_val = svc.numero(prov.get("Total_Pago_12m_por_Cota"))
    if div_12m_val is None:
        div_12m_val = svc.numero(num.get("dividendos_12m"))

    graham_val = svc.numero(val.get("Valor_Intrinseco_Graham"))
    if graham_val is None:
        graham_val = svc.numero(num.get("graham"))

    bazin_val = svc.numero(val.get("Preco_Teto_Bazin_6pct"))
    if bazin_val is None:
        bazin_val = svc.numero(num.get("bazin"))

    min_52s_val = svc.numero(cot.get("Minima_52_Semanas"))
    if min_52s_val is None:
        min_52s_val = svc.numero(num.get("min_52s"))

    max_52s_val = svc.numero(cot.get("Maxima_52_Semanas"))
    if max_52s_val is None:
        max_52s_val = svc.numero(num.get("max_52s"))

    nova_cotacao = Cotacao(
        ticker=ticker_ok,
        coletado_em=data_atual,
        preco=preco_val,
        variacao_dia_pct=var_dia_val,
        volume_acoes=svc.numero(fluxo.get("Volume_Acoes_Dia")),
        volume_financeiro=vol_fin_val,
        p_l=svc.numero(val.get("P_L")),
        p_l_projetado=svc.numero(val.get("P_L_Projetado")),
        p_vp=svc.numero(val.get("P_VP")),
        ev_ebitda=svc.numero(val.get("EV_EBITDA")),
        lpa=svc.numero(val.get("LPA")),
        vpa=svc.numero(val.get("VPA")),
        dy_12m_pct=dy_pct_val,
        dividendos_12m=div_12m_val,
        graham=graham_val,
        bazin=bazin_val,
        roe=svc.numero(rent.get("ROE")),
        margem_liquida=svc.numero(rent.get("Margem_Liquida")),
        ebitda=svc.numero(end.get("EBITDA")),
        divida_liquida=svc.numero(end.get("Divida_Liquida")),
        alavancagem=svc.numero(end.get("Divida_Liquida_sobre_EBITDA")),
        min_52s=min_52s_val,
        max_52s=max_52s_val,
        valor_mercado=svc.numero(cot.get("Valor_de_Mercado")),
        dados=dados,
    )
    db.add(nova_cotacao)
    db.flush()

    ativo.ultima_cotacao_id = nova_cotacao.id

    if veio_da_ia:
        db.add(
            AnaliseIA(
                ticker=ticker_ok,
                cotacao_id=nova_cotacao.id,
                modelo=motor_usado,
                veio_da_ia=True,
                relatorio_md=relatorio_md,
                criado_em=data_atual,
            )
        )

    db.commit()


def _mensagem_validacao(exc: RequestValidationError, padrao: str) -> str:
    for erro in exc.errors():
        loc = [str(p) for p in erro.get("loc", ())]
        campo = loc[-1] if loc else ""
        if campo in ("senha", "nova_senha"):
            return "A senha deve ter entre 8 e 100 caracteres."
        if campo == "nome":
            return "O nome deve ter entre 2 e 120 caracteres."
        if campo == "email":
            return "Informe um e-mail válido (entre 5 e 150 caracteres)."
        if campo == "perfil":
            return "Selecione um perfil de investidor válido."
        if campo == "notas":
            return "As anotações devem ter no máximo 500 caracteres."
        if campo == "ticker":
            return "Verifique os dados informados. Digite o ticker (ex.: SAPR4) ou o nome da empresa (ex.: Sanepar)."
    return padrao


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    caminho = request.url.path
    with SessionLocal() as db:
        usuario = auth.obter_usuario_atual(request, db)
        if caminho in ("/cadastro", "/login", "/esqueci-senha") or caminho.startswith("/redefinir-senha/"):
            modo_map = {
                "/cadastro": "cadastro",
                "/login": "login",
                "/esqueci-senha": "esqueci",
            }
            modo = modo_map.get(caminho, "redefinir")
            token = caminho.split("/redefinir-senha/", 1)[1] if caminho.startswith("/redefinir-senha/") else None
            msg = _mensagem_validacao(exc, "Verifique os dados preenchidos no formulário.")
            return templates.TemplateResponse(
                request=request,
                name="auth.html",
                context={"modo": modo, "token": token, "usuario": usuario, "error_msg": msg},
                status_code=422,
            )

        if caminho.startswith("/watchlist"):
            msg = _mensagem_validacao(exc, "Verifique os dados informados para a Watchlist.")
            return RedirectResponse(url=f"/watchlist?erro={quote_plus(msg)}", status_code=303)

        painel_ativos = obter_recentes_seguro(db)

    msg = _mensagem_validacao(
        exc,
        "Verifique os dados informados. Digite o ticker (ex.: SAPR4) ou o nome da empresa (ex.: Sanepar).",
    )
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "usuario": usuario,
            "error_msg": msg,
            "painel_ativos": painel_ativos,
        },
        status_code=422,
    )


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    with SessionLocal() as db:
        painel_ativos = obter_recentes_seguro(db)
        usuario = auth.obter_usuario_atual(request, db)
    detalhe = str(getattr(exc, "detail", "") or str(exc)).lower()
    if "day" in detalhe or "dia" in detalhe:
        msg = "Limite diário de requisições atingido por segurança. Tente novamente amanhã."
    else:
        msg = "Limite de requisições por minuto atingido por segurança. Aguarde um minuto e tente novamente."
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "usuario": usuario,
            "error_msg": msg,
            "painel_ativos": painel_ativos,
        },
        status_code=429,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    accept = request.headers.get("accept", "")
    quer_json = request.url.path.startswith("/api/") or ("application/json" in accept and "text/html" not in accept)
    if exc.status_code != 404 or quer_json:
        return await http_exception_handler_padrao(request, exc)
    try:
        with SessionLocal() as db:
            usuario = auth.obter_usuario_atual(request, db)
    except Exception:
        usuario = None
    return templates.TemplateResponse(request=request, name="404.html", context={"usuario": usuario}, status_code=404)


def _msg_email(enviado: str, local: str) -> str:
    """Mensagem honesta sobre o e-mail: enviado, impresso no terminal (local) ou indisponível (produção sem SMTP)."""
    if auth.email_configurado():
        return enviado
    if not auth.EM_PRODUCAO:
        return local
    return "O envio de e-mail está indisponível no momento. Tente novamente mais tarde ou contate o suporte."


# ---------------------------------------------------------------------------
# Rotas Principais de Análise
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def pagina_inicial(
    request: Request,
    msg: Optional[str] = None,
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    painel_ativos = obter_recentes_seguro(db)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"painel_ativos": painel_ativos, "usuario": usuario, "success_msg": msg},
    )


@app.get("/saude")
def verificar_saude():
    """Endpoint simples para monitoramento (usado por plataformas de hospedagem)."""
    return {"status": "ok"}


@app.get("/api/buscar-ativos")
@limiter.limit("40/minute")
def api_buscar_ativos(request: Request, q: str = Query("", max_length=60)):
    """Autocomplete da barra de busca: aceita marcas populares, cooperativas, empresas e tickers."""
    return JSONResponse({"resultados": svc.buscar_ativos(q, limite=8)})


def _cotacao_tem_dados_novos(cotacao: Cotacao) -> bool:
    dados = cotacao.dados
    if isinstance(dados, str):
        try:
            dados = json.loads(dados)
        except ValueError:
            return False
    return isinstance(dados, dict) and bool(dados.get("Metadados"))


def _buscar_cache_ativo(db: Session, ticker_ok: str, limite_minimo: datetime.datetime):
    par = (
        db.query(Ativo, Cotacao)
        .join(Cotacao, Ativo.ultima_cotacao_id == Cotacao.id)
        .filter(
            Ativo.ticker == ticker_ok,
            Cotacao.coletado_em >= limite_minimo,
        )
        .first()
    )
    if par and not _cotacao_tem_dados_novos(par[1]):
        return None  # registro migrado do formato antigo: trata como sem cache e coleta de novo
    return par


def _montar_resposta_cache(
    request: Request,
    db: Session,
    ticker_ok: str,
    cotacao_cache: Cotacao,
    usuario: Optional[Usuario],
    origem: str,
    success_msg: Optional[str] = None,
):
    dados_cache = (
        cotacao_cache.dados
        if isinstance(cotacao_cache.dados, dict)
        else json.loads(cotacao_cache.dados)
    )
    dados_cache = svc.garantir_blocos_didaticos(dados_cache)
    eh_pro = bool(usuario and getattr(usuario, "eh_pro", False))
    analise_cache = (
        db.query(AnaliseIA)
        .filter(AnaliseIA.cotacao_id == cotacao_cache.id)
        .order_by(AnaliseIA.criado_em.desc())
        .first()
    )
    if analise_cache:
        relatorio_md = analise_cache.relatorio_md
        motor_cache = analise_cache.modelo
    else:
        relatorio_md, motor_cache, veio_ia_agora = svc.gerar_relatorio_ia(dados_cache)
        if veio_ia_agora:
            try:
                db.add(
                    AnaliseIA(
                        ticker=ticker_ok,
                        cotacao_id=cotacao_cache.id,
                        modelo=motor_cache,
                        veio_da_ia=True,
                        relatorio_md=relatorio_md,
                        criado_em=agora_utc(),
                    )
                )
                db.commit()
            except Exception:
                db.rollback()

    relatorio_html = renderizar_markdown(relatorio_md)
    painel_ativos = obter_recentes_seguro(db)
    return templates.TemplateResponse(
        request=request,
        name="resultado.html",
        context={
            "usuario": usuario,
            "eh_pro": eh_pro,
            "usuario_pro": eh_pro,
            "dados": dados_cache,
            "bloco1": dados_cache.get("Dossie_Didatico") or {},
            "bloco2": dados_cache.get("Simulador_Realidade") or {},
            "bloco3": {
                "pares": dados_cache.get("Pares_Setoriais") or [],
                "macro": dados_cache.get("Termometro_Macro") or {},
            },
            "bloco4": {
                "simulador_premissas": dados_cache.get("Simulador_Premissas") or svc.simular_premissas_usuario(dados_cache),
                "multiplos_traduzidos": dados_cache.get("Multiplos_Traduzidos") or {},
            },
            "relatorio_html": relatorio_html,
            "origem": origem,
            "motor": motor_cache,
            "data_dados": cotacao_cache.coletado_em,
            "painel_ativos": painel_ativos,
            "success_msg": success_msg,
        },
    )


@app.get("/analisar", response_class=HTMLResponse)
@limiter.limit(LIMITE_ANALISE)
def analisar_ativo(
    request: Request,
    ticker: str = Query(..., min_length=2, max_length=60),
    atualizar: bool = False,
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    entidade_nl = svc.obter_entidade_nao_listada(ticker)
    if entidade_nl:
        return templates.TemplateResponse(
            request=request,
            name="nao_listada.html",
            context={
                "entidade": entidade_nl,
                "usuario": usuario,
            },
            status_code=200,
        )

    try:
        ticker_ok = svc.resolver_ticker(ticker)
    except svc.EntidadeNaoListada as e:
        return templates.TemplateResponse(
            request=request,
            name="nao_listada.html",
            context={
                "entidade": e.entidade,
                "usuario": usuario,
            },
            status_code=200,
        )
    except svc.ErroAtivo as e:
        painel_ativos = obter_recentes_seguro(db)
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={"error_msg": str(e), "painel_ativos": painel_ativos, "usuario": usuario},
        )

    agora = agora_utc()
    limite_24h = agora - datetime.timedelta(hours=HORAS_CACHE)
    limite_cooldown = agora - datetime.timedelta(minutes=COOLDOWN_ATUALIZACAO_MINUTOS)

    if not atualizar:
        par_cache = _buscar_cache_ativo(db, ticker_ok, limite_24h)
        if par_cache:
            return _montar_resposta_cache(
                request=request,
                db=db,
                ticker_ok=ticker_ok,
                cotacao_cache=par_cache[1],
                usuario=usuario,
                origem="Banco de Dados (Cache 24h)",
            )
    else:
        par_recente = _buscar_cache_ativo(db, ticker_ok, limite_cooldown)
        if par_recente:
            aviso = (
                f"Os dados de {ticker_ok} já foram atualizados há menos de {COOLDOWN_ATUALIZACAO_MINUTOS} minutos. "
                "Exibindo a coleta mais recente em cache."
            )
            return _montar_resposta_cache(
                request=request,
                db=db,
                ticker_ok=ticker_ok,
                cotacao_cache=par_recente[1],
                usuario=usuario,
                origem=f"Banco de Dados (Cooldown {COOLDOWN_ATUALIZACAO_MINUTOS}m)",
                success_msg=aviso,
            )

    with _obter_lock_ticker(ticker_ok):
        # Revalida dentro do lock para evitar duas chamadas simultâneas ao Gemini/yfinance para o mesmo ticker
        db.expire_all()
        par_concorrente = _buscar_cache_ativo(
            db,
            ticker_ok,
            agora_utc() - datetime.timedelta(minutes=COOLDOWN_ATUALIZACAO_MINUTOS),
        )
        if par_concorrente:
            aviso_conc = (
                f"Os dados de {ticker_ok} já foram atualizados há menos de {COOLDOWN_ATUALIZACAO_MINUTOS} minutos. "
                "Exibindo a coleta mais recente em cache."
                if atualizar
                else None
            )
            return _montar_resposta_cache(
                request=request,
                db=db,
                ticker_ok=ticker_ok,
                cotacao_cache=par_concorrente[1],
                usuario=usuario,
                origem="Banco de Dados (Cache 24h)",
                success_msg=aviso_conc,
            )

        try:
            dados = svc.extrair_raio_x_b3(ticker_ok)
            dados = svc.garantir_blocos_didaticos(dados)
        except svc.ErroAtivo as e:
            painel_ativos = obter_recentes_seguro(db)
            return templates.TemplateResponse(
                request=request,
                name="home.html",
                context={"error_msg": str(e), "painel_ativos": painel_ativos, "usuario": usuario},
            )
        except Exception:
            log.exception("Erro ao extrair %s", ticker_ok)
            painel_ativos = obter_recentes_seguro(db)
            return templates.TemplateResponse(
                request=request,
                name="home.html",
                context={
                    "error_msg": f"Não foi possível obter os dados de {ticker_ok} na B3 no momento.",
                    "painel_ativos": painel_ativos,
                    "usuario": usuario,
                },
            )

        relatorio_md, motor_usado, veio_da_ia = svc.gerar_relatorio_ia(dados)
        relatorio_html = renderizar_markdown(relatorio_md)
        data_atual = agora_utc()

        try:
            _salvar_coleta_no_banco(
                db=db,
                ticker_ok=ticker_ok,
                dados=dados,
                relatorio_md=relatorio_md,
                motor_usado=motor_usado,
                veio_da_ia=veio_da_ia,
                data_atual=data_atual,
            )
        except Exception:
            db.rollback()
            log.exception("Erro ao gravar coleta no banco de dados")

    eh_pro = bool(usuario and getattr(usuario, "eh_pro", False))
    painel_ativos = obter_recentes_seguro(db)
    return templates.TemplateResponse(
        request=request,
        name="resultado.html",
        context={
            "usuario": usuario,
            "eh_pro": eh_pro,
            "usuario_pro": eh_pro,
            "dados": dados,
            "bloco1": dados.get("Dossie_Didatico") or {},
            "bloco2": dados.get("Simulador_Realidade") or {},
            "bloco3": {
                "pares": dados.get("Pares_Setoriais") or [],
                "macro": dados.get("Termometro_Macro") or {},
            },
            "bloco4": {
                "simulador_premissas": dados.get("Simulador_Premissas") or svc.simular_premissas_usuario(dados),
                "multiplos_traduzidos": dados.get("Multiplos_Traduzidos") or {},
            },
            "relatorio_html": relatorio_html,
            "origem": "Tempo Real (B3)",
            "motor": motor_usado,
            "data_dados": data_atual,
            "painel_ativos": painel_ativos,
        },
    )


# ---------------------------------------------------------------------------
# Fase 2: Cadastro, Login (Argon2), Confirmação de E-mail e Recuperação
# ---------------------------------------------------------------------------
@app.get("/cadastro", response_class=HTMLResponse)
def tela_cadastro(request: Request, usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual)):
    if usuario:
        return RedirectResponse(url="/watchlist", status_code=303)
    return templates.TemplateResponse(request=request, name="auth.html", context={"modo": "cadastro", "usuario": None})


@app.post("/cadastro", response_class=HTMLResponse)
@limiter.limit("5/minute")
def processar_cadastro(
    request: Request,
    nome: str = Form(..., min_length=2, max_length=120),
    email: str = Form(..., min_length=5, max_length=150),
    senha: str = Form(..., min_length=8, max_length=100),
    perfil: str = Form("Fundamentalista"),
    aceite_termos: Optional[str] = Form(None),
    db: Session = Depends(obter_sessao_banco),
):
    nome_limpo = (nome or "").strip()
    if len(nome_limpo) < 2 or len(nome_limpo) > 120:
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "cadastro", "error_msg": "O nome deve ter entre 2 e 120 caracteres."},
            status_code=422,
        )

    if perfil not in PERFIS_PERMITIDOS:
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "cadastro", "error_msg": "Selecione um perfil de investidor válido."},
            status_code=422,
        )

    if not aceite_termos:
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "cadastro", "error_msg": "Você precisa aceitar os Termos de Uso e Política de Privacidade (LGPD)."},
        )

    email_limpo = email.strip().lower()
    usuario_existente = db.query(Usuario).filter(Usuario.email == email_limpo).first()

    if usuario_existente:
        # Se era um registro antigo da Fase 1 sem senha definida, atualiza com a nova senha Argon2
        if not usuario_existente.senha_hash:
            try:
                usuario_existente.nome = nome_limpo
                usuario_existente.senha_hash = auth.gerar_hash_senha(senha)
                usuario_existente.perfil_investidor = perfil
                usuario_existente.aceitou_termos_em = agora_utc()
                db.commit()
                db.refresh(usuario_existente)
            except IntegrityError:
                db.rollback()
                return templates.TemplateResponse(
                    request=request,
                    name="auth.html",
                    context={"modo": "cadastro", "error_msg": "Este e-mail já está cadastrado. Faça login ou recupere sua senha."},
                )
            auth.disparar_confirmacao_email(usuario_existente)
            msg_atualizado = _msg_email(
                "Cadastro atualizado com sucesso! Enviamos um link para o seu e-mail.",
                "Cadastro atualizado com sucesso! Confira o terminal para confirmar o e-mail.",
            )
            resp = RedirectResponse(
                url=f"/watchlist?msg={quote_plus(msg_atualizado)}",
                status_code=303,
            )
            auth.definir_cookie_sessao(resp, usuario_existente.id)
            return resp

        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "cadastro", "error_msg": "Este e-mail já está cadastrado. Faça login ou recupere sua senha."},
        )

    novo_usuario = Usuario(
        nome=nome_limpo,
        email=email_limpo,
        senha_hash=auth.gerar_hash_senha(senha),
        email_confirmado=False,
        perfil_investidor=perfil,
        aceitou_termos_em=agora_utc(),
    )
    db.add(novo_usuario)
    try:
        db.commit()
        db.refresh(novo_usuario)
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "cadastro", "error_msg": "Este e-mail já está cadastrado. Faça login ou recupere sua senha."},
        )

    auth.disparar_confirmacao_email(novo_usuario)

    msg_criada = _msg_email(
        "Conta criada! Enviamos um link para o seu e-mail para confirmar o cadastro.",
        "Conta criada! Verifique seu e-mail (ou confira o terminal de teste) para confirmar o cadastro.",
    )
    resp = RedirectResponse(url=f"/watchlist?msg={quote_plus(msg_criada)}", status_code=303)
    auth.definir_cookie_sessao(resp, novo_usuario.id)
    return resp


@app.get("/confirmar-email/{token}", response_class=HTMLResponse)
def confirmar_email(
    request: Request,
    token: str,
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    dados = auth.ler_token(token, salt="confirmar-email", max_age_segundos=86400)
    if not dados:
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "login", "usuario": usuario, "error_msg": "Link de confirmação inválido ou expirado."},
        )

    u = db.query(Usuario).filter(Usuario.id == dados["uid"]).first()
    if u:
        u.email_confirmado = True
        db.commit()
    return RedirectResponse(url="/watchlist?msg=E-mail+confirmado+com+sucesso!", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def tela_login(request: Request, msg: Optional[str] = None, usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual)):
    if usuario:
        return RedirectResponse(url="/watchlist", status_code=303)
    return templates.TemplateResponse(
        request=request, name="auth.html", context={"modo": "login", "usuario": None, "success_msg": msg}
    )


@app.post("/login", response_class=HTMLResponse)
@limiter.limit("8/minute")
def processar_login(
    request: Request,
    email: str = Form(..., min_length=5, max_length=150),
    senha: str = Form(..., min_length=1, max_length=100),
    db: Session = Depends(obter_sessao_banco),
):
    email_limpo = email.strip().lower()
    u = db.query(Usuario).filter(Usuario.email == email_limpo).first()
    if not u or not auth.verificar_senha(u.senha_hash, senha):
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "login", "error_msg": "E-mail ou senha incorretos."},
        )

    resp = RedirectResponse(url="/watchlist", status_code=303)
    auth.definir_cookie_sessao(resp, u.id)
    return resp


@app.get("/logout")
def fazer_logout():
    resp = RedirectResponse(url="/?msg=Sessao+encerrada+com+seguranca.", status_code=303)
    auth.limpar_cookie_sessao(resp)
    return resp


@app.get("/esqueci-senha", response_class=HTMLResponse)
def tela_esqueci_senha(request: Request, usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual)):
    return templates.TemplateResponse(request=request, name="auth.html", context={"modo": "esqueci", "usuario": usuario})


@app.post("/esqueci-senha", response_class=HTMLResponse)
@limiter.limit("3/minute")
def processar_esqueci_senha(
    request: Request,
    email: str = Form(..., min_length=5, max_length=150),
    db: Session = Depends(obter_sessao_banco),
):
    u = db.query(Usuario).filter(Usuario.email == email.strip().lower()).first()
    if u:
        auth.disparar_recuperacao_senha(u)
    msg_esqueci = _msg_email(
        "Se o e-mail estiver cadastrado, enviamos um link para o seu e-mail.",
        "Se o e-mail estiver cadastrado, enviamos o link de recuperação (confira o terminal em modo local).",
    )
    return templates.TemplateResponse(
        request=request,
        name="auth.html",
        context={
            "modo": "login",
            "success_msg": msg_esqueci,
        },
    )


@app.get("/redefinir-senha/{token}", response_class=HTMLResponse)
def tela_redefinir_senha(request: Request, token: str, db: Session = Depends(obter_sessao_banco)):
    dados = auth.ler_token(token, salt="recuperar-senha", max_age_segundos=3600)
    if not dados:
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "esqueci", "error_msg": "O link de recuperação expirou ou é inválido. Solicite um novo."},
        )
    if "pwd_stamp" in dados:
        u = db.query(Usuario).filter(Usuario.id == dados["uid"]).first()
        if not u or auth.obter_carimbo_senha(u.senha_hash) != dados["pwd_stamp"]:
            return templates.TemplateResponse(
                request=request,
                name="auth.html",
                context={"modo": "esqueci", "error_msg": "Este link de recuperação já foi utilizado. Solicite um novo."},
            )
    return templates.TemplateResponse(request=request, name="auth.html", context={"modo": "redefinir", "token": token})


@app.post("/redefinir-senha/{token}", response_class=HTMLResponse)
@limiter.limit("5/minute")
def processar_redefinir_senha(
    request: Request,
    token: str,
    nova_senha: str = Form(..., min_length=8, max_length=100),
    db: Session = Depends(obter_sessao_banco),
):
    dados = auth.ler_token(token, salt="recuperar-senha", max_age_segundos=3600)
    if not dados:
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "esqueci", "error_msg": "Link expirado ou inválido."},
        )
    u = db.query(Usuario).filter(Usuario.id == dados["uid"]).first()
    if not u or ("pwd_stamp" in dados and auth.obter_carimbo_senha(u.senha_hash) != dados["pwd_stamp"]):
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "esqueci", "error_msg": "Este link de recuperação já foi utilizado ou é inválido."},
        )
    u.senha_hash = auth.gerar_hash_senha(nova_senha)
    db.commit()
    return RedirectResponse(url="/login?msg=Senha+atualizada+com+sucesso!+Faca+login.", status_code=303)


# ---------------------------------------------------------------------------
# Watchlist Privada, Alertas de Preço e Exclusão de Conta (LGPD)
# ---------------------------------------------------------------------------
@app.get("/watchlist", response_class=HTMLResponse)
def painel_watchlist(
    request: Request,
    msg: Optional[str] = None,
    erro: Optional[str] = None,
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    if not usuario:
        return RedirectResponse(url="/login?msg=Faca+login+para+acessar+sua+Watchlist+privada.", status_code=303)

    # Consulta única com OUTER JOIN para trazer a última cotação de todos os itens (sem N+1)
    linhas = (
        db.query(
            MonitoramentoUsuario.id.label("id"),
            MonitoramentoUsuario.ticker.label("ticker"),
            MonitoramentoUsuario.preco_alvo_alerta.label("preco_alvo"),
            MonitoramentoUsuario.notas_documentacao.label("notas"),
            MonitoramentoUsuario.data_adicao.label("data_adicao"),
            Cotacao.preco.label("preco_atual"),
            Cotacao.coletado_em.label("coletado_em"),
        )
        .outerjoin(Ativo, Ativo.ticker == MonitoramentoUsuario.ticker)
        .outerjoin(Cotacao, Ativo.ultima_cotacao_id == Cotacao.id)
        .filter(MonitoramentoUsuario.usuario_id == usuario.id)
        .order_by(MonitoramentoUsuario.data_adicao.desc())
        .all()
    )

    lista_enriquecida = []
    for linha in linhas:
        preco_atual = linha.preco_atual
        alerta_atingido = bool(
            preco_atual is not None
            and linha.preco_alvo is not None
            and preco_atual <= linha.preco_alvo
        )
        lista_enriquecida.append({
            "id": linha.id,
            "ticker": linha.ticker,
            "preco_atual": preco_atual,
            "coletado_em": linha.coletado_em,
            "preco_alvo": linha.preco_alvo,
            "alerta_atingido": alerta_atingido,
            "notas": linha.notas,
            "data_adicao": linha.data_adicao,
        })

    return templates.TemplateResponse(
        request=request,
        name="watchlist.html",
        context={
            "usuario": usuario,
            "itens": lista_enriquecida,
            "success_msg": msg,
            "error_msg": erro,
        },
    )


@app.post("/watchlist/adicionar")
@limiter.limit("30/minute")
def adicionar_watchlist(
    request: Request,
    ticker: str = Form(..., min_length=2, max_length=60),
    preco_alvo: str = Form(""),
    notas: str = Form("", max_length=500),
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
    if not usuario.email_confirmado:
        msg_confirm = (
            "Confirme seu endereço de e-mail para adicionar ativos à Watchlist. "
            "Use o botão 'Reenviar Link de Confirmação' no topo da página."
        )
        return RedirectResponse(url=f"/watchlist?erro={quote_plus(msg_confirm)}", status_code=303)

    try:
        ticker_ok = svc.resolver_ticker(ticker)
    except svc.ErroAtivo as e:
        return RedirectResponse(url=f"/watchlist?erro={quote_plus(str(e))}", status_code=303)

    alvo_val = svc.numero(preco_alvo.replace(",", ".")) if preco_alvo.strip() else None
    if alvo_val is not None and alvo_val <= 0:
        alvo_val = None

    db.add(
        MonitoramentoUsuario(
            usuario_id=usuario.id,
            ticker=ticker_ok,
            preco_alvo_alerta=alvo_val,
            notas_documentacao=notas.strip()[:500],
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        msg_dup = f"O ativo {ticker_ok} já está cadastrado na sua Watchlist."
        return RedirectResponse(url=f"/watchlist?erro={quote_plus(msg_dup)}", status_code=303)

    return RedirectResponse(url=f"/watchlist?msg=Ativo+{ticker_ok}+adicionado+a+sua+Watchlist!", status_code=303)


@app.post("/watchlist/remover/{item_id}")
@limiter.limit("30/minute")
def remover_watchlist(
    request: Request,
    item_id: int,
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
    # Garante que só o próprio dono pode excluir seu item
    item = (
        db.query(MonitoramentoUsuario)
        .filter(MonitoramentoUsuario.id == item_id, MonitoramentoUsuario.usuario_id == usuario.id)
        .first()
    )
    if item:
        db.delete(item)
        db.commit()
    return RedirectResponse(url="/watchlist?msg=Ativo+removido.", status_code=303)


@app.post("/conta/reenviar-confirmacao")
@limiter.limit("2/minute")
def reenviar_confirmacao(
    request: Request,
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
    auth.disparar_confirmacao_email(usuario)
    msg_reenvio = _msg_email(
        "Enviamos um link para o seu e-mail.",
        "Link de confirmação reenviado! Confira o terminal ou seu e-mail.",
    )
    return RedirectResponse(url=f"/watchlist?msg={quote_plus(msg_reenvio)}", status_code=303)


@app.post("/conta/excluir")
@limiter.limit("5/minute")
def excluir_minha_conta(
    request: Request,
    senha_confirmacao: str = Form(...),
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
    if not auth.verificar_senha(usuario.senha_hash, senha_confirmacao):
        return RedirectResponse(url="/watchlist?erro=Senha+incorreta.+A+conta+nao+foi+excluida.", status_code=303)

    db.delete(usuario)
    db.commit()
    resp = RedirectResponse(url="/?msg=Sua+conta+e+todos+os+seus+dados+foram+excluidos+permanentemente+(LGPD).", status_code=303)
    auth.limpar_cookie_sessao(resp)
    return resp


@app.get("/termos", response_class=HTMLResponse)
def pagina_termos_privacidade(request: Request, usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual)):
    return templates.TemplateResponse(request=request, name="legal.html", context={"usuario": usuario})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
