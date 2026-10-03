# -*- coding: utf-8 -*-
"""
RunBeat 音乐引擎 —— 用 numpy 程序化合成"步频精确对齐"的跑步音乐。

核心映射关系（跑步听感的关键）:
  - 音乐四分音符 BPM = 目标步频(spm) × tempo_ratio
      tempo_ratio = 0.5  -> 每拍两步（默认，最常见的跑步音乐听感，步点=8分音符）
      tempo_ratio = 1.0  -> 每拍一步（高步频冲刺感）
  - 8分音符 hi-hat 密度 = 步频，即每一步都落在帽子上，步点与音乐乐点严格对齐

v1.13 差异化重构: 每个内置风格拥有独立"音色配方 + 节奏骨架 + 贝斯/琶音编排",
不再只是同一套合成器的音量微调。10 个风格听感可闻区别:
  - 底鼓音高/衰减、军鼓有无/音色、踩镲明暗 各不相同
  - 节奏骨架: 4-on-floor / 2-step / 民谣 boom-chick(2拍+反拍军鼓) / ambient 稀疏
  - 贝斯: 四分驱动 / 反拍弹跳 / 两拍长音 / 切分推进; 波形 saw/square/sine
  - 和弦 pad: saw 明亮 / tri 柔和 / sine 空旷
  - 琶音: 上行/下行/波状/同音, 8分音符点缀(不形成第二节奏线)
"""
import math
import numpy as np
from scipy import signal as _sig

SR = 44100
MIDI_A4 = 69

# ---------------- 基础合成原语 ----------------

def note_freq(midi: float) -> float:
    """MIDI 音高 -> 频率 Hz"""
    return 440.0 * 2.0 ** ((midi - MIDI_A4) / 12.0)


def one_pole_lp(x: np.ndarray, cutoff: float, sr: int = SR) -> np.ndarray:
    """一阶低通滤波器（简单、快，足够做合成塑形）"""
    dt = 1.0 / sr
    a = dt / (dt + 1.0 / (2.0 * np.pi * max(cutoff, 20.0)))
    return _sig.lfilter([a], [1.0, a - 1.0], x)


def kick(sr: int = SR, f0: float = 140.0, f1: float = 42.0,
         decay: float = 26.0, click: float = 0.4) -> np.ndarray:
    """参数化底鼓: f0 起始音高(越高越亮/硬), f1 终止音高, decay 衰减速度, click 瞬态强度"""
    dur, n = 0.22, int(0.22 * sr)
    t = np.arange(n) / sr
    freq = f0 * np.exp(-t * decay) + f1
    phase = 2.0 * np.pi * np.cumsum(freq) / sr
    x = np.sin(phase) * np.exp(-t * (decay * 0.9))
    nc = int(0.004 * sr)
    click_sig = np.random.randn(nc) * np.exp(-np.arange(nc) / (0.0012 * sr))
    x[:nc] += click_sig * click
    return x


def snare(sr: int = SR, bw: float = 2100.0, res: float = 190.0,
          body: float = 0.8, tone: float = 0.5, decay: float = 32.0) -> np.ndarray:
    """参数化军鼓/拍手: bw 噪声带宽, res 共振音高, body 噪声占比, tone 音高占比, decay 衰减"""
    dur, n = 0.18, int(0.18 * sr)
    t = np.arange(n) / sr
    noise = np.random.randn(n)
    b = one_pole_lp(noise, bw, sr)
    b = b - one_pole_lp(b, 400.0, sr) * 0.7
    tne = np.sin(2.0 * np.pi * res * t) * np.exp(-t * 45.0) * tone
    return (b * body + tne) * np.exp(-t * decay)


def hat(sr: int = SR, open_: bool = False, dark: float = 0.0) -> np.ndarray:
    """参数化踩镲: dark 0=明亮 1=暗哑"""
    dur = 0.30 if open_ else 0.05
    n = int(dur * sr)
    t = np.arange(n) / sr
    noise = np.random.randn(n)
    hp = noise - one_pole_lp(noise, 8000.0 - dark * 3000.0, sr)
    hp = one_pole_lp(hp, 16000.0 - dark * 9000.0, sr)
    decay = 13.0 if open_ else 55.0
    return hp * np.exp(-t * decay) * 0.45


def bass_note(freq: float, dur: float, sr: int = SR, vel: float = 1.0,
              wave: str = "saw") -> np.ndarray:
    """参数化贝斯: wave = saw(锯齿)/square(方波)/sine(正弦)"""
    n = int(dur * sr)
    if n <= 0:
        return np.zeros(0)
    t = np.arange(n) / sr
    if wave == "sine":
        x = np.sin(2 * np.pi * freq * t)
    elif wave == "square":
        x = np.sign(np.sin(2 * np.pi * freq * t)) * 0.55 + np.sin(2 * np.pi * freq * t) * 0.45
    else:  # saw
        x = (np.sin(2 * np.pi * freq * t)
             + 0.5 * np.sin(2 * np.pi * 2 * freq * t)
             + 0.25 * np.sin(2 * np.pi * 3 * freq * t))
    x = one_pole_lp(x, 900.0, sr)
    env = np.minimum(1.0, t * 900.0) * np.exp(-t * 7.0)
    return x * env * vel


def pad_chord(freqs, dur: float, sr: int = SR, wave: str = "saw",
              bright: float = 1600.0, gain: float = 0.42) -> np.ndarray:
    """参数化和弦 pad: wave = saw(明亮)/tri(柔和)/sine(空旷), bright 低通截止"""
    n = int(dur * sr)
    if n <= 0:
        return np.zeros(0)
    t = np.arange(n) / sr
    x = np.zeros(n)
    for f in freqs:
        for det in (-0.12, 0.0, 0.12):
            w = 2 * np.pi * f * (1.0 + det / 100.0) * t
            if wave == "sine":
                x += np.sin(w)
            elif wave == "tri":
                x += np.sin(w) - 0.30 * np.sin(3 * w)
            else:  # saw
                x += np.sin(w) + 0.5 * np.sin(2 * w) + 0.25 * np.sin(3 * w)
    x = one_pole_lp(x / (len(freqs) * 3.0), bright, sr)
    env = np.sin(np.pi * np.minimum(t / dur, 1.0)) ** 2
    return x * env * gain


def pluck(freq: float, dur: float = 0.15, sr: int = SR,
          decay: float = 22.0, bright: float = 0.8) -> np.ndarray:
    """参数化拨弦: decay 衰减(越小越木/短促), bright 高频成分"""
    n = int(dur * sr)
    t = np.arange(n) / sr
    x = np.sin(2 * np.pi * freq * t) + bright * 0.3 * np.sin(2 * np.pi * 2 * freq * t)
    return x * np.exp(-t * decay) * 0.8


# ---------------- 和弦进行 ----------------

# (根音MIDI, 和弦类型 minor/major, 进行[半音程])
PROGRESSIONS = [
    # (root_midi, kind, [interval_steps])
    (57, "minor", [0, -4, 3, -3]),    # Am F C G   (经典流行)
    (52, "minor", [0, -4, 3, -3]),    # Em C G D
    (54, "minor", [0, -4, 3, -3]),    # F#m D A E (偏燃)
    (60, "major", [0, 5, -3, -5]),    # C G Am F
    (62, "major", [0, 5, -3, -5]),    # D A Bm G
    (55, "major", [0, 5, -3, -5]),    # G D Em C (明亮)
    (57, "major", [0, 4, -3, -5]),    # A F#m D E (轻快)
    (59, "minor", [0, -3, 5, -4]),    # Bm G D A (自然/沉稳)
    (64, "major", [0, -3, -4, -3]),   # E C#m A B (清亮上扬)
    (56, "minor", [0, -3, 3, -3]),    # G#m E B F#m (通透小调)
]

CHORD_TONES = {"minor": [0, 3, 7], "major": [0, 4, 7]}


# ---------------- 工具函数 ----------------

def _add(buf: np.ndarray, start_s: float, sig: np.ndarray, gain: float = 1.0, sr: int = SR):
    """把 sig 以 start_s 秒为起点叠加进 buf（越界安全）"""
    i = int(round(start_s * sr))
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i] * gain


def _sidechain_duck(n: int, beat: float, sr: int = SR, amount: float = 0.55) -> np.ndarray:
    """生成侧链压榨包络: 每个底鼓后压低 pad，做出 EDM 呼吸感"""
    duck = np.ones(n, dtype=np.float32)
    i = 0
    step = int(beat * sr)
    t = np.arange(int(0.16 * sr)) / sr
    env = 1.0 - amount * np.exp(-t / 0.05)
    while i < n:
        j = min(n, i + len(env))
        duck[i:j] *= env[: j - i]
        i += step
    return duck


# ---------------- 内置风格配方(差异化) ----------------
# prog: PROGRESSIONS 索引; hat: HAT_PATTERNS 索引; -1=随机
# drums:  底鼓/军鼓/踩镲 音色参数
# groove: 节奏骨架  kick=底鼓位置(0-3拍)  snare=军鼓位置(空=无)  open_hat=是否反拍开镲
# bass:   wave=波形(saw/square/sine)  pattern=four/offbeat/two/drive
# pad:    wave=saw(明亮)/tri(柔和)/sine(空旷)  bright=低通截止
# arp:    on=出现时机(0~1)  pattern=up/down/updown/wave/pedal
HAT_PATTERNS = [
    [1.0, 0.55, 0.8, 0.55, 1.0, 0.55, 0.8, 0.55],
    [1.0, 0.7, 0.6, 0.7, 0.9, 0.7, 0.6, 0.7],
    [0.9, 0.6, 1.0, 0.6, 0.9, 0.7, 0.8, 0.6],
]

ARP_SEQS = {
    "up": [0, 1, 2, 3],
    "down": [3, 2, 1, 0],
    "updown": [0, 1, 2, 1],
    "wave": [0, 1, 3, 1],
    "pedal": [0, 0, 0, 0],
}

STYLES = {
    # 基础电子(默认): 标准 EDM 4-on-floor, 锯波 pad + 锯齿贝斯, 经典流行进行
    "default": {
        "name": "基础电子(默认)", "prog": 0, "hat": 0,
        "kick_gain": 1.0, "snare_gain": 1.0, "hat_gain": 1.0,
        "bass_gain": 1.0, "pad_gain": 1.0, "arp_gain": 1.0, "sidechain": 0.55,
        "drums": {"kick_f0": 140, "kick_f1": 42, "kick_decay": 26, "kick_click": 0.4,
                  "snare_bw": 2100, "snare_res": 190, "snare_decay": 32, "hat_dark": 0.0},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": True},
        "bass": {"wave": "saw", "pattern": "four"},
        "pad": {"wave": "saw", "bright": 1600},
        "arp": {"on": 0.55, "pattern": "updown"},
    },
    # 晨光: 大调明亮柔和, 深底鼓 + 柔和军鼓 + 正弦贝斯 + 三角波 pad, 无琶音
    "sunlight": {
        "name": "晨光", "prog": 3, "hat": 0,
        "kick_gain": 0.85, "snare_gain": 0.60, "hat_gain": 0.75,
        "bass_gain": 0.85, "pad_gain": 0.90, "arp_gain": 0.0, "sidechain": 0.35,
        "drums": {"kick_f0": 90, "kick_f1": 38, "kick_decay": 22, "kick_click": 0.35,
                  "snare_bw": 1700, "snare_res": 175, "snare_decay": 38, "hat_dark": 0.15},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": True},
        "bass": {"wave": "sine", "pattern": "four"},
        "pad": {"wave": "tri", "bright": 1400},
        "arp": {"on": 0.0, "pattern": "updown"},
    },
    # 清风: 轻盈分解, 短促底鼓 + 无军鼓 + 反拍弹跳贝斯 + 无 pad, 上行琶音
    "breeze": {
        "name": "清风", "prog": 4, "hat": 1,
        "kick_gain": 0.80, "snare_gain": 0.0, "hat_gain": 0.80,
        "bass_gain": 0.85, "pad_gain": 0.0, "arp_gain": 1.00, "sidechain": 0.0,
        "drums": {"kick_f0": 125, "kick_f1": 45, "kick_decay": 30, "kick_click": 0.5,
                  "snare_bw": 1800, "snare_res": 160, "snare_decay": 30, "hat_dark": 0.05},
        "groove": {"kick": [0, 1, 2, 3], "snare": [], "open_hat": False},
        "bass": {"wave": "square", "pattern": "offbeat"},
        "pad": {"wave": "tri", "bright": 1300},
        "arp": {"on": 0.25, "pattern": "up"},
    },
    # 林间: 幽暗 ambient, 2-step 沉稳底鼓 + 无军鼓 + 暗踩镲 + 正弦长贝斯 + 空旷 pad
    "forest": {
        "name": "林间", "prog": 7, "hat": 0,
        "kick_gain": 0.80, "snare_gain": 0.0, "hat_gain": 0.60,
        "bass_gain": 0.75, "pad_gain": 1.15, "arp_gain": 0.0, "sidechain": 0.0,
        "drums": {"kick_f0": 70, "kick_f1": 40, "kick_decay": 20, "kick_click": 0.3,
                  "snare_bw": 1500, "snare_res": 150, "snare_decay": 34, "hat_dark": 0.55},
        "groove": {"kick": [0, 2], "snare": [], "open_hat": False},
        "bass": {"wave": "sine", "pattern": "two"},
        "pad": {"wave": "sine", "bright": 900},
        "arp": {"on": 0.0, "pattern": "updown"},
    },
    # 柠檬气泡: 活泼舞曲, 高亮短底鼓 + 清脆军鼓 + 方波反拍贝斯 + 明亮 pad + 下行琶音
    "lemon": {
        "name": "柠檬气泡", "prog": 8, "hat": 2,
        "kick_gain": 0.95, "snare_gain": 0.70, "hat_gain": 1.10,
        "bass_gain": 0.90, "pad_gain": 0.70, "arp_gain": 1.00, "sidechain": 0.45,
        "drums": {"kick_f0": 170, "kick_f1": 50, "kick_decay": 32, "kick_click": 0.55,
                  "snare_bw": 2600, "snare_res": 230, "snare_decay": 28, "hat_dark": 0.0},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": True},
        "bass": {"wave": "square", "pattern": "offbeat"},
        "pad": {"wave": "saw", "bright": 1800},
        "arp": {"on": 0.5, "pattern": "down"},
    },
    # 云朵: 梦幻 dreamy, 轻底鼓 + 极轻军鼓 + 暗踩镲 + 正弦贝斯 + 加厚三角波 pad
    "cloud": {
        "name": "云朵", "prog": 5, "hat": 1,
        "kick_gain": 0.70, "snare_gain": 0.30, "hat_gain": 0.60,
        "bass_gain": 0.70, "pad_gain": 1.25, "arp_gain": 0.0, "sidechain": 0.0,
        "drums": {"kick_f0": 105, "kick_f1": 40, "kick_decay": 24, "kick_click": 0.3,
                  "snare_bw": 1900, "snare_res": 200, "snare_decay": 40, "hat_dark": 0.35},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": False},
        "bass": {"wave": "sine", "pattern": "four"},
        "pad": {"wave": "tri", "bright": 1200},
        "arp": {"on": 0.0, "pattern": "updown"},
    },
    # 溪谷: lofi chill, 轻快 4-on-floor + 单点军鼓 + 暗踩镲 + 反拍正弦贝斯 + 波状琶音
    "stream": {
        "name": "溪谷", "prog": 6, "hat": 2,
        "kick_gain": 0.75, "snare_gain": 0.45, "hat_gain": 0.70,
        "bass_gain": 0.75, "pad_gain": 0.80, "arp_gain": 0.90, "sidechain": 0.0,
        "drums": {"kick_f0": 120, "kick_f1": 45, "kick_decay": 26, "kick_click": 0.4,
                  "snare_bw": 1600, "snare_res": 165, "snare_decay": 36, "hat_dark": 0.45},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": False},
        "bass": {"wave": "sine", "pattern": "two"},
        "pad": {"wave": "tri", "bright": 1100},
        "arp": {"on": 0.5, "pattern": "wave"},
    },
    # 麦浪: 民谣 boom-chick, 2拍底鼓+反拍军鼓 + 方波两拍贝斯 + 木琴琶音
    "wheat": {
        "name": "麦浪", "prog": 1, "hat": 0,
        "kick_gain": 0.85, "snare_gain": 0.55, "hat_gain": 0.70,
        "bass_gain": 0.85, "pad_gain": 0.0, "arp_gain": 1.00, "sidechain": 0.0,
        "drums": {"kick_f0": 80, "kick_f1": 40, "kick_decay": 24, "kick_click": 0.35,
                  "snare_bw": 1500, "snare_res": 165, "snare_decay": 34, "hat_dark": 0.2},
        "groove": {"kick": [0, 2], "snare": [1, 3], "open_hat": False},
        "bass": {"wave": "square", "pattern": "two"},
        "pad": {"wave": "tri", "bright": 1200},
        "arp": {"on": 0.4, "pattern": "down", "decay": 14, "bright": 1.0},
    },
    # 纸飞机: synthpop 能量, 明亮锯波 pad + 切分推进贝斯 + 波状琶音
    "paperplane": {
        "name": "纸飞机", "prog": 7, "hat": 1,
        "kick_gain": 1.05, "snare_gain": 0.95, "hat_gain": 1.20,
        "bass_gain": 0.90, "pad_gain": 0.70, "arp_gain": 0.95, "sidechain": 0.45,
        "drums": {"kick_f0": 175, "kick_f1": 52, "kick_decay": 30, "kick_click": 0.55,
                  "snare_bw": 2500, "snare_res": 245, "snare_decay": 26, "hat_dark": 0.0},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": True},
        "bass": {"wave": "square", "pattern": "drive"},
        "pad": {"wave": "saw", "bright": 2900},
        "arp": {"on": 0.5, "pattern": "updown"},
    },
    # 水波: 通透弛放, 轻底鼓 + 无军鼓 + 稀疏踩镲 + 正弦贝斯 + 通透 pad + 同音琶音
    "ripple": {
        "name": "水波", "prog": 9, "hat": 2,
        "kick_gain": 0.62, "snare_gain": 0.0, "hat_gain": 0.50,
        "bass_gain": 0.78, "pad_gain": 1.00, "arp_gain": 0.80, "sidechain": 0.0,
        "drums": {"kick_f0": 110, "kick_f1": 42, "kick_decay": 26, "kick_click": 0.35,
                  "snare_bw": 1700, "snare_res": 175, "snare_decay": 32, "hat_dark": 0.10},
        "groove": {"kick": [0, 1, 2, 3], "snare": [], "open_hat": False},
        "bass": {"wave": "sine", "pattern": "offbeat"},
        "pad": {"wave": "tri", "bright": 1500},
        "arp": {"on": 0.4, "pattern": "pedal"},
    },
    # 松风: cinematic 深暗, 2-step 超低底鼓 + 无军鼓 + 深暗踩镲 + 两拍正弦贝斯 + 加厚暗 pad
    "pine": {
        "name": "松风", "prog": 2, "hat": 0,
        "kick_gain": 0.80, "snare_gain": 0.0, "hat_gain": 0.55,
        "bass_gain": 0.85, "pad_gain": 1.35, "arp_gain": 0.0, "sidechain": 0.0,
        "drums": {"kick_f0": 60, "kick_f1": 38, "kick_decay": 18, "kick_click": 0.3,
                  "snare_bw": 1400, "snare_res": 145, "snare_decay": 36, "hat_dark": 0.6},
        "groove": {"kick": [0, 2], "snare": [], "open_hat": False},
        "bass": {"wave": "sine", "pattern": "two"},
        "pad": {"wave": "saw", "bright": 800},
        "arp": {"on": 0.0, "pattern": "updown"},
    },
    # 摇滚: 强冲击底鼓 + 亮军鼓 + 密集踩镲 + 锯齿贝斯 drive + 明亮锯波 pad, 燃向小调进行
    "rock": {
        "name": "摇滚", "prog": 2, "hat": 0,
        "kick_gain": 1.10, "snare_gain": 1.10, "hat_gain": 0.90,
        "bass_gain": 1.05, "pad_gain": 0.80, "arp_gain": 0.35, "sidechain": 0.35,
        "drums": {"kick_f0": 150, "kick_f1": 46, "kick_decay": 28, "kick_click": 0.55,
                  "snare_bw": 2400, "snare_res": 230, "snare_decay": 26, "hat_dark": 0.0},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": True},
        "bass": {"wave": "saw", "pattern": "drive"},
        "pad": {"wave": "saw", "bright": 2400},
        "arp": {"on": 0.35, "pattern": "updown"},
    },
    # 流行: 经典 Am F C G 明亮流行, 中强鼓组 + 方波反拍贝斯 + 明亮 pad + 上行琶音
    "pop": {
        "name": "流行", "prog": 0, "hat": 1,
        "kick_gain": 0.95, "snare_gain": 0.75, "hat_gain": 0.95,
        "bass_gain": 0.90, "pad_gain": 0.85, "arp_gain": 0.50, "sidechain": 0.40,
        "drums": {"kick_f0": 130, "kick_f1": 44, "kick_decay": 28, "kick_click": 0.45,
                  "snare_bw": 2200, "snare_res": 210, "snare_decay": 28, "hat_dark": 0.05},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": True},
        "bass": {"wave": "square", "pattern": "offbeat"},
        "pad": {"wave": "saw", "bright": 1900},
        "arp": {"on": 0.5, "pattern": "up"},
    },
    # 抒情钢琴: 华语情歌式抒情流行(原创编曲, 不复制任何既有作品旋律), 柔和 4-on-floor + 正弦长贝斯 + 三角波 pad + 钢琴感琶音
    "lyric": {
        "name": "抒情钢琴", "prog": 4, "hat": 2,
        "kick_gain": 0.80, "snare_gain": 0.50, "hat_gain": 0.70,
        "bass_gain": 0.80, "pad_gain": 1.00, "arp_gain": 0.60, "sidechain": 0.25,
        "drums": {"kick_f0": 100, "kick_f1": 40, "kick_decay": 22, "kick_click": 0.35,
                  "snare_bw": 1800, "snare_res": 180, "snare_decay": 34, "hat_dark": 0.25},
        "groove": {"kick": [0, 1, 2, 3], "snare": [1, 3], "open_hat": False},
        "bass": {"wave": "sine", "pattern": "four"},
        "pad": {"wave": "tri", "bright": 1500},
        "arp": {"on": 0.6, "pattern": "up"},
    },
    # 简约轻快: 干净极简, 轻底鼓 + 无军鼓 + 稀疏踩镲 + 正弦贝斯 + 三角波 pad, 无琶音
    "light": {
        "name": "简约轻快", "prog": 8, "hat": 1,
        "kick_gain": 0.70, "snare_gain": 0.0, "hat_gain": 0.65,
        "bass_gain": 0.70, "pad_gain": 1.10, "arp_gain": 0.0, "sidechain": 0.0,
        "drums": {"kick_f0": 115, "kick_f1": 42, "kick_decay": 24, "kick_click": 0.4,
                  "snare_bw": 1700, "snare_res": 165, "snare_decay": 30, "hat_dark": 0.2},
        "groove": {"kick": [0, 1, 2, 3], "snare": [], "open_hat": False},
        "bass": {"wave": "sine", "pattern": "two"},
        "pad": {"wave": "tri", "bright": 1300},
        "arp": {"on": 0.0, "pattern": "updown"},
    },
}

STYLE_NAMES = [(k, v["name"]) for k, v in STYLES.items()]


# ---------------- 阶段渲染 ----------------

def render_phase(phase: dict, sr: int = SR, seed: int = 0, tempo_ratio: float = 0.5,
             metronome_gain: float = 0.18) -> np.ndarray:
    """
    渲染单个训练阶段。
    phase: {"name", "duration_min", "cadence"(步频spm), "music_bpm"(可选, 覆盖tempo_ratio)}
    """
    rng = np.random.default_rng(seed)
    np.random.seed(seed)   # kick/snare/hat 瞬态噪声走全局流: 重置以保证同种子=同编曲
    style_name = str(phase.get("style") or "default")
    style = STYLES.get(style_name, STYLES["default"])
    cadence = float(phase["cadence"])
    bpm = float(phase.get("music_bpm") or cadence * tempo_ratio)
    if not (40 <= bpm <= 220):
        raise ValueError(f"音乐BPM超出合理范围(40-220): {bpm:.1f} (步频 {cadence} × ratio {tempo_ratio})")

    beat = 60.0 / bpm
    dur_s = float(phase["duration_min"]) * 60.0
    bar_s = 4 * beat
    # 整小节向上对齐: 阶段实际渲染为完整小节(末尾无静音尾巴),
    # 衔接处前阶段末拍与后阶段首拍直接对拍连续
    bars_total = max(1, int(math.ceil(dur_s / bar_s)))
    # 阶段实际时长向上对齐到一拍(beat): 偏差<1拍; 步频恒定; 衔接处末拍->首拍连续无空白
    n = int(math.ceil(dur_s / beat) * beat * sr)
    n = max(n, int(beat * sr))
    if n <= 0:
        raise ValueError("阶段时长必须大于 0")

    track = np.zeros(n, dtype=np.float32)
    # 节拍器音量 -> 鼓组(节拍声部)缩放: 默认18% ≈ 1.0 保持原听感
    drum_scale = 0.6 + 2.2 * max(0.0, min(1.0, float(metronome_gain)))

    # 和弦进行: 每 2 小节换一个和弦 (seed 在同调性池内选组; 同 seed 稳定可复现)
    _KIND_POOL = {"minor": [0, 1, 2, 7, 9], "major": [3, 4, 5, 6, 8]}
    if style["prog"] >= 0:
        _kind0 = PROGRESSIONS[style["prog"]][1]
        _pool = _KIND_POOL[_kind0]
        root, kind, steps = PROGRESSIONS[_pool[seed % len(_pool)]]
    else:
        root, kind, steps = PROGRESSIONS[seed % len(PROGRESSIONS)]
    tones = CHORD_TONES[kind]
    prog_roots = [root + s for s in steps]

    # 小节结构
    intro_bars = min(2, max(0, bars_total // 4))          # 前几小节轻(只留鼓)
    break_bars = 0   # 不抽离末小节: 阶段始终完整编曲到最后一拍, 衔接处无密度骤降

    dr = style["drums"]; gr = style["groove"]
    bs = style["bass"]; pd = style["pad"]; ar = style["arp"]

    kk = kick(sr, dr["kick_f0"], dr["kick_f1"], dr["kick_decay"], dr["kick_click"])
    sn = snare(sr, dr["snare_bw"], dr["snare_res"], decay=dr["snare_decay"]) if gr["snare"] else None
    hat_c = hat(sr, open_=False, dark=dr["hat_dark"])
    hat_o = hat(sr, open_=True, dark=dr["hat_dark"]) if gr["open_hat"] else None

    # 帽子重音花样: seed 轮换(位置固定8分=步点, 仅力度花样变化)
    if style["hat"] >= 0:
        hat_pat = HAT_PATTERNS[(style["hat"] + seed) % len(HAT_PATTERNS)]
    else:
        hat_pat = HAT_PATTERNS[seed % len(HAT_PATTERNS)]

    for bar in range(bars_total):
        t0 = bar * bar_s
        chord = prog_roots[bar % len(prog_roots)]
        freqs = [note_freq(chord + t) for t in tones]
        full = (bar >= intro_bars) and (bar < bars_total - break_bars)

        # 底鼓 按节奏骨架 (节拍声部: 受 drum_scale 控制)
        for b in gr["kick"]:
            _add(track, t0 + b * beat, kk,
                 (1.0 if b == 0 else 0.95) * style["kick_gain"] * drum_scale, sr)
        # 军鼓 按节奏骨架
        if full and sn is not None:
            for b in gr["snare"]:
                _add(track, t0 + b * beat, sn, 0.75 * style["snare_gain"] * drum_scale, sr)
        # 踩镲 8分音符 = 步点 (所有风格统一, 保证"每一步一帽"的步频引导)
        for e in range(8):
            g = hat_pat[e] if e % 2 == 0 else hat_pat[e] * 0.6
            _add(track, t0 + e * beat / 2, hat_c, g * style["hat_gain"] * drum_scale, sr)
        # open hat 在反拍(第5个8分)
        if full and hat_o is not None:
            _add(track, t0 + 5 * beat / 2, hat_o, 0.30 * style["hat_gain"] * drum_scale, sr)

        if not full:
            continue

        # 贝斯 按编排
        root_f = note_freq(chord)
        pat = bs["pattern"]
        if pat == "four":
            for b in range(4):
                _add(track, t0 + b * beat, bass_note(root_f, beat * 0.95, sr, 1.0, bs["wave"]),
                     1.0 * style["bass_gain"], sr)
                _add(track, t0 + b * beat + beat / 2, bass_note(root_f * 2, beat * 0.45, sr, 0.55, bs["wave"]),
                     0.7 * style["bass_gain"], sr)
        elif pat == "offbeat":
            for b in range(4):
                _add(track, t0 + b * beat + beat / 2, bass_note(root_f * 2, beat * 0.45, sr, 0.8, bs["wave"]),
                     0.9 * style["bass_gain"], sr)
        elif pat == "two":
            for b in (0, 2):
                _add(track, t0 + b * beat, bass_note(root_f, beat * 1.8, sr, 1.0, bs["wave"]),
                     1.0 * style["bass_gain"], sr)
        elif pat == "drive":
            for b in range(4):
                _add(track, t0 + b * beat, bass_note(root_f, beat * 0.95, sr, 1.0, bs["wave"]),
                     1.0 * style["bass_gain"], sr)
                _add(track, t0 + b * beat + beat / 2, bass_note(root_f, beat * 0.4, sr, 0.5, bs["wave"]),
                     0.5 * style["bass_gain"], sr)

        # pad 和弦(2小节), 带侧链
        if bar % 2 == 0 and style["pad_gain"] > 0:
            pad = pad_chord(freqs, min(2 * bar_s, dur_s - t0), sr, pd["wave"], pd["bright"])
            duck = _sidechain_duck(len(pad), beat, sr, style["sidechain"])
            _add(track, t0, pad * duck.astype(np.float32), style["pad_gain"], sr)

        # 琶音旋律点缀 (后半段; 8分音符, 与踩镲同轨不同音色, 不形成第二节奏线)
        if ar["on"] > 0 and bar >= bars_total * ar["on"]:
            _ARPS = ["up", "down", "updown", "wave", "pedal"]
            if ar.get("pattern") in _ARPS:
                seq = ARP_SEQS[_ARPS[(_ARPS.index(ar["pattern"]) + seed) % len(_ARPS)]]
            else:
                seq = ARP_SEQS.get(ar["pattern"], ARP_SEQS["updown"])
            _off = seed % 4
            ad = float(ar.get("decay", 22)); ab = float(ar.get("bright", 0.8))
            for e in range(8):
                f = note_freq(freqs[seq[(e + _off) % 4] % len(freqs)] + 12)
                _add(track, t0 + e * beat / 2, pluck(f, decay=ad, bright=ab),
                     0.12 * style["arp_gain"], sr)

    # 母带: 软削波 + 归一
    track = np.tanh(track * 1.15) * 0.82
    peak = np.max(np.abs(track)) or 1.0
    track *= (0.85 / peak)
    return track.astype(np.float32)


def concat_phases(sections, crossfade_s: float = 2.0, sr: int = SR) -> np.ndarray:
    """阶段拼接 + 等功率交叉淡化, 避免 BPM 突变产生爆音"""
    if not sections:
        return np.zeros(0, dtype=np.float32)
    out = sections[0].astype(np.float32).copy()
    for sec in sections[1:]:
        sec = sec.astype(np.float32)
        cf = int(crossfade_s * sr)
        cf = min(cf, len(out) // 2, len(sec) // 2)
        if cf <= 0:
            out = np.concatenate([out, sec])
            continue
        ramp = np.linspace(0.0, 1.0, cf, dtype=np.float32)
        out[-cf:] = out[-cf:] * (1.0 - ramp) + sec[:cf] * ramp
        out = np.concatenate([out, sec[cf:]])
    return out


def render_bridge(bpm_from: float, bpm_to: float, dur_s: float, sr: int = SR,
                  seed: int = 0, style_name: str = None) -> np.ndarray:
    """BPM 渐变过渡桥(无缝): 拍点间隔从 bpm_from 平滑过渡到 bpm_to。

    用于阶段之间——例如热身 150 步频 → 匀速 165 步频时, 用一段节拍连续加速的
    过渡桥衔接, 避免 BPM 突变造成的节拍断崖/衔接不上。"""
    style = STYLES.get(str(style_name or "default"), STYLES["default"])
    rng = np.random.default_rng(seed)
    np.random.seed(seed)   # 同种子=同编曲
    n = int(dur_s * sr)
    track = np.zeros(n, dtype=np.float32)
    if n <= 0:
        return track

    if style["prog"] >= 0:
        root, kind, steps = PROGRESSIONS[style["prog"]]
    else:
        root, kind, steps = PROGRESSIONS[rng.integers(len(PROGRESSIONS))]
    tones = CHORD_TONES[kind]
    freqs = [note_freq(root + t) for t in tones]

    dr = style["drums"]; bs = style["bass"]
    kk, sn, hat_c, hat_o = (kick(sr, dr["kick_f0"], dr["kick_f1"], dr["kick_decay"], dr["kick_click"]),
                            snare(sr, dr["snare_bw"], dr["snare_res"], decay=dr["snare_decay"]),
                            hat(sr, dark=dr["hat_dark"]), hat(sr, open_=True, dark=dr["hat_dark"]))
    eighths = _pulses(bpm_from, bpm_to, dur_s, sr, div=2)
    for e, pos in enumerate(eighths):
        _add(track, pos, hat_c, 0.5 * style["hat_gain"], sr)
        if e % 2 == 0:
            _add(track, pos, kk, 0.9 * style["kick_gain"], sr)
        if e % 8 == 2 or e % 8 == 6:
            _add(track, pos, sn, 0.6 * style["snare_gain"], sr)
        if e % 8 == 4:
            _add(track, pos, hat_o, 0.25 * style["hat_gain"], sr)

    for pos in eighths[0::2]:
        _add(track, pos, bass_note(note_freq(root), 0.5 * (60.0 / bpm_from + 60.0 / bpm_to), sr, 1.0, bs["wave"]),
             0.8 * style["bass_gain"], sr)

    pad = pad_chord(freqs, dur_s, sr, style["pad"]["wave"], style["pad"]["bright"])
    duck = _sidechain_duck(len(pad), 60.0 / bpm_from, sr, style["sidechain"])
    _add(track, 0.0, pad * duck.astype(np.float32), 0.7 * style["pad_gain"], sr)

    if style["arp"]["on"] > 0:
        seq = ARP_SEQS.get(style["arp"]["pattern"], ARP_SEQS["updown"])
        sixteenths = _pulses(bpm_from, bpm_to, dur_s, sr, div=4)
        for e, pos in enumerate(sixteenths):
            f = note_freq(freqs[seq[e % 4] % len(freqs)] + 12)
            _add(track, pos, pluck(f), 0.10 * style["arp_gain"], sr)

    fade = min(int(0.4 * sr), n // 4)
    if fade > 0:
        r = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        track[:fade] *= r
        track[-fade:] *= r[::-1]

    track = np.tanh(track * 1.15) * 0.82
    peak = np.max(np.abs(track)) or 1.0
    track *= (0.85 / peak)
    return track.astype(np.float32)


def _pulses(bpm_start: float, bpm_end: float, dur_s: float, sr: int = SR, div: int = 2) -> list:
    """BPM 从 bpm_start 线性渐变到 bpm_end, 返回每个 div 分拍脉冲的起始秒位置"""
    pos = []
    t = 0.0
    while t < dur_s - 1e-6:
        pos.append(t)
        frac = min(1.0, t / dur_s)
        bpm = bpm_start + (bpm_end - bpm_start) * frac
        t += 60.0 / bpm / div
    return pos
