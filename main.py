import datetime
import json
import logging
import os
from typing import Optional
from urllib.parse import quote_plus
import markdown as md
from fastapi import FastAPI, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import servico_analise as svc
import seguranca as auth
from banco import (
    MonitoramentoUsuario,
    RegistroAnalise,
    Usuario,
    agora_utc,
    obter_sessao_banco,
    SessionLocal,
    sincronizar_sequencias_postgres,
)

# Garante que as sequências de ID do PostgreSQL estejam sincronizadas ao iniciar
sincronizar_sequencias_postgres()

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

templates = Jinja2Templates(directory=os.path.join(PASTA_PROJETO, "templates"))
templates.env.globals["PERFIS_OPCOES"] = PERFIS_OPCOES
log = logging.getLogger("investidor_ia")

HORAS_CACHE = 24
FUSO_BRASILIA = datetime.timedelta(hours=-3)
# Limite de consultas de análise por IP (protege a cota do Gemini). Pode ser ajustado no .env.
LIMITE_ANALISE = (os.getenv("LIMITE_ANALISE") or "5/minute;10/day").strip()


def renderizar_markdown(texto: str) -> str:
    """Converte o relatório (Markdown) em HTML, neutralizando qualquer tag HTML vinda da IA."""
    texto_seguro = (texto or "").replace("<", "&lt;")
    return md.markdown(texto_seguro, extensions=["extra", "sane_lists"])


def filtro_moeda_br(valor):
    return svc.br(valor, 2)


def filtro_horario_br(data_utc):
    if not data_utc:
        return "N/D"
    return (data_utc + FUSO_BRASILIA).strftime("%d/%m/%Y às %H:%M")


templates.env.filters["moeda_br"] = filtro_moeda_br
templates.env.filters["horario_br"] = filtro_horario_br


def obter_recentes_seguro(db: Session):
    try:
        registros = db.query(RegistroAnalise).order_by(RegistroAnalise.data_criacao.desc()).limit(100).all()
        unicos = []
        vistos = set()
        for r in registros:
            if r.ticker not in vistos:
                vistos.add(r.ticker)
                unicos.append(r)
                if len(unicos) == 12:
                    break
        return unicos
    except Exception:
        log.exception("Erro ao buscar ativos recentes")
        return []


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
    """Autocomplete da barra de busca: aceita nome da empresa ou parte do ticker."""
    return JSONResponse({"resultados": svc.buscar_ativos(q, limite=8)})


@app.get("/analisar", response_class=HTMLResponse)
@limiter.limit(LIMITE_ANALISE)
def analisar_ativo(
    request: Request,
    ticker: str = Query(..., min_length=2, max_length=60),
    atualizar: bool = False,
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    painel_ativos = obter_recentes_seguro(db)
    try:
        ticker_ok = svc.resolver_ticker(ticker)
    except svc.ErroAtivo as e:
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={"error_msg": str(e), "painel_ativos": painel_ativos, "usuario": usuario},
        )

    limite = agora_utc() - datetime.timedelta(hours=HORAS_CACHE)

    if not atualizar:
        registro = (
            db.query(RegistroAnalise)
            .filter(
                RegistroAnalise.ticker == ticker_ok,
                RegistroAnalise.data_dados >= limite,
            )
            .order_by(RegistroAnalise.data_criacao.desc())
            .first()
        )

        if registro:
            dados = json.loads(registro.dados_json)
            relatorio_html = renderizar_markdown(registro.relatorio_ia)
            return templates.TemplateResponse(
                request=request,
                name="resultado.html",
                context={
                    "usuario": usuario,
                    "dados": dados,
                    "relatorio_html": relatorio_html,
                    "origem": "Banco de Dados (Cache 24h)",
                    "motor": registro.modelo_ia,
                    "data_dados": registro.data_dados,
                    "painel_ativos": painel_ativos,
                },
            )

    try:
        dados = svc.extrair_raio_x_b3(ticker_ok)
    except svc.ErroAtivo as e:
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={"error_msg": str(e), "painel_ativos": painel_ativos, "usuario": usuario},
        )
    except Exception:
        log.exception("Erro ao extrair %s", ticker_ok)
        return templates.TemplateResponse(
            request=request,
            name="home.html",
            context={
                "error_msg": f"Não foi possível obter os dados de {ticker_ok} na B3 no momento.",
                "painel_ativos": painel_ativos,
                "usuario": usuario,
            },
        )

    relatorio_md, motor_usado = svc.gerar_relatorio_ia(dados)
    relatorio_html = renderizar_markdown(relatorio_md)
    data_atual = agora_utc()

    if "Algorítmico" not in motor_usado:
        try:
            db.add(
                RegistroAnalise(
                    ticker=ticker_ok,
                    nome_empresa=dados["Identificacao"]["Nome_Empresa"],
                    tipo_ativo=dados["Identificacao"]["Tipo_Ativo"],
                    preco_atual=dados["Numeros"]["preco_atual"],
                    variacao_dia_pct=dados["Numeros"].get("variacao_dia_pct", 0.0),
                    volume_financeiro=dados["Fluxo_e_Volume"]["Volume_Financeiro_Dia"],
                    p_vp=str(dados["Valuation"]["P_VP"]),
                    p_l=str(dados["Valuation"]["P_L"]),
                    dividend_yield=dados["Proventos"]["Dividend_Yield_Real_12m"],
                    dados_json=json.dumps(dados, ensure_ascii=False),
                    modelo_ia=motor_usado,
                    relatorio_ia=relatorio_md,
                    data_dados=data_atual,
                )
            )
            db.commit()
        except Exception:
            db.rollback()
            log.exception("Erro ao gravar cache no PostgreSQL")

    return templates.TemplateResponse(
        request=request,
        name="resultado.html",
        context={
            "usuario": usuario,
            "dados": dados,
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
            resp = RedirectResponse(
                url="/watchlist?msg=Cadastro+atualizado+com+sucesso!+Confira+o+terminal+para+confirmar+o+e-mail.",
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

    resp = RedirectResponse(url="/watchlist?msg=Conta+criada!+Verifique+seu+e-mail+(ou+o+terminal+de+teste)+para+confirmar+o+cadastro.", status_code=303)
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
    return templates.TemplateResponse(
        request=request,
        name="auth.html",
        context={
            "modo": "login",
            "success_msg": "Se o e-mail estiver cadastrado, enviamos o link de recuperação (confira também o console em modo local).",
        },
    )


@app.get("/redefinir-senha/{token}", response_class=HTMLResponse)
def tela_redefinir_senha(request: Request, token: str):
    dados = auth.ler_token(token, salt="recuperar-senha", max_age_segundos=3600)
    if not dados:
        return templates.TemplateResponse(
            request=request,
            name="auth.html",
            context={"modo": "esqueci", "error_msg": "O link de recuperação expirou ou é inválido. Solicite um novo."},
        )
    return templates.TemplateResponse(request=request, name="auth.html", context={"modo": "redefinir", "token": token})


@app.post("/redefinir-senha/{token}", response_class=HTMLResponse)
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
            context={"modo": "esqueci", "error_msg": "Link expirado."},
        )
    u = db.query(Usuario).filter(Usuario.id == dados["uid"]).first()
    if u:
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

    itens = (
        db.query(MonitoramentoUsuario)
        .filter(MonitoramentoUsuario.usuario_id == usuario.id)
        .order_by(MonitoramentoUsuario.data_adicao.desc())
        .all()
    )

    # Cruza os ativos da watchlist com a última cotação conhecida no banco para disparar alertas
    lista_enriquecida = []
    for item in itens:
        ultimo_reg = (
            db.query(RegistroAnalise)
            .filter(RegistroAnalise.ticker == item.ticker)
            .order_by(RegistroAnalise.data_criacao.desc())
            .first()
        )
        preco_atual = ultimo_reg.preco_atual if ultimo_reg else None
        alerta_atingido = False
        if preco_atual and item.preco_alvo_alerta and preco_atual <= item.preco_alvo_alerta:
            alerta_atingido = True

        lista_enriquecida.append({
            "id": item.id,
            "ticker": item.ticker,
            "preco_atual": preco_atual,
            "preco_alvo": item.preco_alvo_alerta,
            "alerta_atingido": alerta_atingido,
            "notas": item.notas_documentacao,
            "data_adicao": item.data_adicao,
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
def adicionar_watchlist(
    ticker: str = Form(..., min_length=2, max_length=60),
    preco_alvo: str = Form(""),
    notas: str = Form("", max_length=500),
    db: Session = Depends(obter_sessao_banco),
    usuario: Optional[Usuario] = Depends(auth.obter_usuario_atual),
):
    if not usuario:
        return RedirectResponse(url="/login", status_code=303)
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
    db.commit()
    return RedirectResponse(url=f"/watchlist?msg=Ativo+{ticker_ok}+adicionado+a+sua+Watchlist!", status_code=303)


@app.post("/watchlist/remover/{item_id}")
def remover_watchlist(
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
    return RedirectResponse(url="/watchlist?msg=Link+de+confirmacao+reenviado!(Confira+o+terminal+ou+e-mail).", status_code=303)


@app.post("/conta/excluir")
def excluir_minha_conta(
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
