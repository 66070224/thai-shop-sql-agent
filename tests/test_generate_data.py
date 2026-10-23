import hashlib
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_data.py"
TABLES = ["categories", "products", "customers", "coupons", "orders", "order_items", "payments", "reviews"]


def run_generator(out: Path, seed: int) -> dict[str, str]:
    subprocess.run([sys.executable, str(SCRIPT), "--seed", str(seed), "--out", str(out)],
                   check=True, capture_output=True)
    return {t: hashlib.sha256((out / f"{t}.csv").read_bytes()).hexdigest() for t in TABLES}


def test_same_seed_gives_identical_bytes(tmp_path):
    assert run_generator(tmp_path / "a", 42) == run_generator(tmp_path / "b", 42)


def test_different_seed_changes_data(tmp_path):
    a, b = run_generator(tmp_path / "a", 42), run_generator(tmp_path / "b", 7)
    assert a["orders"] != b["orders"]
    assert a["categories"] == b["categories"]  # fixed reference table
