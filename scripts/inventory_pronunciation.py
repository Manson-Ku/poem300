#!/usr/bin/env python3
"""Validate Age 7 pronunciation candidates against canonical source."""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path

def split_lines(v:str)->list[str]:
    return (v or "").replace("\r\n","\n").replace("\r","\n").split("\n")

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--poems",default="data/poems.csv")
    ap.add_argument("--candidates",default="data/pronunciation_candidates_age7.json")
    args=ap.parse_args()
    with Path(args.poems).open("r",encoding="utf-8-sig",newline="") as f:
        poems={int(r["poem_id"]):r for r in csv.DictReader(f)}
    doc=json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    errors=[]; unresolved=0
    for x in doc.get("items",[]):
        pid=int(x["poem_id"]); p=poems.get(pid)
        if not p: errors.append(f"p{pid:03d}: missing poem"); continue
        field=x["field"]; line_no=int(x.get("line_no",0))
        if field in ("title","author"): text=p[field]
        elif field=="content":
            ls=split_lines(p["content"])
            if not 1<=line_no<=len(ls): errors.append(f"p{pid:03d}: bad line {line_no}"); continue
            text=ls[line_no-1]
        else: errors.append(f"p{pid:03d}: bad field {field}"); continue
        if x["character"] not in text or x["context"] not in text:
            errors.append(f"p{pid:03d} {field} line={line_no}: source mismatch")
        if x["candidate_reading"]=="authority_check": unresolved+=1
    print("Age 7 pronunciation inventory")
    print(f"candidates={len(doc.get('items',[]))}")
    print(f"authority_check={unresolved}")
    print(f"source_errors={len(errors)}")
    if errors:
        for e in errors: print("  "+e)
        print("SOURCE BLOCKED"); return 1
    print("SOURCE PASS")
    for x in doc.get("items",[]):
        print(f"p{int(x['poem_id']):03d} {x['field']} {x['context']} -> {x['candidate_reading']} | {x['reason']}")
    return 0
if __name__=="__main__": raise SystemExit(main())
