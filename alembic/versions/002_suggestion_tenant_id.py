"""Add missing tenant_id to suggestions

Revision ID: 002
Revises: 001
Create Date: 2026-07-07 20:30:00.000000

The 001 migration introduced tenant_id across every tenant-owned table but
missed the "suggestions" table, which left Suggestion.tenant_id undefined
while other services (marketplace_publish_service, alert_service) already
queried it, and perform_product_audit() never had a column to populate.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('suggestions') as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_suggestions_tenant_id', ['tenant_id'], unique=False)
        batch_op.create_foreign_key('fk_suggestions_tenant_id', 'tenants', ['tenant_id'], ['id'])

    # Backfill via the parent product, which already carries the correct tenant_id
    # (more robust than a hardcoded default in case tenants ever diverge).
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE suggestions SET tenant_id = "
        "(SELECT products.tenant_id FROM products WHERE products.id = suggestions.product_id)"
    ))

    with op.batch_alter_table('suggestions') as batch_op:
        batch_op.alter_column('tenant_id', existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table('suggestions') as batch_op:
        batch_op.drop_constraint('fk_suggestions_tenant_id', type_='foreignkey')
        batch_op.drop_index('ix_suggestions_tenant_id')
        batch_op.drop_column('tenant_id')
