"""Multi-tenant

Revision ID: 001
Revises: 
Create Date: 2026-07-07 19:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create Tenant table
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('plan', sa.String(), nullable=True, default='free'),
        sa.Column('created_at', sa.DateTime(), nullable=True)
    )

    # 2. Add tenant_id column as nullable=True to all tables
    tables = [
        'users', 'products', 'audit_logs', 'import_batches', 'credentials',
        'marketplace_publications', 'erp_sync_logs', 'customer_questions', 'orders', 'alerts'
    ]

    for table in tables:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
            batch_op.create_index(f'ix_{table}_tenant_id', ['tenant_id'], unique=False)
            batch_op.create_foreign_key(f'fk_{table}_tenant_id', 'tenants', ['tenant_id'], ['id'])

    # 3. Create a default tenant
    # Bind to current connection to insert
    connection = op.get_bind()
    connection.execute(sa.text("INSERT INTO tenants (name, plan) VALUES ('Default Tenant', 'free')"))
    
    # 4. Update all rows in all tables to use the default tenant_id
    for table in tables:
        connection.execute(sa.text(f"UPDATE {table} SET tenant_id = 1"))

    # 5. Alter columns to nullable=False using batch_alter_table (SQLite support)
    for table in tables:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column('tenant_id', existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    tables = [
        'alerts', 'orders', 'customer_questions', 'erp_sync_logs', 'marketplace_publications',
        'credentials', 'import_batches', 'audit_logs', 'products', 'users'
    ]
    for table in tables:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f'fk_{table}_tenant_id', type_='foreignkey')
            batch_op.drop_index(f'ix_{table}_tenant_id')
            batch_op.drop_column('tenant_id')
            
    op.drop_table('tenants')
