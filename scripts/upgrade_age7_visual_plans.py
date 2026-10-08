#!/usr/bin/env python3
"""Upgrade Age 7 visual plans to source-grounded visual_plan_v2.

This script is deterministic and API-free. It does not modify canonical
poem text or Scene boundaries. The checked-in data/poems.csv is already
the applied production SSOT; this script documents/reproduces the rule.
"""

from __future__ import annotations
import argparse, csv, json
from pathlib import Path

NOTES = {
    "p085_s04": "「兒女共霑巾」是離別情態，不畫成實際兒童；以成年友人道別為主。",
    "p109_s04": "「王孫」不必畫成王室人物；依本句表達人在秋山中願意留下。",
    "p236_s04": "「藁砧」是丈夫的代稱，不把砧板當畫面主角；重點是盼望丈夫歸來。",
    "p257_s02": "「冰心在玉壺」是品格清白的比喻；不要畫人體心臟，可用清澈玉壺作含蓄象徵。",
    "p260_s02": "表現豪邁與戰爭風險，但禁止血腥、屍體或受傷特寫。",
    "p272_s01": "「沙似雪、月如霜」是月色比喻，不必真的下雪或結霜。",
    "p290_s02": "「墮樓人」是歷史典故聯想；兒童版不要畫人物墜樓，以落花與荒園表現盛衰。",
    "p292_s01": "「雙鯉」在此是書信意象，不把兩條魚畫成主要事件。",
    "p293_s02": "「金龜婿」指顯貴丈夫，不畫金色烏龜；重點是丈夫早朝離去。",
    "p297_s02": "「問鬼神」不做恐怖靈異畫面；重點是皇帝關注錯置的諷喻。",
    "p302_s01": "兒童版禁止屍骨、血腥與殘肢，以荒涼河岸、遺落軍裝或遠景表現戰爭代價。",
    "p302_s02": "以妻子夢見丈夫仍平安的對照呈現，不出現屍骨。",
}

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--poems",default="data/poems.csv")
    ap.add_argument("--scenes",default="data/scenes.csv")
    ap.add_argument("--check",action="store_true")
    args=ap.parse_args()
    pp,sp=Path(args.poems),Path(args.scenes)
    with pp.open("r",encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f); headers=list(reader.fieldnames or []); poems=list(reader)
    with sp.open("r",encoding="utf-8-sig",newline="") as f:
        scenes=list(csv.DictReader(f))
    changed=scount=0
    for p in poems:
        if int(p["recommended_age"]) != 7: continue
        ss=sorted((s for s in scenes if s["poem_id"]==p["poem_id"]),key=lambda s:int(s["scene_no"]))
        world=(f"唐代〈{p['title']}〉的共同詩意世界；保持古代中國時代感與全詩氣氛一致。"
               "具體人物、景物、地點與動作只依當前 Scene 的原詩和既有兒童解釋建立，"
               "不提前加入其他 Scene 事件，也不把比喻或典故強行畫成字面事件。")
        plan_scenes=[]
        for s in ss:
            plan_scenes.append({
                "id":s["scene_id"],
                "setting":"依當前原詩與兒童解釋直接建立",
                "time":"原詩有明示才具體化，未明示不額外推定",
                "mood":"依當前原詩與兒童解釋",
                "entities":[],
                "actions":[],
                "visual_focus":s["child_explanation_line"],
                "note":NOTES.get(s["scene_id"],"只呈現本句原詩與既有兒童解釋可支持的內容，不提前加入其他 Scene 的主要事件。"),
            })
        plan={"v":"visual_plan_v2","semantic_mode":"source_grounded_scene_v2","world":world,"entities":[],
              "continuity":{"mode":"soft_world_continuity","recurring_entities":[],
              "note":"同首詩共用時代感與整體氣氛；各 Scene 構圖獨立，不為 continuity 犧牲本句語意。"},
              "scenes":plan_scenes}
        p["visual_plan_version"]="visual_plan_v2"; p["visual_plan_status"]="approved"
        p["visual_plan_json"]=json.dumps(plan,ensure_ascii=False,separators=(",",":"))
        p["visual_world"]=world
        p["visual_scene_text"]="\n".join(f"{s['id']}｜場景：{s['setting']}｜畫面核心：{s['visual_focus']}" for s in plan_scenes)
        p["visual_object_text"]="source-grounded v2：不使用規則式關鍵字抽取硬猜 entity；人物、景物與動作由各 Scene 原詩與兒童解釋直接驅動。"
        p["visual_continuity_text"]="同首詩共用時代感與整體氣氛；各 Scene 構圖獨立。"
        changed+=1; scount+=len(plan_scenes)
    print(f"poems={changed} scenes={scount} mode={'check' if args.check else 'write'}")
    if changed!=50 or scount!=127: return 1
    if args.check: return 0
    with pp.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=headers,lineterminator="\n");w.writeheader();w.writerows(poems)
    return 0
if __name__=="__main__": raise SystemExit(main())
