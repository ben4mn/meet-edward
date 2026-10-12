"""Pricing a Claude call in dollars (2026-10-10).

Under ``EDWARD_LLM_PROVIDER=anthropic`` Edward pays per token from a
prepaid balance, so every call is priced from its ``usage`` and the daily
cap (``services/llm_spend.py``) adds those dollars up. The table and the
arithmetic live in ``services/llm_pricing.py``, pure.

Usage fields, as the Messages API reports them: ``input_tokens`` is the
*uncached remainder* of the prompt; ``cache_read_input_tokens`` and
``cache_creation_input_tokens`` are the rest, the write split by TTL under
``cache_creation.ephemeral_5m_input_tokens`` / ``ephemeral_1h_input_tokens``;
``output_tokens`` includes thinking (``output_tokens_details.thinking_tokens``
says how much); ``server_tool_use.web_search_requests`` counts searches.
Prices per MTok: opus-5-5 $4/$20 (read $0.20, 5m write $5, 1h write $8);
sonnet-5-5 $2/$10 (read $0.20, 5m $2.50, 1h $4); haiku-5-5 $0.10/$0.50
(read $0.01, 5m $0.125, 1h $0.20) and, for a prompt over 100K tokens,
$0.50/$2.50 (read $0.05, 5m $0.625, 1h $1). Web search $10 per 1,000.
"""

from __future__ import annotations

import pytest

from services import llm_pricing as lp

pytestmark = pytest.mark.unit

M = 1_000_000


def test_opus_input_and_output_at_list_price():
    priced = lp.price("claude-opus-5-5", {"input_tokens": M, "output_tokens": M})
    assert priced.total == pytest.approx(4.0 + 20.0)
    assert priced.parts["input"] == pytest.approx(4.0)
    assert priced.parts["output"] == pytest.approx(20.0)
    assert priced.known is True and priced.priced_as == "claude-opus-5-5"


def test_cache_reads_and_writes_are_priced_apart_from_fresh_input():
    usage = {
        "input_tokens": 10_000,                 # uncached remainder
        "cache_read_input_tokens": 200_000,
        "cache_creation_input_tokens": 50_000,  # all 5-minute when no breakdown is given
        "output_tokens": 2_000,
    }
    priced = lp.price("claude-opus-5-5", usage)
    assert priced.parts["input"] == pytest.approx(10_000 * 4.0 / M)
    assert priced.parts["cache_read"] == pytest.approx(200_000 * 0.20 / M)
    assert priced.parts["cache_write_5m"] == pytest.approx(50_000 * 5.0 / M)
    assert priced.parts["cache_write_1h"] == 0
    assert priced.parts["output"] == pytest.approx(2_000 * 20.0 / M)
    assert priced.total == pytest.approx(0.04 + 0.04 + 0.25 + 0.04)


def test_one_hour_writes_cost_more_than_five_minute_writes():
    usage = {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 300_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 100_000, "ephemeral_1h_input_tokens": 200_000},
    }
    opus = lp.price("claude-opus-5-5", usage)
    assert opus.parts["cache_write_5m"] == pytest.approx(0.1 * 5.0)
    assert opus.parts["cache_write_1h"] == pytest.approx(0.2 * 8.0)
    sonnet = lp.price("claude-sonnet-5-5", usage)
    assert sonnet.parts["cache_write_5m"] == pytest.approx(0.1 * 2.50)
    assert sonnet.parts["cache_write_1h"] == pytest.approx(0.2 * 4.0)


def test_a_write_breakdown_short_of_the_total_prices_the_rest_at_the_default_ttl():
    usage = {"cache_creation_input_tokens": 1_000_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 400_000}}
    priced = lp.price("claude-sonnet-5-5", usage)
    assert priced.parts["cache_write_1h"] == pytest.approx(0.4 * 4.0)
    assert priced.parts["cache_write_5m"] == pytest.approx(0.6 * 2.50)


def test_sonnet_rates():
    priced = lp.price("claude-sonnet-5-5", {"input_tokens": M, "output_tokens": M, "cache_read_input_tokens": M})
    assert priced.total == pytest.approx(2.0 + 10.0 + 0.20)


def test_haiku_short_prompt_rates():
    usage = {"input_tokens": 50_000, "cache_read_input_tokens": 40_000, "cache_creation_input_tokens": 10_000,
             "output_tokens": 1_000}
    priced = lp.price("claude-haiku-5-5", usage)
    assert priced.tier == "base"
    assert priced.parts["input"] == pytest.approx(50_000 * 0.10 / M)
    assert priced.parts["cache_read"] == pytest.approx(40_000 * 0.01 / M)
    assert priced.parts["cache_write_5m"] == pytest.approx(10_000 * 0.125 / M)
    assert priced.parts["output"] == pytest.approx(1_000 * 0.50 / M)


def test_haiku_prompt_of_exactly_100k_stays_on_the_base_card():
    # input + cache read + cache write = 100,000: "100K tokens or fewer"
    usage = {"input_tokens": 60_000, "cache_read_input_tokens": 30_000, "cache_creation_input_tokens": 10_000,
             "output_tokens": 0}
    assert lp.price("claude-haiku-5-5", usage).tier == "base"


def test_haiku_prompt_over_100k_moves_every_rate_to_the_long_card():
    usage = {"input_tokens": 60_000, "cache_read_input_tokens": 30_000, "cache_creation_input_tokens": 10_001,
             "output_tokens": 2_000, "cache_creation": {"ephemeral_5m_input_tokens": 10_001}}
    priced = lp.price("claude-haiku-5-5", usage)
    assert priced.tier == "long"
    assert priced.parts["input"] == pytest.approx(60_000 * 0.50 / M)
    assert priced.parts["cache_read"] == pytest.approx(30_000 * 0.05 / M)
    assert priced.parts["cache_write_5m"] == pytest.approx(10_001 * 0.625 / M)
    assert priced.parts["output"] == pytest.approx(2_000 * 2.50 / M)


def test_haiku_long_card_one_hour_write():
    usage = {"input_tokens": 200_000, "cache_creation_input_tokens": 100_000,
             "cache_creation": {"ephemeral_1h_input_tokens": 100_000}}
    assert lp.price("claude-haiku-5-5", usage).parts["cache_write_1h"] == pytest.approx(0.1 * 1.00)


def test_web_searches_are_ten_dollars_a_thousand():
    usage = {"input_tokens": 0, "output_tokens": 0, "server_tool_use": {"web_search_requests": 3,
                                                                       "web_fetch_requests": 2}}
    priced = lp.price("claude-sonnet-5-5", usage)
    assert priced.parts["web_search"] == pytest.approx(0.03)
    assert priced.web_searches == 3
    assert priced.total == pytest.approx(0.03)


def test_thinking_is_billed_inside_output_and_only_reported_apart():
    usage = {"input_tokens": 0, "output_tokens": 10_000, "output_tokens_details": {"thinking_tokens": 7_000}}
    priced = lp.price("claude-opus-5-5", usage)
    assert priced.thinking_tokens == 7_000
    assert priced.total == pytest.approx(10_000 * 20.0 / M)   # not 17,000


def test_usage_may_be_an_sdk_object_with_nested_objects():
    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    usage = Obj(input_tokens=1_000, output_tokens=100, cache_read_input_tokens=None, cache_creation_input_tokens=0,
                cache_creation=Obj(ephemeral_5m_input_tokens=0, ephemeral_1h_input_tokens=0),
                server_tool_use=Obj(web_search_requests=1, web_fetch_requests=0))
    priced = lp.price("claude-opus-5-5", usage)
    assert priced.total == pytest.approx(1_000 * 4.0 / M + 100 * 20.0 / M + 0.01)


def test_missing_or_broken_usage_costs_nothing_and_never_raises():
    assert lp.price("claude-opus-5-5", None).total == 0
    assert lp.price("claude-opus-5-5", {}).total == 0
    assert lp.price("claude-opus-5-5", {"input_tokens": "lots", "output_tokens": -5}).total == 0
    assert lp.cost("claude-opus-5-5", {"input_tokens": M}) == pytest.approx(4.0)


def test_model_ids_are_normalised():
    for spelled in ("claude-opus-5-5", "claude-opus-5-5-20261001", "anthropic.claude-opus-5-5",
                    "claude-opus-5-5[1m]", "CLAUDE-OPUS-5-5"):
        priced = lp.price(spelled, {"input_tokens": M})
        assert priced.known and priced.priced_as == "claude-opus-5-5", spelled
        assert priced.total == pytest.approx(4.0)


def test_fallback_targets_have_their_own_rates():
    # Opus 5.5 and Sonnet 5.5 refusals may be served by Opus 5 / Sonnet 5.
    assert lp.price("claude-opus-5", {"input_tokens": M, "output_tokens": M}).total == pytest.approx(5.0 + 25.0)
    assert lp.price("claude-sonnet-5", {"input_tokens": M, "output_tokens": M}).total == pytest.approx(2.0 + 10.0)


def test_an_unknown_model_is_priced_high_not_free_and_says_so():
    priced = lp.price("claude-something-new", {"input_tokens": M, "output_tokens": M})
    assert priced.known is False
    assert priced.total >= 15.0 + 75.0           # never under the dearest row there is (Opus 4.1)
    family = lp.price("claude-sonnet-9", {"input_tokens": 100_000})
    assert family.known is False and family.total == pytest.approx(0.30)  # the dearest sonnet (4.x, $3)
    assert lp.price("claude-sonnet-9", {"input_tokens": M}).total == pytest.approx(6.0)   # and its 1M premium


# Review 2026-10-11: the family fallback under-priced real ids —
# claude-fable-5 / claude-mythos-5 read the cache at $1/MTok, not Fable
# 5.1's $0.25 (4x under), and claude-opus-4-0 / 4-1 cost $15/$75, not Opus
# 5's $5/$25 (3x under). EDWARD_CLAUDE_<PURPOSE>_MODEL accepts any claude-*
# id, so they can reach production. Every listed id has its own row now,
# and an id the table does not know is priced at its family's dearest
# rates — never under any listed member.

LISTED = {   # USD per MTok: input, output, cache read (models.md list prices)
    "claude-fable-5": (10.0, 50.0, 1.00),
    "claude-mythos-5": (10.0, 50.0, 1.00),
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-mythos-5-1": (10.0, 50.0, 0.25),
    "claude-opus-4-0": (15.0, 75.0, 1.50),
    "claude-opus-4-1": (15.0, 75.0, 1.50),
    "claude-opus-4-5": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-sonnet-4-0": (3.0, 15.0, 0.30),
    "claude-sonnet-4-5": (3.0, 15.0, 0.30),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
}


@pytest.mark.parametrize("model,rates", list(LISTED.items()))
def test_older_and_dearer_ids_are_priced_at_their_list_price(model, rates):
    inp, out, read = rates
    priced = lp.price(model, {"input_tokens": M // 10, "output_tokens": M // 10, "cache_read_input_tokens": M // 10})
    assert priced.known and priced.priced_as == model
    assert priced.total == pytest.approx((inp + out + read) / 10)


def test_fable_5_and_mythos_5_read_the_cache_at_a_dollar():
    for model in ("claude-fable-5", "claude-mythos-5", "claude-fable-5-20260801"):
        assert lp.price(model, {"cache_read_input_tokens": M}).total == pytest.approx(1.00), model


def test_opus_4_1_is_not_priced_as_opus_5():
    assert lp.price("claude-opus-4-1", {"input_tokens": M, "output_tokens": M}).total == pytest.approx(90.0)
    assert lp.price("claude-opus-4-20250514", {"input_tokens": M}).total >= 15.0     # Opus 4.0's full id


USAGES = [
    {"input_tokens": 50_000, "output_tokens": 2_000, "cache_read_input_tokens": 30_000,
     "cache_creation_input_tokens": 10_000},
    {"input_tokens": 120_000, "output_tokens": 4_000, "cache_read_input_tokens": 20_000,
     "cache_creation_input_tokens": 10_000, "cache_creation": {"ephemeral_1h_input_tokens": 10_000}},
    {"input_tokens": 230_000, "output_tokens": 8_000, "cache_read_input_tokens": 40_000},
    {"cache_creation_input_tokens": 300_000, "cache_creation": {"ephemeral_1h_input_tokens": 300_000}},
    {"cache_read_input_tokens": 900_000, "output_tokens": 1_000},
    {"output_tokens": M, "server_tool_use": {"web_search_requests": 3}},
]


@pytest.mark.parametrize("family", lp.FAMILIES)
def test_an_unknown_version_costs_at_least_every_listed_member_of_its_family(family):
    unknown = f"claude-{family}-9-9"
    members = [k for k in lp.PRICES if family in k]
    assert members, family
    for usage in USAGES:
        fallback = lp.price(unknown, usage)
        assert fallback.known is False
        for member in members:
            assert fallback.total >= lp.price(member, usage).total - 1e-12, (unknown, member, usage)


def test_an_unknown_family_costs_at_least_every_listed_model():
    for usage in USAGES:
        fallback = lp.price("claude-something-new", usage)
        for model in lp.PRICES:
            assert fallback.total >= lp.price(model, usage).total - 1e-12, (model, usage)


def test_iterations_are_summed_each_at_its_own_models_rates():
    """A server-side fallback (or compaction) itemizes every attempt under
    ``usage.iterations``; the top level covers only the attempt that
    produced the message. Each attempt bills at the rates of the model that
    ran it."""
    usage = {
        "input_tokens": 1_000, "output_tokens": 500,        # the served attempt only
        "server_tool_use": {"web_search_requests": 1},
        "iterations": [
            {"type": "message", "input_tokens": 1_000, "output_tokens": 10},
            {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 1_000, "output_tokens": 500},
        ],
    }
    priced = lp.price("claude-opus-5-5", usage)
    first = 1_000 * 4.0 / M + 10 * 20.0 / M
    second = 1_000 * 5.0 / M + 500 * 25.0 / M
    assert priced.total == pytest.approx(first + second + 0.01)
    assert priced.input_tokens == 2_000 and priced.output_tokens == 510


def test_a_fallback_iteration_without_a_model_is_priced_at_the_served_model():
    usage = {"iterations": [
        {"type": "message", "input_tokens": M, "output_tokens": 0},
        {"type": "fallback_message", "input_tokens": M, "output_tokens": 0},
    ]}
    priced = lp.price("claude-opus-5-5", usage, served_model="claude-opus-5")
    assert priced.total == pytest.approx(4.0 + 5.0)


def test_an_attempt_after_the_fallback_is_priced_at_the_served_model():
    """Once the fallback serves, the conversation stays on that model
    (sticky routing): an attempt itemized after the first
    ``fallback_message`` with no ``model`` of its own ran on the served
    model, not the one asked for — the rule ``claude_chat_model`` priced by
    before its price table moved here (merge 2026-10-11). One that names
    its model is priced at it wherever it sits."""
    usage = {"iterations": [
        {"type": "message", "input_tokens": M, "output_tokens": 0},
        {"type": "fallback_message", "input_tokens": M, "output_tokens": 0},
        {"type": "compaction", "input_tokens": M, "output_tokens": 0},
        {"type": "compaction", "model": "claude-haiku-5-5", "input_tokens": M, "output_tokens": 0},
    ]}
    priced = lp.price("claude-opus-5-5", usage, served_model="claude-opus-5")
    haiku = 0.50  # a 1M-token prompt is on Haiku 5.5's >100K card
    assert priced.total == pytest.approx(4.0 + 5.0 + 5.0 + haiku)
    # Without a served model the requested one stands in throughout.
    assert lp.price("claude-opus-5-5", usage).total == pytest.approx(4.0 + 4.0 + 4.0 + haiku)


def test_token_totals_are_reported_for_the_ledger():
    usage = {"input_tokens": 11, "cache_read_input_tokens": 22, "cache_creation_input_tokens": 33,
             "output_tokens": 44, "output_tokens_details": {"thinking_tokens": 5},
             "server_tool_use": {"web_search_requests": 2}}
    p = lp.price("claude-sonnet-5-5", usage)
    assert (p.input_tokens, p.cache_read_tokens, p.cache_write_tokens, p.output_tokens, p.thinking_tokens,
            p.web_searches) == (11, 22, 33, 44, 5, 2)


def test_without_iterations_the_top_level_is_the_served_models_attempt():
    """Sticky fallback routing serves later turns on the fallback model
    directly; the top-level usage is then that model's attempt."""
    priced = lp.price("claude-opus-5-5", {"input_tokens": M}, served_model="claude-opus-5")
    assert priced.total == pytest.approx(5.0)
    assert priced.priced_as == "claude-opus-5"
