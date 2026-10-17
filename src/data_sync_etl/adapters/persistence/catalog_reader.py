"""Paged read model over existing denormalized Master data, no catalog tables."""

from math import ceil

from sqlalchemy import String, func, literal, or_, select, true
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

from data_sync_etl.db.models import MasterGradeLevel, MasterLearningResource

WHITESPACE = "".join(chr(i) for i in range(0x3001) if chr(i).isspace())
# Match the previous Python casefold contract, not database-locale lower().
# Translation handles 1:1 mappings, replacement the full-fold expansions (ß, İ…).
_FOLD_PAIRS = [(chr(i), chr(i).casefold()) for i in range(0x110000) if chr(i).casefold() != chr(i)]
_FOLD_FROM = "".join(a for a, b in _FOLD_PAIRS if len(b) == 1)
_FOLD_TO = "".join(b for _, b in _FOLD_PAIRS if len(b) == 1)
_FOLD_MULTI = [(a, b) for a, b in _FOLD_PAIRS if len(b) != 1]


class _CaseFold(FunctionElement):
    """Compile a flat expression node, avoiding recursive SQLAlchemy traversal."""

    type = String()
    inherit_cache = True


@compiles(_CaseFold, "postgresql")
def _compile_casefold(element, compiler, **kwargs):
    column = compiler.process(next(iter(element.clauses)), **kwargs)
    # Only immutable Unicode mappings are literal SQL; user input stays bound.
    def constant(value):
        return compiler.process(literal(value), literal_binds=True)

    value = f"translate({column}, {constant(_FOLD_FROM)}, {constant(_FOLD_TO)})"
    for original, folded in _FOLD_MULTI:
        value = f"replace({value}, {constant(original)}, {constant(folded)})"
    return value


class CatalogQueryMixin:
    def catalog_page(self, kind, *, page, size, query=None):
        dialect = self.session.bind.dialect.name
        if dialect == "sqlite":
            self.session.connection().connection.driver_connection.create_function(
                "etl_casefold", 1, lambda value: (value or "").casefold(), deterministic=True
            )

        def fold(column):
            if dialect != "postgresql":
                return func.etl_casefold(column)
            return _CaseFold(column).collate("C")

        def trim(column):
            return (
                func.btrim(column, WHITESPACE)
                if dialect == "postgresql"
                else func.trim(column, WHITESPACE)
            )

        if kind == "grades":
            model = MasterGradeLevel
            base = (
                select(
                    model.id.label("id"),
                    model.grade_code.label("grade_code"),
                    model.grade_name_vi.label("grade_name_vi"),
                )
                .where(model.grade_is_active.is_(True), model.grade_deleted_at.is_(None))
                .cte("catalog")
            )
            fields, names, sort_field = (
                ["id", "grade_code", "grade_name_vi"],
                ["grade_code", "grade_name_vi"],
                "grade_name_vi",
            )
        else:
            model = MasterLearningResource
            if kind == "providers":
                key, identity = model.provider_id, (model.provider_name, model.provider_type)
                active, deleted, updated = (
                    model.provider_is_active,
                    model.provider_deleted_at,
                    model.provider_updated_at,
                )
                fields, names, sort_field = ["id", "name", "provider_type"], ["id", "name"], "name"
            elif kind == "types":
                key, identity = (
                    model.resource_type_code,
                    (model.resource_type_id, model.resource_type_name_vi),
                )
                active, deleted, updated = (
                    model.resource_type_is_active,
                    model.resource_type_deleted_at,
                    model.resource_type_updated_at,
                )
                fields, names, sort_field = ["code", "name"], ["code", "name"], "name"
            else:
                raise ValueError("Unknown catalog")
            key = trim(key)
            identity = tuple(trim(c) for c in identity)
            snapshots = (
                select(
                    key.label("key"),
                    *(c.label("value" + str(i)) for i, c in enumerate(identity)),
                    func.count().label("frequency"),
                    func.max(func.coalesce(updated, model.updated_at)).label("freshness"),
                )
                .where(active.is_(True), deleted.is_(None), key != "", *(c != "" for c in identity))
                .group_by(key, *identity)
                .cte("snapshots")
            )
            rank = func.row_number().over(
                partition_by=snapshots.c.key,
                order_by=(
                    snapshots.c.frequency.desc(),
                    snapshots.c.freshness.desc().nulls_last(),
                    *(
                        snapshots.c["value" + str(i)].collate("C").desc()
                        if dialect == "postgresql"
                        else snapshots.c["value" + str(i)].desc()
                        for i in range(len(identity))
                    ),
                ),
            )
            ranked = select(snapshots, rank.label("position")).cte("ranked")
            columns = (
                [
                    ranked.c.key.label("id"),
                    ranked.c.value0.label("name"),
                    ranked.c.value1.label("provider_type"),
                ]
                if kind == "providers"
                else [ranked.c.key.label("code"), ranked.c.value1.label("name")]
            )
            base = select(*columns).where(ranked.c.position == 1).cte("catalog")
        stmt = select(base)
        term = (query or "").strip().casefold()
        if term:
            # Escape wildcard characters; query text remains bound parameters.
            term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stmt = stmt.where(
                or_(*(fold(base.c[name]).like("%" + term + "%", escape="\\") for name in names))
            )
        filtered = stmt.cte("filtered")
        identity_name = fields[0]
        order = (
            fold(filtered.c[sort_field]),
            filtered.c[identity_name].collate("C")
            if dialect == "postgresql"
            else filtered.c[identity_name],
        )
        total = select(func.count().label("total")).select_from(filtered).cte("totals")
        paged = select(filtered).order_by(*order).offset((page - 1) * size).limit(size).cte("paged")
        # One statement/snapshot: count is preserved even for an empty/out-of-range page.
        rows = (
            self.session.execute(
                select(total.c.total, *(paged.c[name] for name in fields)).select_from(
                    total.outerjoin(paged, true())
                ).order_by(fold(paged.c[sort_field]), paged.c[identity_name])
            )
            .mappings()
            .all()
        )
        count = rows[0]["total"]
        return {
            "items": [
                {name: row[name] for name in fields}
                for row in rows
                if row[identity_name] is not None
            ],
            "pagination": {
                "page": page,
                "size": size,
                "total": count,
                "total_pages": ceil(count / size) if count else 0,
            },
        }
