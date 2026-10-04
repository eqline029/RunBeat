# -*- coding: utf-8 -*-
"""
RunBeat BPM 对齐模块 —— 把你的歌变速不变调对齐到目标步频。
  - BPM 检测: librosa (onset 能量 + 自相关)
  - 变速: ffmpeg rubberband 滤镜 (保持音高不变)
用法: python runbeat.py stretch --src 歌.mp3 --bpm 172 --out aligned.wav
"""
import os
import subprocess
import tempfile
import numpy as np


def _librosa_load_safe(path: str, sr: int):
    """
    安全加载音频: 部分 mp3 的 ID3v2 标签含 UTF-16 文本帧,
    libsndfile 解析会抛 "Unspecified internal error"(如《七里香》),
    此时用 ffmpeg 兜底解码为 wav 再加载(ffmpeg 能正常读取该文件)。
    """
    import librosa
    try:
        return librosa.load(path, sr=sr, mono=True)
    except Exception:
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                   "-ac", "1", "-ar", str(sr), tmp]
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(f"音频解码失败(ffmpeg): {r.stderr.decode(errors='ignore')[:200]}")
            return librosa.load(tmp, sr=sr, mono=True)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def detect_bpm(path: str) -> float:
    """检测音频文件的全局 BPM (librosa 读不动时自动 ffmpeg 兜底)"""
    import librosa
    y, sr = _librosa_load_safe(path, 22050)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    return bpm



# ---------------- 拍段智能对齐(C模式) ----------------

def detect_beats(path: str, sr: int = 44100):
    """返回 (audio, sample_rate, source_bpm, beat_times秒)。无清晰律动时 beats 数量会很少"""
    import librosa
    y, sr = _librosa_load_safe(path, sr)
    tempo, bf = librosa.beat.beat_track(y=y, sr=sr)
    beats = librosa.frames_to_time(bf, sr=sr)
    return y, sr, float(np.atleast_1d(tempo)[0]), beats


def _write_wav16(path: str, x: np.ndarray, sr: int):
    import wave
    x = np.asarray(x, dtype=np.float32)
    peak = np.max(np.abs(x)) or 1.0
    x = x / peak * 0.98
    wf = wave.open(path, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sr)
    wf.writeframes((x * 32767).astype("int16").tobytes())
    wf.close()


def beat_sync_align(src: str, target_bpm: float, out: str,
                    seg_beats: int = 8, max_micro: float = 0.03) -> dict:
    """
    拍段智能对齐: 歌曲整体速度保持接近原速(每段±3%内微调, 保调不变速),
    以 8 拍(两小节)为组做节拍网格对齐, 累积偏差用周期性跳拍/补拍吸收。
    适合歌曲BPM与目标BPM差距 ±30% 内的曲目; 差距过大时听感退化为"原速+节拍器"。
    返回 {"source_bpm", "beats", "factor_avg", "clamped", "out"}
    """
    import librosa
    y, sr, src_bpm, beats = detect_beats(src)
    if len(beats) < 8:
        raise RuntimeError("未检测到清晰律动(拍点不足8个)，请改用「原速背景」模式")
    interval = 60.0 / target_bpm
    total_src = len(y) / sr
    n_groups = len(beats) // seg_beats
    if n_groups < 1:
        raise RuntimeError("拍点不足一组(8拍)，请改用「原速背景」模式")

    parts = []
    acc = 0.0
    n_micro = 0
    factors = []
    for g in range(n_groups):
        s0 = beats[g * seg_beats]
        s1 = beats[(g + 1) * seg_beats] if g < n_groups - 1 else min(beats[-1], total_src)
        i0 = int(s0 * sr)
        i1 = int(s1 * sr)
        seg = y[i0:i1]
        if len(seg) < int(0.05 * sr):
            continue
        src_len = len(seg) / sr
        target_len = seg_beats * interval
        factor = target_len / src_len
        # 组级微调: clamp 到 ±3% 内(保调, 听感接近原速)
        f2 = min(max(factor, 1.0 - max_micro), 1.0 + max_micro)
        factors.append(f2)
        if abs(f2 - 1.0) >= 0.003:
            seg2 = librosa.effects.time_stretch(seg, rate=f2)
            n_micro += 1
        else:
            seg2 = seg
        parts.append(seg2)
        acc += (len(seg2) / sr) - seg_beats * interval
        # 累积偏差超过半拍 -> 插入极短空隙(补拍)吸收, 或标记跳拍由下一组微调吸收
        if acc > interval * 0.5:
            parts.append(np.zeros(int(interval * 0.06 * sr), dtype=np.float32))
            acc -= interval * 0.5
        elif acc < -interval * 0.5:
            acc += interval * 0.5  # 下一组起点自然前移 = 跳拍效果

    if not parts:
        raise RuntimeError("对齐后为空, 请改用「原速背景」模式")
    aligned = np.concatenate(parts)
    _write_wav16(out, aligned, sr)
    avg = float(np.mean(factors)) if factors else 1.0
    return {"source_bpm": round(src_bpm, 1), "beats": int(len(beats)),
            "factor_avg": round(avg, 3), "micro_segments": n_micro, "out": out}

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
