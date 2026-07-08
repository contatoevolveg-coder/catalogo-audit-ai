"""Add tenant_id to oauth_states

Revision ID: 004
Revises: 003
Create Date: 2026-07-08 15:00:00.000000

oauth_states never carried a tenant_id, so the OAuth callback had no way to
attribute an incoming authorization (a plain browser redirect, with no
Bearer token) to the tenant that started the flow. Now start_authorization
stamps the initiating tenant on the state row, and handle_callback reads it
back instead of trusting a bearer token that a third-party redirect can't
carry.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('oauth_states') as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_oauth_states_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_foreign_key('fk_oauth_states_tenant_id', 'tenants', ['tenant_id'], ['id'])

    # Não há dados a preservar (states são efêmeros, expiram em 10 minutos);
    # limpamos qualquer linha órfã e só então tornamos a coluna obrigatória.
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM oauth_states"))

    with op.batch_alter_table('oauth_states') as batch_op:
        batch_op.alter_column('tenant_id', existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('oauth_states') as batch_op:
        batch_op.drop_constraint('fk_oauth_states_tenant_id', type_='foreignkey')
        batch_op.drop_index('ix_oauth_states_tenant_id')
        batch_op.drop_column('tenant_id')
