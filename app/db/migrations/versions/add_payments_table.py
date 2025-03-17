"""add_payments_table

Revision ID: bb1234567892
Revises: aa1234567891
Create Date: 2024-03-15 08:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'bb1234567892'
down_revision = 'aa1234567891'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Создаем таблицу payments
    op.create_table(
        'payments',
        sa.Column('payment_id', sa.String(255), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('income_amount', sa.Numeric(10, 2), nullable=True),
        sa.Column('status', sa.String(50), nullable=False),
        sa.Column('description', sa.String(1000), nullable=True),
        sa.Column('payment_method', sa.String(50), nullable=True),
        sa.Column('payment_method_details', sa.String(1000), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.Column('captured_at', sa.DateTime(), nullable=True),
        sa.Column('payment_metadata', sa.String(1000), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['telegram_users.user_id']),
        sa.PrimaryKeyConstraint('payment_id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci'
    )
    op.create_index(op.f('ix_payments_user_id'), 'payments', ['user_id'], unique=False)


def downgrade() -> None:
    # Удаляем таблицу payments
    op.drop_index(op.f('ix_payments_user_id'), table_name='payments')
    op.drop_table('payments') 