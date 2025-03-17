"""merge referral heads

Revision ID: dd1234567894
Revises: cc1234567893, ff2345678901, 01a2b3c4d5e6
Create Date: 2024-03-16 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'dd1234567894'
down_revision = None
branch_labels = None
depends_on = None

# Миграции, которые объединяются
depends_on = ('cc1234567893', 'ff2345678901', '01a2b3c4d5e6')


def upgrade():
    pass


def downgrade():
    pass 