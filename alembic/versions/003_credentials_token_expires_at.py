"""Add missing token_expires_at to credentials

Revision ID: 003
Revises: 002
Create Date: 2026-07-08 13:00:00.000000

token_expires_at was added to the Credential model back in Fase 10 (OAuth2)
but the column was never added to already-existing Postgres databases created
before Alembic was introduced (only fresh SQLite databases via create_all
picked it up). Any OAuth2 callback (handle_callback in oauth_service.py) that
sets credential.token_expires_at fails at INSERT time without this column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('credentials') as batch_op:
        batch_op.add_column(sa.Column('token_expires_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('credentials') as batch_op:
        batch_op.drop_column('token_expires_at')
