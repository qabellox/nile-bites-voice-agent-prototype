"""Shared fixtures. The real dataset and real config are used everywhere.

No mocks in the deterministic layer: if a test passes, the shipped database
produced the answer. Only the LLM, ASR and TTS boundaries are substituted, and
those are the components a real deployment is allowed to vary.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from nbva.config.loader import load_config  # noqa: E402
from nbva.db.orders import OrderStore  # noqa: E402
from nbva.db.repository import RestaurantRepository  # noqa: E402
from nbva.db.schema import RestaurantData  # noqa: E402

DB_PATH = ROOT / "data" / "restaurant" / "nile_bites.json"
TASKS_PATH = ROOT / "data" / "tasks" / "tasks.yaml"

# A weekday with no weekday-limited promotion, so expected money is stable.
WEDNESDAY = "wednesday"


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def config():
    # load_config resolves the project root itself; extra_files are overlays.
    return load_config()


@pytest.fixture(scope="session")
def restaurant_data() -> RestaurantData:
    return RestaurantData.model_validate_json(DB_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def repo(restaurant_data) -> RestaurantRepository:
    return RestaurantRepository(restaurant_data)


@pytest.fixture
def orders(repo) -> OrderStore:
    return OrderStore(repo, "test-session")


@pytest.fixture(scope="session")
def tasks():
    from nbva.research.taskdefs import load_tasks

    return load_tasks(TASKS_PATH)
