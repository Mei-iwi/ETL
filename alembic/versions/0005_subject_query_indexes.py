from alembic import op

revision="0005"
down_revision="0004"
branch_labels=None
depends_on=None

def upgrade():
    op.create_index(
        "ix_subject_grade_active_deleted",
        "subjects",
        ["grade_id", "is_active", "deleted_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_subjects_name",
        "subjects",
        ["subject_name"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_subjects_slug",
        "subjects",
        ["subject_slug"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_resource_subjects_subject",
        "resource_subjects",
        ["subject_id"],
        if_not_exists=True
    )
    
def downgrade():
    op.drop_index("ix_resource_subjects_subject", table_name="resource_subjects", if_exists=True)
    op.drop_index("ix_subjects_slug", table_name="subjects", if_exists=True)
    op.drop_index("ix_subjects_name", table_name="subjects", if_exists=True)
    op.drop_index("ix_subjects_grade_active_deleted", table_name="subjects", if_exists=True)
