"""Spoken turns skip the slow pre-LLM extras.

Measured 2026-09-05: ~2 s between the user's words and the first LLM
token went to speculative deep retrieval and the session briefing. A
voice turn cannot afford that, so it asks for the fast plan.
"""

from services.graph.streaming import _context_plan


def test_default_plan_keeps_everything():
    plan = _context_plan(fast_context=False)
    assert plan["speculative_timeout"] > 0
    assert plan["include_session_briefing"] is True


def test_fast_plan_skips_the_wait_and_the_briefing():
    plan = _context_plan(fast_context=True)
    assert plan["speculative_timeout"] == 0
    assert plan["include_session_briefing"] is False
