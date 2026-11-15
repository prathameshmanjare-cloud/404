import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LLM_MODE", "replay")


@pytest.fixture(autouse=True)
def fresh_state():
    from guard.quota import QUOTA
    from llm.reliability import BREAKERS
    from runs import RUNS
    from sandbox import EXECUTIONS
    RUNS.reset()
    QUOTA.reset()
    BREAKERS.reset()
    EXECUTIONS.count = 0
    yield
