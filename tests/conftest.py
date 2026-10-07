"""The one place that lets an agent, or a check, run on this machine.

The suite has no Docker. Its harnesses and CLIs are local scripts written to
stand in for real ones, and its checks run in temporary directories. Nothing
under `factory/` may set this; `test_no_agent_runs_on_this_machine` reads the
source to be sure. See `factory/isolation.py`.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from factory import config as config_module
from factory import isolation

isolation.TEST_SUITE_ON_HOST = True

ROOT = Path(__file__).resolve().parent.parent

#: A key that authenticates nothing. The harness refuses to start without one,
#: and a test that launches a stand-in harness needs one to get that far.
TEST_KEY = "sk-test-not-a-real-key"


@pytest.fixture(autouse=True)
def _suite_home(tmp_path_factory, monkeypatch):
    """Every test runs in a directory of its own, configured as documented.

    The config is read from `factory.yaml` in the working directory, and the
    credentials file sits beside it -- so a suite run from a checkout read
    whatever config and key the person running it happened to have, and failed
    on a fresh clone that has neither. Each test now starts in a fresh
    directory holding a copy of `factory.example.yaml` as its `factory.yaml`
    and a credentials file whose key authenticates nothing. The default
    evidence store, `.factory`, lands there too, never in the checkout.

    A config loaded from the repository by name (`factory.example.yaml`, as the
    fixtures in helpers.py do) has its credentials file redirected to the same
    stand-in, so nothing reads or writes the real one. The variable a
    deployment would set instead is cleared for the duration.
    """
    home = tmp_path_factory.mktemp("suite-home")
    shutil.copyfile(ROOT / "factory.example.yaml", home / "factory.yaml")
    stand_in = home / config_module.CREDENTIALS_FILE
    stand_in.write_text(json.dumps({"api_key": TEST_KEY}) + "\n", encoding="utf-8")
    os.chmod(stand_in, 0o600)
    monkeypatch.chdir(home)

    real = ROOT / config_module.CREDENTIALS_FILE
    resolve = config_module.credentials_path

    def credentials_path(config):
        path = resolve(config)
        return stand_in if path == real else path

    monkeypatch.setattr(config_module, "credentials_path", credentials_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
