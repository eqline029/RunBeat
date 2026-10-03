# -*- coding: utf-8 -*-
"""打包 v1.23（修正: 排除 output/ 与用户私人计划）"""
import io, os, zipfile

SRC = r"C:\Users\品老大\Downloads\runbeat-v1.3"
OUT = r"C:\Users\品老大\Doubao\chats\2026-10-01\new-chat\runbeat-v1.23.zip"

EXCLUDE_DIRS = {".git", "data/out", "output", "screenshots", "__pycache__"}
EXCLUDE_FILES = {"plans/EQ的HIIT燃脂跑.json"}

files = []
for root, dirs, names in os.walk(SRC):
    rel = os.path.relpath(root, SRC).replace("\\", "/")
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    if rel in EXCLUDE_DIRS:
        continue
    for n in names:
        if n.startswith("_") or n.endswith(".pyc") or n == "runbeat-v1.22.zip":
            continue
        rp = (rel + "/" + n) if rel != "." else n
        if rp in EXCLUDE_FILES:
            continue
        files.append((os.path.join(root, n), rp))

files.sort(key=lambda x: x[1])
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for abs_p, arc in files:
        z.write(abs_p, arc)

log = io.open(r"C:\Users\品老大\Downloads\runbeat-v1.3\_zip_log.txt", "w", encoding="utf-8")
log.write("files: %d\n" % len(files))
for _, arc in files:
    log.write("  " + arc + "\n")
log.write("size: %.2f MB\n" % (os.path.getsize(OUT) / 1048576.0))
log.close()
