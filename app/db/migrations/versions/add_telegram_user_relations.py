"""add_telegram_user_relations

Revision ID: ff2345678901
Revises: ff1234567890
Create Date: 2024-03-14 10:01:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ff2345678901'
down_revision = 'ff1234567890'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем только колонку telegram_user_id в таблицу users
    # Без внешнего ключа и индекса
    op.add_column('users', sa.Column('telegram_user_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    # Удаляем столбец telegram_user_id из таблицы users
    op.drop_column('users', 'telegram_user_id') 