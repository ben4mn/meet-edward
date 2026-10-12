"""R/G TDD red for P2 — the forgetting pass.

Every other subsystem in Edward decays: decay_stale_links,
decay_stale_weak_claims, decay_stale_alignments, decay_stale_negotiations.
Memory does not. Nothing has ever been removed from the memories table except
by consolidation's dedup merge or an explicit remember_forget call, so 36% of
the live store had never been retrieved once.

The model borrows the parts of human forgetting that are actually productive:

  * Situational things fade fastest. ``temporal_nature`` sets the base
    half-life — a dated appointment is not a fact about a person.
  * Use protects. Each turn a memory genuinely earned its place extends its
    life; this is where P1's ``used_count`` pays off.
  * Being told beats inferring. ``user_stated`` outlives ``inferred`` — but
    the multiplier is smaller for temporary memories, because Ellis saying
    "Monday works for the dentist" does not make that durable.
  * Consolidated memories are sticky. The tier ladder multiplies the life.
  * Nothing is forgotten instantly. Below MIN_AGE_DAYS a memory is untouchable
    however unpromising it looks.

Retirement stamps ``valid_until``; it never deletes. Retrieval already filters
on that column, so a retired memory stops surfacing while remaining auditable
and revivable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _mem(**kw):
    """A plain namespace standing in for a memory row."""
    from types import SimpleNamespace

    defaults = dict(
        id="m1",
        content="something",
        memory_type="context",
        temporal_nature="temporary",
        source_type="inferred",
        importance=0.5,
        tier="observation",
        used_count=0,
        access_count=0,
        reinforcement_count=0,
        created_at=NOW - timedelta(days=30),
        last_accessed=None,
        last_used_at=None,
        valid_until=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_surface():
    from services import memory_decay_service as d

    for name in (
        "compute_retention",
        "should_retire",
        "decay_stale_memories",
        "RETENTION_FLOOR",
        "MIN_AGE_DAYS",
        "BASE_HALF_LIFE_DAYS",
    ):
        assert hasattr(d, name), f"memory_decay_service must expose {name}"


def test_base_half_lives_are_ordered_by_shelf_life():
    from services.memory_decay_service import BASE_HALF_LIFE_DAYS

    assert (
        BASE_HALF_LIFE_DAYS["temporary"]
        < BASE_HALF_LIFE_DAYS["evolving"]
        < BASE_HALF_LIFE_DAYS["timeless"]
    ), "temporal_nature is the primary shelf-life signal and must order the half-lives."


# ---------------------------------------------------------------------------
# Retention curve
# ---------------------------------------------------------------------------


def test_retention_is_one_at_zero_age():
    from services.memory_decay_service import compute_retention

    assert compute_retention(_mem(created_at=NOW), now=NOW) == pytest.approx(1.0)


def test_retention_falls_with_age():
    from services.memory_decay_service import compute_retention

    young = compute_retention(_mem(created_at=NOW - timedelta(days=5)), now=NOW)
    old = compute_retention(_mem(created_at=NOW - timedelta(days=200)), now=NOW)
    assert 0.0 <= old < young <= 1.0


def test_timeless_outlives_temporary_all_else_equal():
    from services.memory_decay_service import compute_retention

    args = dict(created_at=NOW - timedelta(days=60))
    assert compute_retention(_mem(temporal_nature="timeless", **args), now=NOW) > (
        compute_retention(_mem(temporal_nature="temporary", **args), now=NOW)
    )


def test_use_protects_more_than_mere_retrieval():
    """A memory the model actually saw beats one that merely scored well."""
    from services.memory_decay_service import compute_retention

    args = dict(created_at=NOW - timedelta(days=60))
    used = compute_retention(_mem(used_count=6, **args), now=NOW)
    unused = compute_retention(_mem(used_count=0, **args), now=NOW)
    assert used > unused, (
        "used_count is the whole point of P1 — it must move the decay curve."
    )


def test_user_stated_outlives_inferred():
    from services.memory_decay_service import compute_retention

    args = dict(created_at=NOW - timedelta(days=60), temporal_nature="timeless")
    assert compute_retention(_mem(source_type="user_stated", **args), now=NOW) > (
        compute_retention(_mem(source_type="inferred", **args), now=NOW)
    )


def test_user_stated_bonus_is_smaller_for_temporary_memories():
    """Ellis saying "Monday works for the dentist" does not make it durable.

    Asserted on the half-life multiplier rather than on retention. Retention
    is exponential, so comparing retention ratios across two different base
    half-lives measures the bases, not the source bonus — the timeless curve
    has barely moved at 60 days, which compresses its ratio toward 1 however
    large the bonus is.
    """
    from services.memory_decay_service import effective_half_life_days

    def bonus(nature):
        args = dict(created_at=NOW - timedelta(days=60), temporal_nature=nature)
        stated = effective_half_life_days(_mem(source_type="user_stated", **args))
        inferred = effective_half_life_days(_mem(source_type="inferred", **args))
        return stated / inferred

    assert bonus("temporary") < bonus("evolving") < bonus("timeless"), (
        "A stated appointment should not get the same protection as a stated "
        f"fact about a person. Got {bonus('temporary'):.2f} / "
        f"{bonus('evolving'):.2f} / {bonus('timeless'):.2f}."
    )


def test_higher_tier_outlives_lower():
    from services.memory_decay_service import compute_retention

    args = dict(created_at=NOW - timedelta(days=120), temporal_nature="evolving")
    assert (
        compute_retention(_mem(tier="knowledge", **args), now=NOW)
        > compute_retention(_mem(tier="belief", **args), now=NOW)
        > compute_retention(_mem(tier="observation", **args), now=NOW)
    )


def test_importance_protects():
    from services.memory_decay_service import compute_retention

    args = dict(created_at=NOW - timedelta(days=60))
    assert compute_retention(_mem(importance=0.9, **args), now=NOW) > (
        compute_retention(_mem(importance=0.1, **args), now=NOW)
    )


def test_age_is_measured_from_creation_not_last_use():
    """It was "recall resets the clock" until 2026-09-27; recall is
    retrieval, and the rows retrieval favours never aged (see the last
    section)."""
    from services.memory_decay_service import compute_retention

    stale = _mem(created_at=NOW - timedelta(days=200))
    recalled = _mem(
        created_at=NOW - timedelta(days=200),
        last_used_at=NOW - timedelta(days=2),
    )
    assert compute_retention(recalled, now=NOW) == pytest.approx(compute_retention(stale, now=NOW))


# ---------------------------------------------------------------------------
# Retirement decision
# ---------------------------------------------------------------------------


def test_stale_inferred_temporary_observation_is_retired():
    from services.memory_decay_service import should_retire

    assert should_retire(
        _mem(
            temporal_nature="temporary",
            source_type="inferred",
            created_at=NOW - timedelta(days=45),
        ),
        now=NOW,
    ) is True


def test_young_memory_is_never_retired():
    from services.memory_decay_service import should_retire, MIN_AGE_DAYS

    assert should_retire(
        _mem(
            temporal_nature="temporary",
            source_type="inferred",
            importance=0.0,
            created_at=NOW - timedelta(days=MIN_AGE_DAYS - 1),
        ),
        now=NOW,
    ) is False, "Nothing is forgotten before it has had a chance to be useful."


def test_standing_instructions_are_never_auto_retired():
    """A ``temporary`` instruction is a one-off with a horizon (the last
    section); any other is a standing directive."""
    from services.memory_decay_service import should_retire

    for nature in ("evolving", "timeless"):
        assert should_retire(
            _mem(
                memory_type="instruction",
                temporal_nature=nature,
                source_type="inferred",
                created_at=NOW - timedelta(days=900),
            ),
            now=NOW,
        ) is False, (
            "Instructions are Ellis's standing directives to Edward. Forgetting one "
            "silently changes Edward's behaviour with no trace."
        )


def test_knowledge_tier_is_never_auto_retired():
    """A lasting row, that is: the hold is not for ``temporary`` ones
    (2026-09-27, ``test_a_temporary_knowledge_row_is_not_held`` below) —
    this row used ``_mem``'s default ``temporary`` until then."""
    from services.memory_decay_service import should_retire

    for nature in ("evolving", "timeless"):
        assert should_retire(
            _mem(tier="knowledge", temporal_nature=nature, created_at=NOW - timedelta(days=900)), now=NOW
        ) is False


def test_stated_timeless_facts_are_never_auto_retired():
    from services.memory_decay_service import should_retire

    assert should_retire(
        _mem(
            temporal_nature="timeless",
            source_type="user_stated",
            created_at=NOW - timedelta(days=900),
        ),
        now=NOW,
    ) is False, "Ellis telling Edward a durable fact must survive silence."


def test_already_retired_is_not_retired_again():
    from services.memory_decay_service import should_retire

    assert should_retire(
        _mem(valid_until=NOW - timedelta(days=1), created_at=NOW - timedelta(days=90)),
        now=NOW,
    ) is False


def test_a_well_used_temporary_memory_survives():
    from services.memory_decay_service import should_retire

    assert should_retire(
        _mem(
            temporal_nature="temporary",
            used_count=12,
            last_used_at=NOW - timedelta(days=1),
            created_at=NOW - timedelta(days=45),
        ),
        now=NOW,
    ) is False


# P2b (2026-08-21) distrusted ``last_accessed`` values written before the
# P1 fix via ``ACCESS_TRUST_EPOCH``. Since 2026-09-27 neither retrieval
# stamp anchors decay at all, so the epoch is gone.


# ---------------------------------------------------------------------------
# 2026-09-27: retrieval alone cannot make a stale row immortal
# ---------------------------------------------------------------------------
#
# The memory audit replayed this pass read-only against the live store and
# it retired 0 rows. The 100 most-retrieved rows took 70% of all retrievals,
# 84 of them from before Aug 15 — a July reminder note retrieved 2,759
# times among them. Being retrieved stamped ``last_used_at`` (every memory
# placed in the prompt is "used"), which reset the decay clock on every
# turn, and multiplied the half-life by 1 + log1p(used) without bound. Now
# age runs from creation — nothing records that a memory was actually
# cited, only that retrieval returned it — and use lengthens the half-life
# by at most MAX_USE_MULTIPLIER.


def test_a_temporary_memory_retrieved_every_day_still_ages_out():
    from services.memory_decay_service import should_retire

    stale = _mem(
        content="Ellis is waiting for the roofer to reply to a quote request",
        temporal_nature="temporary",
        tier="belief",
        used_count=785,
        access_count=1997,
        created_at=NOW - timedelta(days=120),
        last_used_at=NOW - timedelta(hours=3),
        last_accessed=NOW - timedelta(hours=3),
    )
    assert should_retire(stale, now=NOW) is True, (
        "Retrieval returned it again this morning; that says it is reachable, "
        "not that it is still true."
    )


def test_use_lengthens_life_only_up_to_a_cap():
    from services.memory_decay_service import MAX_USE_MULTIPLIER, effective_half_life_days

    base = effective_half_life_days(_mem(used_count=0))
    once = effective_half_life_days(_mem(used_count=1))
    heavy = effective_half_life_days(_mem(used_count=2891))
    assert base < once <= heavy
    assert heavy == pytest.approx(base * MAX_USE_MULTIPLIER)
    assert MAX_USE_MULTIPLIER <= 2.0


def test_retrieval_stamps_do_not_move_the_clock():
    from services.memory_decay_service import compute_retention

    created = NOW - timedelta(days=60)
    quiet = _mem(created_at=created, used_count=5)
    busy = _mem(created_at=created, used_count=5,
                last_used_at=NOW - timedelta(days=1), last_accessed=NOW - timedelta(days=1))
    assert compute_retention(busy, now=NOW) == pytest.approx(compute_retention(quiet, now=NOW))


# ---------------------------------------------------------------------------
# 2026-09-27: sensitive memories are never forgotten automatically
# ---------------------------------------------------------------------------
#
# The memory audit's first principle: never auto-touch sensitive rows —
# pets and their meds, health, family, money. A wrong forget there costs
# most (Biscuit's meds, Pepper's pilling), and retiring one is Ellis's
# line-item call. The pass now holds them whatever their curve says.


@pytest.mark.parametrize("content,sensitive", [
    ("Biscuit needs her steroid every morning", True),
    ("Pepper is on a prescription diet and gets pilled at night", True),
    ("Rowan said her mom is visiting on Friday", True),
    ("Ellis's sister liked the scarecrow joke", True),
    ("The hero of the novel has been sober for a year", True),
    ("Ellis covers the rent this month", True),
    ("Ellis has insomnia again", True),
    ("Rowan sent Ellis a sad face and #sad", True),
    ("Rowan said her ear hurts so bad", True),
    ("Parker texted that he needs a friend", True),
    ("Ellis enjoyed the moment at the concert", False),
    ("Ellis bought a new pillow", False),
    ("Ellis is a veteran disc golfer", False),
    ("Ellis prefers terse replies", False),
    # Word ends and no bare affect words (2026-09-27 review): chatter
    # matched ``mother\w*``, ``diagnos\w*``, "worried", "needed help" and
    # "pain", and was never retired — the BIOS row among them.
    ("Parker replaced the motherboard in his PC", False),
    ("Ran diagnostics on the router", False),
    ("Parker was diagnosing the Wi-Fi drop", False),
    ("Parker was worried he could become a scapegoat", False),
    ("Parker was working in BIOS and needed help with the boot order", False),
    ("The printer is a pain in the neck", False),
    ("Ellis was upset the Rangers lost", False),
    ("Ellis's mother-in-law is visiting", True),
    ("Dinner at Ellis's father's place", True),
    ("Rowan was diagnosed with the flu", True),
    ("Rowan is in a lot of pain", True),
    ("Rowan's back hurts after the move", True),
    ("Rowan has a migraine", True),
])
def test_sensitive_content(content, sensitive):
    from services.memory_decay_service import is_sensitive

    assert is_sensitive(content) is sensitive


def test_a_sensitive_memory_is_never_auto_retired():
    from services.memory_decay_service import is_protected, should_retire

    dose = _mem(content="Biscuit was given carprofen after the vet visit", temporal_nature="temporary",
                created_at=NOW - timedelta(days=90))
    assert is_protected(dose) is True
    assert should_retire(dose, now=NOW) is False


# The hold is for what records Ellis's world — what he said or what Edward
# read — never for Edward's own notes to himself (2026-09-27 review). Held
# for every row, it made any thought, did or intention that mentions a
# dose, a bonus or someone's mom permanent: 34 of the 177 thought/did/
# intention rows in the store matched, and an intention nothing waits on
# no longer aged out (Phase 9). Intentions have their own hygiene — a
# pending event holds one (``protected_ids``), a horizon ends it.
@pytest.mark.parametrize("row", [
    dict(content="Watching ACME stock for Ellis's bonus", memory_type="intention", origin="thought", about="self",
         temporal_nature="evolving"),
    dict(content="Check whether Biscuit's evening dose was logged", memory_type="intention", origin="thought",
         about="self", temporal_nature="evolving"),
    dict(content="Call his grandmother on September 24, 2026", memory_type="intention", origin="heard",
         about="owner", temporal_nature="evolving"),
    dict(content="I left the dose reminder alone; Rowan already logged it", origin="did", about="self"),
    dict(content="Ellis seemed worried about the rent", origin="thought", about="owner"),
])
def test_edwards_own_sensitive_notes_still_age_out(row):
    from services.memory_decay_service import is_protected, should_retire

    note = _mem(created_at=NOW - timedelta(days=400), **row)
    assert is_protected(note, now=NOW) is False
    assert should_retire(note, now=NOW) is True


def test_what_edward_read_about_owner_is_still_held():
    from services.memory_decay_service import should_retire

    read = _mem(content="Rowan texted that Biscuit's dose was late", origin="read", about="contact",
                created_at=NOW - timedelta(days=400))
    assert should_retire(read, now=NOW) is False
    assert should_retire(_mem(content="Biscuit was given carprofen", created_at=NOW - timedelta(days=400)),
                         now=NOW) is False, "a row with no origin is heard, Ellis's"


# ---------------------------------------------------------------------------
# 2026-09-27: a dated episode or a reminder echo earns nothing from use
# ---------------------------------------------------------------------------
#
# Of the 100 most-retrieved live rows, 24 were dated July/August episodes
# ("On July 9, 2026, Ellis was waiting for Parker to reply…", 1,997
# retrievals) and 29 were reminder-shaped ("Ellis needed an immediate in-app
# chat reminder to answer Disc Golf Crew…"). Retrieval promoted them up
# the tier ladder (3 uses make a belief, 10 plus 5 restatements knowledge)
# and knowledge was never retired. What such a row says is fixed in time:
# being retrieved again does not make it true again. So its tier and its
# use lengthen nothing, and the knowledge tier does not hold it.

from datetime import datetime as _dt  # noqa: E402


def _utc(y, m, d):
    return _dt(y, m, d, tzinfo=timezone.utc)


@pytest.mark.parametrize("content,created,expected", [
    ("On July 9, 2026, Ellis was waiting for Parker to reply", (2026, 7, 10), (2026, 7, 9)),
    ("On July 10–12, 2026, Rowan and Ellis were at the lake", (2026, 7, 13), (2026, 7, 12)),
    ("For ice-cream club reminders on July 24, July 31, and August 28, 2026, keep it in-app",
     (2026, 7, 31), (2026, 8, 28)),
    ("In August 2026, Ellis needed to reply in the Disc Golf Crew chat", (2026, 8, 2), (2026, 8, 31)),
    ("Booked tickets for 2026-08-09 at 6:30 PM", (2026, 8, 8), (2026, 8, 9)),
    ("Ellis was born on March 5, 1990", (2026, 7, 10), None),
    ("Ellis's birthday is July 20, 2026", (2026, 7, 10), None),
    ("Ellis's flight to the conference is on December 3, 2026", (2026, 9, 1), None),
    ("On September 20, 2026, Ellis said the pizza was great", (2026, 9, 20), None),
    ("Ellis likes tacos", (2026, 7, 10), None),
])
def test_episode_date(content, created, expected):
    from services.memory_decay_service import episode_date

    got = episode_date(content, created_at=_utc(*created), now=_utc(2026, 9, 27))
    assert (got.date() if got else None) == (_utc(*expected).date() if expected else None)


@pytest.mark.parametrize("content", [
    "Order placed Jan 5, 0000 per the receipt",
    "Shipment slipped in May 0000",
    "The form defaults to 0000-01-01",
])
def test_a_date_with_year_zero_is_no_date(content):
    """``datetime`` has no year 0. One row naming "Jan 5, 0000" (a form
    default, a receipt's placeholder) raised out of ``_dates_named`` and,
    with no per-row guard in the pass, stopped every reflect wake's
    decay pass for good — the row it choked on was never retired."""
    from services.memory_decay_service import episode_date, retire_reason

    assert episode_date(content, created_at=_utc(2026, 7, 10), now=_utc(2026, 9, 27)) is None
    row = _mem(content=content, created_at=NOW - timedelta(days=60))
    assert retire_reason(row, now=NOW) == "curve", "it decays like any other row"
    assert retire_reason(_mem(content=content, tier="knowledge", created_at=NOW - timedelta(days=1)),
                         now=NOW) is None


@pytest.mark.parametrize("content,echo", [
    ("On July 21, 2026, Ellis needed an immediate in-app chat reminder to answer Disc Golf Crew", True),
    ("For reminders about personal texts from Parker, Edward should notify Ellis in-app only", True),
    ("Ellis should receive one-time in-app chat reminders only with no external contact", True),
    ("Ellis regularly uses Walmart delivery, and timely in-app reminders about order arrivals are useful for him",
     False),
    ("Ellis has recurring reminders to check whether his credit card payment has been made", False),
    ("Ellis prefers terse replies", False),
])
def test_reminder_echo(content, echo):
    from services.memory_decay_service import is_reminder_echo

    assert is_reminder_echo(content) is echo


def _episode(**kw):
    fields = dict(
        content="On June 12, 2026, Ellis needed to reply in the Disc Golf Crew chat about disc golf",
        temporal_nature="temporary", source_type="user_stated", importance=0.8, tier="knowledge",
        used_count=555, reinforcement_count=7, created_at=NOW - timedelta(days=70),
        last_used_at=NOW - timedelta(hours=1),
    )
    fields.update(kw)
    return _mem(**fields)


def test_a_knowledge_tier_dated_episode_can_be_retired():
    from services.memory_decay_service import is_protected, should_retire

    assert is_protected(_episode(), now=NOW) is False
    assert should_retire(_episode(), now=NOW) is True


def test_a_knowledge_tier_reminder_echo_can_be_retired():
    from services.memory_decay_service import should_retire

    echo = _episode(content="Ellis needed an immediate in-app chat reminder to answer the Disc Golf Crew chat")
    assert should_retire(echo, now=NOW) is True


def test_knowledge_that_is_not_an_episode_is_still_held():
    from services.memory_decay_service import is_protected, should_retire

    # A habit, so evolving: the knowledge hold is not for temporary rows
    # (2026-09-27, below). ``_episode``'s own nature is temporary.
    fact = _episode(content="Ellis plays early-morning disc golf with the Disc Golf Crew group",
                    temporal_nature="evolving")
    assert is_protected(fact, now=NOW) is True
    assert should_retire(fact, now=NOW) is False


def test_use_and_tier_do_not_keep_a_belief_tier_episode_alive():
    from services.memory_decay_service import should_retire

    assert should_retire(_episode(tier="belief"), now=NOW) is True
    undated = _episode(tier="belief", content="Ellis is coordinating a disc golf round with the group")
    assert should_retire(undated, now=NOW) is False, "an undated row keeps its (capped) credit"


def test_a_stated_timeless_episode_keeps_its_protection():
    from services.memory_decay_service import should_retire

    assert should_retire(_episode(temporal_nature="timeless", tier="observation"), now=NOW) is False


# ---------------------------------------------------------------------------
# 2026-09-27: a one-off instruction that is not Ellis's lapses after 30 days
# ---------------------------------------------------------------------------
#
# 38 live instructions were ``temporary`` with no horizon, and instructions
# were never auto-retired. Most were reminder echoes: "For Parker's
# August 24, 2026 message, Edward should only send Ellis an in-app
# chat reminder…" (809 retrievals), and the two blanket "in-app only, never
# contact anyone" rules came from reminder turns too — extraction read
# Edward's own reminder trigger as Ellis speaking (memory-audit-scope §2.2).
# A temporary instruction now lapses INSTRUCTION_HORIZON_DAYS after it was
# written, unless it is Ellis's own: confirmed by him (``source_ref`` kind
# ``owner_confirmed``) or heard in his own chat. Sensitive ones stay held.
# On a fresh install the horizon covers every instruction
# (``DEFAULT_INSTRUCTION_HORIZON_SINCE``); an install migrated from an older
# Edward sets ``EDWARD_INSTRUCTION_HORIZON_SINCE`` to the day it upgraded
# (``migrated_install`` below). The clock here is ``LATER``, not the
# module's August ``NOW``.

LATER = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
UPGRADED = datetime(2026, 9, 28, tzinfo=timezone.utc)


@pytest.fixture
def migrated_install(monkeypatch):
    """An install upgraded from an older Edward on ``UPGRADED``: the horizon
    covers only instructions written since (``EDWARD_INSTRUCTION_HORIZON_SINCE``,
    read at import, so the module constant is patched)."""
    monkeypatch.setattr("services.memory_decay_service.INSTRUCTION_HORIZON_SINCE", UPGRADED)
    return UPGRADED


def _instruction(**kw):
    fields = dict(
        memory_type="instruction", temporal_nature="temporary", source_type="user_stated", origin="heard",
        tier="belief", used_count=809, importance=0.9, conversation_source="scheduled_event", source_ref=None,
        content="For Parker's message, Edward should only send Ellis an in-app chat reminder",
        created_at=LATER - timedelta(days=40),
    )
    fields.update(kw)
    return _mem(**fields)


def test_the_horizon_covers_every_instruction_unless_an_upgrade_names_its_day(monkeypatch):
    from services.memory_decay_service import DEFAULT_INSTRUCTION_HORIZON_SINCE, _instruction_horizon_since

    assert DEFAULT_INSTRUCTION_HORIZON_SINCE == datetime(1970, 1, 1, tzinfo=timezone.utc)
    monkeypatch.delenv("EDWARD_INSTRUCTION_HORIZON_SINCE", raising=False)
    assert _instruction_horizon_since() == DEFAULT_INSTRUCTION_HORIZON_SINCE
    monkeypatch.setenv("EDWARD_INSTRUCTION_HORIZON_SINCE", "2026-09-28")
    assert _instruction_horizon_since() == UPGRADED
    monkeypatch.setenv("EDWARD_INSTRUCTION_HORIZON_SINCE", "not a date")
    assert _instruction_horizon_since() == DEFAULT_INSTRUCTION_HORIZON_SINCE


@pytest.mark.parametrize("install", ["fresh", "migrated"])
def test_a_one_off_instruction_lapses_after_its_horizon(install, monkeypatch):
    from services.memory_decay_service import INSTRUCTION_HORIZON_DAYS, retire_reason, should_retire

    if install == "migrated":
        monkeypatch.setattr("services.memory_decay_service.INSTRUCTION_HORIZON_SINCE", UPGRADED)
    assert INSTRUCTION_HORIZON_DAYS == 30
    old = _instruction()
    assert should_retire(old, now=LATER) is True
    assert retire_reason(old, now=LATER) == "instruction_horizon"
    assert should_retire(_instruction(created_at=LATER - timedelta(days=20)), now=LATER) is False


@pytest.mark.parametrize("owners", [
    dict(conversation_source="user"),
    dict(conversation_source=None, source_ref='{"kind": "extraction", "turn_source": "chat"}'),
    dict(conversation_source=None, source_ref='{"kind": "extraction", "turn_source": "voice"}'),
    dict(origin="read", source_ref='{"kind": "owner_confirmed", "audit": "2026-09-28"}'),
])
def test_owners_own_instruction_never_lapses(owners):
    from services.memory_decay_service import should_retire

    assert should_retire(_instruction(**owners), now=LATER + timedelta(days=400)) is False


def test_heard_in_a_reminder_turn_is_not_owners_own():
    from services.memory_decay_service import should_retire

    reminder = _instruction(conversation_source=None,
                            source_ref='{"kind": "extraction", "turn_source": "scheduled_event"}')
    assert should_retire(reminder, now=LATER) is True
    read_in_chat = _instruction(origin="read", conversation_source="user")
    assert should_retire(read_in_chat, now=LATER) is True, "only what Ellis said is his"


def test_a_sensitive_one_off_instruction_is_held():
    from services.memory_decay_service import should_retire

    held = _instruction(content="For Biscuit's 6 pm dose reminder, Edward should tell Ellis in-app only")
    assert should_retire(held, now=LATER) is False


# On a migrated install the horizon is for instructions written from the
# upgrade on (2026-09-27 review). Replayed read-only on one live store, the
# first reflect wake after the upgrade retired 27 older instructions, among
# them four the memory audit reserved for the owner's own review: the two
# blanket in-app rules, a friend's stay-out request and the owner's own
# "don't search back through that thread" (said over iMessage, so backfilled
# ``read``, so not "Ellis's own" by provenance). The backlog is the audit's
# instruction sheet. (The rows below are invented stand-ins of that shape.)
_BACKLOG = [
    dict(content="Edward should send Ellis only in-app/chat reminders and must not message any external contact "
                 "directly unless Ellis explicitly instructs otherwise in a future conversation. Remind Ellis on "
                 "July 23; August 4 regarding Parker's group-chat message",
         origin="heard", conversation_source=None, tier="knowledge", created_at=_utc(2026, 8, 4)),
    dict(content="Ellis instructs Edward to use in-app/chat reminders only and never contact external people "
                 "directly unless Ellis gives new explicit authorization; Ellis handles all external replies "
                 "personally.",
         origin="heard", conversation_source="scheduled_event",
         source_ref='{"kind": "extraction", "profile": "mind", "turn_source": "mind"}',
         created_at=_utc(2026, 8, 27)),
    dict(content="Jordan Vale asked Edward to stay out of Jordan Vale's direct-message conversation, and Edward "
                 "should not message Jordan Vale's thread again unless Jordan Vale explicitly invites Edward "
                 "back in or 24 hours pass.",
         origin="read", conversation_source="heartbeat", created_at=_utc(2026, 8, 22)),
    dict(content="Ellis does not want Edward to use messaging tools to search back through Ellis's iMessage thread "
                 "with Parker, and explicitly stated this on July 14, 2026.",
         origin="read", conversation_source="heartbeat", created_at=_utc(2026, 7, 14)),
]


@pytest.mark.parametrize("row", _BACKLOG)
def test_an_instruction_written_before_the_horizon_began_is_the_audits(row, migrated_install):
    from services.memory_decay_service import retire_reason

    first_reflect = _utc(2026, 10, 4) + timedelta(hours=14)
    for now in (first_reflect, _utc(2027, 9, 27)):
        assert retire_reason(_instruction(**row), now=now) is None, "the owner reviews these by hand"


@pytest.mark.integration
async def test_the_pass_reads_where_an_instruction_came_from(clean_db):
    from services.conversation_service import create_conversation
    from services.database import MemoryModel, async_session
    from services.memory_decay_service import decay_stale_memories

    await create_conversation("conv-owner", title="Ellis", source="user")
    await create_conversation("conv-reminder", title="Reminder", source="scheduled_event")
    written = (LATER - timedelta(days=40)).replace(tzinfo=None)
    async with async_session() as session:
        owners = MemoryModel(content="Do not cancel the lawn service unless Ellis says so", memory_type="instruction",
                           temporal_nature="temporary", origin="heard", source_type="user_stated",
                           importance=0.8, source_conversation_id="conv-owner", created_at=written)
        echo = MemoryModel(content="For the Alder wedding reminder, tell Ellis in chat only",
                           memory_type="instruction", temporal_nature="temporary", origin="heard",
                           source_type="user_stated", importance=0.8, source_conversation_id="conv-reminder",
                           created_at=written)
        session.add_all([owners, echo])
        await session.commit()
        owners_id, echo_id = owners.id, echo.id

    out = await decay_stale_memories(dry_run=False, now=LATER)

    assert out["candidates"] == 1 and out["retired"] == 1
    assert out["by_rule"] == {"instruction_horizon": 1}
    async with async_session() as session:
        rows = {m.id: m.valid_until for m in (await session.execute(
            __import__("sqlalchemy").select(MemoryModel))).scalars().all()}
    assert rows[owners_id] is None, "Ellis's own instruction stands"
    assert rows[echo_id] is not None, "the reminder echo lapses"


@pytest.mark.integration
async def test_the_pass_says_which_rows_it_retired(clean_db):
    """Only counts reached the reflect wake's ledger row (``detail.decay``),
    and every row one pass retires shares one ``valid_until``: a wrong
    forget could be found only by hunting for that timestamp. The pass now
    returns the stamp it wrote and the ids it retired, so the ledger can
    carry the stamp and a revert is one UPDATE."""
    from sqlalchemy import select

    from services.database import MemoryModel, async_session
    from services.memory_decay_service import decay_stale_memories

    now = LATER
    async with async_session() as session:
        stale = MemoryModel(content="Parker was at the gym", memory_type="context", temporal_nature="temporary",
                            source_type="inferred", importance=0.3, origin="read",
                            created_at=(now - timedelta(days=90)).replace(tzinfo=None))
        fresh = MemoryModel(content="Parker is training for a marathon", memory_type="context",
                            temporal_nature="temporary", source_type="inferred", importance=0.3, origin="read",
                            created_at=(now - timedelta(days=2)).replace(tzinfo=None))
        session.add_all([stale, fresh])
        await session.commit()
        stale_id = stale.id

    dry = await decay_stale_memories(dry_run=True, now=now)
    assert dry["retired_ids"] == [] and dry["stamp"] is None, "a dry run writes nothing"

    out = await decay_stale_memories(dry_run=False, now=now)

    assert out["retired_ids"] == [stale_id]
    async with async_session() as session:
        written = (await session.execute(select(MemoryModel.valid_until).where(MemoryModel.id == stale_id))).scalar_one()
    assert out["stamp"] == written.isoformat(), "the exact valid_until the pass wrote"


# ---------------------------------------------------------------------------
# 2026-09-27: knowledge does not hold a temporary row, and a dated
# preference can be fixed in time (memory audit, preferences, B3)
# ---------------------------------------------------------------------------
#
# 36 live rows were temporary and knowledge tier (32 of them context):
# retrieval promoted them there and the knowledge hold kept them for good —
# "For Ellis's upcoming 2026 birthday, he prefers…" (33 uses) among
# them. What a temporary row says expires whatever its tier; the tier still
# lengthens its half-life. And ``_RECURRING_RE`` counted "prefers" and
# "likes" as recurring words, so a preference naming its date ("Ellis
# prefers a low-interruption weekend on September 26–27, 2026") was never
# a dated episode.


def test_a_temporary_knowledge_row_is_not_held():
    from services.memory_decay_service import is_protected, should_retire

    row = _mem(tier="knowledge", temporal_nature="temporary", memory_type="preference",
               content="Ellis wants a low-interruption weekend", created_at=NOW - timedelta(days=900))
    assert is_protected(row, now=NOW) is False
    assert should_retire(row, now=NOW) is True
    young = _mem(tier="knowledge", temporal_nature="temporary", memory_type="preference",
                 content="Ellis wants a low-interruption weekend", created_at=NOW - timedelta(days=30))
    assert should_retire(young, now=NOW) is False, "the tier still lengthens its half-life"


@pytest.mark.parametrize("row", [
    dict(content="Biscuit gets her steroid hidden in cheese this week", origin="read"),
    dict(content="Ellis covers Rowan's rent this month", origin="heard"),
])
def test_a_sensitive_temporary_knowledge_row_is_still_held(row):
    from services.memory_decay_service import is_protected, should_retire

    held = _mem(tier="knowledge", temporal_nature="temporary", created_at=NOW - timedelta(days=900), **row)
    assert is_protected(held, now=NOW) is True
    assert should_retire(held, now=NOW) is False


def test_a_temporary_instruction_on_the_knowledge_tier_keeps_the_instruction_hold(migrated_install):
    from services.memory_decay_service import should_retire

    old = _mem(memory_type="instruction", tier="knowledge", temporal_nature="temporary", origin="heard",
               content="For the Alder wedding reminder, tell Ellis in chat only", created_at=NOW - timedelta(days=900))
    assert should_retire(old, now=NOW) is False, "written before the horizon began: the audit's"


def test_on_a_fresh_install_a_knowledge_tier_one_off_instruction_still_lapses():
    """No backlog to exempt: the knowledge tier does not hold a temporary
    instruction that is not the owner's own past its horizon."""
    from services.memory_decay_service import retire_reason

    old = _mem(memory_type="instruction", tier="knowledge", temporal_nature="temporary", origin="heard",
               content="For the Alder wedding reminder, tell Ellis in chat only", created_at=NOW - timedelta(days=900))
    assert retire_reason(old, now=NOW) == "instruction_horizon"


@pytest.mark.parametrize("content,created,expected", [
    ("Ellis prefers a low-interruption weekend on September 26–27, 2026", (2026, 9, 26), (2026, 9, 27)),
    ("Ellis likes the pasta at the Alder wedding on August 9, 2026", (2026, 8, 9), (2026, 8, 9)),
    ("For Ellis's upcoming birthday on October 3, 2026, he prefers a quiet dinner", (2026, 9, 1), None),
    ("Ellis likes tacos", (2026, 7, 10), None),
])
def test_a_dated_preference_is_an_episode(content, created, expected):
    from services.memory_decay_service import episode_date

    got = episode_date(content, created_at=_utc(*created), now=_utc(2026, 10, 27))
    assert (got.date() if got else None) == (_utc(*expected).date() if expected else None)


# A preference word still keeps a lasting row out of the episodes (review of
# B3, 2026-09-27): replayed against the live store, the only row B3 fixed in
# time was a lasting, 41-times-reinforced taste that cites one
# dated example ("…as done on September 26, 2026") — which lost its tier
# and use credit (half-life 522 → 130 days). Only a temporary row that
# names its date is an episode.
DATED_EXAMPLE = ("Ellis prefers shared household screens to stay discreet and neutral; quietly clear the "
                 "custom widget script, as done on September 26, 2026")


@pytest.mark.parametrize("nature,fixed", [("evolving", False), ("timeless", False), ("temporary", True)])
def test_a_dated_preference_is_fixed_in_time_only_when_it_is_temporary(nature, fixed):
    from services.memory_decay_service import fixed_in_time

    row = _mem(content=DATED_EXAMPLE, memory_type="preference", temporal_nature=nature,
               created_at=_utc(2026, 9, 26))
    assert fixed_in_time(row, now=_utc(2026, 10, 27)) is fixed


def test_a_lasting_dated_preference_keeps_its_knowledge_hold():
    from services.memory_decay_service import is_protected

    row = _mem(content=DATED_EXAMPLE, memory_type="preference", temporal_nature="evolving", tier="knowledge",
               created_at=_utc(2026, 9, 26))
    assert is_protected(row, now=_utc(2026, 10, 27)) is True


def test_a_dated_non_preference_is_still_an_episode_whatever_its_nature():
    from services.memory_decay_service import fixed_in_time

    row = _mem(content="On September 26, 2026, Ellis cleared the custom widget script", temporal_nature="evolving",
               created_at=_utc(2026, 9, 26))
    assert fixed_in_time(row, now=_utc(2026, 10, 27)) is True


def test_a_preference_about_reminders_is_still_no_reminder_echo():
    """``_RECURRING_RE`` lost "prefers" and "likes"; the reminder-echo test
    keeps them, because a taste about reminders is not a reminder's text."""
    from services.memory_decay_service import is_reminder_echo

    assert is_reminder_echo("Ellis prefers in-app reminders about bills over texts") is False
    assert is_reminder_echo("Ellis likes reminders for his 6 am tee times") is False
    assert is_reminder_echo("Ellis needed an in-app chat reminder to answer Parker") is True
