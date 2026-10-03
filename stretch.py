# -*- coding: utf-8 -*-
"""
RunBeat BPM 对齐模块 —— 把你的歌变速不变调对齐到目标步频。
  - BPM 检测: librosa (onset 能量 + 自相关)
  - 变速: ffmpeg rubberband 滤镜 (保持音高不变)
用法: python runbeat.py stretch --src 歌.mp3 --bpm 172 --out aligned.wav
"""
import os
import subprocess
import numpy as np


def detect_bpm(path: str) -> float:
    """检测音频文件的全局 BPM"""
    import librosa
    y, sr = librosa.load(path, sr=22050, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    return bpm


def stretch(src: str, target_bpm: float, out: str, min_factor: float = 0.70,
            max_factor: float = 1.45) -> dict:
    """
    变速不变调对齐。自动做八度校正: 检测BPM可能为实际速度的 1/2 或 2 倍，
    在 [bpm, bpm/2, bpm*2] 中选"变速系数最接近 1.0 且在可接受范围"的候选。
    返回 {source_bpm, bpm_used, factor, clamped, out}
    """
    if not os.path.exists(src):
        raise FileNotFoundError(f"源文件不存在: {src}")
    src_bpm = detect_bpm(src)

    candidates = [src_bpm, src_bpm / 2.0, src_bpm * 2.0]
    best = None  # (candidate_bpm, factor)
    for cand in candidates:
        f = target_bpm / cand
        if min_factor <= f <= max_factor:
            if best is None or abs(f - 1.0) < abs(best[1] - 1.0):
                best = (cand, f)
    clamped = False
    if best is None:
        factor = target_bpm / src_bpm
        if factor < min_factor:
            factor, clamped = min_factor, True
        if factor > max_factor:
            factor, clamped = max_factor, True
        bpm_used = src_bpm
    else:
        bpm_used, factor = best

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
           "-filter:a", f"rubberband=tempo={factor:.6f}",
           "-ar", "44100", "-ac", "2", out]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(f"变速失败: {r.stderr.decode(errors='ignore')[:300]}")

    return {"source_bpm": round(src_bpm, 1), "bpm_used": round(bpm_used, 1),
            "factor": round(factor, 3), "clamped": clamped, "out": out}
