"""
${message}

Create Date: ${create_date}

"""

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}

from alembic import op
% if upgrades:
import sqlalchemy as sa
% endif
${imports if imports else ""}

def upgrade():
    % if upgrades:
    ${upgrades}
    % else:
    op.execute("""
        
    """)
    % endif


def downgrade():
    % if downgrades:
    ${downgrades}
    % else:
    op.execute("""
        
    """)
    % endif
