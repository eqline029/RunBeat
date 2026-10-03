#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RunBeat 本地配置操作台 —— 后端服务 (纯标准库)
===============================================
启动: python3 runbeat.py serve   (或 python3 server.py)
界面: http://127.0.0.1:8787

接口:
  GET  /                      操作台页面
  GET  /api/voices            可用中文音色
  POST /api/song/upload       上传歌曲(原始字节 + X-Filename), 自动检测BPM
  POST /api/preview           试听单阶段 {plan, phase_index} -> {url}
  POST /api/generate          导出整段 {plan, format} -> {job_id} (异步)
  GET  /api/jobs/<id>         查询导出任务状态
  GET  /data/out/<file>       读取生成的音频文件
"""
import json
import os
import re
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, parse_qs, urlencode

import session
import stretch
import voice

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
SONGS_DIR = os.path.join(DATA_DIR, "songs")
OUT_DIR = os.path.join(DATA_DIR, "out")
CACHE_DIR = os.path.join(HERE, "output", "_cue_cache")
REGISTRY_PATH = os.path.join(SONGS_DIR, "_registry.json")

ALLOWED_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}
VERSION = "1.24.0"


def get_lan_ips() -> list:
    """尽力获取本机局域网 IPv4 地址(不联网, 只借 UDP 握手拿路由出口)"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    if not ips:
        try:
            host = socket.gethostname()
            for info in socket.getaddrinfo(host, None, socket.AF_INET):
                ip = info[4][0]
                if not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
    return ips


def _load_registry() -> dict:
    if os.path.exists(REGISTRY_PATH):
        try:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_registry(reg: dict):
    os.makedirs(SONGS_DIR, exist_ok=True)
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRY_PATH)  # 原子替换, 避免并发写坏


SONG_REGISTRY = _load_registry()
JOBS = {}
JOBS_LOCK = threading.Lock()


def _validate_job_plan(plan: dict):
    errs = session.validate_plan(plan)
    if errs:
        raise ValueError("；".join(errs))
    for p in plan.get("phases", []):
        if p.get("source") == "song":
            sid = p.get("song_id")
            if not sid or sid not in SONG_REGISTRY:
                raise ValueError(f"阶段[{p.get('name')}] 的歌曲不存在，请重新上传")


def _run_generate_job(job_id: str, plan: dict, fmt: str, seed: int, bitrate: str = "192k"):
    """后台任务: 生成整段音频"""
    try:
        _validate_job_plan(plan)
        stamp = time.strftime("%m%d_%H%M%S")
        safe_title = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", str(plan.get("title", "session")))[:40]
        wav = os.path.join(OUT_DIR, f"{safe_title}_{stamp}.wav")
        os.makedirs(OUT_DIR, exist_ok=True)
        stats = session.build_session(
            plan, seed=seed, cache_dir=CACHE_DIR, cues_on=True, out_wav=wav,
            songs_dir=SONGS_DIR, song_registry=SONG_REGISTRY)
        if fmt == "mp3":
            mp3 = wav.rsplit(".", 1)[0] + ".mp3"
            ok = session.to_mp3(wav, mp3, bitrate)
            if not ok:
                raise RuntimeError("MP3 转码失败")
            url = "/data/out/" + os.path.basename(mp3)
        else:
            url = "/data/out/" + os.path.basename(wav)
        with JOBS_LOCK:
            JOBS[job_id] = {"state": "done", "url": url, "stats": stats, "error": None}
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job_id] = {"state": "error", "url": None, "stats": None, "error": str(e)}


class Handler(BaseHTTPRequestHandler):
    server_version = "RunBeat/1.0"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):
        print(f"[web] {self.address_string()} {fmt % args}")

    def _send(self, code: int, body: bytes, ctype: str, extra: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # CORS: 允许任何来源访问（file:// 双击打开的页面也能连通本服务）
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """CORS 预检请求"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length > 0 else b""

    # ---------- 路由 ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._serve_ui()
        elif path == "/api/voices":
            self._json({"voices": voice.list_voices()})
        elif path == "/api/health":
            self._json({"ok": True, "version": VERSION})
        elif path == "/api/info":
            self._json(self._info())
        elif path == "/api/song/status":
            self._song_status()
        elif path == "/api/song/raw":
            self._song_raw()
        elif path == "/api/qr":
            self._qr()
        elif path.startswith("/api/jobs/"):
            self._json(self._get_job(path.rsplit("/", 1)[-1]))
        elif path.startswith("/data/out/"):
            import urllib.parse as _up
            self._serve_file(path, os.path.join(OUT_DIR, _up.unquote(os.path.basename(path))))
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/song/upload":
            self._upload_song()
        elif path == "/api/preview":
            self._preview()
        elif path == "/api/generate":
            self._generate()
        else:
            self._send(404, b"not found", "text/plain")

    # ---------- 页面 ----------
    def _serve_ui(self):
        ui_path = os.path.join(HERE, "ui.html")
        if not os.path.exists(ui_path):
            self._send(500, b"ui.html missing", "text/plain")
            return
        with open(ui_path, "rb") as f:
            self._send(200, f.read(), "text/html; charset=utf-8")

    def _serve_file(self, url_path: str, full: str):
        real = os.path.realpath(full)
        if not real.startswith(os.path.realpath(OUT_DIR) + os.sep) or not os.path.exists(real):
            self._send(404, b"file not found", "text/plain")
            return
        ext = os.path.splitext(real)[1].lower()
        ctype = "audio/mpeg" if ext == ".mp3" else ("audio/wav" if ext == ".wav" else "application/octet-stream")
        size = os.path.getsize(real)
        range_h = self.headers.get("Range")
        if range_h:
            m = re.match(r"bytes=(\d*)-(\d*)", range_h.strip())
            start = int(m.group(1)) if m and m.group(1) else 0
            end = int(m.group(2)) if m and m.group(2) else size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            length = end - start + 1
            with open(real, "rb") as f:
                f.seek(start)
                body = f.read(length)
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
            self.end_headers()
            self.wfile.write(body)
            return
        with open(real, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.end_headers()
        self.wfile.write(body)

    def _audio_duration(self, path: str) -> float:
        try:
            import subprocess
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
            return round(float(r.stdout.strip()), 1) if r.returncode == 0 else 0.0
        except Exception:
            return 0.0

    # ---------- API ----------
    def _info(self) -> dict:
        port = self.server.server_address[1] if hasattr(self, "server") else 8787
        lan = [f"http://{ip}:{port}" for ip in get_lan_ips()]
        return {"local": f"http://127.0.0.1:{port}", "lan": lan, "port": port,
                "version": VERSION}

    def _qr(self):
        """生成二维码 PNG(手机扫码直达操作台)。需要 qrcode 库, 未安装则提示。"""
        q = parse_qs(self.path.split("?", 1)[-1])
        text = (q.get("text") or [""])[0][:600]
        if not text:
            self._json({"error": "缺少 text 参数"}, 400)
            return
        try:
            import io
            import qrcode
            img = qrcode.make(text)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            self._send(200, buf.getvalue(), "image/png")
        except ImportError:
            self._send(503, b"qrcode library missing; run: pip install qrcode pillow",
                       "text/plain")

    def _song_raw(self):
        """返回上传歌曲原文件(带 Range 支持, 供原曲试听播放条)"""
        q = parse_qs(self.path.split("?", 1)[-1])
        sid = (q.get("song_id") or [""])[0]
        rec = SONG_REGISTRY.get(sid)
        if not rec or not os.path.exists(rec.get("file", "")):
            self._send(404, b"no such song", "text/plain")
            return
        real = os.path.realpath(rec["file"])
        ext = os.path.splitext(real)[1].lower()
        ctype = "audio/mpeg" if ext == ".mp3" else ("audio/wav" if ext == ".wav" else "application/octet-stream")
        size = os.path.getsize(real)
        range_h = self.headers.get("Range")
        if range_h:
            m = re.match(r"bytes=(\d*)-(\d*)", range_h.strip())
            start = int(m.group(1)) if m and m.group(1) else 0
            end = int(m.group(2)) if m and m.group(2) else size - 1
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            length = end - start + 1
            with open(real, "rb") as f:
                f.seek(start)
                body = f.read(length)
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        with open(real, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _song_status(self):
        q = parse_qs(self.path.split("?", 1)[-1])
        sid = (q.get("song_id") or [""])[0]
        info = SONG_REGISTRY.get(sid)
        if not info:
            self._json({"error": "歌曲不存在"}, 404)
            return
        self._json({"song_id": sid, "name": info.get("name"), "bpm": info.get("bpm"),
                    "status": info.get("status", "ready")})

    def _upload_song(self):
        name = unquote(self.headers.get("X-Filename", "song"))
        base = os.path.basename(name)
        ext = os.path.splitext(base)[1].lower()
        if ext not in ALLOWED_EXT:
            self._json({"error": f"不支持的格式: {ext or '(无扩展名)'}，支持 {', '.join(sorted(ALLOWED_EXT))}"}, 400)
            return
        data = self._read_body()
        if not data:
            self._json({"error": "空文件"}, 400)
            return
        song_id = uuid.uuid4().hex[:12]
        os.makedirs(SONGS_DIR, exist_ok=True)
        fname = f"{song_id}{ext}"
        fpath = os.path.join(SONGS_DIR, fname)
        with open(fpath, "wb") as f:
            f.write(data)
        try:
            bpm = round(stretch.detect_bpm(fpath), 1)
        except Exception as e:
            bpm = None
            print(f"  [song] BPM 检测失败: {e}")
        SONG_REGISTRY[song_id] = {"file": fpath, "bpm": None, "name": base, "status": "analyzing"}
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
        self._json({"song_id": song_id, "bpm": None, "name": base, "status": "analyzing"})

    def _preview(self):
        try:
            body = json.loads(self._read_body().decode("utf-8"))
            plan, idx = body["plan"], int(body.get("phase_index", 0))
            preview_sec = body.get("preview_sec", 40.0)
            if isinstance(preview_sec, str):
                ps = preview_sec.strip().lower()
                if ps in ("full", "all", "完整", "完整阶段"):
                    preview_sec = 1e9
                else:
                    try:
                        preview_sec = float(ps)
                    except ValueError:
                        preview_sec = 40.0
            elif preview_sec is None or preview_sec <= 0:
                preview_sec = 40.0
            errs = session.validate_plan(plan)
            if errs:
                self._json({"error": "；".join(errs)}, 400)
                return
            for p in plan.get("phases", []):
                if p.get("source") == "song":
                    sid = p.get("song_id")
                    if not sid or sid not in SONG_REGISTRY:
                        self._json({"error": f"阶段[{p.get('name')}] 的歌曲不存在"}, 400)
                        return
            try:
                seed = int(body.get("seed", 7))
            except (TypeError, ValueError):
                seed = 7
            wav = session.build_preview(plan, idx, seed=seed, cache_dir=CACHE_DIR,
                                        songs_dir=SONGS_DIR, song_registry=SONG_REGISTRY,
                                        preview_sec=preview_sec)
            os.makedirs(OUT_DIR, exist_ok=True)
            mp3_tmp = wav.rsplit(".", 1)[0] + ".mp3"
            if not session.to_mp3(wav, mp3_tmp):
                mp3_tmp = wav
            mp3 = os.path.join(OUT_DIR, os.path.basename(mp3_tmp))
            import shutil
            shutil.move(mp3_tmp, mp3)
            if mp3 != wav and os.path.exists(wav):
                os.remove(wav)
            url = "/data/out/" + os.path.basename(mp3)
            dur = self._audio_duration(mp3)
            self._clean_old_previews()
            self._json({"url": url, "duration": dur})
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _clean_old_previews(self, keep: int = 30):
        """清理 data/out 下试听临时文件(_preview_*), 只保留最近 keep 个"""
        try:
            pat = os.path.join(OUT_DIR, "_preview_*")
            import glob
            files = [f for f in glob.glob(pat) if os.path.isfile(f)]
            if len(files) > keep:
                files.sort(key=os.path.getmtime, reverse=True)
                for f in files[keep:]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass
        except Exception:
            pass

    def _generate(self):
        try:
            body = json.loads(self._read_body().decode("utf-8"))
            plan = body["plan"]
            fmt = body.get("format", "mp3")
            if fmt not in ("mp3", "wav"):
                fmt = "mp3"
            job_id = uuid.uuid4().hex[:10]
            with JOBS_LOCK:
                JOBS[job_id] = {"state": "running", "url": None, "stats": None, "error": None}
            t = threading.Thread(target=_run_generate_job,
                                 args=(job_id, plan, fmt, int(body.get("seed", 7)),
                                       str(body.get("bitrate") or "192k")),
                                 daemon=True)
            t.start()
            self._json({"job_id": job_id})
        except Exception as e:
            self._json({"error": str(e)}, 400)

    def _get_job(self, job_id: str):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job:
            return {"state": "unknown"}
        return job


def _open_browser(port: int):
    """服务就绪后自动打开浏览器（跨平台）"""
    try:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass


def run(port: int = 8787, host: str = "0.0.0.0", open_browser: bool = None):
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(SONGS_DIR, exist_ok=True)
    if open_browser is None:
        open_browser = not os.environ.get("RUNBEAT_NO_BROWSER")
    print("=" * 56)
    print("  RunBeat 跑步节拍训练音乐操作台")
    print(f"  本机访问 : http://127.0.0.1:{port}")
    for ip in get_lan_ips():
        print(f"  手机/其他电脑(同一WiFi): http://{ip}:{port}")
    print("  停止服务: Ctrl+C")
    print("=" * 56)
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        print(f"[提示] 端口 {port} 已被占用 —— 操作台可能已在运行。")
        if open_browser:
            print("  直接打开浏览器…")
            _open_browser(port)
        return
    if open_browser:
        threading.Timer(1.0, _open_browser, args=(port,)).start()
    srv.serve_forever()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    run(port=port)
