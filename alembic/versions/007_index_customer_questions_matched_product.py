"""Add index on customer_questions.matched_product_id

Revision ID: 007
Revises: 006
Create Date: 2026-07-10 11:30:00.000000

Foreign key sem índice de cobertura, sinalizado pelo advisor de performance
do Supabase (unindexed_foreign_keys).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_customer_questions_matched_product_id',
        'customer_questions',
        ['matched_product_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_customer_questions_matched_product_id', table_name='customer_questions')
