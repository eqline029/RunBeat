# -*- coding: utf-8 -*-
"""v1.23: 新增 4 种音乐风格（摇滚/流行/抒情钢琴/简约轻快）"""
import io

# ============ 1. musicgen.py STYLES 追加 4 个风格 ============
p = r"C:\Users\品老大\Downloads\runbeat-v1.3\musicgen.py"
s = io.open(p, "r", encoding="utf-8").read()

new_styles = '''
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
}'''

anchor = '''        "arp": {"on": 0.0, "pattern": "updown"},
    },
}'''
assert s.count(anchor) == 1, "styles anchor"
s = s.replace(anchor, new_styles)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("musicgen.py +4 styles")

# ============ 2. ui.html 下拉选项 ============
p2 = r"C:\Users\品老大\Downloads\runbeat-v1.3\ui.html"
u = io.open(p2, "r", encoding="utf-8").read()
a2 = '          <option value="pine">松风</option>'
assert u.count(a2) == 1, "ui option anchor"
u = u.replace(a2, a2 + '''
          <option value="rock">摇滚</option>
          <option value="pop">流行</option>
          <option value="lyric">抒情钢琴</option>
          <option value="light">简约轻快</option>''')
io.open(p2, "w", encoding="utf-8", newline="\n").write(u)
print("ui.html +4 options")

# ============ 3. server.py VERSION ============
p3 = r"C:\Users\品老大\Downloads\runbeat-v1.3\server.py"
v = io.open(p3, "r", encoding="utf-8").read()
assert 'VERSION = "1.22.0"' in v, "version anchor"
v = v.replace('VERSION = "1.22.0"', 'VERSION = "1.23.0"')
io.open(p3, "w", encoding="utf-8", newline="\n").write(v)
print("server.py -> 1.23.0")
