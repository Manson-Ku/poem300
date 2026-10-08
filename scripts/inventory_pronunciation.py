#!/usr/bin/env python3
"""Validate age-specific pronunciation candidates against canonical source."""

from __future__ import annotations
import argparse, csv, json
from pathlib import Path

def split_lines(value: str) -> list[str]:
    return (value or "").replace("\r\n","\n").replace("\r","\n").split("\n")

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--age",type=int,choices=(6,7,8,9),default=7,
                    help="Recommended-age cohort; default 7 preserves old commands.")
    ap.add_argument("--poems",default="data/poems.csv")
    ap.add_argument("--candidates",
                    help="Default: data/pronunciation_candidates_age<age>.json")
    args=ap.parse_args()
    cp=Path(args.candidates or f"data/pronunciation_candidates_age{args.age}.json")
    with Path(args.poems).open("r",encoding="utf-8-sig",newline="") as f:
        poems={int(r["poem_id"]):r for r in csv.DictReader(f)}
    doc=json.loads(cp.read_text(encoding="utf-8"))
    scope=doc.get("scope") or {}
    if scope.get("recommended_age") is not None and int(scope["recommended_age"]) != args.age:
        print(f"ERROR candidate scope age={scope['recommended_age']} expected={args.age}")
        return 2
    errors=[]; unresolved=0; selected=set()
    for x in doc.get("items",[]):
        pid=int(x["poem_id"]); p=poems.get(pid)
        if not p:
            errors.append(f"p{pid:03d}: missing poem"); continue
        if int(p["recommended_age"]) != args.age:
            errors.append(f"p{pid:03d}: recommended_age={p['recommended_age']} expected={args.age}"); continue
        selected.add(pid)
        field=str(x["field"]); line_no=int(x.get("line_no",0))
        if field in ("title","author"):
            if line_no != 0:
                errors.append(f"p{pid:03d} {field}: line_no must be 0"); continue
            text=p[field]
        elif field=="content":
            lines=split_lines(p["content"])
            if not 1 <= line_no <= len(lines):
                errors.append(f"p{pid:03d}: bad line {line_no}"); continue
            text=lines[line_no-1]
        else:
            errors.append(f"p{pid:03d}: bad field {field}"); continue
        if str(x["character"]) not in text or str(x["context"]) not in text:
            errors.append(f"p{pid:03d} {field} line={line_no}: source mismatch")
        if str(x["candidate_reading"])=="authority_check":
            unresolved += 1
    print(f"Age {args.age} pronunciation inventory")
    print(f"candidate_file={cp.as_posix()}")
    print(f"candidates={len(doc.get('items',[]))}")
    print(f"candidate_poems={len(selected)}")
    print(f"authority_check={unresolved}")
    print(f"source_errors={len(errors)}")
    if errors:
        for e in errors: print("  "+e)
        print("SOURCE BLOCKED"); return 1
    print("SOURCE PASS")
    for x in doc.get("items",[]):
        print(f"p{int(x['poem_id']):03d} {x['field']} {x['context']} -> {x['candidate_reading']} | {x['reason']}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
