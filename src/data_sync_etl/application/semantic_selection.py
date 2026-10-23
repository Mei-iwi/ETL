"""Offline selective-retrieval experiments; intentionally not wired to Search.

Cosine/margins are features, never probabilities or proof that an answer exists.
Fit only on validation. Policies must be reviewed before any runtime adoption.
"""

from data_sync_etl.application.vector_quality import evaluate_ranking, summarize, validate_scores
from data_sync_etl.domain.evaluation import percentile


def judgment_distribution(rows):
    """Separate missing judgments from explicit grade0; never train on unjudged."""
    groups = {
        key: []
        for key in ("answer", "partial", "judged_negative", "unjudged", "no_answer_query_top")
    }
    for row in rows:
        if not any(grade >= 2 for grade in row["qrels"].values()) and row["hits"]:
            groups["no_answer_query_top"].append(row["hits"][0]["score"])
        for hit in row["hits"]:
            grade = row["qrels"].get(hit["id"])
            group = (
                "unjudged"
                if grade is None
                else "answer"
                if grade >= 2
                else "partial"
                if grade == 1
                else "judged_negative"
            )
            groups[group].append(hit["score"])
    return {
        key: {
            "count": len(values),
            "min": min(values) if values else None,
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
            "max": max(values) if values else None,
        }
        for key, values in groups.items()
    }


def features(hits):
    validate_scores([hit["score"] for hit in hits])
    if len({hit["id"] for hit in hits}) != len(hits):
        raise ValueError("Resource grouping required before selection")
    ordered = sorted(hits, key=lambda hit: (-hit["score"], hit["id"]))
    if not ordered:
        return {"top": 0.0, "gap2": 0.0, "gap5": 0.0}
    top = ordered[0]["score"]
    # Missing competitors are not evidence of confidence.
    return {
        "top": top,
        "gap2": top - ordered[1]["score"] if len(ordered) >= 2 else 0.0,
        "gap5": top - ordered[4]["score"] if len(ordered) >= 5 else 0.0,
    }


def select(hits, policy):
    values = features(hits)
    absolute, margin = policy["absolute"], policy["margin"]
    validate_scores([absolute, margin])
    if not 0 <= absolute <= 1 or not 0 <= margin <= 2 or policy["feature"] not in ("gap2", "gap5"):
        raise ValueError("Invalid selection policy")
    if values["top"] <= absolute or (margin > 0 and values[policy["feature"]] <= margin):
        return []
    return sorted(
        (hit for hit in hits if hit["score"] > absolute), key=lambda hit: (-hit["score"], hit["id"])
    )


def assess(rows, policy):
    changed = [{**row, "hits": select(row["hits"], policy)} for row in rows]
    for row in changed:
        row["metrics"] = evaluate_ranking([hit["id"] for hit in row["hits"]], row["qrels"])
    result = summarize(changed)
    positives = [row for row in changed if row["metrics"]["has_positive"]]
    result["false_abstention_rate"] = (
        sum(not row["hits"] for row in positives) / len(positives) if positives else None
    )
    result["positive_query_wrong_top1_rate"] = (
        sum(bool(row["hits"]) and row["metrics"]["precision1"] == 0 for row in positives)
        / len(positives)
        if positives
        else None
    )
    result["coverage"] = (
        sum(bool(row["hits"]) for row in changed) / len(changed) if changed else None
    )
    return result, changed


def calibrate(rows, *, min_recall=0.95, max_false_abstention=0.05):
    """Finite rules, no fitted confidence model; hard recall/abstention safeguards.

    Choose minimum no-answer FPR under constraints; tie on recall, P@1, less
    restrictive absolute/margin. No-answer labels are query-level annotations;
    unjudged resource relevance is NOT used as a negative training label.
    """
    if (
        not rows
        or any(row["split"] != "validation" for row in rows)
        or not 0 <= min_recall <= 1
        or not 0 <= max_false_abstention <= 1
    ):
        raise ValueError("Validation rows and valid risk constraints required")
    values = [features(row["hits"]) for row in rows]
    # Query maxima define abstention breakpoints; resource scores additionally
    # define result-filtering breakpoints, without reading any holdout scores.
    thresholds = sorted(
        {0.0, 1.0, *(hit["score"] for row in rows for hit in row["hits"] if hit["score"] > 0)}
    )
    policies = {"absolute": [], "margin2": [], "margin5": [], "selective": []}
    for threshold in thresholds:
        policies["absolute"].append({"absolute": threshold, "margin": 0.0, "feature": "gap5"})
    for feature in ("gap2", "gap5"):
        margins = sorted({0.0, *(value[feature] for value in values if value[feature] > 0)})
        for margin in margins:
            policies["margin2" if feature == "gap2" else "margin5"].append(
                {"absolute": 0.0, "margin": margin, "feature": feature}
            )
        # Coarse, predeclared absolute grid bounds the joint search on tiny labels.
        for threshold in (0.0, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7):
            for margin in margins:
                policies["selective"].append(
                    {"absolute": threshold, "margin": margin, "feature": feature}
                )
    result = {}
    for name, candidates in policies.items():
        curve, eligible = [], []
        for policy in candidates:
            metrics, _ = assess(rows, policy)
            cell = {"policy": policy, "metrics": metrics}
            curve.append(cell)
            if (
                (metrics["recall5"] or 0) >= min_recall
                and metrics["false_abstention_rate"] is not None
                and metrics["false_abstention_rate"] <= max_false_abstention
            ):
                eligible.append(cell)
        chosen = (
            min(
                eligible,
                key=lambda cell: (
                    cell["metrics"]["no_answer_false_positive_rate"]
                    if cell["metrics"]["no_answer_false_positive_rate"] is not None
                    else 1.0,
                    -cell["metrics"]["recall5"],
                    -cell["metrics"]["precision1"],
                    cell["policy"]["absolute"],
                    cell["policy"]["margin"],
                    cell["policy"]["feature"],
                ),
            )
            if eligible
            else None
        )
        result[name] = {
            "selected": chosen,
            "feasible": len(eligible),
            "trials": len(curve),
            "curve": curve,
        }
    return {
        "validation_query_ids": [row["query_id"] for row in rows],
        "constraints": {"min_recall5": min_recall, "max_false_abstention": max_false_abstention},
        "methods": result,
    }


def bounded_aggregate(scores, locators, *, policy="best", top_n=3, evidence_scores=None, alpha=0.5):
    """One result/resource with a locator from the actual deciding evidence.

    Policies: dense max; bounded mean (no count bonus); contextual/raw agreement
    within top-N dense candidates. Never merges different resource IDs by title.
    """
    if policy not in ("best", "mean", "evidence") or not 1 <= top_n <= 5 or not 0 <= alpha <= 1:
        raise ValueError("Invalid bounded aggregation")
    validate_scores(list(scores.values()))
    if evidence_scores is not None:
        validate_scores(list(evidence_scores.values()))
    groups, versions = {}, {}
    for key in sorted(scores, key=lambda key: (-scores[key], key)):
        resource = locators[key]["resource_id"]
        version = locators[key]["version_id"]
        if resource in versions and versions[resource] != version:
            raise ValueError("Select one verified version per resource before aggregation")
        versions[resource] = version
        group = groups.setdefault(resource, [])
        if len(group) < top_n:
            group.append(key)
    hits = []
    for resource, keys in groups.items():
        selected, score = keys[0], scores[keys[0]]
        if policy == "mean":
            score = sum(scores[key] for key in keys) / len(keys)
        elif policy == "evidence":
            if evidence_scores is None or any(key not in evidence_scores for key in keys):
                raise ValueError("Evidence scores required")
            combined = {
                key: alpha * scores[key] + (1 - alpha) * evidence_scores[key] for key in keys
            }
            selected = min(keys, key=lambda key: (-combined[key], key))
            score = combined[selected]
        hits.append(
            {
                "id": resource,
                "score": score,
                "chunk_id": selected,
                "resource_version_id": locators[selected]["version_id"],
            }
        )
    return sorted(hits, key=lambda hit: (-hit["score"], hit["id"]))
