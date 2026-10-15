"""Real Mongo schema tests, confined to the UUID database owned by the fixture."""

import copy
from datetime import UTC, timedelta

import pytest
from bson.int64 import Int64
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, WriteError

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.foundation_store import (
    MongoBagStore,
    MongoContentReader,
    MongoMetadataStore,
)
from data_sync_etl.adapters.mongo.schema import INDEXES, contracts, initialize, inventory
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import (
    ContentBagDocument,
    ResourceDescriptionDocument,
    UserProfileDocument,
)


@pytest.fixture
def mongo_db(mongo_test_database):
    client = MongoClient(
        mongo_test_database[0], serverSelectionTimeoutMS=3000, timeoutMS=10000, tz_aware=True
    )
    try:
        yield client[mongo_test_database[1]]
    finally:
        client.close()


def test_excel_content_limits_are_enforced_by_real_mongo(mongo_db):
    initialize(mongo_db, apply=True, dimension=2)
    docs = samples()
    for name, change in [('mongo_content_units', {'title': 'a' * 501}),
                         ('mongo_content_units', {'keywords': [str(i) for i in range(6)]}),
                         ('content_bags', {'title': 'a' * 501}),
                         ('content_bags', {'embedding_model': 'a' * 256})]:
        with pytest.raises(WriteError):
            mongo_db[name].insert_one({**docs[name], **change})
        assert mongo_db[name].count_documents({}) == 0


def samples():
    stamp = now()
    common = {"_id": "id-1", "created_at": stamp, "updated_at": stamp}
    return {
        "users": {**common, "bio": None, "subject_id": "subject-1"},
        "learning_resources": {
            **common,
            "description": "Mô tả",
            "authors": [{"name": "Nguyễn A"}],
            "tags": ["toán"],
        },
        "ocr_pages": {
            **common,
            "_id": "job-1:1",
            "job_id": "job-1",
            "resource_version_id": "v-1",
            "page_num": 1,
            "raw_text": "Tiếng Việt\n😀",
            "char_count": len("Tiếng Việt\n😀"),
            "source_checksum": "checksum",
            "projection_generation": 1,
        },
        "mongo_content_units": {
            **common,
            "resource_version_id": "v-1",
            "text": "Tiếng Việt",
            "markdown": "Tiếng Việt",
            "keywords": ["toán"],
            "page_from": 1,
            "page_to": 1,
            "is_current": True,
            "projection_generation": 1,
            "content_unit_id": "id-1",
        },
        # Fixture vector checks persistence only, not real semantic search.
        "content_bags": {
            **common,
            "resource_version_id": "v-1",
            "unit_type": "SECTION",
            "keywords": ["toán"],
            "embedding_text": "toán",
            "embedding": [0.2, 0.8],
            "embedding_model": "fixture-only@1",
            "is_current": True,
            "projection_generation": 1,
        },

    }


def test_installer_catalog_idempotent_read_only_check_and_preserve(mongo_db):
    assert not initialize(mongo_db, dimension=2)["ready"]
    assert mongo_db.list_collection_names() == []
    report = initialize(mongo_db, dimension=2, apply=True)
    assert report["ready"] and report["collections"] == report["validators"] == 5
    assert report["indexes"] == 6
    for name, data in samples().items():
        mongo_db[name].insert_one(data)
    assert initialize(mongo_db, dimension=2, apply=True)["ready"]
    validators = contracts(2)
    for name in validators:
        options = next(mongo_db.list_collections(filter={"name": name}))["options"]
        assert options["validator"] == validators[name]
        assert options["validationAction"] == "error"
        indexes = list(mongo_db[name].list_indexes())
        assert len(indexes) == 1 + len(INDEXES[name])
        assert mongo_db[name].count_documents({}) == 1


def test_inventory_five_collections_and_dimension_mismatch(mongo_db):
    initialize(mongo_db, dimension=384, apply=True)
    report = inventory(mongo_db)
    assert report['warnings'] == [] and report['extra_collections'] == []
    assert len(report['actual']) == 5
    assert initialize(mongo_db, dimension=384)['ready']
    mismatch = initialize(mongo_db, dimension=256)
    assert not mismatch['schema_ready']
    assert {item['path'] for item in mismatch['validator_mismatches']} == {
        'content_bags.embedding.minItems', 'content_bags.embedding.maxItems'}
    assert initialize(mongo_db, dimension=384, apply=True)['extra_collections'] == []
    with pytest.raises(DomainError, match='Incompatible Mongo validator'):
        initialize(mongo_db, dimension=256, apply=True)
    assert initialize(mongo_db, dimension=384)['ready']


@pytest.mark.parametrize(
    "collection,changes",
    [
        ("users", {"hashed_password": "DO-NOT-STORE"}),
        ("users", {"_id": "x" * 31}),
        ("users", {"bio": "x" * 2001}),
        ("users", {"subject_id": 1}),
        ("users", {"created_at": "not-date"}),
        ("learning_resources", {"authors": [{"name": " "}]}),
        ("learning_resources", {"authors": ["name"]}),
        ("learning_resources", {"tags": [1]}),
        ("learning_resources", {"tags": ["x", "x"]}),
        ("ocr_pages", {"page_num": 0}),
        ("ocr_pages", {"page_num": Int64(1)}),
        ("ocr_pages", {"char_count": 0}),
        ("ocr_pages", {"_id": "wrong-id"}),
        ("ocr_pages", {"source_checksum": ""}),
        ("mongo_content_units", {"page_from": 2, "page_to": 1}),
        ("mongo_content_units", {"content_unit_id": "wrong"}),
        ("mongo_content_units", {"markdown": None}),
        ("mongo_content_units", {"keywords": [{"word": "x"}]}),
        ("content_bags", {"embedding": [0.0, 0.0]}),
        ("content_bags", {"embedding": [float("nan"), 1.0]}),
        ("content_bags", {"embedding": [float("inf"), 1.0]}),
        ("content_bags", {"embedding": [1.0]}),
        ("content_bags", {"embedding_text": "wrong"}),
        ("content_bags", {"unit_type": "MP3_TEXT"}),
    ],
)
def test_real_negative_validators(mongo_db, collection, changes):
    initialize(mongo_db, dimension=2, apply=True)
    with pytest.raises(WriteError) as exc:
        mongo_db[collection].insert_one({**samples()[collection], **changes})
    assert exc.value.code == 121


@pytest.mark.parametrize("collection", list(samples()))
def test_real_required_date_field(mongo_db, collection):
    initialize(mongo_db, dimension=2, apply=True)
    data = samples()[collection]
    data.pop("updated_at")
    with pytest.raises(WriteError):
        mongo_db[collection].insert_one(data)


def test_adapters_crud_reads_keywords_and_bag_invalidation(mongo_db):
    initialize(mongo_db, dimension=2, apply=True)
    values = samples()
    meta = MongoMetadataStore(mongo_db)
    profile = UserProfileDocument.model_validate(values["users"])
    meta.upsert_user(profile)
    meta.upsert_user(profile.model_copy(update={"bio": "Updated", "created_at": now()}))
    fetched = meta.get_user("id-1")
    assert fetched.bio == "Updated" and fetched.created_at.tzinfo == UTC
    assert fetched.created_at.timestamp() == pytest.approx(
        profile.created_at.timestamp(), abs=0.001
    )
    meta.upsert_resource(ResourceDescriptionDocument.model_validate(values["learning_resources"]))
    assert meta.get_resource("id-1").authors[0].name == "Nguyễn A"
    assert meta.get_user("missing") is None
    store = MongoContentStore(mongo_db)
    reader = MongoContentReader(mongo_db)
    store.upsert_ocr_page(values["ocr_pages"])
    assert reader.ocr_pages(job_id="job-1", page_num=1)[0].raw_text == "Tiếng Việt\n😀"
    assert reader.ocr_pages(version_id="v-1", offset=1) == []
    store.upsert_content_unit(values["mongo_content_units"])
    observed = reader.get_unit("id-1")
    reader.update_leaf_keywords(observed, ["ĐẠI SỐ", "toán", " Toán "])
    assert reader.keyword_candidates(["Đại số"], version_id="v-1")[0].id == "id-1"
    assert reader.units("v-1", page_from=1, page_to=1)[0].keywords == ["toán", "đại số"]
    assert reader.units("v-1", page_from=2) == []
    bag_store = MongoBagStore(mongo_db, 2)
    data = {**values["content_bags"], "content_fingerprint": reader.content_fingerprint("v-1")}
    data.pop("is_current")  # The typed default must also be persisted by the Bag adapter.
    bag_store.upsert_bag(ContentBagDocument.model_validate(data))
    assert bag_store.get_bag("id-1").embedding_model == "fixture-only@1"
    assert len(bag_store.bag_candidates(["toán"], version_id="v-1")) == 1
    # Same-text replay preserves extracted leaf keywords atomically; created_at retained.
    store.upsert_content_unit(
        {**values["mongo_content_units"], "keywords": [], "created_at": now()}
    )
    assert reader.get_unit("id-1").keywords == ["toán", "đại số"]
    assert bag_store.get_bag("id-1") is None
    # Rebuild succeeds from the same stable snapshot.
    bag_store.upsert_bag(ContentBagDocument.model_validate(data))
    store.upsert_content_unit(
        {
            **values["mongo_content_units"],
            "text": "$changed\n",
            "markdown": "$changed\n",
            "keywords": [],
            "projection_generation": 2,
        }
    )
    assert reader.get_unit("id-1").text == "$changed\n"
    assert reader.get_unit("id-1").keywords == []
    assert not bag_store.bag_candidates(["toán"])
    with pytest.raises(DomainError, match="snapshot"):
        reader.update_leaf_keywords(observed, ["old"])
    with pytest.raises(DomainError):
        bag_store.upsert_bag(ContentBagDocument.model_validate(data))
    with pytest.raises(DomainError, match="ownership"):
        store.upsert_content_unit({**values["mongo_content_units"], "text": "stale"})
    unfenced = copy.deepcopy(values["mongo_content_units"])
    unfenced.pop("projection_generation")
    with pytest.raises(DomainError, match="ownership"):
        store.upsert_content_unit(unfenced)
    store.reconcile_content_units("v-1", [], generation=1)
    assert reader.get_unit("id-1").is_current
    store.reconcile_content_units("v-1", [], generation=2)
    assert not reader.get_unit("id-1").is_current
    assert reader.keyword_candidates(["toán"]) == []
    assert mongo_db.mongo_content_units.count_documents({}) == 1


def test_legacy_moderate_and_index_conflicts_do_not_delete(mongo_db):
    mongo_db.users.insert_one({"_id": "legacy"})
    report = initialize(mongo_db, dimension=2)
    assert report["legacy_invalid"]["users"] == 1
    with pytest.raises(DomainError, match="Legacy invalid"):
        initialize(mongo_db, dimension=2, apply=True)
    assert not mongo_db.users.options().get("validator")
    initialize(mongo_db, dimension=2, apply=True, validation_level="moderate")
    mongo_db.users.update_one({"_id": "legacy"}, {"$set": {"bio": "untouched legacy"}})
    assert mongo_db.users.count_documents({}) == 1
    with pytest.raises(WriteError):
        mongo_db.users.insert_one({"_id": "invalid-new"})
    with pytest.raises(DomainError, match="Incompatible Mongo validator"):
        initialize(mongo_db, dimension=3, apply=True, validation_level="moderate")
    assert mongo_db.users.count_documents({}) == 1


def test_duplicate_or_incompatible_indexes_block_before_ddl(mongo_db):
    mongo_db.ocr_pages.insert_many(
        [
            {"_id": "legacy1", "job_id": "j", "page_num": 1},
            {"_id": "legacy2", "job_id": "j", "page_num": 1},
        ]
    )
    with pytest.raises(DomainError, match="Legacy duplicate"):
        initialize(mongo_db, dimension=2, apply=True, validation_level="moderate")
    assert mongo_db.list_collection_names() == ["ocr_pages"]
    assert not mongo_db.ocr_pages.options().get("validator")


def test_incompatible_index_not_dropped(mongo_db):
    mongo_db.ocr_pages.create_index([("job_id", 1), ("page_num", 1)], name="ocr_job_page")
    before = mongo_db.ocr_pages.index_information()
    with pytest.raises(DomainError, match="Incompatible Mongo index"):
        initialize(mongo_db, dimension=2, apply=True)
    assert mongo_db.ocr_pages.index_information() == before
    assert mongo_db.list_collection_names() == ["ocr_pages"]


def test_unique_job_page_and_query_index(mongo_db):
    initialize(mongo_db, dimension=2, apply=True)
    mongo_db.ocr_pages.insert_one(samples()["ocr_pages"])
    # Bypass validation only in this isolated negative test: unique index is independent of ID validator.
    with pytest.raises(DuplicateKeyError):
        mongo_db.ocr_pages.insert_one(
            {**samples()["ocr_pages"], "_id": "different"}, bypass_document_validation=True
        )
    assert len(list(mongo_db.ocr_pages.find({"job_id": "job-1"}).hint("ocr_job_page"))) == 1


def test_empty_ocr_and_insert_timestamp_retained(mongo_db):
    initialize(mongo_db, dimension=2, apply=True)
    data = {**samples()["ocr_pages"], "raw_text": "", "char_count": 0}
    store = MongoContentStore(mongo_db)
    store.upsert_ocr_page(data)
    first = mongo_db.ocr_pages.find_one({"_id": "job-1:1"})
    later = data["created_at"] + timedelta(seconds=10)
    store.upsert_ocr_page({**data, "created_at": later, "updated_at": later})
    stored = mongo_db.ocr_pages.find_one({"_id": "job-1:1"})
    assert stored["created_at"] == first["created_at"]
    assert stored["updated_at"] > first["updated_at"]
    assert stored["raw_text"] == "" and stored["char_count"] == 0
    assert mongo_db.ocr_pages.count_documents({}) == 1


def test_legacy_auth_fields_not_returned(mongo_db):
    mongo_db.users.insert_one({**samples()["users"], "hashed_password": "PRIVATE"})
    doc = MongoMetadataStore(mongo_db).get_user("id-1").bson()
    assert "hashed_password" not in doc and "PRIVATE" not in repr(doc)
