from __future__ import annotations

import json
from pathlib import Path

from app.main import app

output = Path('openapi.json')
output.write_text(json.dumps(app.openapi(), indent=2), encoding='utf-8')
print(f'Wrote {output.resolve()}')
