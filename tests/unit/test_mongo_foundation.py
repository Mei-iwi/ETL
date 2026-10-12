from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from data_sync_etl.adapters.mongo.content_store import MongoContentStore, generation_filter
from data_sync_etl.adapters.mongo.foundation_store import (
    MongoContentReader,
    MongoMetadataStore,
    checked,
)
from data_sync_etl.adapters.mongo.schema import INDEXES, Index, compatible_index, contracts
from data_sync_etl.application.content_bags import ContentBagBuilder, StructuredUnit
from data_sync_etl.application.leaf_keywords import SaveLeafKeywords
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import (
    ContentBagDocument,
    ContentUnitDocument,
    OCRPageDocument,
    ResourceDescriptionDocument,
    UserProfileDocument,
    normalize_keywords,
)


def base(**kwargs):
    return {"_id": "id-1", "created_at": now(), "updated_at": now(), **kwargs}


def leaf(**kwargs):
    return ContentUnitDocument(
        **base(
            _id="leaf-1",
            resource_version_id="v-1",
            text="Tiếng Việt\n",
            markdown="Tiếng Việt\n",
            keywords=[" TOÁN ", "toán", "Đại số"],
            projection_generation=1,
            is_current=True,
            **kwargs,
        )
    )


def tree(root_type="CHAPTER"):
    return [
        StructuredUnit(
            id="root-1",
            resource_version_id="v-1",
            parent_unit_id=None,
            unit_type=root_type,
            sequence_no=1,
        ),
        StructuredUnit(
            id="leaf-1",
            resource_version_id="v-1",
            parent_unit_id="root-1",
            unit_type="PAGE_TEXT",
            sequence_no=2,
        ),
    ]


class StubEmbedding:
    """Only test flow; never registered in production composition."""

    model_id = "test-stub"
    version = "1"
    dimension = 2

    def embed(self, text):
        return [0.25, 0.75]


def test_metadata_contract_and_no_auth_fields():
    profile = UserProfileDocument(**base(bio=None, subject_id="subject-1"))
    assert profile.bson()["_id"] == "id-1"
    for key in ("password", "hashed_password", "token", "session"):
        with pytest.raises(ValidationError) as error:
            UserProfileDocument(**base(**{key: "SECRET"}))
        assert "SECRET" not in str(error.value)
    for field, length in (
        ("organization_name", 255),
        ("address", 1000),
        ("bio", 2000),
        ("subject_id", 30),
        ("_id", 30),
    ):
        UserProfileDocument(**base(**{field: "a" * length}))
        with pytest.raises(ValidationError):
            UserProfileDocument(**base(**{field: "a" * (length + 1)}))
    with pytest.raises(ValidationError):
        UserProfileDocument(_id="id")


def test_dates_and_embedded_authors():
    stamp = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=7)))
    doc = ResourceDescriptionDocument(
        **base(
            created_at=stamp,
            description=None,
            authors=[{"name": "Nguyễn Văn A", "role": None}],
            tags=[" Toán ", "toán", "", "TOA\u0301N"],
        )
    )
    assert doc.tags == ["toán"]
    assert doc.created_at.tzinfo == UTC
    assert doc.created_at.hour == 17
    for changes in (
        {"created_at": datetime(2026, 1, 1)},
        {"authors": [{"name": " "}]},
        {"authors": ["Author"]},
        {"tags": [1]},
        {"description": 12},
    ):
        with pytest.raises(ValidationError):
            ResourceDescriptionDocument(**base(**changes))


def test_ocr_raw_text_empty_codepoints_and_identity():
    for text in ("", "Tiếng Việt\n😀"):
        values = base(
            _id="job-1:1",
            job_id="job-1",
            resource_version_id="v-1",
            page_num=1,
            raw_text=text,
            char_count=len(text),
            source_checksum="checksum",
        )
        assert OCRPageDocument(**values).raw_text == text
        for changes in (
            {"char_count": 99},
            {"_id": "wrong"},
            {"page_num": True},
            {"page_num": 0},
            {"page_num": 2**31},
            {"source_checksum": ""},
        ):
            with pytest.raises(ValidationError):
                OCRPageDocument(**{**values, **changes})


def test_content_range_and_keyword_arrays():
    assert leaf().keywords == ["toán", "đại số"]
    for changes in (
        {"page_from": 3, "page_to": 2},
        {"page_from": 0},
        {"content_unit_id": "other"},
        {"keywords": [{"word": "toán"}]},
        {"markdown": None},
    ):
        with pytest.raises(ValidationError):
            ContentUnitDocument.model_validate({**leaf().bson(), **changes})
    assert normalize_keywords(["ĐẠI SỐ", " đại số ", "", "Vie\u0323\u0302t"]) == ["việt", "đại số"]
    unfenced = leaf().bson()
    unfenced.pop("projection_generation")
    assert (
        ContentUnitDocument.model_validate(
            ContentUnitDocument.model_validate(unfenced)
        ).projection_generation
        is None
    )
    assert (
        checked(
            ContentUnitDocument, ContentUnitDocument.model_validate(unfenced)
        ).projection_generation
        is None
    )
    with pytest.raises(ValidationError):
        ContentUnitDocument.model_validate({**unfenced, "projection_generation": None})


@pytest.mark.parametrize("root_type", ["CHAPTER", "LESSON", "SECTION"])
def test_bag_fallback_and_fixture_embedding(root_type):
    result = ContentBagBuilder(StubEmbedding()).build(
        "v-1", tree(root_type), [leaf()], generation=1, content_fingerprint="a" * 64
    )
    assert result.status == "BUILT"
    bag = result.bags[0]
    assert bag.id == "root-1" and bag.unit_type == root_type
    assert bag.embedding_text == "toán | đại số"
    assert bag.embedding_model == "test-stub@1"
    for changes in (
        {"embedding": [0.0, 0.0]},
        {"embedding": [float("nan"), 1.0]},
        {"embedding": [float("inf"), 1.0]},
        {"embedding_text": "invented"},
        {"unit_type": "PAGE_TEXT"},
        {"keywords": None},
        {"embedding_model": " "},
    ):
        with pytest.raises(ValidationError):
            ContentBagDocument.model_validate({**bag.bson(), **changes})


def test_no_hierarchy_or_model_no_bags():
    builder = ContentBagBuilder()
    assert (
        builder.build("v-1", tree(), [leaf()], generation=1, content_fingerprint="a" * 64).status
        == "BLOCKED"
    )
    units = [tree()[1].model_copy(update={"parent_unit_id": None})]
    assert (
        builder.build("v-1", units, [leaf()], generation=1, content_fingerprint="a" * 64).status
        == "DEFERRED"
    )


def test_chapter_priority_and_nested_same_type_root_not_duplicated():
    nodes = tree("LESSON")
    nodes[0] = nodes[0].model_copy(update={"parent_unit_id": "chapter"})
    nodes.append(
        StructuredUnit(
            id="chapter",
            resource_version_id="v-1",
            parent_unit_id="outer",
            unit_type="CHAPTER",
            sequence_no=1,
        )
    )
    nodes.append(
        StructuredUnit(
            id="outer",
            resource_version_id="v-1",
            parent_unit_id=None,
            unit_type="CHAPTER",
            sequence_no=1,
        )
    )
    result = ContentBagBuilder(StubEmbedding()).build(
        "v-1", nodes, [leaf()], generation=1, content_fingerprint="a" * 64
    )
    assert result.status == "BUILT" and [bag.id for bag in result.bags] == ["outer"]


@pytest.mark.parametrize(
    "change", ["cycle", "cross-version", "missing-parent", "duplicate", "stale-leaf"]
)
def test_reject_invalid_tree(change):
    nodes, leaves = tree(), [leaf()]
    if change == "cycle":
        nodes[0] = nodes[0].model_copy(update={"parent_unit_id": "leaf-1"})
    elif change == "cross-version":
        nodes[1] = nodes[1].model_copy(update={"resource_version_id": "other"})
    elif change == "missing-parent":
        nodes[1] = nodes[1].model_copy(update={"parent_unit_id": "other"})
    elif change == "duplicate":
        nodes.append(nodes[1])
    else:
        leaves[0] = leaves[0].model_copy(update={"projection_generation": 2})
    with pytest.raises(DomainError):
        ContentBagBuilder(StubEmbedding()).build(
            "v-1", nodes, leaves, generation=1, content_fingerprint="a" * 64
        )


def test_contract_indexes_and_fencing_filter():
    assert len(contracts(2)) == 5
    assert sum(len(indexes) for indexes in INDEXES.values()) == 6
    expected = Index("test", (("job_id", 1), ("page_num", 1)), True)
    assert compatible_index({"key": dict(expected.keys), "unique": True}, expected)
    for options in (
        {"unique": False},
        {"unique": True, "sparse": True},
        {"unique": True, "expireAfterSeconds": 0},
    ):
        assert not compatible_index({"key": dict(expected.keys), **options}, expected)
    assert generation_filter(None) == {"projection_generation": {"$exists": False}}


def test_adapters_validate_before_writing_and_bound_reads():
    db = MagicMock()
    db.with_options.return_value = db
    metadata = MongoMetadataStore(db)
    with pytest.raises(DomainError):
        metadata.upsert_user(base(hashed_password="secret"))
    db.__getitem__.assert_not_called()
    reader = MongoContentReader(db)
    for call in (
        lambda: reader.ocr_pages(),
        lambda: reader.units("v", limit=0),
        lambda: reader.units("v", offset=10001),
        lambda: reader.get_unit({"$ne": ""}),
        lambda: reader.keyword_candidates([]),
        lambda: reader.ocr_pages(job_id="job", page_num=True),
    ):
        with pytest.raises(DomainError):
            call()
    store = MongoContentStore(db)
    with pytest.raises(DomainError):
        store.upsert_content_unit({"_id": "x", "text": "bad"})


def test_bad_embedding_dimension_and_no_keywords():
    embedding = StubEmbedding()
    embedding.dimension = 3
    with pytest.raises(DomainError, match="dimension"):
        ContentBagBuilder(embedding).build(
            "v-1", tree(), [leaf()], generation=1, content_fingerprint="a" * 64
        )
    result = ContentBagBuilder(StubEmbedding()).build(
        "v-1",
        tree(),
        [leaf().model_copy(update={"keywords": []})],
        generation=1,
        content_fingerprint="a" * 64,
    )
    assert result.status == "DEFERRED" and not result.bags


def test_save_keywords_requires_pg_leaf_and_matching_version():
    uow, documents = MagicMock(), MagicMock()
    repo = uow.return_value.__enter__.return_value
    repo.get.return_value = {"resource_version_id": "v-1"}
    repo.find.return_value = [{"id": "child"}]
    usecase = SaveLeafKeywords(uow, documents)
    with pytest.raises(DomainError, match="leaf"):
        usecase.execute("root-1", ["toán"])
    documents.update_leaf_keywords.assert_not_called()
    repo.find.return_value = []
    documents.get_unit.return_value = leaf().model_copy(update={"resource_version_id": "other"})
    with pytest.raises(DomainError, match="version"):
        usecase.execute("leaf-1", ["toán"])
    documents.get_unit.return_value = leaf()
    usecase.execute("leaf-1", ["toán"])
    documents.update_leaf_keywords.assert_called_once()


def test_api_can_start_with_mongo_unavailable(container):
    from fastapi.testclient import TestClient

    from data_sync_etl.composition import Container
    from data_sync_etl.main import create_app

    settings = container.settings.model_copy(update={"mongo_uri": "mongodb://127.0.0.1:1"})
    runtime = Container(settings, container.engine)
    try:
        with TestClient(create_app(runtime)) as api:
            assert api.get("/health").status_code == 200
    finally:
        runtime.close()
