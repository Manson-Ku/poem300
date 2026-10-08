#!/usr/bin/env python3
"""Upgrade Age 8 visual plans to source-grounded visual_plan_v2.

Deterministic and API-free. Canonical poem text and Scene boundaries are
never changed. The checked-in data/poems.csv is the applied production SSOT.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

NOTES = {
    "p004_s03": "月亮與影子的擬人只作詩意陪伴；不要新增真實人物，也不要把影子畫成獨立角色。",
    "p005_s02": "「斷腸」是極度思念的比喻；不得畫受傷、器官或血腥內容。",
    "p034_s03": "邊塞與風沙呈現守邊歲月感；不畫戰鬥、屍體、流血或受傷特寫。",
    "p037_s03": "以等待遠征丈夫平安歸來為重點；戰爭只作遠方背景，不畫戰鬥傷亡。",
    "p077_s02": "拔劍表現困頓與迷惘，不是攻擊或自傷；畫面避免劍指人物或傷害動作。",
    "p077_s04": "姜太公、伊尹典故只作含蓄歷史意象，不做超自然事件，也不搶走本句主體。",
    "p098_s01": "以荒城、草木與戰亂後環境表現時局；不出現屍體、血腥或受傷特寫。",
    "p098_s03": "烽火是戰亂與家書難得的背景證據；不畫近距離戰鬥與傷亡。",
    "p101_s02": "「未招魂」表達驚魂未定，不畫真實鬼魂、招魂儀式或恐怖靈異畫面。",
    "p103_s04": "「冤魂」與汨羅是屈原歷史追念；不畫鬼魂、溺水或遺體。",
    "p105_s01": "以墓地祭別與追思為主，保持莊重；不呈現屍體、開棺或恐怖元素。",
    "p107_s04": "遠方戰事只作國事憂思背景；不畫戰鬥、傷亡或血腥。",
    "p113_s04": "「毒龍」是內心煩惱惡念的譬喻，不畫真實怪獸；以安靜禪修與潭景表達。",
    "p117_s02": "「波撼岳陽城」表現洞庭湖氣勢，不畫城市被洪水摧毀或災害場景。",
    "p127_s03": "「輕生」指不畏危險、勇於守邊，不是自傷；不得呈現自殺或自殘。",
    "p163_s01": "黃鶴仙人屬傳說，可作柔和遠景意象；不要建立成全詩的真實超自然 continuity。",
    "p175_s04": "諸葛亮去世以歷史追思呈現；不畫遺體、死亡瞬間或血腥。",
    "p249_s01": "軍事場景保持兒童可接受的遠景與守備氛圍；不畫受傷、流血或屍體。",
    "p250_s01": "將軍與夜間軍事活動以守備氣氛呈現；不畫傷亡或血腥。",
    "p251_s01": "敵軍撤退以遠景動勢呈現；不得畫殺傷、追砍命中或屍體。",
    "p251_s02": "追擊只呈現出發與雪地行軍，不畫武器擊中人物或傷亡。",
    "p252_s01": "凱旋與軍隊氣氛可表現，但不畫戰鬥傷亡或血腥。",
    "p252_s02": "飲酒與金甲舞是歷史宴飲情境，不強調醉態危險行為；以鼓舞與慶祝氣氛為主。",
    "p283_s01": "斷戟是沙中出土的歷史遺物，不把畫面轉成正在發生的戰場。",
    "p283_s02": "二喬被困是反事實歷史想像；用銅雀臺與人物遠景象徵，不畫綁縛、擄掠或受辱。",
    "p288_s01": "人物為年少少女時，保持兒童友善、端莊、非性感化；重點放在豆蔻春景與詩意比喻。",
    "p299_s01": "楊貴妃之死只呈現歷史事件後的離別與空景，不畫死亡瞬間、遺體或暴力。",
    "p311_s01": "巫山雲雨是文學典故；以雲霧神話意象處理，避免情色化或成人情慾畫面。",
    "p119_s03": "丹竈、仙桃依原詩作道家山房意象，不渲染危險煉製或真實超自然效果。",
    "p119_s04": "「醉流霞」是詩意仙酒意象，以宴飲文化背景處理，不鼓勵或聚焦醉酒行為。"
}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poems", default="data/poems.csv")
    ap.add_argument("--scenes", default="data/scenes.csv")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    pp, sp = Path(args.poems), Path(args.scenes)
    with pp.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        poems = list(reader)
    with sp.open("r", encoding="utf-8-sig", newline="") as f:
        scenes = list(csv.DictReader(f))

    changed = 0
    scene_count = 0

    for poem in poems:
        if int(poem["recommended_age"]) != 8:
            continue

        pid = int(poem["poem_id"])
        poem_scenes = sorted(
            (s for s in scenes if int(s["poem_id"]) == pid),
            key=lambda s: int(s["scene_no"]),
        )
        poem_lines = (
            poem["content"].replace("\r\n", "\n").replace("\r", "\n").split("\n")
        )
        explanation_lines = (
            poem["child_explanation_6_8"]
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .split("\n")
        )

        if len(poem_scenes) != len(poem_lines):
            raise RuntimeError(
                f"p{pid:03d}: scenes={len(poem_scenes)} lines={len(poem_lines)}"
            )
        if len(explanation_lines) != len(poem_lines):
            raise RuntimeError(
                f"p{pid:03d}: explanations={len(explanation_lines)} "
                f"lines={len(poem_lines)}"
            )

        for index, scene in enumerate(poem_scenes):
            if scene["original_line"] != poem_lines[index]:
                raise RuntimeError(f"{scene['scene_id']}: original_line mismatch")
            if scene["child_explanation_line"] != explanation_lines[index]:
                raise RuntimeError(f"{scene['scene_id']}: explanation mismatch")

        world = (
            f"唐代〈{poem['title']}〉的共同詩意世界；保持古代中國時代感與全詩氣氛一致。"
            "具體人物、景物、地點與動作只依當前 Scene 的原詩和既有兒童解釋建立，"
            "不提前加入其他 Scene 事件，也不把比喻、典故或想像強行畫成字面事實。"
        )

        plan_scenes = []
        for scene in poem_scenes:
            plan_scenes.append(
                {
                    "id": scene["scene_id"],
                    "setting": "依當前原詩與兒童解釋直接建立",
                    "time": "原詩有明示才具體化，未明示不額外推定",
                    "mood": "依當前原詩與兒童解釋",
                    "entities": [],
                    "actions": [],
                    "visual_focus": scene["child_explanation_line"],
                    "note": NOTES.get(
                        scene["scene_id"],
                        "只呈現本句原詩與既有兒童解釋可支持的內容，不提前加入其他 Scene 的主要事件。",
                    ),
                }
            )

        plan = {
            "v": "visual_plan_v2",
            "semantic_mode": "source_grounded_scene_v2",
            "world": world,
            "entities": [],
            "continuity": {
                "mode": "soft_world_continuity",
                "recurring_entities": [],
                "note": "同首詩共用時代感與整體氣氛；各 Scene 構圖獨立，不為 continuity 犧牲本句語意。",
            },
            "scenes": plan_scenes,
        }

        poem["visual_plan_version"] = "visual_plan_v2"
        poem["visual_plan_status"] = "approved"
        poem["visual_plan_json"] = json.dumps(
            plan, ensure_ascii=False, separators=(",", ":")
        )
        poem["visual_world"] = world
        poem["visual_scene_text"] = "\n".join(
            f"{s['id']}｜場景：{s['setting']}｜畫面核心：{s['visual_focus']}"
            for s in plan_scenes
        )
        poem["visual_object_text"] = (
            "source-grounded v2：不使用規則式關鍵字抽取硬猜 entity；"
            "人物、景物與動作由各 Scene 原詩與兒童解釋直接驅動。"
        )
        poem["visual_continuity_text"] = (
            "同首詩共用時代感與整體氣氛；各 Scene 構圖獨立。"
        )

        changed += 1
        scene_count += len(plan_scenes)

    print(
        f"poems={changed} scenes={scene_count} "
        f"mode={'check' if args.check else 'write'}"
    )
    if changed != 100 or scene_count != 395:
        return 1
    if args.check:
        return 0

    with pp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(poems)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
