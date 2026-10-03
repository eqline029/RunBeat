# -*- coding: utf-8 -*-
import io
p = r"C:\Users\品老大\Downloads\runbeat-v1.3\musicgen.py"
s = io.open(p, "r", encoding="utf-8").read()
old = '        "pad": {"wave": "saw", "bright": 800},\n\n    # 摇滚:'
new = '        "pad": {"wave": "saw", "bright": 800},\n        "arp": {"on": 0.0, "pattern": "updown"},\n    },\n    # 摇滚:'
assert s.count(old) == 1, "pine fix anchor: %d" % s.count(old)
s = s.replace(old, new)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("pine closed")
