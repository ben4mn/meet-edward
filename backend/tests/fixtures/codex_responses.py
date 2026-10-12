"""
Captured / representative GPT-5.4 (Codex) JSON response shapes.

These exercise the JSON-parsing helper. Each entry is a raw ``response.content``
string as the LLM might return it — exactly what flows into ``extract_json_payload``.
"""

# Happy path — plain JSON array with no markup
CLEAN_ARRAY = """[
  {"content": "Ellis lives in Seattle", "memory_type": "fact", "importance": 0.8,
   "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.9}
]"""

# The bug: dict-wrapped payload. Non-greedy regex still finds `[...]` but the
# outer object structure is lost (ok here, but breaks when inner has nested arrays).
DICT_WRAPPED_MEMORIES = """{
  "memories": [
    {"content": "Ellis prefers dark mode", "memory_type": "preference", "importance": 0.5,
     "temporal_nature": "timeless", "source_type": "inferred", "confidence": 0.7}
  ]
}"""

DICT_WRAPPED_ITEMS = """{
  "items": [
    {"content": "Ellis prefers tea", "memory_type": "preference", "importance": 0.6,
     "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.8}
  ]
}"""

DICT_WRAPPED_RESULTS = """{
  "results": [
    {"content": "Ellis has a cat named Mochi", "memory_type": "fact", "importance": 0.7,
     "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.95}
  ]
}"""

# The worst case: object with an inner array that itself contains arrays.
# Non-greedy regex stops at the first ']' which is inside a nested structure,
# producing truncated/invalid JSON.
NESTED_ARRAY_IN_OBJECT = """{
  "memories": [
    {"content": "Ellis likes [Python, Rust, TypeScript]", "memory_type": "preference",
     "importance": 0.6, "temporal_nature": "timeless",
     "source_type": "user_stated", "confidence": 0.8,
     "tags": ["language", "code"]}
  ]
}"""

# Preamble text before the array
PREAMBLE_TEXT = """Here are the memories I extracted:

[
  {"content": "Ellis works on Edward", "memory_type": "fact", "importance": 0.9,
   "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 1.0}
]

Let me know if you need anything else."""

# Markdown code fence
MARKDOWN_FENCED = """```json
[
  {"content": "Ellis drinks coffee", "memory_type": "preference", "importance": 0.5,
   "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.85}
]
```"""

MARKDOWN_FENCED_PLAIN = """```
[
  {"content": "Ellis drinks tea", "memory_type": "preference", "importance": 0.5,
   "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.7}
]
```"""

EMPTY_ARRAY = "[]"

EMPTY_ARRAY_WITH_WHITESPACE = "  \n  []  \n  "

# Malformed — no recoverable JSON
MALFORMED = "This is just text, no JSON at all."

# Malformed — looks like JSON but isn't
BROKEN_JSON = "[{'content': 'bad quotes',}"

# Multiple top-level objects outside any array (GPT sometimes does this)
STREAM_OF_OBJECTS = """
{"content": "first", "memory_type": "fact", "importance": 0.5, "confidence": 0.6}
{"content": "second", "memory_type": "fact", "importance": 0.5, "confidence": 0.6}
"""
