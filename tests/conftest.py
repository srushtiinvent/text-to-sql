import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import make_demo_db  # noqa: E402

from t2s.config import Config, DatabaseSpec  # noqa: E402


@pytest.fixture(scope="session")
def shop_db(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("shop")
    path = d / "shop.sqlite"
    make_demo_db.write_sqlite(make_demo_db.generate(), path)
    make_demo_db.write_descriptions(d / "database_description")
    return path


@pytest.fixture
def cfg(shop_db) -> Config:
    c = Config(base_dir=ROOT)
    c.databases["shop"] = DatabaseSpec(name="shop", dialect="sqlite", path=str(shop_db),
                                       description_dir=str(shop_db.parent / "database_description"))
    c.confidence_weights_path = "does/not/exist.json"
    return c
