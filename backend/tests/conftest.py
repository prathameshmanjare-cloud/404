import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LLM_MODE", "replay")
os.environ["CODEFORGE_DB"] = ":memory:"   # tests never read or write the real history file


@pytest.fixture(autouse=True)
def fresh_state():
    from guard.quota import QUOTA
    from llm.reliability import BREAKERS
    from runs import RUNS
    from sandbox import EXECUTIONS
    from guard.session import SESSIONS
    SESSIONS.reset()
    RUNS.reset()
    QUOTA.reset()
    BREAKERS.reset()
    EXECUTIONS.count = 0
    yield
