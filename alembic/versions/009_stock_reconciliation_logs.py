"""Add stock_reconciliation_logs table

Revision ID: 009
Revises: 008
Create Date: 2026-07-10 15:00:00.000000

Suporte à auditoria de reconciliação entre o saldo real do Bling ERP e a
quantidade anunciada em cada marketplace (detecção de "vendendo fantasma",
estoque preso e divergências de quantidade).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'stock_reconciliation_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id', ondelete='CASCADE'), nullable=False),
        sa.Column('bling_quantity', sa.Integer(), nullable=False),
        sa.Column('marketplace_quantity', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('checked_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_stock_reconciliation_logs_tenant_id', 'stock_reconciliation_logs', ['tenant_id'])
    op.create_index('ix_stock_reconciliation_logs_product_id', 'stock_reconciliation_logs', ['product_id'])
    op.create_index('ix_stock_reconciliation_logs_category', 'stock_reconciliation_logs', ['category'])


def downgrade() -> None:
    op.drop_index('ix_stock_reconciliation_logs_category', table_name='stock_reconciliation_logs')
    op.drop_index('ix_stock_reconciliation_logs_product_id', table_name='stock_reconciliation_logs')
    op.drop_index('ix_stock_reconciliation_logs_tenant_id', table_name='stock_reconciliation_logs')
    op.drop_table('stock_reconciliation_logs')
