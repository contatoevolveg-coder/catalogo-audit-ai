"""Add marketplace_status to products

Revision ID: 006
Revises: 005
Create Date: 2026-07-08 20:00:00.000000

Guarda o status real do anúncio no marketplace (active | paused | closed |
under_review), permitindo dividir o catálogo entre anúncios ATIVOS na
plataforma e os que não estão ativos (pausados/encerrados/não publicados).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('products') as batch_op:
        batch_op.add_column(sa.Column('marketplace_status', sa.String(), nullable=True))
        batch_op.create_index('ix_products_marketplace_status', ['marketplace_status'], unique=False)

    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE products SET marketplace_status = 'active' "
        "WHERE status = 'published' AND external_listing_id IS NOT NULL"
    ))


def downgrade() -> None:
    with op.batch_alter_table('products') as batch_op:
        batch_op.drop_index('ix_products_marketplace_status')
        batch_op.drop_column('marketplace_status')
