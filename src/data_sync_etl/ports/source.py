from collections.abc import Iterator
from typing import Annotated, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

Id = Annotated[str, Field(min_length=1, max_length=30)]


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: Id
    created_at: AwareDatetime
    updated_at: AwareDatetime
    is_active: bool = True
    deleted_at: AwareDatetime | None = None
    deleted_by: Id | None = None


class Provider(SourceRecord):
    name: str = Field(max_length=255)
    type: str = Field(max_length=30)


class ResourceType(SourceRecord):
    code: str = Field(max_length=50)
    name_vi: str = Field(max_length=150)


class LearningResource(SourceRecord):
    provider_id: Id
    resource_type_id: Id
    title: str = Field(max_length=500)
    publication_status: str = Field(max_length=30)
    created_by: Id | None = None


class Role(SourceRecord):
    code: str = Field(max_length=50)
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=255)


class User(SourceRecord):
    email: str = Field(max_length=255)
    hashed_password: SecretStr = Field(max_length=255)
    full_name: str = Field(max_length=255)
    role_id: Id
    email_verified_at: AwareDatetime | None = None


class EducationLevel(SourceRecord):
    code: str = Field(max_length=30)
    name_vi: str = Field(max_length=100)
    ordinal: int | None = None


class GradeLevel(SourceRecord):
    education_level_id: Id
    code: str = Field(max_length=50)
    name_vi: str = Field(max_length=100)
    ordinal: int | None = None
    created_by: Id | None = None
    updated_by: Id | None = None


class Subject(SourceRecord):
    grade_id: Id | None = None
    subject_name: str = Field(max_length=255)
    subject_slug: str = Field(max_length=255)


class ResourceSubjectRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    resource_id: Id
    subject_id: Id
    is_primary: bool = False
    updated_at: AwareDatetime


class ResourceGradeLevelRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    resource_id: Id
    grade_level_id: Id
    updated_at: AwareDatetime


class Cursor(BaseModel):
    updated_at: AwareDatetime
    id: Id

    def key(self):
        return self.updated_at, self.id


class SourceAggregate(BaseModel):
    cursor: Cursor
    values: dict = Field(repr=False)
    subjects: list[ResourceSubjectRelation] = Field(default_factory=list)
    grades: list[ResourceGradeLevelRelation] = Field(default_factory=list)


class SourceDataReader(Protocol):
    def batches(
        self, stream: str, cursor: Cursor | None, batch_size: int
    ) -> Iterator[list[SourceAggregate]]: ...
    def iter_learning_resources(self, cursor=None, batch_size=100): ...
    def iter_users(self, cursor=None, batch_size=100): ...
    def iter_grade_levels(self, cursor=None, batch_size=100): ...
    def iter_subjects(self, cursor=None, batch_size=100): ...
    def get_provider(self, provider_id: str) -> Provider: ...
    def get_resource_type(self, resource_type_id: str) -> ResourceType: ...
    def get_role(self, role_id: str) -> Role: ...
    def get_education_level(self, education_level_id: str) -> EducationLevel: ...
    def get_resource_subjects(self, resource_id: str) -> list[ResourceSubjectRelation]: ...
    def get_resource_grade_levels(self, resource_id: str) -> list[ResourceGradeLevelRelation]: ...
