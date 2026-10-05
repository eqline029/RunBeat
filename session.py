# -*- coding: utf-8 -*-
"""
RunBeat 会话构建模块 —— 计划 -> 音频。
- 合成音乐阶段: musicgen.render_phase
- 歌曲背景阶段: 歌曲变速对齐到目标BPM -> 循环填充阶段时长 -> 可选节拍器叠加
- 阶段拼接/语音提示避让混音/母带/导出 与 CLI、Web 界面共用
"""
import json
import os
import subprocess
import time
import wave

import numpy as np

import musicgen
import stretch
import voice
from musicgen import SR

HERE = os.path.dirname(os.path.abspath(__file__))

# BPM 模块级缓存: 同一文件只检测一次(4-5分钟音频检测约20秒)
_BPM_CACHE = {}


def _bpm_for(path: str) -> float:
    if path not in _BPM_CACHE:
        _BPM_CACHE[path] = stretch.detect_bpm(path)
    return _BPM_CACHE[path]


# ---------------- 计划校验 ----------------

def load_plan(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_plan(plan: dict) -> list:
    errs = []
    phases = plan.get("phases")
    if not phases or not isinstance(phases, list):
        return ["phases 必须是非空数组"]
    for i, p in enumerate(phases):
        name = p.get("name", f"阶段{i + 1}")
        if not (0 < p.get("duration_min", 0)):
            errs.append(f"[{name}] duration_min 必须 > 0")
        cad = p.get("cadence")
        if not cad or not (110 <= cad <= 230):
            errs.append(f"[{name}] cadence(步频) 应在 110-230 之间")
        if "music_bpm" in p and p["music_bpm"] and not (40 <= p["music_bpm"] <= 220):
            errs.append(f"[{name}] music_bpm 应在 40-220 之间")
        if p.get("pre_cue"):
            lead = p.get("pre_cue_lead", 15)
            if lead <= 0 or lead >= p["duration_min"] * 60 - 3:
                errs.append(f"[{name}] pre_cue_lead 需在 (0, 时长-3s) 内")
        if p.get("source") == "song" and not p.get("song_path") and not p.get("song_id"):
            errs.append(f"[{name}] 歌曲模式缺少歌曲(song_path/song_id)")
    ratio = plan.get("tempo_ratio", 0.5)
    if not (0.25 <= ratio <= 2.0):
        errs.append(f"tempo_ratio 应在 0.25-2.0 之间(0.5=每拍两步)")
    return errs


# ---------------- 歌曲背景层 ----------------

def _decode_wav(src: str, out: str, sr: int = SR):
    """ffmpeg 解码任意音频 -> 44.1k 单声道 wav(原速背景/智能对齐共用)"""
    import subprocess
    cmd = ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", str(sr), out]
    subprocess.run(cmd, check=True, capture_output=True)


def _load_wav_mono(path: str, sr: int = SR) -> np.ndarray:
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x


def _fit_to_duration(x: np.ndarray, dur_s: float, sr: int = SR,
                     loop_xfade: float = 0.8) -> np.ndarray:
    """把音频填充/裁剪到指定时长。
    - 歌比阶段长: 裁剪(不做末尾淡出, 由阶段拼接的3ms去咔哒统一防爆音, 保证刚性衔接)
    - 歌比阶段短: 循环填充。
      loop_xfade<=0 -> 刚性循环: 歌曲(已对齐节拍网格)尾部边界直接接到头部起点,
        仅3ms去咔哒, 节拍零断崖(用于变速/智能对齐模式, 歌内每拍都在目标网格上);
      loop_xfade>0  -> 交叉淡化循环: 歌曲未对齐网格(原速背景), 用交叉淡化平滑接缝。
    """
    target = int(dur_s * sr)
    if len(x) >= target:
        out = x[:target].copy()
        return out
    if loop_xfade <= 0:
        # 刚性循环: 尾部边界 -> 头部起点 直接拼接, 仅3ms去咔哒消除数字咔哒声
        declick = min(int(0.003 * sr), 2048)
        dc = np.linspace(0.0, 1.0, declick, dtype=np.float32) if declick > 0 else None
        out = x.copy()
        while len(out) < target:
            nxt = x.copy()
            if dc is not None:
                out[-declick:] *= dc[::-1]
                nxt[:declick] *= dc
            out = np.concatenate([out, nxt])
        out = out[:target]
        return out
    cf = int(loop_xfade * sr)
    if cf >= len(x):
        cf = len(x) // 2
    out = x.copy()
    while len(out) < target:
        nxt = x.copy()
        if cf > 0:
            ramp = np.linspace(0.0, 1.0, cf)
            out[-cf:] = out[-cf:] * (1.0 - ramp) + nxt[:cf] * ramp
            out = np.concatenate([out, nxt[cf:]])
        else:
            out = np.concatenate([out, nxt])
    out = out[:target]
    return out


def _metronome_ticks(n: int, bpm: float, gain: float, sr: int = SR) -> np.ndarray:
    """四分音符节拍器点击层(叠加在歌曲上增强踩点)"""
    if gain <= 0:
        return np.zeros(0, dtype=np.float32)
    beat = 60.0 / bpm
    tick_n = int(0.05 * sr)
    t = np.arange(tick_n) / sr
    tick = (np.sin(2 * np.pi * 1200 * t) + 0.5 * np.sin(2 * np.pi * 2400 * t)) \
        * np.exp(-t * 90.0) * gain
    out = np.zeros(n, dtype=np.float32)
    i = 0
    while i < n:
        j = min(n, i + tick_n)
        out[i:j] += tick[: j - i]
        i += int(beat * sr)
    return out


def render_song_phase(phase: dict, song_path: str, song_bpm: float,
                      tempo_ratio: float, work_dir: str,
                      metronome_gain: float = 0.18, sr: int = SR,
                      mode: str = "stretch"):
    """
    歌曲背景阶段, mode 三选一:
      stretch  = 变速对齐(整曲 rubberband, 听感最"准"但改变原速)
      original = 原速背景(完全不变速, 节拍由节拍器叠加负责, 歌是氛围底)
      smart    = 拍段智能对齐(整体保原速, 8拍组级±3%微调+跳/补拍吸收, DJ手法)
    返回 (audio float32 mono, warning str|None)
    """
    cadence = float(phase["cadence"])
    target_bpm = float(phase.get("music_bpm") or cadence * tempo_ratio)
    if not (40 <= target_bpm <= 220):
        raise ValueError(f"音乐BPM超出合理范围(40-220): {target_bpm:.1f}")

    _ALIGN_DIR = os.path.join(HERE, "output", "_aligned")
    os.makedirs(_ALIGN_DIR, exist_ok=True)
    _tag = os.path.splitext(os.path.basename(song_path))[0][:24]
    warning = None

    if mode == "original":
        # 原速背景: 不检测BPM、不变速; 仅解码 -> 循环填充 -> 节拍器叠加(踩点由节拍器负责)
        dec = os.path.join(_ALIGN_DIR, f"{_tag}_orig.wav")
        if not (os.path.exists(dec) and os.path.getsize(dec) >= 1024):
            _decode_wav(song_path, dec)
        x = _load_wav_mono(dec, sr)
    elif mode == "smart":
        # 拍段智能对齐: 整体保原速(每段±3%内微调), 8拍组级网格对齐 + 跳/补拍吸收累积偏差
        aligned = os.path.join(_ALIGN_DIR, f"{_tag}_smart_{target_bpm:.0f}.wav")
        if os.path.exists(aligned) and os.path.getsize(aligned) >= 1024:
            info = {"source_bpm": song_bpm, "factor": 1.0}
        else:
            try:
                info = stretch.beat_sync_align(song_path, target_bpm, aligned)
            except Exception as e:
                raise RuntimeError(f"智能对齐失败: {e}")
        if info["source_bpm"] and abs(info["source_bpm"] - target_bpm) > 0.35 * target_bpm:
            warning = (f"智能对齐: 歌曲BPM {info['source_bpm']:.0f} 与目标 {target_bpm:.0f} 差距较大，"
                       f"对齐效果有限，建议改用「原速背景」模式")
        x = _load_wav_mono(aligned, sr)
    else:
        # 变速对齐(默认): 整曲 rubberband 保调变速
        aligned = os.path.join(_ALIGN_DIR, f"{_tag}_{target_bpm:.0f}.wav")
        if os.path.exists(aligned) and os.path.getsize(aligned) >= 1024:
            info = {"source_bpm": song_bpm, "bpm_used": song_bpm, "factor": 1.0, "clamped": False}
        else:
            try:
                info = stretch.stretch(song_path, target_bpm, aligned)
            except Exception as e:
                raise RuntimeError(f"歌曲变速失败: {e}")
        if info["clamped"]:
            warning = (f"歌曲「{os.path.basename(song_path)}」BPM {info['source_bpm']} 距目标 {target_bpm:.0f} 过远，"
                       f"变速系数被限制在 {info['factor']}（建议换更接近的歌曲，效果更好）")
        elif info["bpm_used"] != info["source_bpm"]:
            warning = f"歌曲「{os.path.basename(song_path)}」已做八度校正: 检测 {info['source_bpm']} → 按 {info['bpm_used']} 对齐"
        x = _load_wav_mono(aligned, sr)
    dur_s = float(phase["duration_min"]) * 60.0
    # 对齐模式(smart/stretch)歌曲每拍都在目标网格上 -> 刚性循环(节拍零断崖);
    # 原速背景(original)未对齐网格 -> 交叉淡化平滑接缝
    loop_xfade = 0.0 if mode != "original" else 0.8
    out = _fit_to_duration(x, dur_s, sr, loop_xfade=loop_xfade)
    # 节拍器作为独立层: 歌曲先归一化, 再按绝对增益叠加(调整立竿见影, 不再被归一化稀释)
    peak = np.max(np.abs(out)) or 1.0
    out = out * (0.85 / peak)
    if metronome_gain > 0:
        ticks = _metronome_ticks(len(out), target_bpm, 0.5 * metronome_gain, sr)
        out = out + ticks
    peak = np.max(np.abs(out)) or 1.0
    if peak > 1.0:
        out = out * (1.0 / peak)
    return out.astype(np.float32), warning


# ---------------- 语音提示混音 ----------------

def _apply_cue(music: np.ndarray, t_cue: float, cue: np.ndarray,
               duck_db: float, sr: int = SR) -> int:
    """在 music 的 t_cue 秒处叠加提示, 期间压低音乐。返回是否成功放置"""
    if t_cue < 0 or len(cue) == 0:
        return 0
    pre = 0.10
    i0 = int((t_cue - pre) * sr)
    i1 = min(len(music), int((t_cue - pre + len(cue) / sr + 0.25) * sr))
    if i0 >= len(music):
        return 0
    if i0 < 0:
        i0 = 0
    duck = float(10 ** (duck_db / 20.0))
    ramp = int(0.05 * sr)
    seg = music[i0:i1].copy()
    n_seg = len(seg)
    g = np.ones(n_seg, dtype=np.float32) * duck
    g[:ramp] = np.linspace(1.0, duck, min(ramp, n_seg))
    g[-ramp:] = np.linspace(duck, 1.0, min(ramp, n_seg))
    music[i0:i1] = seg * g
    j0 = int(t_cue * sr)
    j1 = min(len(music), j0 + len(cue))
    if j0 < len(music):
        music[j0:j1] += cue[: j1 - j0] * 0.92
        return 1
    return 0


# ---------------- 会话构建 ----------------

def build_session(plan: dict, seed: int = 7, cache_dir: str = None,
                  cues_on: bool = True, out_wav: str = None,
                  songs_dir: str = None, song_registry: dict = None,
                  max_phase_sec: float = None) -> dict:
    """
    完整构建一次训练音频。
    - 阶段 source='song': 使用歌曲背景(需 song_path 或 song_registry[song_id]['file'] + song_bpm)
    - max_phase_sec: 截断阶段渲染时长(用于试听)
    """
    t0 = time.time()
    ratio = plan.get("tempo_ratio", 0.5)
    phases = plan["phases"]
    crossfade = float(plan.get("crossfade_sec", 2.0))
    duck_db = float(plan.get("duck_db", -7))
    cue_lead = float(plan.get("cue_lead_sec", 0.8))
    voice_name = plan.get("voice", "zh-CN-YunxiNeural")
    voice_rate = plan.get("voice_rate", "+0%")
    work_dir = cache_dir or os.path.join(HERE, "output", "_work")

    # 计划级全局歌曲: 贯穿所有阶段的统一背景音乐(优先于阶段级设置)
    global_song_path = None
    global_song_bpm = None
    gsid = plan.get("song_id")
    gsp = plan.get("song_path")
    if gsp:
        global_song_path = gsp
    elif gsid and song_registry:
        global_song_path = song_registry.get(gsid, {}).get("file")
    if global_song_path:
        global_song_bpm = plan.get("song_bpm")
        if not global_song_bpm and gsid and song_registry:
            global_song_bpm = song_registry.get(gsid, {}).get("bpm")

    sections, section_durs, warnings = [], [], []
    phase_bpms = []
    for i, p in enumerate(phases):
        dur_s = float(p["duration_min"]) * 60.0
        if max_phase_sec:
            dur_s = min(dur_s, max_phase_sec)
        p_eff = dict(p)
        p_eff["duration_min"] = dur_s / 60.0
        if not p_eff.get("style"):
            p_eff["style"] = plan.get("music_style") or "default"
        phase_bpms.append(float(p.get("music_bpm") or float(p.get("cadence")) * ratio))

        if global_song_path:
            song_path = global_song_path
            song_bpm = global_song_bpm or _bpm_for(song_path)
            mg = float(p.get("metronome_gain", plan.get("metronome_gain", 0.18)))
            smode = plan.get("song_mode", "stretch")
            sec, warn = render_song_phase(p_eff, song_path, song_bpm, ratio,
                                          work_dir, metronome_gain=mg, mode=smode)
            if warn:
                warnings.append(f"阶段[{p.get('name')}] {warn}")
        elif p.get("source") == "song":
            song_path = p.get("song_path")
            if not song_path and p.get("song_id") and song_registry:
                song_path = song_registry.get(p["song_id"], {}).get("file")
            if not song_path:
                raise ValueError(f"阶段[{p.get('name')}] 缺少歌曲文件")
            song_bpm = p.get("song_bpm") or (
                song_registry.get(p["song_id"], {}).get("bpm") if p.get("song_id") else None)
            if not song_bpm:
                song_bpm = _bpm_for(song_path)
            mg = float(p.get("metronome_gain", plan.get("metronome_gain", 0.18)))
            smode = plan.get("song_mode", "stretch")
            sec, warn = render_song_phase(p_eff, song_path, song_bpm, ratio,
                                          work_dir, metronome_gain=mg, mode=smode)
            if warn:
                warnings.append(f"阶段[{p.get('name')}] {warn}")
        else:
            sec = musicgen.render_phase(
                p_eff, seed=seed + i, tempo_ratio=ratio,
                metronome_gain=float(plan.get("metronome_gain", 0.18)))
        sections.append(sec)
        section_durs.append(len(sec) / SR)
        src = "歌曲(全局)" if global_song_path else ("歌曲" if p.get("source") == "song" else "合成")
        print(f"      阶段{i + 1} {p.get('name','?')}: {p_eff['duration_min']:.2f}min @ "
              f"步频{p.get('cadence')} 音乐BPM "
              f"{p.get('music_bpm') or round(float(p.get('cadence')) * ratio, 1)} ({src})")

    print("[2/4] 阶段拼接 + 刚性衔接(末拍->首拍) ...")

    # 刚性拼接: 上一阶段最后一个节拍与下一阶段第一个节拍直接对拍衔接,
    # 不做交叉淡化、不插渐变 —— 跑者在提示音后瞬间切换到新步频.
    # 仅做 3ms 防爆音(人耳不可感知), 消除硬切产生的数字咔哒声.
    declick = min(int(0.003 * SR), 2048)
    dc = np.linspace(0.0, 1.0, declick, dtype=np.float32) if declick > 0 else None

    def _guard(x):
        if dc is not None:
            x = x.copy()
            x[:declick] *= dc
            x[-declick:] *= dc[::-1]
        return x

    starts = []
    parts = []
    _cur = 0.0
    for _sec in sections:
        starts.append(_cur)
        _g = _guard(_sec.astype(np.float32))
        parts.append(_g)
        _cur += len(_g) / SR
    music = np.concatenate(parts)

    events = []
    total_s = sum(section_durs)
    for i, p in enumerate(phases):
        start_i = starts[i]
        end_i = start_i + section_durs[i]
        if cues_on and p.get("cue"):
            kind = None
            if i > 0:
                kind = "up" if p["cadence"] > phases[i - 1]["cadence"] \
                    else ("down" if p["cadence"] < phases[i - 1]["cadence"] else "neutral")
            events.append((start_i + cue_lead, p["cue"], kind))
        if cues_on and p.get("pre_cue"):
            lead = float(p.get("pre_cue_lead", 15))
            t_pre = end_i - lead
            t_min = start_i + cue_lead + 4.0      # 至少与本阶段开始提示拉开 4s
            if t_pre < t_min:
                t_pre = t_min
            if t_pre < end_i - 1.0 and t_pre < total_s - 1.0:  # 离开阶段末尾/音频末尾至少 1s
                events.append((t_pre, p["pre_cue"], None))

    print(f"[3/4] 合成 {len(events)} 条语音提示 (voice={voice_name}) ...")
    os.makedirs(cache_dir or work_dir, exist_ok=True)
    placed = 0
    prev_end = -1.0
    music_len = len(music) / SR
    for t_cue, text, kind in sorted(events):
        cue = voice.render_cue(text, voice_name, voice_rate, cache_dir or work_dir)
        if len(cue) == 0:
            cue = voice.beep_cue(kind or "neutral")
        cue = cue / (np.max(np.abs(cue)) or 1.0)
        cue_len = len(cue) / SR
        if t_cue < prev_end + 0.35:              # 与前一条语音重叠/过近 -> 顺延
            t_cue = prev_end + 0.35
        if t_cue + cue_len > music_len + 0.05:
            # 语音放不下(常见于最后一阶段的“完成前提醒”): 整体前移, 宁早勿缺,
            # 保证语音完整播完; 前移后仍与上一条冲突或越界, 才放弃该条
            t_cue = music_len - cue_len - 0.05
            if t_cue < prev_end + 0.35 or t_cue < 0:
                continue
        if _apply_cue(music, t_cue, cue, duck_db):
            placed += 1
            prev_end = t_cue + cue_len

    print("[4/4] 母带处理 + 写出 ...")
    master = np.tanh(music * 1.05) * 0.90
    peak = np.max(np.abs(master)) or 1.0
    master = master * (0.90 / peak)
    pcm = (master * 32767.0).astype(np.int16)

    if out_wav:
        os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
        with wave.open(out_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())

    return {
        "duration_s": len(master) / SR,
        "phases": len(phases),
        "cues": placed,
        "rms": float(np.sqrt(np.mean(master ** 2))),
        "elapsed_s": round(time.time() - t0, 1),
        "warnings": warnings,
    }


def build_preview(plan: dict, phase_index: int, seed: int = 7,
                  cache_dir: str = None, songs_dir: str = None,
                  song_registry: dict = None,
                  preview_sec: float = 40.0) -> str:
    """试听单个阶段: 音乐(截断) + 开始提示 + (放得下的)提前提醒。返回 wav 路径"""
    import copy
    p = copy.deepcopy(plan)
    phase = p["phases"][phase_index]
    dur = min(float(phase.get("duration_min", 1)) * 60.0, preview_sec)
    phase["duration_min"] = dur / 60.0
    if phase.get("pre_cue"):
        lead = float(phase.get("pre_cue_lead", 15))
        if lead + 3.0 >= dur:
            phase.pop("pre_cue", None)
    p["phases"] = [phase]
    p["crossfade_sec"] = 0.0
    out_wav = os.path.join(cache_dir or os.path.join(HERE, "output"),
                           f"_preview_{phase_index}_{int(time.time() * 1000)}.wav")
    build_session(p, seed=seed, cache_dir=cache_dir, cues_on=True,
                  out_wav=out_wav, songs_dir=songs_dir, song_registry=song_registry)
    return out_wav


def to_mp3(wav: str, mp3: str, bitrate: str = "192k") -> bool:
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                        "-codec:a", "libmp3lame", "-b:a", bitrate, "-ac", "1", mp3],
                       capture_output=True)
    return r.returncode == 0 and os.path.exists(mp3)
