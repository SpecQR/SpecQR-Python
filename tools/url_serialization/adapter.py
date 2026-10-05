"""Public Python GS1 adapter; the same independently pinned baseline serializer."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [sys.argv[1], str(ROOT/'tests')]
from test_gs1 import _evaluate
for line in sys.stdin:
    print(json.dumps(_evaluate(json.loads(line)), ensure_ascii=True, separators=(',', ':')))
