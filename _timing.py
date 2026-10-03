# -*- coding: utf-8 -*-
"""计时: detect_bpm + stretch（用用户上传的 4:30 mp3）"""
import io, os, sys, time
sys.path.insert(0, r"C:\Users\品老大\Downloads\runbeat-v1.3")
import stretch

song = r"C:\Users\品老大\Downloads\runbeat-v1.3\data\songs\f2609466a3f1.mp3"
log = io.open(r"C:\Users\品老大\Downloads\runbeat-v1.3\_timing.txt", "w", encoding="utf-8")

t0 = time.time()
bpm = stretch.detect_bpm(song)
t1 = time.time()
log.write("detect_bpm: %.1f s -> bpm=%.1f\n" % (t1 - t0, bpm))

out = r"C:\Users\品老大\Downloads\runbeat-v1.3\data\out\_timing_aligned.wav"
t2 = time.time()
try:
    r = stretch.stretch(song, 160.0, out)
    t3 = time.time()
    log.write("stretch(160bpm): %.1f s -> %s\n" % (t3 - t2, r))
except Exception as e:
    log.write("stretch FAILED: %s\n" % str(e)[:300])
log.close()
