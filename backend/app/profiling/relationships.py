"""P3 candidate search, P4 verification and P5 join structure. SQL is the source of truth;
the judge (LLM or heuristic) only chooses among SQL-backed evidence."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import duckdb

from .contracts import RelationshipReview
from .interpreters import singular
from .models import Containment, Link, LinkCandidate, TableModel
from .sql import is_unique, quote_ident as q

ACCEPT_CONTAINMENT = 0.95
CANDIDATE_CONTAINMENT = 0.5
NAME_SIM_STRONG = 0.6
NAME_SIM_CONTAINMENT = 0.2


def _core(name: str) -> str:
    n = name.lower()
    n = re.sub(r"^[a-z]{1,2}_", "", n)  # table prefixes such as c_, ps_
    n = re.sub(r"[^a-z0-9]", "", n)
    return re.sub(r"(id|key|code|no|num)$", "", n)


def name_similarity(child_column: str, parent_table: str, parent_column: str) -> float:
    core = _core(child_column)
    if not core:
        return 0.0
    targets = [t for t in (_core(parent_column), re.sub(r"[^a-z0-9]", "", singular(parent_table))) if t]
    return round(max((SequenceMatcher(None, core, t).ratio() for t in targets), default=0.0), 3)


def measure_containment(
    con: duckdb.DuckDBPyConnection, child: str, child_col: str, parent: str, parent_col: str
) -> Containment:
    distinct, matched, rows, orphan_rows = con.execute(
        f"SELECT count(DISTINCT c.v), count(DISTINCT c.v) FILTER (WHERE p.k IS NOT NULL), count(c.v), "
        f"count(c.v) FILTER (WHERE p.k IS NULL) "
        f"FROM (SELECT {q(child_col)} AS v FROM {q(child)}) c "
        f"LEFT JOIN (SELECT DISTINCT {q(parent_col)} AS k FROM {q(parent)}) p ON c.v = p.k"
    ).fetchone()
    parent_distinct = con.execute(f"SELECT count(DISTINCT {q(parent_col)}) FROM {q(parent)}").fetchone()[0]
    return Containment(
        containment=round(matched / distinct, 4) if distinct else 0.0,
        child_distinct=distinct,
        child_non_null_rows=rows,
        orphan_rows=orphan_rows,
        parent_coverage=round(matched / parent_distinct, 4) if parent_distinct else 0.0,
    )


def is_spurious_key_link(child: TableModel, child_col: str, parent: str, parent_col: str) -> bool:
    """A column linking to itself, or a table's own key pointing at a differently named key elsewhere."""
    if child.name == parent and child_col == parent_col:
        return True
    return child.primary_key == [child_col] and child_col.lower() != parent_col.lower()


class CandidateFinder:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def find(self, tables: dict[str, TableModel]) -> list[LinkCandidate]:
        candidates: list[LinkCandidate] = []
        for parent in tables.values():
            if len(parent.primary_key) != 1:
                continue
            parent_col = parent.primary_key[0]
            parent_family = parent.stats.column(parent_col).family
            if parent_family not in ("number", "string"):
                continue
            for child in tables.values():
                for col in child.stats.columns:
                    if col.family != parent_family or is_spurious_key_link(child, col.name, parent.name, parent_col):
                        continue
                    sim = name_similarity(col.name, parent.name, parent_col)
                    if child.role(col.name) != "identifier" and sim < NAME_SIM_STRONG:
                        continue
                    evidence = measure_containment(self._con, child.name, col.name, parent.name, parent_col)
                    strong = evidence.containment >= CANDIDATE_CONTAINMENT
                    named = sim >= NAME_SIM_STRONG and evidence.containment >= NAME_SIM_CONTAINMENT
                    if strong or named:
                        candidates.append(LinkCandidate(
                            id=len(candidates), child_table=child.name, child_column=col.name,
                            parent_table=parent.name, parent_column=parent_col, name_similarity=sim,
                            child_column_unique=col.unique, evidence=evidence,
                        ))
        return candidates


class LinkVerifier:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def verify(
        self, review: RelationshipReview, candidates: list[LinkCandidate], tables: dict[str, TableModel]
    ) -> list[Link]:
        by_id = {c.id: c for c in candidates}
        links: list[Link] = []
        for decision in review.decisions:
            cand = by_id.get(decision.candidate_id)
            if cand is None or decision.decision == "reject":
                continue
            accepted = decision.decision == "accept" and cand.evidence.containment >= ACCEPT_CONTAINMENT
            links.append(Link(
                cand.child_table, cand.child_column, cand.parent_table, cand.parent_column, cand.evidence,
                status="accepted" if accepted else "needs_review", source="candidate", reason=decision.reason,
            ))

        for proposal in review.missing_links:
            link = self._verify_proposal(proposal.from_column, proposal.to_column, proposal.reason, tables, links)
            if link:
                links.append(link)

        self._demote_ambiguous(links)
        return links

    def _verify_proposal(
        self, ref_from: str, ref_to: str, reason: str, tables: dict[str, TableModel], links: list[Link]
    ) -> Link | None:
        src, dst = _resolve(ref_from, tables), _resolve(ref_to, tables)
        if not src or not dst or f"{src[0]}.{src[1]}" in {l.ref_from for l in links}:
            return None
        if is_spurious_key_link(tables[src[0]], src[1], dst[0], dst[1]):
            return None
        parent_unique = is_unique(self._con, dst[0], [dst[1]], tables[dst[0]].stats.row_count)
        try:
            evidence = measure_containment(self._con, src[0], src[1], dst[0], dst[1])
        except duckdb.Error:
            return None  # incompatible types: the proposal cannot be a real key
        accepted = parent_unique and evidence.containment >= ACCEPT_CONTAINMENT
        return Link(src[0], src[1], dst[0], dst[1], evidence,
                    status="accepted" if accepted else "needs_review", source="llm_proposed", reason=reason)

    @staticmethod
    def _demote_ambiguous(links: list[Link]) -> None:
        """One child column references one parent; several accepted parents go to human review."""
        counts: dict[str, int] = {}
        for link in links:
            if link.status == "accepted":
                counts[link.ref_from] = counts.get(link.ref_from, 0) + 1
        for link in links:
            if link.status == "accepted" and counts[link.ref_from] > 1:
                link.status = "needs_review"
                link.reason += " [demoted: column accepted for several parents]"


def _resolve(ref: str, tables: dict[str, TableModel]) -> tuple[str, str] | None:
    if "." not in ref:
        return None
    table, column = ref.split(".", 1)
    table = {k.lower(): k for k in tables}.get(table.lower())
    if table is None:
        return None
    column = {c.lower(): c for c in tables[table].stats.column_names}.get(column.lower())
    return (table, column) if column else None


class StructureAnnotator:
    """P5: cardinality and fan-out so the chat agent can avoid double counting."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def annotate(self, links: list[Link]) -> None:
        for link in links:
            rows, distinct = self._con.execute(
                f"SELECT count({q(link.child_column)}), count(DISTINCT {q(link.child_column)}) FROM {q(link.child_table)}"
            ).fetchone()
            link.cardinality = "one-to-one" if rows == distinct else "many-to-one"
            link.fan_out = round(rows / distinct, 3) if distinct else None
            if link.fan_out and link.fan_out > 1:
                parent = link.parent_table
                link.join_warning = (
                    f"Joining {link.child_table} to {parent} repeats each {parent} row ~{link.fan_out}x; "
                    f"aggregate {parent} measures before joining."
                )
