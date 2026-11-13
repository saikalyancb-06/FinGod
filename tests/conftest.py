import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Isolated throwaway databases for tests (inside the project folder).
_tmp = Path(tempfile.mkdtemp(prefix="test_stores_", dir=ROOT / "data"))
os.environ["FINLENS_DATABASE_URL"] = f"sqlite:///{(_tmp / 'finlens_test.db').as_posix()}"
os.environ["MULE_DATABASE_URL"] = f"sqlite:///{(_tmp / 'mule_test.db').as_posix()}"
# Offline by default so tests are deterministic; set FINLENS_TEST_LIVE=1 to use real keys.
if os.environ.get("FINLENS_TEST_LIVE") != "1":
    os.environ["GROQ_API_KEY"] = ""
    os.environ["SERP_API"] = ""
    os.environ["SERPAPI_KEY"] = ""
    os.environ["SARVAM_API_KEY"] = ""


def _cleanup_test_stores():
    # SQLite files can still be open at interpreter exit on Windows; ignore what can't be removed yet
    import shutil
    for d in (ROOT / "data").glob("test_stores_*"):
        shutil.rmtree(d, ignore_errors=True)


import atexit  # noqa: E402

atexit.register(_cleanup_test_stores)
