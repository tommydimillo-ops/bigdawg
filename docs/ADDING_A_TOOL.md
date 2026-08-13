# How to add a new Jarvis tool

Everything about a tool — its schema, permission level, and how it's
actually called — lives in exactly one place: a `ToolSpec` registered in
`tools/schemas/*.py`. You never touch `agent/brain.py`, `agent/permissions.py`,
or `agent/executor.py` to add a tool; they all derive from the registry.

## 1. Write the underlying function

If it doesn't already exist, write the actual implementation in
`tools/<something>.py` (or reuse a function that's already there). This
is plain Python — no knowledge of the registry, schemas, or the agent loop
needed. It should return a string (what the model will see as the tool
result).

```python
# tools/example.py
def get_favorite_color():
    return "blue, apparently"
```

## 2. Add the schema + registration

Pick the most fitting existing file under `tools/schemas/` (grouped by
theme — `system.py`, `productivity.py`, `computer_use.py`, etc.) rather
than creating a new file for one tool. Add a `register(ToolSpec(...))`
call:

```python
from tools.example import get_favorite_color
from tools.registry import ToolSpec, register

register(ToolSpec(
    name="get_favorite_color",
    description="Tell the user their favorite color.",
    input_schema={"type": "object", "properties": {}, "required": []},
    permission_level=0,  # read-only -- see LEVEL_NAMES in tools/registry.py
    handler=lambda ti: get_favorite_color(),
))
```

For a tool that takes arguments, declare them in `input_schema` and read
them from the `ti` (tool_input) dict the handler receives:

```python
register(ToolSpec(
    name="set_favorite_color",
    description="Remember the user's favorite color.",
    input_schema={
        "type": "object",
        "properties": {"color": {"type": "string", "description": "The color."}},
        "required": ["color"],
    },
    permission_level=1,  # writes something, but nothing risky
    handler=lambda ti: set_favorite_color(ti["color"]),
    side_effect=True,  # see step 3
))
```

If the handler needs more than one line (a try/finally, multiple steps),
write a small named function instead of a lambda:

```python
def _handle_set_favorite_color(tool_input):
    color = tool_input["color"]
    save_it(color)
    return f"Got it — {color} it is."

register(ToolSpec(name="set_favorite_color", ..., handler=_handle_set_favorite_color))
```

## 3. Choose the permission level and flags

`permission_level` (required) — pick from `tools/registry.py`'s
`LEVEL_NAMES`:

| Level | Meaning | Example |
|---|---|---|
| 0 | read-only | `get_weather`, `recall_facts` |
| 1 | safe local action | `add_reminder`, `lock_screen` |
| 2 | modifies files/executes code | `run_python`, `computer_click` |
| 3 | external communication | `confirm_login`, `send_email` |
| 4 | financial | (none yet — reserved) |
| 5 | destructive/system | `computer_confirm_action` |

Optional flags (all default to a safe "no"):

- **`side_effect=True`** — the tool has a real-world effect that
  shouldn't silently repeat. Set this if a provider failing mid-request
  after this tool already ran should be reported to the user rather than
  quietly retried on the other provider (e.g. anything that saves,
  sends, clicks, or types).
- **`parallel_safe=True`** — only for pure reads with no shared mutable
  state. Lets the agent run several of these concurrently in one turn
  (e.g. "check my battery and the weather"). If your tool writes
  anything, or shares a resource other calls also touch (like the one
  live browser tab), leave this False.
- **`requires_live_confirmation=True`** — for a tool that *finalizes* an
  already-previewed, human-approved action (mirrors `confirm_login`/
  `send_email`/`computer_confirm_action`). This hard-blocks it from
  `source="scheduled"` runs. Only use this for the actual finalizing
  step, not the preview step.
- **`unattended_allowed=False`** — blocks the tool from `source="scheduled"`
  runs entirely, for risk broader than "needs a confirmation" (currently
  used for the whole `computer_*` family — real screen control while no
  one's watching). Rare; most tools should leave this True.

## 4. Tell the model when to use it

If the tool's own `description` isn't enough context on its own (e.g. it
needs to be picked over a similar existing tool in specific situations),
add a sentence to `agent/brain.py`'s `BASE_SYSTEM_PROMPT` — but only if
genuinely needed. Most tools don't need this; a clear `description` is
usually sufficient.

## 5. Add a test

At minimum, `tests/test_registry.py`'s existing generic tests
(`test_every_schema_has_required_fields`, `test_every_tool_has_a_valid_level`,
etc.) already cover your new tool automatically — no changes needed there.
If the tool has any non-trivial logic of its own, add a focused test for
it, following `tests/test_safety.py`'s style (exercise the real function,
not a mock, unless it needs a paid API call).

## 6. Verify

```bash
python -m unittest discover -s tests -v
python3 -c "from agent.executor import execute_task; print(execute_task('use my new tool'))"
```

Check the tool actually got picked up:

```bash
python3 -c "from tools import registry; import tools.schemas; print('get_favorite_color' in registry.all_names())"
```

That's the whole process — no other file needs to change.
