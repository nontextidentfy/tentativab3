"""Fase 2 contas e autenticacao

Revision ID: c0d49a3075c6
Revises: 6c6b96237d69
Create Date: 2026-10-17 21:23:53.417644

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0d49a3075c6'
down_revision: Union[str, Sequence[str], None] = '6c6b96237d69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_MONITORAMENTO_USUARIO = 'monitoramento_usuarios_usuario_id_fkey'


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('monitoramento_usuarios', schema=None) as batch_op:
        batch_op.drop_constraint(FK_MONITORAMENTO_USUARIO, type_='foreignkey')
        batch_op.create_foreign_key(
            FK_MONITORAMENTO_USUARIO,
            'usuarios',
            ['usuario_id'],
            ['id'],
            ondelete='CASCADE',
        )

    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.add_column(sa.Column('senha_hash', sa.String(length=255), server_default='', nullable=False))
        batch_op.add_column(sa.Column('email_confirmado', sa.Boolean(), server_default='false', nullable=False))
        batch_op.add_column(sa.Column('aceitou_termos_em', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.drop_column('aceitou_termos_em')
        batch_op.drop_column('email_confirmado')
        batch_op.drop_column('senha_hash')

    with op.batch_alter_table('monitoramento_usuarios', schema=None) as batch_op:
        batch_op.drop_constraint(FK_MONITORAMENTO_USUARIO, type_='foreignkey')
        batch_op.create_foreign_key(
            FK_MONITORAMENTO_USUARIO,
            'usuarios',
            ['usuario_id'],
            ['id'],
        )
