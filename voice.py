# -*- coding: utf-8 -*-
"""
RunBeat 语音提示模块。
  - 首选: edge-tts (微软神经网络中文音色, 免费, 需联网)
  - 回退: 合成提示音 beep (上行动/下行动/中性), 断网也能用
  - 提示音频缓存到 cache_dir, 重复生成不重复合成
"""
import asyncio
import hashlib
import os
import subprocess
import numpy as np
import wave

SR = 44100

# 常用中文音色
VOICE_CANDIDATES = [
    "zh-CN-XiaoxiaoNeural",   # 女声, 温柔清晰
    "zh-CN-YunxiNeural",      # 男声, 少年感
    "zh-CN-YunjianNeural",    # 男声, 沉稳
    "zh-CN-YunyangNeural",    # 男声, 新闻感
    "zh-CN-YunxiaNeural",     # 男声, 青春
]


async def _edge_tts_save(text: str, voice: str, rate: str, out_mp3: str):
    import edge_tts
    tts = edge_tts.Communicate(text, voice, rate=rate)
    await asyncio.wait_for(tts.save(out_mp3), timeout=18.0)


def _tts_save_with_retry(text: str, voice: str, rate: str, out_mp3: str) -> bool:
    """带超时与重试的 TTS 保存，网络抖动不致命"""
    for attempt in range(3):
        try:
            asyncio.run(_edge_tts_save(text, voice, rate, out_mp3))
            if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 0:
                return True
        except Exception:
            pass
        if os.path.exists(out_mp3):  # 清掉 0 字节半成品
            try:
                os.remove(out_mp3)
            except OSError:
                pass
        print(f"  [voice] TTS 尝试 {attempt + 1}/3 失败，重试...")
    return False


def _mp3_to_wav(mp3: str, wav: str) -> bool:
    """ffmpeg 转 44.1k mono 16bit wav"""
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
         "-ar", str(SR), "-ac", "1", "-sample_fmt", "s16", wav],
        capture_output=True)
    return r.returncode == 0 and os.path.exists(wav)


def render_cue(text: str, voice: str, rate: str, cache_dir: str) -> np.ndarray:
    """
    把一条提示语渲染成 float32 mono 音频(44.1k)。
    返回 np.zeros(0) 表示失败(上层回退 beep)。
    """
    key = hashlib.md5(f"{text}|{voice}|{rate}".encode("utf-8")).hexdigest()[:16]
    os.makedirs(cache_dir, exist_ok=True)
    wav_path = os.path.join(cache_dir, f"cue_{key}.wav")

    if not os.path.exists(wav_path):
        mp3_path = os.path.join(cache_dir, f"cue_{key}.mp3")
        try:
            if _tts_save_with_retry(text, voice, rate, mp3_path):
                if not _mp3_to_wav(mp3_path, wav_path):
                    return np.zeros(0, dtype=np.float32)
            else:
                return np.zeros(0, dtype=np.float32)
        finally:
            if os.path.exists(mp3_path):
                try:
                    os.remove(mp3_path)
                except OSError:
                    pass

    try:
        with wave.open(wav_path, "rb") as w:
            n = w.getnframes()
            raw = w.readframes(n)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        return np.zeros(0, dtype=np.float32)


# ---------------- beep 回退 ----------------

def _beep(freq: float, dur: float, sr: int = SR) -> np.ndarray:
    n = int(dur * sr)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * freq * t)
    env = np.minimum(1.0, t * 600) * np.exp(-t * 3.0)
    return x * env


def beep_cue(kind: str = "neutral", sr: int = SR) -> np.ndarray:
    """上行=提速, 下行=降速, neutral=中性"""
    parts = []
    if kind == "up":
        for f in (523, 659, 784):
            parts.append(_beep(f, 0.16, sr))
            parts.append(np.zeros(int(0.08 * sr)))
    elif kind == "down":
        for f in (784, 659, 523):
            parts.append(_beep(f, 0.16, sr))
            parts.append(np.zeros(int(0.08 * sr)))
    else:
        parts.append(_beep(660, 0.25, sr))
    return np.concatenate(parts) if parts else np.zeros(0)


def list_voices() -> list:
    """列出可用的中文神经网络音色"""
    try:
        import edge_tts
        voices = asyncio.run(edge_tts.list_voices())
        zh = sorted(v["ShortName"] for v in voices
                    if v["Locale"].startswith("zh-CN") and "Neural" in v["ShortName"])
        return zh or VOICE_CANDIDATES
    except Exception:
        return VOICE_CANDIDATES
