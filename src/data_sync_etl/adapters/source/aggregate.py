"""Shared source-to-Master mapping for JSON fixtures and PostgreSQL snapshots."""

from data_sync_etl.ports.source import Cursor, Provider, SourceAggregate


def build_aggregate(reader, stream, row):
    values = row.model_dump()
    # Missing fixture information is not an instruction to clear a real mapping.
    if stream in ('users', 'resources', 'grades') and 'mongo_id' not in row.model_fields_set:
        values.pop('mongo_id', None)
    timestamps = [row.updated_at]
    subjects, grades = [], []
    if stream == "resources":
        if row.provider_id is None:
            values.update({"provider_" + key: None for key in Provider.model_fields if key != "id"})
            # The DTO's provider field is called type, matching the existing fixture contract.
        else:
            provider = reader.get_provider(row.provider_id)
            values.update(
                {"provider_" + k: v for k, v in provider.model_dump().items() if k != "id"}
            )
            timestamps.append(provider.updated_at)
        kind = reader.get_resource_type(row.resource_type_id)
        values.update({"resource_type_" + k: v for k, v in kind.model_dump().items() if k != "id"})
        timestamps.append(kind.updated_at)
        subjects = reader.get_resource_subjects(row.id)
        grades = reader.get_resource_grade_levels(row.id)
        timestamps += [r.updated_at for r in subjects + grades]
    elif stream == "users":
        role = reader.get_role(row.role_id)
        values.update({"role_" + k: v for k, v in role.model_dump().items() if k != "id"})
        values["hashed_password"] = row.hashed_password.get_secret_value()
        timestamps.append(role.updated_at)
    elif stream == "grades":
        education = reader.get_education_level(row.education_level_id)
        grade_values = values
        values = {"id": row.id, "education_level_id": row.education_level_id}
        values.update(
            {
                "grade_" + k: v
                for k, v in grade_values.items()
                if k not in ("id", "education_level_id")
            }
        )
        values.update({"education_" + k: v for k, v in education.model_dump().items() if k != "id"})
        timestamps.append(education.updated_at)
    return SourceAggregate(
        cursor=Cursor(updated_at=max(timestamps), id=row.id),
        values=values,
        subjects=subjects,
        grades=grades,
    )
