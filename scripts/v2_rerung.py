"""Put every ladder bullet of a v2 pool on the rung its printed lines fill.

    uv run python scripts/v2_rerung.py report <pool.json>
    uv run python scripts/v2_rerung.py edit <pool.json>

A line holds 96 characters. A bullet that fills 1 to 3 lines other than its rung moves, up or down,
to the rung it fills, as the first free wording there (c, d, ...). One over 3 lines is listed and
stays. ``report`` lists the moves; ``edit`` also saves the file.
"""

import sys
from pathlib import Path

from pipeline_v2 import Pool
from pipeline_v2.pool_store import lines, pool_dump

mode, path = sys.argv[1], Path(sys.argv[2])
pool = Pool.model_validate_json(path.read_text(encoding="utf-8"))
moved = 0
for block in pool.experiences:
    for s in block.stories:
        for r in sorted(s.ladder, key=lambda r: (r.rung, r.wording)):
            n = lines(r.text)
            if n > 3:
                print(f"{r.id}: {len(r.text)} characters, over 3 lines: stays")
            elif n != r.rung:
                taken = {(x.rung, x.wording) for x in s.ladder}
                wording = next(w for w in "abcdef" if (n, w) not in taken)
                new = f"{s.story}.r{n}.{wording}"
                print(f"{r.id} -> {new}: {len(r.text)} characters, {n} line(s)")
                r.id, r.rung, r.wording = new, n, wording
                moved += 1
        s.ladder.sort(key=lambda r: (r.rung, r.wording))
print(f"{moved} bullet(s) moved")
if mode == "edit" and moved:
    path.write_text(pool_dump(pool), encoding="utf-8")
    print(f"wrote {path}")
