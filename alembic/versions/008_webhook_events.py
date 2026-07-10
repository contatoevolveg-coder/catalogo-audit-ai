"""Add webhook_events table

Revision ID: 008
Revises: 007
Create Date: 2026-07-10 12:00:00.000000

Suporte a webhooks do Bling ERP (estoque/produto atualizados em tempo real).
Garante idempotência (external_event_id único) e serve de log de auditoria.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '008'
down_revision: Union[str, None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'webhook_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('external_event_id', sa.String(), nullable=False, unique=True),
        sa.Column('event_type', sa.String(), nullable=True),
        sa.Column('company_id', sa.String(), nullable=True),
        sa.Column('raw_payload', sa.JSON(), nullable=True),
        sa.Column('matched_product_id', sa.Integer(), sa.ForeignKey('products.id', ondelete='SET NULL'), nullable=True),
        sa.Column('matched_tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='received'),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_webhook_events_provider', 'webhook_events', ['provider'])
    op.create_index('ix_webhook_events_external_event_id', 'webhook_events', ['external_event_id'], unique=True)
    op.create_index('ix_webhook_events_event_type', 'webhook_events', ['event_type'])
    op.create_index('ix_webhook_events_company_id', 'webhook_events', ['company_id'])


def downgrade() -> None:
    op.drop_index('ix_webhook_events_company_id', table_name='webhook_events')
    op.drop_index('ix_webhook_events_event_type', table_name='webhook_events')
    op.drop_index('ix_webhook_events_external_event_id', table_name='webhook_events')
    op.drop_index('ix_webhook_events_provider', table_name='webhook_events')
    op.drop_table('webhook_events')
