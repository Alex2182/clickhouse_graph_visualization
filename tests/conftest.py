import sys
from pathlib import Path

# Make the repo root importable when invoking pytest from the repo root or tests/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
