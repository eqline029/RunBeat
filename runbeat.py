#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RunBeat —— 跑步节拍训练音乐生成器 (CLI)
=======================================
按你设计的训练计划，生成"步频乐点精确对齐 + 中文语音提示"的跑步音频。

用法:
  python runbeat.py demo                     生成内置减脂间歇跑演示
  python runbeat.py make 计划.json           按计划文件生成(支持 wav/mp3)
  python runbeat.py make --interactive       交互式设计训练计划并生成
  python runbeat.py stretch --src 歌.mp3 --bpm 82 --out aligned.wav   你的歌变速对齐
  python runbeat.py voices                   列出可用中文音色
  python runbeat.py serve                    启动本地配置操作台(浏览器界面)

计划文件格式见 README.md / plans/fatburn_demo.json
"""
import argparse
import json
import os
import sys
import time

import session
import voice
from session import load_plan, validate_plan

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DEMO = os.path.join(HERE, "plans", "fatburn_demo.json")
DEFAULT_OUT = os.path.join(HERE, "output")


def cmd_demo(args):
    plan_path = DEFAULT_DEMO if os.path.exists(DEFAULT_DEMO) else None
    if plan_path is None:
        print("缺少 plans/fatburn_demo.json")
        return 1
    return cmd_make(args, plan_path)


def cmd_make(args, plan_path: str = None):
    path = plan_path or args.plan
    plan = load_plan(path)
    errs = validate_plan(plan)
    if errs:
        print("计划校验失败:")
        for e in errs:
            print("  -", e)
        return 1

    # 命令行歌曲覆盖: --song a.mp3 让指定阶段或全部阶段用歌曲
    if getattr(args, "song", None) and plan.get("phases"):
        idxs = args.song_phase or list(range(len(plan["phases"])))
        for i in idxs:
            if 0 <= i < len(plan["phases"]):
                plan["phases"][i]["source"] = "song"
                plan["phases"][i]["song_path"] = args.song
        print(f"歌曲模式: {args.song} -> 阶段 {[i + 1 for i in idxs]}")

    out_wav = args.out or os.path.join(DEFAULT_OUT,
        f"{plan.get('title', 'session')}_{time.strftime('%m%d_%H%M')}.wav")
    cache = os.path.join(HERE, "output", "_cue_cache")
    print(f"计划: {plan.get('title', path)} | 阶段 {len(plan['phases'])} 个 | 音色 {plan.get('voice', '默认')}")
    stats = session.build_session(plan, seed=args.seed, cache_dir=cache,
                                  cues_on=not args.no_cues, out_wav=out_wav)

    print(f"\n生成完成 ✔")
    print(f"  时长: {stats['duration_s'] / 60:.1f} 分钟 ({stats['duration_s']:.0f}s)")
    print(f"  阶段: {stats['phases']} 个 | 语音提示: {stats['cues']} 条")
    print(f"  响度(RMS): {stats['rms']:.3f} | 耗时: {stats['elapsed_s']}s")
    print(f"  文件: {out_wav}")

    if args.mp3:
        mp3 = out_wav.rsplit(".", 1)[0] + ".mp3"
        if session.to_mp3(out_wav, mp3):
            print(f"  MP3: {mp3}")
    return 0


def cmd_stretch(args):
    import stretch
    r = stretch.stretch(args.src, args.bpm, args.out)
    print(f"源BPM {r['source_bpm']} -> 目标 {args.bpm}, 变速系数 {r['factor']}"
          + (" (已夹紧到可接受范围, 建议换更接近的歌)" if r["clamped"] else ""))
    print(f"输出: {r['out']}")


def cmd_voices(_):
    for v in voice.list_voices():
        print(v)


def cmd_serve(_):
    sys.path.insert(0, HERE)
    import server
    print("RunBeat 操作台: http://127.0.0.1:8787  (浏览器打开, Ctrl+C 停止)")
    server.run(port=8787)


def interactive_plan() -> dict:
    print("== 交互式训练计划设计 ==")
    title = input("计划名称(如: 周末减脂间歇) [默认: 我的跑步计划]: ").strip() or "我的跑步计划"
    voice_name = input(f"音色(回车用 {voice.VOICE_CANDIDATES[1]}, 可用: {', '.join(voice.list_voices()[:5])}...): ").strip()
    voice_name = voice_name or "zh-CN-YunxiNeural"
    ratio = input("音乐节拍密度 0.5=每拍两步(默认), 1=每拍一步: ").strip() or "0.5"

    phases = []
    idx = 1
    print("\n逐阶段配置 (名称/时长分钟/步频/提示语, 提示语可回车自动生成, 直接回车结束):")
    while True:
        name = input(f"  阶段{idx} 名称 [回车结束]: ").strip()
        if not name:
            break
        dur = float(input(f"  {name} 时长(分钟): ").strip())
        cad = float(input(f"  {name} 目标步频(120-220, 如165): ").strip())
        cue = input(f"  {name} 提示语 [回车自动]: ").strip()
        if not cue:
            cue = f"{name}开始，时长{dur}分钟，步频{int(cad)}"
        pre = input(f"  {name} 结束前提醒语(秒数, 如 15; 回车不要): ").strip()
        ph = {"name": name, "duration_min": dur, "cadence": cad, "cue": cue}
        if pre:
            ph["pre_cue"] = "即将进入下一阶段，请准备"
            ph["pre_cue_lead"] = float(pre)
        phases.append(ph)
        idx += 1
    if not phases:
        raise SystemExit("未输入任何阶段, 退出")
    return {"title": title, "voice": voice_name, "tempo_ratio": float(ratio), "phases": phases}


def main():
    ap = argparse.ArgumentParser(description="RunBeat 跑步节拍训练音乐生成器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_demo = sub.add_parser("demo", help="生成内置减脂间歇跑演示")
    p_demo.add_argument("--out", default=None)
    p_demo.add_argument("--mp3", action="store_true")
    p_demo.add_argument("--seed", type=int, default=7)
    p_demo.add_argument("--no-cues", action="store_true")
    p_demo.add_argument("--song", default=None, help="用指定歌曲做全部阶段背景")
    p_demo.set_defaults(fn=cmd_demo)

    p_make = sub.add_parser("make", help="按计划文件生成")
    p_make.add_argument("plan", nargs="?", default=None)
    p_make.add_argument("--interactive", action="store_true", help="交互式设计计划")
    p_make.add_argument("--out", default=None)
    p_make.add_argument("--mp3", action="store_true")
    p_make.add_argument("--seed", type=int, default=7)
    p_make.add_argument("--no-cues", action="store_true")
    p_make.add_argument("--song", default=None, help="用指定歌曲做背景")
    p_make.add_argument("--song-phase", type=int, nargs="*", default=None,
                        help="--song 只作用于这些阶段(1起始), 默认全部")
    p_make.set_defaults(fn=cmd_make)

    p_st = sub.add_parser("stretch", help="你的歌变速对齐到目标音乐BPM")
    p_st.add_argument("--src", required=True)
    p_st.add_argument("--bpm", type=float, required=True,
                      help="目标音乐BPM(=步频×tempo_ratio, 如步频165→约82; 想每拍一步则165)")
    p_st.add_argument("--out", required=True)
    p_st.set_defaults(fn=cmd_stretch)

    p_vo = sub.add_parser("voices", help="列出可用中文音色")
    p_vo.set_defaults(fn=cmd_voices)

    p_sv = sub.add_parser("serve", help="启动本地配置操作台(浏览器界面)")
    p_sv.set_defaults(fn=cmd_serve)

    args = ap.parse_args()
    if getattr(args, "cmd", None) == "make" and args.interactive:
        plan = interactive_plan()
        plan_path = os.path.join(HERE, "plans", f"{plan['title']}.json")
        os.makedirs(os.path.dirname(plan_path), exist_ok=True)
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        print(f"计划已保存: {plan_path}")
        return cmd_make(args, plan_path)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
