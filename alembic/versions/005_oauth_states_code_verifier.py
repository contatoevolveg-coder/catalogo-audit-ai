"""Add code_verifier (PKCE) to oauth_states

Revision ID: 005
Revises: 004
Create Date: 2026-07-08 19:30:00.000000

Apps do Mercado Livre com "PKCE obrigatório" exigem code_challenge na
autorização e code_verifier na troca de token. O verifier precisa ser guardado
entre as duas etapas (authorize -> callback), então adicionamos a coluna ao
registro de state efêmero.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('oauth_states') as batch_op:
        batch_op.add_column(sa.Column('code_verifier', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('oauth_states') as batch_op:
        batch_op.drop_column('code_verifier')
