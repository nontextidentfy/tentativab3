"""fase 2: reestrutura cotacoes e analises

Revision ID: a1b2c3d4e5f6
Revises: c0d49a3075c6
Create Date: 2026-10-20 10:45:00.000000

"""
import datetime
import json
import math
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "c0d49a3075c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TIPO_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
UQ_WATCHLIST = "uq_monitoramento_usuarios_usuario_id_ticker"
FK_ULTIMA_COTACAO = "fk_ativos_ultima_cotacao_id_cotacoes"


def _num_json(val, *, eh_percentual_razao: bool = False):
    """Extrai float de valor cru ou string legada; 'N/D' ou inválido vira None."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(val, str):
        s = val.strip()
        if not s or s.upper().startswith("N/D"):
            return None
        mult = 1.0
        tem_pct = s.endswith("%")
        if tem_pct:
            s = s[:-1].strip()
        if s.endswith("x"):
            s = s[:-1].strip()
        for sufixo, fator in ((" Bi", 1_000_000_000.0), (" Mi", 1_000_000.0), (" Mil", 1_000.0)):
            if s.endswith(sufixo):
                mult = fator
                s = s[: -len(sufixo)].strip()
                break
        for prefixo in ("R$", "US$", "€", "BRL", "USD", "EUR"):
            if s.startswith(prefixo):
                s = s[len(prefixo) :].strip()
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif s.count(".") > 1:
            s = s.replace(".", "")
        try:
            v = float(s) * mult
        except ValueError:
            return None
        if math.isnan(v) or math.isinf(v):
            return None
        if tem_pct and eh_percentual_razao:
            return v / 100.0
        return v
    return None


def _normalizar_tipo(dados: dict, tipo_legado: str | None, ticker: str) -> str:
    num = dados.get("Numeros") or {}
    meta = dados.get("Metadados") or {}
    ide = dados.get("Identificacao") or {}
    for candidato in (meta.get("tipo"), num.get("tipo"), ide.get("Tipo_Codigo")):
        if candidato in ("Acao", "Unit", "FII", "ETF", "BDR"):
            return candidato
    txt = (tipo_legado or ide.get("Tipo_Ativo") or "").upper()
    if "FII" in txt or "IMOB" in txt:
        return "FII"
    if "ETF" in txt:
        return "ETF"
    if "BDR" in txt:
        return "BDR"
    if "UNIT" in txt or (ticker or "").upper().endswith("11"):
        return "Unit"
    return "Acao"


def _parse_dt(val) -> datetime.datetime:
    if isinstance(val, datetime.datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    if isinstance(val, str) and val.strip():
        try:
            return datetime.datetime.fromisoformat(val.strip().replace("Z", ""))
        except ValueError:
            pass
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def upgrade() -> None:
    """Cria ativos, cotacoes e analises_ia, migra dados de analises_ativos e adiciona UniqueConstraint na watchlist."""
    op.create_table(
        "ativos",
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("nome", sa.String(length=200), nullable=True),
        sa.Column("tipo", sa.String(length=50), nullable=True),
        sa.Column("setor", sa.String(length=120), nullable=True),
        sa.Column("industria", sa.String(length=120), nullable=True),
        sa.Column("site", sa.String(length=255), nullable=True),
        sa.Column("resumo", sa.Text(), nullable=True),
        sa.Column("moeda_cotacao", sa.String(length=10), server_default="BRL", nullable=True),
        sa.Column("ultima_cotacao_id", sa.Integer(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("ticker", name="pk_ativos"),
    )
    op.create_index("ix_ativos_ticker", "ativos", ["ticker"], unique=False)

    op.create_table(
        "cotacoes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("coletado_em", sa.DateTime(), nullable=False),
        sa.Column("preco", sa.Float(), nullable=True),
        sa.Column("variacao_dia_pct", sa.Float(), server_default="0", nullable=False),
        sa.Column("volume_acoes", sa.Float(), nullable=True),
        sa.Column("volume_financeiro", sa.Float(), nullable=True),
        sa.Column("p_l", sa.Float(), nullable=True),
        sa.Column("p_l_projetado", sa.Float(), nullable=True),
        sa.Column("p_vp", sa.Float(), nullable=True),
        sa.Column("ev_ebitda", sa.Float(), nullable=True),
        sa.Column("lpa", sa.Float(), nullable=True),
        sa.Column("vpa", sa.Float(), nullable=True),
        sa.Column("dy_12m_pct", sa.Float(), nullable=True),
        sa.Column("dividendos_12m", sa.Float(), nullable=True),
        sa.Column("graham", sa.Float(), nullable=True),
        sa.Column("bazin", sa.Float(), nullable=True),
        sa.Column("roe", sa.Float(), nullable=True),
        sa.Column("margem_liquida", sa.Float(), nullable=True),
        sa.Column("ebitda", sa.Float(), nullable=True),
        sa.Column("divida_liquida", sa.Float(), nullable=True),
        sa.Column("alavancagem", sa.Float(), nullable=True),
        sa.Column("min_52s", sa.Float(), nullable=True),
        sa.Column("max_52s", sa.Float(), nullable=True),
        sa.Column("valor_mercado", sa.Float(), nullable=True),
        sa.Column("dados", TIPO_JSON, nullable=False),
        sa.ForeignKeyConstraint(["ticker"], ["ativos.ticker"], name="fk_cotacoes_ticker_ativos", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_cotacoes"),
    )
    op.create_index("ix_cotacoes_id", "cotacoes", ["id"], unique=False)
    op.create_index("ix_cotacoes_ticker", "cotacoes", ["ticker"], unique=False)
    op.create_index("ix_cotacoes_coletado_em", "cotacoes", ["coletado_em"], unique=False)
    op.create_index("ix_cotacoes_ticker_coletado_em", "cotacoes", ["ticker", "coletado_em"], unique=False)

    with op.batch_alter_table("ativos", schema=None) as batch_op:
        batch_op.create_foreign_key(
            FK_ULTIMA_COTACAO,
            "cotacoes",
            ["ultima_cotacao_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "analises_ia",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=12), nullable=False),
        sa.Column("cotacao_id", sa.Integer(), nullable=False),
        sa.Column("modelo", sa.String(length=60), nullable=True),
        sa.Column("veio_da_ia", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("relatorio_md", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["cotacao_id"], ["cotacoes.id"], name="fk_analises_ia_cotacao_id_cotacoes", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticker"], ["ativos.ticker"], name="fk_analises_ia_ticker_ativos", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_analises_ia"),
    )
    op.create_index("ix_analises_ia_id", "analises_ia", ["id"], unique=False)
    op.create_index("ix_analises_ia_ticker", "analises_ia", ["ticker"], unique=False)
    op.create_index("ix_analises_ia_cotacao_id", "analises_ia", ["cotacao_id"], unique=False)

    # -----------------------------------------------------------------------
    # Migração dos dados de analises_ativos -> ativos, cotacoes, analises_ia
    # -----------------------------------------------------------------------
    bind = op.get_bind()
    analises_legadas = bind.execute(
        sa.text(
            "SELECT id, ticker, nome_empresa, tipo_ativo, dados_json, modelo_ia, relatorio_ia, "
            "data_dados, data_criacao FROM analises_ativos ORDER BY COALESCE(data_dados, data_criacao) ASC, id ASC"
        )
    ).mappings().all()

    meta_mig = sa.MetaData()
    ativos_table = sa.Table(
        "ativos",
        meta_mig,
        sa.Column("ticker", sa.String, primary_key=True),
        sa.Column("nome", sa.String),
        sa.Column("tipo", sa.String),
        sa.Column("setor", sa.String),
        sa.Column("industria", sa.String),
        sa.Column("site", sa.String),
        sa.Column("resumo", sa.Text),
        sa.Column("moeda_cotacao", sa.String),
        sa.Column("ultima_cotacao_id", sa.Integer),
        sa.Column("atualizado_em", sa.DateTime),
    )
    cotacoes_table = sa.Table(
        "cotacoes",
        meta_mig,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String),
        sa.Column("coletado_em", sa.DateTime),
        sa.Column("preco", sa.Float),
        sa.Column("variacao_dia_pct", sa.Float),
        sa.Column("volume_acoes", sa.Float),
        sa.Column("volume_financeiro", sa.Float),
        sa.Column("p_l", sa.Float),
        sa.Column("p_l_projetado", sa.Float),
        sa.Column("p_vp", sa.Float),
        sa.Column("ev_ebitda", sa.Float),
        sa.Column("lpa", sa.Float),
        sa.Column("vpa", sa.Float),
        sa.Column("dy_12m_pct", sa.Float),
        sa.Column("dividendos_12m", sa.Float),
        sa.Column("graham", sa.Float),
        sa.Column("bazin", sa.Float),
        sa.Column("roe", sa.Float),
        sa.Column("margem_liquida", sa.Float),
        sa.Column("ebitda", sa.Float),
        sa.Column("divida_liquida", sa.Float),
        sa.Column("alavancagem", sa.Float),
        sa.Column("min_52s", sa.Float),
        sa.Column("max_52s", sa.Float),
        sa.Column("valor_mercado", sa.Float),
        sa.Column("dados", TIPO_JSON),
    )
    analises_ia_table = sa.Table(
        "analises_ia",
        meta_mig,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String),
        sa.Column("cotacao_id", sa.Integer),
        sa.Column("modelo", sa.String),
        sa.Column("veio_da_ia", sa.Boolean),
        sa.Column("relatorio_md", sa.Text),
        sa.Column("criado_em", sa.DateTime),
    )

    tickers_vistos: set[str] = set()
    ultima_cotacao_por_ticker: dict[str, tuple[int, datetime.datetime]] = {}

    for row in analises_legadas:
        ticker = (row["ticker"] or "").strip().upper()
        if not ticker:
            continue

        try:
            dados = json.loads(row["dados_json"]) if row["dados_json"] else {}
        except Exception:
            dados = {}

        ide = dados.get("Identificacao") or {}
        cot = dados.get("Cotacao_e_Mercado") or {}
        fluxo = dados.get("Fluxo_e_Volume") or {}
        val = dados.get("Valuation") or {}
        rent = dados.get("Rentabilidade_e_Eficiencia") or {}
        end = dados.get("Endividamento_e_Caixa") or {}
        prov = dados.get("Proventos") or {}
        num = dados.get("Numeros") or {}
        meta = dados.get("Metadados") or {}

        coletado_em = _parse_dt(row["data_dados"] or row["data_criacao"])
        nome = (ide.get("Nome_Empresa") or row["nome_empresa"] or ticker)[:200]
        tipo = _normalizar_tipo(dados, row["tipo_ativo"], ticker)
        setor = (ide.get("Setor") or None)
        industria = (ide.get("Industria") or None)
        site_raw = ide.get("Site_Oficial")
        site = None if (not site_raw or str(site_raw).strip().upper() == "N/D") else str(site_raw).strip()[:255]
        resumo = ide.get("Resumo_Negocio") or None
        moeda_cotacao = (meta.get("moeda_cotacao") or ide.get("Moeda_Cotacao") or "BRL")[:10]

        if ticker not in tickers_vistos:
            bind.execute(
                ativos_table.insert().values(
                    ticker=ticker,
                    nome=nome,
                    tipo=tipo,
                    setor=setor,
                    industria=industria,
                    site=site,
                    resumo=resumo,
                    moeda_cotacao=moeda_cotacao,
                    ultima_cotacao_id=None,
                    atualizado_em=coletado_em,
                )
            )
            tickers_vistos.add(ticker)
        else:
            bind.execute(
                ativos_table.update()
                .where(ativos_table.c.ticker == ticker)
                .values(
                    nome=nome,
                    tipo=tipo,
                    setor=setor,
                    industria=industria,
                    site=site,
                    resumo=resumo,
                    moeda_cotacao=moeda_cotacao,
                    atualizado_em=coletado_em,
                )
            )

        preco = _num_json(num.get("preco_atual"))
        if preco is None:
            preco = _num_json(cot.get("Preco_Atual"))

        var_dia = _num_json(num.get("variacao_dia_pct"))
        if var_dia is None:
            var_dia = _num_json(cot.get("Variacao_Dia"))
        if var_dia is None:
            var_dia = 0.0

        vol_fin = _num_json(num.get("vol_financeiro_dia"))
        if vol_fin is None:
            vol_fin = _num_json(fluxo.get("Volume_Financeiro_Dia"))

        dy_12m_pct = _num_json(num.get("dy_12m"))
        if dy_12m_pct is None:
            dy_12m_pct = _num_json(prov.get("Dividend_Yield_12m_Pct"))
        if dy_12m_pct is None:
            dy_12m_pct = _num_json(prov.get("Dividend_Yield_Real_12m"))

        div_12m = _num_json(num.get("dividendos_12m"))
        if div_12m is None:
            div_12m = _num_json(prov.get("Total_Pago_12m_por_Cota"))

        graham = _num_json(num.get("graham"))
        if graham is None:
            graham = _num_json(val.get("Valor_Intrinseco_Graham"))

        bazin = _num_json(num.get("bazin"))
        if bazin is None:
            bazin = _num_json(val.get("Preco_Teto_Bazin_6pct"))

        min_52s = _num_json(num.get("min_52s"))
        if min_52s is None:
            min_52s = _num_json(cot.get("Minima_52_Semanas"))

        max_52s = _num_json(num.get("max_52s"))
        if max_52s is None:
            max_52s = _num_json(cot.get("Maxima_52_Semanas"))

        res_cot = bind.execute(
            cotacoes_table.insert().values(
                ticker=ticker,
                coletado_em=coletado_em,
                preco=preco,
                variacao_dia_pct=var_dia,
                volume_acoes=_num_json(fluxo.get("Volume_Acoes_Dia")),
                volume_financeiro=vol_fin,
                p_l=_num_json(val.get("P_L")),
                p_l_projetado=_num_json(val.get("P_L_Projetado")),
                p_vp=_num_json(val.get("P_VP")),
                ev_ebitda=_num_json(val.get("EV_EBITDA")),
                lpa=_num_json(val.get("LPA")),
                vpa=_num_json(val.get("VPA")),
                dy_12m_pct=dy_12m_pct,
                dividendos_12m=div_12m,
                graham=graham,
                bazin=bazin,
                roe=_num_json(rent.get("ROE"), eh_percentual_razao=True),
                margem_liquida=_num_json(rent.get("Margem_Liquida"), eh_percentual_razao=True),
                ebitda=_num_json(end.get("EBITDA")),
                divida_liquida=_num_json(end.get("Divida_Liquida")),
                alavancagem=_num_json(end.get("Divida_Liquida_sobre_EBITDA")),
                min_52s=min_52s,
                max_52s=max_52s,
                valor_mercado=_num_json(cot.get("Valor_de_Mercado")),
                dados=dados,
            )
        )
        cotacao_id = res_cot.inserted_primary_key[0]
        ultima_cotacao_por_ticker[ticker] = (cotacao_id, coletado_em)

        modelo_ia = row["modelo_ia"] or "Motor Algorítmico Local"
        veio_da_ia = "Algorítmico" not in modelo_ia
        relatorio_md = row["relatorio_ia"] or ""

        bind.execute(
            analises_ia_table.insert().values(
                ticker=ticker,
                cotacao_id=cotacao_id,
                modelo=modelo_ia,
                veio_da_ia=veio_da_ia,
                relatorio_md=relatorio_md,
                criado_em=coletado_em,
            )
        )

    for tk, (cid, dt_ref) in ultima_cotacao_por_ticker.items():
        bind.execute(
            ativos_table.update()
            .where(ativos_table.c.ticker == tk)
            .values(ultima_cotacao_id=cid, atualizado_em=dt_ref)
        )

    # Renomeia analises_ativos -> analises_ativos_legado (preservando os dados originais)
    op.drop_index("ix_ticker_data_dados", table_name="analises_ativos")
    op.drop_index(op.f("ix_analises_ativos_ticker"), table_name="analises_ativos")
    op.drop_index(op.f("ix_analises_ativos_id"), table_name="analises_ativos")
    op.rename_table("analises_ativos", "analises_ativos_legado")

    # Remove duplicatas de (usuario_id, ticker) em monitoramento_usuarios mantendo o registro mais recente
    mon_rows = bind.execute(
        sa.text(
            "SELECT id, usuario_id, ticker, data_adicao FROM monitoramento_usuarios "
            "ORDER BY COALESCE(data_adicao, '1970-01-01') DESC, id DESC"
        )
    ).mappings().all()
    pares_vistos: set[tuple[int, str]] = set()
    ids_duplicados: list[int] = []
    for r in mon_rows:
        chave = (int(r["usuario_id"]), str(r["ticker"]).strip().upper())
        if chave in pares_vistos:
            ids_duplicados.append(int(r["id"]))
        else:
            pares_vistos.add(chave)

    if ids_duplicados:
        for dup_id in ids_duplicados:
            bind.execute(
                sa.text("DELETE FROM monitoramento_usuarios WHERE id = :id"),
                {"id": dup_id},
            )

    with op.batch_alter_table("monitoramento_usuarios", schema=None) as batch_op:
        batch_op.create_unique_constraint(UQ_WATCHLIST, ["usuario_id", "ticker"])


def downgrade() -> None:
    """Restaura analises_ativos a partir do legado e remove as tabelas novas."""
    with op.batch_alter_table("monitoramento_usuarios", schema=None) as batch_op:
        batch_op.drop_constraint(UQ_WATCHLIST, type_="unique")

    op.rename_table("analises_ativos_legado", "analises_ativos")
    op.create_index(op.f("ix_analises_ativos_id"), "analises_ativos", ["id"], unique=False)
    op.create_index(op.f("ix_analises_ativos_ticker"), "analises_ativos", ["ticker"], unique=False)
    op.create_index("ix_ticker_data_dados", "analises_ativos", ["ticker", "data_dados"], unique=False)

    op.drop_index("ix_analises_ia_cotacao_id", table_name="analises_ia")
    op.drop_index("ix_analises_ia_ticker", table_name="analises_ia")
    op.drop_index("ix_analises_ia_id", table_name="analises_ia")
    op.drop_table("analises_ia")

    with op.batch_alter_table("ativos", schema=None) as batch_op:
        batch_op.drop_constraint(FK_ULTIMA_COTACAO, type_="foreignkey")

    op.drop_index("ix_cotacoes_ticker_coletado_em", table_name="cotacoes")
    op.drop_index("ix_cotacoes_coletado_em", table_name="cotacoes")
    op.drop_index("ix_cotacoes_ticker", table_name="cotacoes")
    op.drop_index("ix_cotacoes_id", table_name="cotacoes")
    op.drop_table("cotacoes")

    op.drop_index("ix_ativos_ticker", table_name="ativos")
    op.drop_table("ativos")
