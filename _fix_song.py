# -*- coding: utf-8 -*-
"""v1.23.1 修复: 上传/试听带歌曲时进度条卡住
1) server.py: 上传异步化(立即返回, 后台检测BPM) + registry原子写 + /api/song/status
2) session.py: BPM模块级缓存 + 变速结果缓存(同歌同BPM只变速一次)
3) ui.html: 上传后轮询BPM状态, 文案提示"分析中约20秒"
4) server.py VERSION -> 1.23.1
"""
import io

# ============ 1. session.py ============
p = r"C:\Users\品老大\Downloads\runbeat-v1.3\session.py"
s = io.open(p, "r", encoding="utf-8").read()

# 1a. BPM 缓存
old = """HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------- 计划校验 ----------------"""
new = """HERE = os.path.dirname(os.path.abspath(__file__))

# BPM 模块级缓存: 同一文件只检测一次(4-5分钟音频检测约20秒)
_BPM_CACHE = {}


def _bpm_for(path: str) -> float:
    if path not in _BPM_CACHE:
        _BPM_CACHE[path] = stretch.detect_bpm(path)
    return _BPM_CACHE[path]


# ---------------- 计划校验 ----------------"""
assert s.count(old) == 1, "session bpm cache anchor"
s = s.replace(old, new)

# 1b. 变速结果缓存
old2 = """    os.makedirs(work_dir, exist_ok=True)
    aligned = os.path.join(work_dir, f"song_{os.path.basename(song_path)}.aligned.wav")
    try:
        info = stretch.stretch(song_path, target_bpm, aligned)
    except Exception as e:
        raise RuntimeError(f"歌曲变速失败: {e}")"""
new2 = """    _ALIGN_DIR = os.path.join(HERE, "output", "_aligned")
    os.makedirs(_ALIGN_DIR, exist_ok=True)
    _tag = os.path.splitext(os.path.basename(song_path))[0][:24]
    aligned = os.path.join(_ALIGN_DIR, f"{_tag}_{target_bpm:.0f}.wav")
    if os.path.exists(aligned) and os.path.getsize(aligned) >= 1024:
        info = {"source_bpm": song_bpm, "bpm_used": song_bpm, "factor": 1.0, "clamped": False}
    else:
        try:
            info = stretch.stretch(song_path, target_bpm, aligned)
        except Exception as e:
            raise RuntimeError(f"歌曲变速失败: {e}")"""
assert s.count(old2) == 1, "session align cache anchor"
s = s.replace(old2, new2)

# 1c. detect_bpm -> _bpm_for
old3 = "song_bpm = global_song_bpm or stretch.detect_bpm(song_path)"
assert s.count(old3) == 1, "session bpm 234"
s = s.replace(old3, "song_bpm = global_song_bpm or _bpm_for(song_path)")

old4 = """            if not song_bpm:
                song_bpm = stretch.detect_bpm(song_path)"""
assert s.count(old4) == 1, "session bpm 249"
s = s.replace(old4, """            if not song_bpm:
                song_bpm = _bpm_for(song_path)""")

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("session.py ok")

# ============ 2. server.py ============
p2 = r"C:\Users\品老大\Downloads\runbeat-v1.3\server.py"
v = io.open(p2, "r", encoding="utf-8").read()

# 2a. VERSION
assert 'VERSION = "1.23.0"' in v, "version anchor"
v = v.replace('VERSION = "1.23.0"', 'VERSION = "1.23.1"')

# 2b. registry 原子写
old_r = """def _save_registry(reg: dict):
    os.makedirs(SONGS_DIR, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)"""
new_r = """def _save_registry(reg: dict):
    os.makedirs(SONGS_DIR, exist_ok=True)
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRY_PATH)  # 原子替换, 避免并发写坏"""
assert v.count(old_r) == 1, "server registry anchor"
v = v.replace(old_r, new_r)

# 2c. 上传异步化
old_u = """        SONG_REGISTRY[song_id] = {"file": fpath, "bpm": bpm, "name": base}
        _save_registry(SONG_REGISTRY)
        self._json({"song_id": song_id, "bpm": bpm, "name": base})"""
new_u = """        SONG_REGISTRY[song_id] = {"file": fpath, "bpm": None, "name": base, "status": "analyzing"}
        _save_registry(SONG_REGISTRY)

        def _analyze():
            try:
                bpm = round(session._bpm_for(fpath), 1)
                with JOBS_LOCK:
                    SONG_REGISTRY[song_id]["bpm"] = bpm
                    SONG_REGISTRY[song_id]["status"] = "ready"
                _save_registry(SONG_REGISTRY)
            except Exception as e:
                print(f"  [song] BPM 检测失败: {e}")
                with JOBS_LOCK:
                    SONG_REGISTRY[song_id]["status"] = "error"
                _save_registry(SONG_REGISTRY)

        threading.Thread(target=_analyze, daemon=True).start()
        # 立即返回, BPM 后台检测(约20秒), 前端轮询 /api/song/status
        self._json({"song_id": song_id, "bpm": None, "name": base, "status": "analyzing"})"""
assert v.count(old_u) == 1, "server upload anchor"
v = v.replace(old_u, new_u)

# 2d. GET 路由 + status handler
old_g = """        elif path == "/api/info":
            self._json(self._info())"""
new_g = """        elif path == "/api/info":
            self._json(self._info())
        elif path == "/api/song/status":
            self._song_status()"""
assert v.count(old_g) == 1, "server route anchor"
v = v.replace(old_g, new_g)

old_h = """    def _upload_song(self):"""
new_h = """    def _song_status(self):
        q = parse_qs(self.path.split("?", 1)[-1])
        sid = (q.get("song_id") or [""])[0]
        info = SONG_REGISTRY.get(sid)
        if not info:
            self._json({"error": "歌曲不存在"}, 404)
            return
        self._json({"song_id": sid, "name": info.get("name"), "bpm": info.get("bpm"),
                    "status": info.get("status", "ready")})

    def _upload_song(self):"""
assert v.count(old_h) == 1, "server handler anchor"
v = v.replace(old_h, new_h)

io.open(p2, "w", encoding="utf-8", newline="\n").write(v)
print("server.py ok")

# ============ 3. ui.html ============
p3 = r"C:\Users\品老大\Downloads\runbeat-v1.3\ui.html"
u = io.open(p3, "r", encoding="utf-8").read()

old_ui = """    const j = await resp.json();
    if(j.error) throw new Error(j.error);
    state.globalSong = { song_id: j.song_id, song_name: j.name, song_bpm: j.bpm };
    $("songName").textContent = j.name + (j.bpm ? `（BPM ${j.bpm}）` : "（BPM 检测失败，导出时自动检测）");
    $("songClear").style.display = "inline-block";
    setStatus("ok", "全局歌曲已上传：" + j.name);
    scheduleSave();
  }catch(e){
    setStatus("err","上传失败：" + e.message);
  }
}"""
new_ui = """    const j = await resp.json();
    if(j.error) throw new Error(j.error);
    state.globalSong = { song_id: j.song_id, song_name: j.name, song_bpm: j.bpm };
    $("songClear").style.display = "inline-block";
    scheduleSave();
    if(j.status === "analyzing"){
      $("songName").textContent = j.name + "（正在分析节奏…约20秒）";
      setStatus("info", "已上传「" + j.name + "」，正在分析节奏，完成后即可顺畅试听");
      pollSongBpm(j.song_id, j.name);
    } else {
      $("songName").textContent = j.name + (j.bpm ? `（BPM ${j.bpm}）` : "（BPM 检测失败，导出时自动检测）");
      setStatus("ok", "全局歌曲已上传：" + j.name);
    }
  }catch(e){
    setStatus("err","上传失败：" + e.message);
  }
}

// 上传后轮询 BPM 分析状态(后台检测约20秒), 完成后更新显示
async function pollSongBpm(song_id, name){
  try{
    for(let i=0;i<60;i++){
      await new Promise(r=>setTimeout(r,3000));
      const r = await fetch("/api/song/status?song_id=" + encodeURIComponent(song_id));
      const j = await r.json();
      if(j.status === "ready"){
        state.globalSong.song_bpm = j.bpm;
        $("songName").textContent = name + `（BPM ${j.bpm}）`;
        setStatus("ok", "节奏分析完成：" + name + `（BPM ${j.bpm}）`);
        scheduleSave();
        return;
      }
      if(j.status === "error"){
        $("songName").textContent = name + "（BPM 检测失败，导出时自动检测）";
        setStatus("warn", "节奏分析失败，导出/试听时仍会自动检测");
        return;
      }
    }
  }catch(e){ /* 轮询失败忽略: 试听/导出时自动检测兜底 */ }
}"""
assert u.count(old_ui) == 1, "ui upload anchor"
u = u.replace(old_ui, new_ui)
io.open(p3, "w", encoding="utf-8", newline="\n").write(u)
print("ui.html ok")
