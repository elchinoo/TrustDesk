"""The browser code without a browser: every module parses, and the fake-DOM checks under tests/ui pass (needs node and the sibling kit)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.mark.parametrize("js", sorted((ROOT / "src/trustdesk/static").glob("*.js")), ids=lambda p: p.name)
def test_every_ui_module_parses(js):
    r = subprocess.run([NODE, "--input-type=module", "--check"], input=js.read_text(), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not (ROOT.parent / "agentpg-kit/src/apgkit/static").is_dir(), reason="the sibling agentpg-kit checkout is not present")
@pytest.mark.parametrize("script", ["review_typing.mjs", "embeddings_typing.mjs", "validation_typing.mjs", "confirm_dialogs.mjs", "poll_default_tab.mjs", "insights_tab.mjs", "live_updates.mjs"])
def test_fake_dom_checks(script):
    r = subprocess.run([NODE, str(ROOT / "tests/ui" / script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("ok"), r.stdout + r.stderr


def test_no_native_dialogs_in_the_static_code():
    """window.confirm/alert/prompt freeze automated browsers: the shared confirmDialog/promptDialog are the only way to ask."""
    files = [*(ROOT / "src/trustdesk/static").glob("*.js"), *(ROOT / "src/trustdesk/static").glob("*.html")]
    assert files
    for f in files:
        src = "\n".join(line.split("//")[0] for line in f.read_text().splitlines())   # comments may name the banned calls
        assert not re.search(r"(?<![\w.])(window\.)?(confirm|alert|prompt)\s*\(", src.replace("confirmDialog(", "").replace("promptDialog(", "")), f"{f.name} calls a native dialog"
