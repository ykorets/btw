"""Shared pending-review state for normalize and review orchestration.

A fact version named by a pending manifest is an immutable review snapshot.
Callers must hold new work until that review is promoted or rejected instead
of treating the database guard as an unexpected write failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class PendingReview:
    review_id: str
    batch_date: str | None = None
    pr_url: str | None = None

    def label(self) -> str:
        return self.pr_url or f"review {self.review_id}"


class PendingReviewBlocked(RuntimeError):
    """A staging write was deferred because its review snapshot is sealed."""

    def __init__(self, fact_table: str, fact_id: str,
                 review: PendingReview | None = None):
        self.fact_table = fact_table
        self.fact_id = fact_id
        self.review = review
        target = review.label() if review else "a concurrently sealed review"
        super().__init__(f"staging version {fact_id} is pending in {target}")


PendingReviewMap = dict[tuple[str, str], PendingReview]
RestCall = Callable[..., object]


def load_pending_review_targets(rest: RestCall) -> PendingReviewMap:
    """Return every exact fact version currently sealed for human review.

    Two simple PostgREST reads avoid relying on embedded-resource filter
    syntax and keep this helper compatible with the project's existing Data
    API surface.
    """
    reviews = rest("GET", "review", params={
        "select": "id,batch_date,pr_url",
        "decision": "eq.pending",
    }).json()
    if not reviews:
        return {}

    by_id = {
        row["id"]: PendingReview(
            review_id=row["id"],
            batch_date=row.get("batch_date"),
            pr_url=row.get("pr_url"),
        )
        for row in reviews
    }
    ids = ",".join(sorted(by_id))
    items = rest("GET", "review_manifest_item", params={
        "select": "review_id,unit_id,permit_id,event_id",
        "review_id": f"in.({ids})",
    }).json()

    targets: PendingReviewMap = {}
    for item in items:
        review = by_id.get(item.get("review_id"))
        if review is None:
            continue
        for fact_table in ("unit", "permit", "event"):
            fact_id = item.get(f"{fact_table}_id")
            if fact_id:
                targets[(fact_table, fact_id)] = review
    return targets


def raise_if_pending(pending: PendingReviewMap, fact_table: str,
                     fact_id: str) -> None:
    review = pending.get((fact_table, fact_id))
    if review is not None:
        raise PendingReviewBlocked(fact_table, fact_id, review)


def translate_frozen_write(exc: RuntimeError, fact_table: str,
                           fact_id: str) -> None:
    """Turn the trigger's race-safe refusal into expected control flow."""
    if "is frozen by a sealed review" in str(exc):
        raise PendingReviewBlocked(fact_table, fact_id) from exc
    raise exc
