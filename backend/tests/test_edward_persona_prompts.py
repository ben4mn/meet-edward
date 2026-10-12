"""Edward persona prompt contract (2026-07-07 prompt overhaul).

Audit finding: Edward's live voice was his blandest prompt — the corporate
"Enhanced Digital Workflow Assistant for Routine Decisions" DB default —
while a genuinely Jarvis-grade prompt sat dead-shadowed in models/schemas.py
and a well-written persona_service.py had zero callers. The triage
classifier pulled hard toward passivity ("Be conservative: most messages
should be DISMISS or NOTE", stated twice, no counterweight), the heartbeat
inner mind had no persona at all, and the briefing voice differed by
surface.

Direction from Ellis: Jarvis to Iron Man — initiative with purpose, dry
competence, anticipation, silence over filler.

The legacy triage classifier and the heartbeat inner mind have since been
removed (the heartbeat now hands each batch to the mind), so their prompt
contracts went with them.
"""

# ---------------------------------------------------------------------------
# S1 — system prompt code default (live DB row is patched separately)
# ---------------------------------------------------------------------------


def test_system_prompt_default_is_the_generic_owner_voice():
    from models.schemas import DEFAULT_SYSTEM_PROMPT
    from services.database import SettingsModel

    default = SettingsModel.__table__.columns["system_prompt"].default.arg
    # one source of truth: the column default is the schema's default
    assert default == DEFAULT_SYSTEM_PROMPT
    assert "Routine Decisions" not in default, "corporate acronym prompt must be gone"
    # the owner is named through the {OWNER} placeholder, never a literal
    assert "{OWNER}" in default
    assert "dry, warm, first person, brief" in default
    assert "act first" in default.lower()
    assert "schedule it yourself without being asked" in default
    # persona_service's best line is absorbed here
    assert "fawn" in default or "moralize" in default


# ---------------------------------------------------------------------------
# S4 — the morning is a wake, not a briefing template (Phase 7)
# ---------------------------------------------------------------------------


def test_morning_briefing_template_is_gone():
    from pathlib import Path

    assert not (Path(__file__).resolve().parents[1] / "services" / "proactive" / "morning_briefing.py").exists()
    from services.mind_service import MIND_SYSTEM_PROMPT

    assert "dry, warm, specific, brief" in MIND_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# S5 — the mind: leaving it alone is a real outcome, and so is quiet initiative
# ---------------------------------------------------------------------------


def test_mind_prompt_encourages_quiet_initiative():
    from services.mind_service import MIND_SYSTEM_PROMPT

    low = MIND_SYSTEM_PROMPT.lower()
    assert "silence is a feature" in low  # keep
    assert "leave it" in low and "real outcome" in low
    assert "wake_me" in low and "intention" in low


# ---------------------------------------------------------------------------
# S6 — dead persona_service is gone (its voice lives in S1/S2 now)
# ---------------------------------------------------------------------------


def test_persona_service_is_deleted():
    import importlib.util

    assert importlib.util.find_spec("services.persona_service") is None
