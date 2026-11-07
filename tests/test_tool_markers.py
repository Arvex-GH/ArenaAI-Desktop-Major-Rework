"""Tool markers: a line like "~~~write" in front of every call.

The marker is what lets the app hide a call from its first character instead
of printing a file body into the chat and tidying up afterwards. These tests
pin the two halves of that contract on the backend: markers never change what
runs and never leak into a parameter, and they never survive into stored text.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from arena import agent  # noqa: E402

WRITE_REPLY = """Here is the page.

~~~write
<tool name="write_file"><path>src/hero.js</path><content>
export const hero = 'calm';
</content></tool>
~~~

Then I will check the build."""

STEP_REPLY = """Checking first.

~~~command
<tool name="run_cmd"><cmd>npm test</cmd></tool>
~~~
Done."""


def test_marked_call_still_runs():
    calls = agent.parse_tool_calls(WRITE_REPLY)
    assert [call["name"] for call in calls] == ["write_file"]
    assert calls[0]["params"]["path"] == "src/hero.js"
    assert calls[0]["params"]["content"].strip() == "export const hero = 'calm';"


def test_markers_never_reach_a_parameter():
    calls = agent.parse_tool_calls(STEP_REPLY)
    assert calls[0]["params"]["cmd"] == "npm test"
    for value in calls[0]["params"].values():
        assert "~~~" not in str(value)


def test_marker_lines_are_stripped_from_saved_text():
    cleaned = agent.strip_tool_calls(WRITE_REPLY)
    assert "~~~" not in cleaned
    assert "<tool" not in cleaned
    assert "export const hero" not in cleaned
    assert "Here is the page." in cleaned
    assert "Then I will check the build." in cleaned


def test_a_tilde_fence_inside_written_content_is_kept():
    """A Markdown file may legitimately contain a "~~~" fence line."""
    reply = """~~~write
<tool name="write_file"><path>README.md</path><content>
# Notes
~~~js
const x = 1;
~~~
</content></tool>
~~~"""
    calls = agent.parse_tool_calls(reply)
    assert calls[0]["params"]["path"] == "README.md"
    assert "~~~js" in calls[0]["params"]["content"]
    assert "const x = 1;" in calls[0]["params"]["content"]


def test_unclosed_marker_still_parses_its_call():
    reply = """~~~command
<tool name="run_cmd"><cmd>npm run build</cmd></tool>"""
    calls = agent.parse_tool_calls(reply)
    assert calls[0]["params"]["cmd"] == "npm run build"


def test_guide_teaches_every_marker():
    guide = agent.guide("/tmp/project", "Auto", "Work inside the folder runs without asking.", False)
    for marker in ("~~~command", "~~~write", "~~~edit", "~~~read", "~~~search",
                   "~~~list", "~~~delete", "~~~plan", "~~~todos", "~~~ask", "~~~tool"):
        assert marker in guide, marker
    # The closing marker is taught too, right after an example call.
    assert "~~~\n" in guide
