"""
Gerenciamento de Banco de Dados de Produção (PostgreSQL) e Testes (SQLite).
Modelos: Ativo, Cotacao, AnaliseIA, Usuario (Contas/Argon2) e MonitoramentoUsuario (Watchlist Privada).
"""
import datetime
import logging
import os
from typing import Optional
from dotenv import load_dotenv
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PASTA_PROJETO, ".env"))

log = logging.getLogger("investidor_ia.banco")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TIPO_JSON_BANCO = JSON().with_variant(JSONB(), "postgresql")


def normalizar_url_banco(url: str) -> str:
    """
    Padroniza a URL do PostgreSQL (Neon, Render, Heroku etc.) para o driver psycopg 3,
    ou preserva URLs SQLite para execução de testes.
    """
    url = (url or "").strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def criar_engine_banco(url_normalizada: str):
    """Cria o engine SQLAlchemy adequado para PostgreSQL (produção) ou SQLite (testes)."""
    if url_normalizada.startswith("postgresql+psycopg://"):
        return create_engine(
            url_normalizada,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_recycle=300,
        )
    if url_normalizada.startswith("sqlite"):
        eng = create_engine(
            url_normalizada,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(eng, "connect")
        def _ativar_fk_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return eng

    raise ValueError(
        "A DATABASE_URL do PostgreSQL não foi encontrada ou é inválida no .env. "
        "Use a connection string do Neon (começa com 'postgresql://')."
    )


URL_BANCO = normalizar_url_banco(os.getenv("DATABASE_URL"))
engine = criar_engine_banco(URL_BANCO)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
metadata_padrao = MetaData(naming_convention=NAMING_CONVENTION)
Base = declarative_base(metadata=metadata_padrao)


def agora_utc():
    """Data/hora UTC sem fuso para padronização global no banco."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Ativo(Base):
    __tablename__ = "ativos"

    ticker = Column(String(12), primary_key=True, index=True)
    nome = Column(String(200), nullable=True)
    tipo = Column(String(50), nullable=True)  # Acao, Unit, FII, ETF, BDR
    setor = Column(String(120), nullable=True)
    industria = Column(String(120), nullable=True)
    site = Column(String(255), nullable=True)
    resumo = Column(Text, nullable=True)
    moeda_cotacao = Column(String(10), default="BRL", server_default="BRL")
    ultima_cotacao_id = Column(
        Integer,
        ForeignKey(
            "cotacoes.id",
            use_alter=True,
            name="fk_ativos_ultima_cotacao_id_cotacoes",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    atualizado_em = Column(DateTime, default=agora_utc, onupdate=agora_utc)

    ultima_cotacao = relationship(
        "Cotacao",
        foreign_keys=[ultima_cotacao_id],
        post_update=True,
    )
    cotacoes = relationship(
        "Cotacao",
        back_populates="ativo",
        foreign_keys="Cotacao.ticker",
        cascade="all, delete-orphan",
    )
    analises = relationship(
        "AnaliseIA",
        back_populates="ativo",
        cascade="all, delete-orphan",
    )


class Cotacao(Base):
    __tablename__ = "cotacoes"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(12), ForeignKey("ativos.ticker", ondelete="CASCADE"), nullable=False, index=True)
    coletado_em = Column(DateTime, default=agora_utc, nullable=False, index=True)

    preco = Column(Float, nullable=True)
    variacao_dia_pct = Column(Float, default=0.0, server_default="0", nullable=False)
    volume_acoes = Column(Float, nullable=True)
    volume_financeiro = Column(Float, nullable=True)
    p_l = Column(Float, nullable=True)
    p_l_projetado = Column(Float, nullable=True)
    p_vp = Column(Float, nullable=True)
    ev_ebitda = Column(Float, nullable=True)
    lpa = Column(Float, nullable=True)
    vpa = Column(Float, nullable=True)
    dy_12m_pct = Column(Float, nullable=True)
    dividendos_12m = Column(Float, nullable=True)
    graham = Column(Float, nullable=True)
    bazin = Column(Float, nullable=True)
    roe = Column(Float, nullable=True)
    margem_liquida = Column(Float, nullable=True)
    ebitda = Column(Float, nullable=True)
    divida_liquida = Column(Float, nullable=True)
    alavancagem = Column(Float, nullable=True)
    min_52s = Column(Float, nullable=True)
    max_52s = Column(Float, nullable=True)
    valor_mercado = Column(Float, nullable=True)
    dados = Column(TIPO_JSON_BANCO, nullable=False)

    ativo = relationship("Ativo", back_populates="cotacoes", foreign_keys=[ticker])
    analises = relationship("AnaliseIA", back_populates="cotacao", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_cotacoes_ticker_coletado_em", "ticker", "coletado_em"),)


class AnaliseIA(Base):
    __tablename__ = "analises_ia"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(12), ForeignKey("ativos.ticker", ondelete="CASCADE"), nullable=False, index=True)
    cotacao_id = Column(Integer, ForeignKey("cotacoes.id", ondelete="CASCADE"), nullable=False, index=True)
    modelo = Column(String(60), nullable=True)
    veio_da_ia = Column(Boolean, default=True, server_default="true", nullable=False)
    relatorio_md = Column(Text, nullable=False)
    criado_em = Column(DateTime, default=agora_utc, nullable=False)

    ativo = relationship("Ativo", back_populates="analises")
    cotacao = relationship("Cotacao", back_populates="analises")


class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(120), nullable=False)
    email = Column(String(150), unique=True, index=True, nullable=False)
    senha_hash = Column(String(255), nullable=False, server_default="")
    email_confirmado = Column(Boolean, default=False, nullable=False, server_default="false")
    perfil_investidor = Column(String(50), default="Fundamentalista")
    aceitou_termos_em = Column(DateTime, default=agora_utc)
    data_cadastro = Column(DateTime, default=agora_utc)
    plano = Column(String(20), default="free", server_default="free", nullable=False)

    @property
    def eh_pro(self) -> bool:
        return (self.plano or "free").lower() == "pro"

    monitoramentos = relationship(
        "MonitoramentoUsuario",
        back_populates="usuario",
        cascade="all, delete-orphan",
    )


class MonitoramentoUsuario(Base):
    __tablename__ = "monitoramento_usuarios"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    ticker = Column(String(12), index=True, nullable=False)
    preco_alvo_alerta = Column(Float, nullable=True)
    notas_documentacao = Column(Text, nullable=True)
    data_adicao = Column(DateTime, default=agora_utc)

    usuario = relationship("Usuario", back_populates="monitoramentos")

    __table_args__ = (UniqueConstraint("usuario_id", "ticker", name="uq_monitoramento_usuarios_usuario_id_ticker"),)


def obter_sessao_banco():
    """Gera uma sessão isolada com o banco para cada requisição da API."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


TABELAS_SEQUENCIA_POSTGRES: tuple[str, ...] = (
    "usuarios",
    "cotacoes",
    "analises_ia",
    "monitoramento_usuarios",
)


def sincronizar_sequencias_postgres(eng=None):
    """
    Sincroniza o contador de IDs (SERIAL/SEQUENCE) do PostgreSQL com o maior ID existente
    nas tabelas. Não faz nada quando o dialeto não é PostgreSQL.
    """
    eng_alvo = eng or engine
    if eng_alvo.dialect.name != "postgresql":
        return

    sql_por_tabela = {
        "usuarios": text(
            "SELECT setval(pg_get_serial_sequence(:tabela, 'id'), "
            "COALESCE((SELECT MAX(id) FROM usuarios), 1), "
            "(SELECT COUNT(*) > 0 FROM usuarios));"
        ),
        "cotacoes": text(
            "SELECT setval(pg_get_serial_sequence(:tabela, 'id'), "
            "COALESCE((SELECT MAX(id) FROM cotacoes), 1), "
            "(SELECT COUNT(*) > 0 FROM cotacoes));"
        ),
        "analises_ia": text(
            "SELECT setval(pg_get_serial_sequence(:tabela, 'id'), "
            "COALESCE((SELECT MAX(id) FROM analises_ia), 1), "
            "(SELECT COUNT(*) > 0 FROM analises_ia));"
        ),
        "monitoramento_usuarios": text(
            "SELECT setval(pg_get_serial_sequence(:tabela, 'id'), "
            "COALESCE((SELECT MAX(id) FROM monitoramento_usuarios), 1), "
            "(SELECT COUNT(*) > 0 FROM monitoramento_usuarios));"
        ),
    }

    for tabela in TABELAS_SEQUENCIA_POSTGRES:
        try:
            with eng_alvo.begin() as conn:
                conn.execute(sql_por_tabela[tabela], {"tabela": tabela})
        except Exception as e:
            log.warning("Não foi possível sincronizar a sequência da tabela %s: %s", tabela, type(e).__name__)


def limpar_historico(dias: int = 90, db: Optional[Session] = None) -> dict[str, int]:
    """
    Apaga registros de `cotacoes` e `analises_ia` mais antigos que `dias`,
    preservando sempre a `ultima_cotacao` de cada ativo.
    """
    fechar_ao_final = False
    if db is None:
        db = SessionLocal()
        fechar_ao_final = True

    try:
        limite = agora_utc() - datetime.timedelta(days=dias)
        ids_preservados = [
            r[0]
            for r in db.query(Ativo.ultima_cotacao_id).filter(Ativo.ultima_cotacao_id.isnot(None)).all()
        ]

        q_cot_antigas = db.query(Cotacao.id).filter(Cotacao.coletado_em < limite)
        if ids_preservados:
            q_cot_antigas = q_cot_antigas.filter(~Cotacao.id.in_(ids_preservados))
        ids_cot_apagar = [r[0] for r in q_cot_antigas.all()]

        q_analises = db.query(AnaliseIA).filter(AnaliseIA.criado_em < limite)
        if ids_preservados:
            q_analises = q_analises.filter(~AnaliseIA.cotacao_id.in_(ids_preservados))
        analises_removidas = q_analises.delete(synchronize_session=False)

        cotacoes_removidas = 0
        if ids_cot_apagar:
            # Garante remoção das análises ligadas às cotações que serão apagadas
            db.query(AnaliseIA).filter(AnaliseIA.cotacao_id.in_(ids_cot_apagar)).delete(synchronize_session=False)
            cotacoes_removidas = (
                db.query(Cotacao)
                .filter(Cotacao.id.in_(ids_cot_apagar))
                .delete(synchronize_session=False)
            )

        db.commit()
        return {
            "cotacoes_removidas": int(cotacoes_removidas),
            "analises_removidas": int(analises_removidas),
        }
    finally:
        if fechar_ao_final:
            db.close()
