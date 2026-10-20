"""
Gerenciamento de Banco de Dados de Produção (PostgreSQL).
Modelos: RegistroAnalise (Cache e Fluxo), Usuario (Contas/Argon2) e MonitoramentoUsuario (Watchlist Privada).
"""
import datetime
import logging
import os
from dotenv import load_dotenv
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, create_engine, text
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

PASTA_PROJETO = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PASTA_PROJETO, ".env"))

log = logging.getLogger("investidor_ia.banco")


def normalizar_url_banco(url: str) -> str:
    """
    Padroniza a URL do PostgreSQL (Neon, Render, Heroku etc.) para o driver psycopg 3,
    que é o driver declarado no requirements.txt.
    Aceita 'postgres://', 'postgresql://' e 'postgresql+psycopg://'.
    """
    url = (url or "").strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


URL_BANCO = normalizar_url_banco(os.getenv("DATABASE_URL"))

if not URL_BANCO.startswith("postgresql+psycopg://"):
    raise ValueError(
        "A DATABASE_URL do PostgreSQL não foi encontrada ou é inválida no .env. "
        "Use a connection string do Neon (começa com 'postgresql://')."
    )

engine = create_engine(URL_BANCO, pool_pre_ping=True, pool_size=5, max_overflow=10, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def agora_utc():
    """Data/hora UTC sem fuso para padronização global no banco."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class RegistroAnalise(Base):
    __tablename__ = "analises_ativos"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String(12), index=True, nullable=False)
    nome_empresa = Column(String(200))
    tipo_ativo = Column(String(50))
    preco_atual = Column(Float)
    variacao_dia_pct = Column(Float, default=0.0)
    volume_financeiro = Column(String(40))
    p_vp = Column(String(20))
    p_l = Column(String(20))
    dividend_yield = Column(String(20))
    dados_json = Column(Text, nullable=False)
    modelo_ia = Column(String(60))
    relatorio_ia = Column(Text, nullable=False)
    data_dados = Column(DateTime, default=agora_utc)
    data_criacao = Column(DateTime, default=agora_utc)

    __table_args__ = (Index("ix_ticker_data_dados", "ticker", "data_dados"),)


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


def obter_sessao_banco():
    """Gera uma sessão isolada com o banco para cada requisição da API."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sincronizar_sequencias_postgres():
    """
    Sincroniza o contador de IDs (SERIAL/SEQUENCE) do PostgreSQL com o maior ID existente
    nas tabelas, evitando o erro UniqueViolation (usuarios_pkey).
    """
    tabelas = ["usuarios", "analises_ativos", "monitoramento_usuarios"]
    for tabela in tabelas:
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{tabela}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {tabela}), 1), "
                        f"(SELECT COUNT(*) > 0 FROM {tabela}));"
                    )
                )
        except Exception as e:
            log.warning("Não foi possível sincronizar a sequência da tabela %s: %s", tabela, type(e).__name__)
