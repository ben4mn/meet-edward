"""R/G TDD red for P5 — make spreading activation carry signal.

The live graph is 21,326 connections over 1,710 memories: median degree 11,
max 300. Two things are wrong with it.

**Every edge has strength exactly 0.5.** Retrieval spends the strength on
``memory.score += 0.1 * strength`` and ``0.15 * strength`` for pulled-in
memories, so both are constants. The weight dimension exists in the schema
and carries no information at all.

**The types are not equally informative.** ``same_person`` is 11,672 of the
21,326 — it fires whenever two memories mention the same frequent contact,
which for someone Ellis texts daily links essentially everything to everything.
``related`` is 865 and is the consolidation LLM saying two memories connect
when no cheaper category fit. Treating those as equal evidence is what turns
association into a clique.

Strength should track information content, which is roughly inverse to how
often the type fires. And no memory should spread through 300 edges — beyond
a small number of the strongest links, the extra ones only add noise to every
query the memory participates in.

Retirement of surplus edges is a delete: unlike memories, a connection has no
independent content to preserve, and consolidation rebuilds them each cycle.
"""

from __future__ import annotations

import pytest


def test_connection_strength_map_exists():
    from services import consolidation_service as c

    assert hasattr(c, "CONNECTION_TYPE_STRENGTH")
    assert hasattr(c, "MAX_CONNECTIONS_PER_MEMORY")


def test_strength_tracks_information_content():
    """Rarer connection types are stronger evidence."""
    from services.consolidation_service import CONNECTION_TYPE_STRENGTH as S

    assert S["same_person"] < S["same_topic"] < S["same_event"] <= S["related"], (
        "same_person fires on 11672 of 21326 edges — any two memories about a "
        "frequent contact. It cannot be worth as much as an explicit related "
        "link, which fired 865 times."
    )


def test_all_strengths_are_in_range():
    from services.consolidation_service import CONNECTION_TYPE_STRENGTH as S

    for name, value in S.items():
        assert 0.0 < value <= 1.0, f"{name} strength {value} out of range"


def test_no_two_types_share_a_strength():
    """A constant weight is what P5 exists to remove."""
    from services.consolidation_service import CONNECTION_TYPE_STRENGTH as S

    assert len(set(S.values())) == len(S), (
        "Distinct connection types with identical strength reproduce the "
        "uniform-0.5 graph this phase is fixing."
    )


def test_strength_for_type_handles_unknown_types():
    from services.consolidation_service import strength_for_connection_type

    from services.consolidation_service import CONNECTION_TYPE_STRENGTH as S

    assert strength_for_connection_type("same_person") == S["same_person"]
    assert strength_for_connection_type("nonsense") == min(S.values()), (
        "An unrecognized type is the weakest possible evidence, not the "
        "default 0.5 that created the flat graph."
    )
    assert strength_for_connection_type(None) == min(S.values())


# ---------------------------------------------------------------------------
# Degree cap
# ---------------------------------------------------------------------------


def test_degree_cap_is_a_small_number():
    from services.consolidation_service import MAX_CONNECTIONS_PER_MEMORY

    assert 3 <= MAX_CONNECTIONS_PER_MEMORY <= 20, (
        "Spreading activation pulls in at most 5 memories per query. A cap "
        "far above that just adds candidates that can never be used."
    )


def test_select_surplus_keeps_the_strongest():
    from services.consolidation_service import select_surplus_connections

    # 8 edges on one memory; cap is whatever the module says.
    from services.consolidation_service import MAX_CONNECTIONS_PER_MEMORY as CAP

    edges = [
        {"id": f"e{i}", "memory_id_a": "m1", "memory_id_b": f"o{i}", "strength": s}
        for i, s in enumerate([0.9, 0.1, 0.8, 0.2, 0.7, 0.3, 0.6, 0.4])
    ]

    surplus = select_surplus_connections(edges)
    surplus_ids = {e["id"] for e in surplus}

    assert len(edges) - len(surplus) == min(CAP, len(edges))

    kept = [e for e in edges if e["id"] not in surplus_ids]
    kept_strengths = sorted((e["strength"] for e in kept), reverse=True)
    all_strengths = sorted((e["strength"] for e in edges), reverse=True)
    assert kept_strengths == all_strengths[: len(kept)], (
        "The cap must keep the strongest links, not an arbitrary slice."
    )


def test_no_surplus_below_the_cap():
    from services.consolidation_service import (
        MAX_CONNECTIONS_PER_MEMORY as CAP,
        select_surplus_connections,
    )

    edges = [
        {"id": f"e{i}", "memory_id_a": "m1", "memory_id_b": f"o{i}", "strength": 0.5}
        for i in range(CAP)
    ]

    assert select_surplus_connections(edges) == []


def test_cap_is_applied_per_memory_not_globally():
    from services.consolidation_service import (
        MAX_CONNECTIONS_PER_MEMORY as CAP,
        select_surplus_connections,
    )

    # Two memories, each exactly at the cap. Nothing is surplus.
    edges = []
    for owner in ("m1", "m2"):
        for i in range(CAP):
            edges.append(
                {
                    "id": f"{owner}-e{i}",
                    "memory_id_a": owner,
                    "memory_id_b": f"{owner}-other{i}",
                    "strength": 0.5,
                }
            )

    assert select_surplus_connections(edges) == [], (
        "Degree is a property of a memory. Applying the cap globally would "
        "punish a memory for other memories' links."
    )


def test_an_edge_counts_toward_both_endpoints():
    from services.consolidation_service import (
        MAX_CONNECTIONS_PER_MEMORY as CAP,
        select_surplus_connections,
    )

    # m1 is under the cap, but every one of its partners is a hub already
    # over the cap through this same set of edges.
    edges = [
        {"id": f"e{i}", "memory_id_a": "hub", "memory_id_b": f"leaf{i}", "strength": 0.5}
        for i in range(CAP + 4)
    ]

    surplus = select_surplus_connections(edges)
    assert len(surplus) == 4, (
        "A connection is undirected — it occupies a slot on both memories, so "
        "the hub's degree must be counted from both columns."
    )
