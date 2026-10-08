#!/usr/bin/env python3
"""Check local project-font Han glyph coverage for one age cohort."""
from __future__ import annotations
import argparse,csv
from collections import defaultdict
from pathlib import Path
try:
    from fontTools.ttLib import TTFont
except ImportError as exc:
    raise SystemExit("fonttools is required; install requirements.txt") from exc

def is_han(ch:str)->bool:
    cp=ord(ch)
    return 0x3400<=cp<=0x4DBF or 0x4E00<=cp<=0x9FFF or 0xF900<=cp<=0xFAFF or 0x20000<=cp<=0x3134F

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--age",type=int,choices=(6,7,8,9),required=True)
    ap.add_argument("--poems",default="data/poems.csv")
    ap.add_argument("--font-path",default="fonts/BpmfHuninn-Poem300-Regular.ttf")
    args=ap.parse_args()
    fp=Path(args.font_path)
    if not fp.exists(): print(f"ERROR font not found: {fp}"); return 2
    with Path(args.poems).open("r",encoding="utf-8-sig",newline="") as f:
        rows=[r for r in csv.DictReader(f) if int(r["recommended_age"])==args.age]
    ft=TTFont(str(fp))
    try: cmap=ft.getBestCmap() or {}
    finally: ft.close()
    missing=defaultdict(list); seen=set()
    for r in rows:
        for field in ("title","content"):
            for ch in r[field]:
                if not is_han(ch): continue
                seen.add(ch)
                if ord(ch) not in cmap:
                    label=f"p{int(r['poem_id']):03d} {r['title']} {field}"
                    if label not in missing[ch]: missing[ch].append(label)
    print(f"Age {args.age} project-font coverage")
    print(f"poems={len(rows)}")
    print(f"unique_han={len(seen)}")
    print(f"missing_unique_han={len(missing)}")
    for ch,ctx in sorted(missing.items(),key=lambda x:ord(x[0])):
        print(f"{ch} U+{ord(ch):04X} | "+" | ".join(ctx))
    print("PASS" if not missing else "FONT COVERAGE BLOCKED")
    return 0 if not missing else 1
if __name__=="__main__": raise SystemExit(main())
