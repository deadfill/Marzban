"""Add referral system

Revision ID: cc1234567893
Revises: bb1234567892
Create Date: 2024-03-16 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = 'cc1234567893'
down_revision = 'bb1234567892'
branch_labels = None
depends_on = None


def upgrade():
    # Используем batch_alter_table для SQLite
    with op.batch_alter_table('telegram_users') as batch_op:
        batch_op.add_column(sa.Column('referral_code', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('referrer_id', sa.Integer, nullable=True))
        # Добавляем индекс и внешний ключ отдельно
        batch_op.create_index('ix_telegram_users_referral_code', ['referral_code'], unique=True)
        batch_op.create_foreign_key('fk_referrer_id', 'telegram_users', ['referrer_id'], ['id'])
    
    # Создание таблицы referral_bonuses для хранения бонусов
    op.create_table(
        'referral_bonuses',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('telegram_user_id', sa.Integer, nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('bonus_type', sa.String(50), nullable=False, server_default='days'),
        sa.Column('is_applied', sa.Boolean, nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('applied_at', sa.DateTime, nullable=True),
        sa.Column('expires_at', sa.DateTime, nullable=True),
        sa.ForeignKeyConstraint(['telegram_user_id'], ['telegram_users.id'], name='fk_telegram_user_bonus')
    )


def downgrade():
    # Удаление таблицы referral_bonuses
    op.drop_table('referral_bonuses')
    
    # Используем batch_alter_table для SQLite
    with op.batch_alter_table('telegram_users') as batch_op:
        batch_op.drop_constraint('fk_referrer_id', type_='foreignkey')
        batch_op.drop_index('ix_telegram_users_referral_code')
        batch_op.drop_column('referrer_id')
        batch_op.drop_column('referral_code') 