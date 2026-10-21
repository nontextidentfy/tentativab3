"""adicionar coluna plano em usuarios

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-20 20:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Adiciona coluna plano na tabela usuarios com default 'free' se ainda nao existir."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("usuarios")}
    if "plano" not in cols:
        with op.batch_alter_table("usuarios", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "plano",
                    sa.String(length=20),
                    server_default="free",
                    nullable=False,
                )
            )


def downgrade() -> None:
    """Remove coluna plano da tabela usuarios se existir."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("usuarios")}
    if "plano" in cols:
        with op.batch_alter_table("usuarios", schema=None) as batch_op:
            batch_op.drop_column("plano")
