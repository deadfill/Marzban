"""add_test_period_to_telegram_users

Revision ID: aa1234567891
Revises: ff1234567890
Create Date: 2024-03-15 07:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'aa1234567891'
down_revision = 'ff1234567890'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем колонку test_period в таблицу telegram_users
    # Boolean поле, которое будет хранить информацию о том, был ли использован тестовый период
    op.add_column('telegram_users', sa.Column('test_period', sa.Boolean(), 
                                             nullable=False, 
                                             server_default=sa.text('0'),
                                             comment='Флаг, указывающий, был ли использован тестовый период'))


def downgrade() -> None:
    # Удаляем колонку test_period из таблицы telegram_users
    op.drop_column('telegram_users', 'test_period') 