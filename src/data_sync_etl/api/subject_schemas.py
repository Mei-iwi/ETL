from pydantic import BaseModel, ConfigDict, Field


class SubjectItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    slug: str
    grade_level_id: str | None


class SubjectPagination(BaseModel):
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)


class SubjectListResponse(BaseModel):
    items: list[SubjectItem]
    pagination: SubjectPagination
    message: str | None = None


class RecommendedSubjectItem(SubjectItem):
    score: int = Field(ge=0, le=100)
    reason: str


class RecommendedSubjectListResponse(BaseModel):
    items: list[RecommendedSubjectItem]
