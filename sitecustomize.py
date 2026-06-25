import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_NLTK_DATA = PROJECT_ROOT / ".venv" / "nltk_data"

if LOCAL_NLTK_DATA.exists():
    existing = os.environ.get("NLTK_DATA", "")
    paths = [p for p in existing.split(os.pathsep) if p]
    local_path = str(LOCAL_NLTK_DATA)
    if local_path not in paths:
        os.environ["NLTK_DATA"] = os.pathsep.join([local_path] + paths)
