"""add_telegram_users_table

Revision ID: ff1234567890
Revises: e56f1c781e46
Create Date: 2024-03-14 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime, UTC


# revision identifiers, used by Alembic.
revision = 'ff1234567890'
down_revision = 'e56f1c781e46'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Создаем таблицу telegram_users
    op.create_table('telegram_users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=100), nullable=True),
        sa.Column('first_name', sa.String(length=100), nullable=True),
        sa.Column('last_name', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci'
    )
    op.create_index(op.f('ix_telegram_users_user_id'), 'telegram_users', ['user_id'], unique=True)


def downgrade() -> None:
    # Удаляем таблицу telegram_users
    op.drop_index(op.f('ix_telegram_users_user_id'), table_name='telegram_users')
    op.drop_table('telegram_users') 