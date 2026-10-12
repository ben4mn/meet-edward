"""Per-turn directives, tightened for a model that does not need shouting.

Audit (2026-09-04, chat moved to GPT-6 Astra): TOOL_USAGE_DIRECTIVE,
PROACTIVE_DIRECTIVE and the base persona said "act, don't hedge" three
ways (~2.9K chars per turn), PLANNING_DIRECTIVE restated the rule the
plan-tool manual already carries, and "use a tool on a 60% hunch" was
written to push a timid model — on Astra it pushes an already-proactive
model into calls that only cost subscription quota. Every rule that
changes behaviour survives in one place; only the repetition and the
quota-burning nudge go.
"""

from services.graph.streaming import compose_system_prompt, TOOL_USAGE_DIRECTIVE


def _compose():
    return compose_system_prompt(base="You are Edward.", time_context="\n\nNow.")


def test_one_tool_directive_not_two():
    out = _compose()
    assert out.count("## Tool Usage") == 1
    assert "## Proactive Tool Usage" not in out


def test_no_speculative_quota_burn_nudge():
    out = _compose()
    assert "60%" not in out
    assert "speculatively" not in out.lower()


def test_planning_rule_lives_only_in_the_plan_tool_manual():
    from services.graph.tools import get_plan_tools_description

    assert "## Planning Requirement" not in _compose()
    assert "create_plan" in get_plan_tools_description()


def test_behavioural_rules_survive_the_merge():
    d = TOOL_USAGE_DIRECTIVE
    for phrase in (
        "Would you like me to",      # no hedging offers
        "search the web",            # research current info first
        "without narrating",         # chain tools quietly
        "full permission",           # autonomy incl. messaging + scheduling
        "browser",                   # browse when search is thin
        "one line",                  # brief on dead ends
        "I can't do that because",   # decline plainly
    ):
        assert phrase in d, phrase


def test_directives_are_shorter_than_before():
    """The two old directives were 2875 chars together."""
    assert len(TOOL_USAGE_DIRECTIVE) < 2000
