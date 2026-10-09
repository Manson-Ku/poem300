#!/usr/bin/env python3
"""Prepare deterministic TTS synthesis text before API generation.

Pronunciation is a text-layer contract:
- source_text is canonical and immutable;
- each (character, reading) rule is classified once;
- validated canonical rules keep the source character;
- validated proxy rules replace only the intended occurrence;
- unclassified rules are reported for text-level review;
- human listening QA is reserved for a new proxy rule, not every asset.

This script makes no API calls.

Examples:
    py scripts/prepare_tts_synthesis_text.py --age 8 --audit-only
    py scripts/prepare_tts_synthesis_text.py --age 8 --apply
    py scripts/prepare_tts_synthesis_text.py --age 8 --require-classified
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


RuleKey = tuple[str, str]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def age_poem_ids(poems_path: Path, age: int) -> set[int]:
    ids: set[int] = set()
    with poems_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if int(row["recommended_age"]) == age:
                ids.add(int(row["poem_id"]))
    return ids


def load_rules(path: Path) -> dict[RuleKey, dict[str, Any]]:
    payload = read_json(path)
    result: dict[RuleKey, dict[str, Any]] = {}
    for rule in payload.get("rules", []):
        key = (str(rule["character"]), str(rule["reading"]))
        if key in result:
            raise ValueError(f"Duplicate TTS pronunciation rule: {key}")
        strategy = str(rule["strategy"])
        status = str(rule["status"])
        if strategy not in {"canonical", "proxy"}:
            raise ValueError(f"Invalid strategy for {key}: {strategy}")
        if status != "validated":
            raise ValueError(
                f"Production registry may contain only validated rules: {key}"
            )
        if strategy == "proxy" and not str(rule.get("proxy", "")).strip():
            raise ValueError(f"Proxy rule missing proxy character: {key}")
        result[key] = rule
    return result


def all_occurrences(text: str, fragment: str) -> list[int]:
    if not fragment:
        return []
    starts: list[int] = []
    start = 0
    while True:
        index = text.find(fragment, start)
        if index < 0:
            return starts
        starts.append(index)
        start = index + 1


def locate_target(
    source_text: str,
    *,
    character: str,
    context: str,
) -> int:
    candidates: set[int] = set()

    context_parts = [
        part.strip()
        for part in re.split(r"[/／]", context)
        if part.strip()
    ]
    for part in context_parts:
        relative_positions = [
            index
            for index, value in enumerate(part)
            if value == character
        ]
        if len(relative_positions) != 1:
            continue
        rel = relative_positions[0]
        for start in all_occurrences(source_text, part):
            candidates.add(start + rel)

    if len(candidates) == 1:
        return next(iter(candidates))

    direct = [
        index
        for index, value in enumerate(source_text)
        if value == character
    ]
    if len(direct) == 1:
        return direct[0]

    raise ValueError(
        "Cannot identify one target occurrence: "
        f"character={character!r} context={context!r} "
        f"source_text={source_text!r} "
        f"context_candidates={sorted(candidates)} "
        f"direct_candidates={direct}"
    )


def prepare_asset_text(
    item: dict[str, Any],
    rules: dict[RuleKey, dict[str, Any]],
) -> tuple[str, list[RuleKey], list[RuleKey]]:
    source_text = str(item["source_text"])
    replacements: dict[int, str] = {}
    classified: list[RuleKey] = []
    unclassified: list[RuleKey] = []

    for pronunciation in item.get("pronunciations", []):
        character = str(pronunciation["character"])
        reading = str(pronunciation["reading"])
        context = str(pronunciation.get("context", ""))
        key = (character, reading)
        rule = rules.get(key)
        if rule is None:
            unclassified.append(key)
            continue

        classified.append(key)
        if rule["strategy"] == "canonical":
            continue

        position = locate_target(
            source_text,
            character=character,
            context=context,
        )
        proxy = str(rule["proxy"])
        previous = replacements.get(position)
        if previous is not None and previous != proxy:
            raise ValueError(
                f"Conflicting proxy replacements at {position}: "
                f"{previous!r} vs {proxy!r}"
            )
        replacements[position] = proxy

    chars = list(source_text)
    for position, proxy in replacements.items():
        if len(proxy) != 1:
            raise ValueError(
                "Proxy must currently be exactly one character: "
                f"position={position} proxy={proxy!r}"
            )
        chars[position] = proxy

    return "".join(chars), classified, unclassified


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve pronunciation at the text layer before TTS."
    )
    parser.add_argument("--age", type=int, required=True)
    parser.add_argument("--poems", default="data/poems.csv")
    parser.add_argument(
        "--tts-overrides",
        default="data/tts_pronunciation_overrides.json",
    )
    parser.add_argument(
        "--proxy-registry",
        default="data/tts_pronunciation_proxy_registry.json",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Report classification coverage without writing files.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Materialize synthesis_text for fully classified assets. "
            "Unclassified assets are never partially rewritten."
        ),
    )
    parser.add_argument(
        "--require-classified",
        action="store_true",
        help="Exit non-zero when any pronunciation rule is unclassified.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.audit_only and args.apply:
        raise SystemExit("--audit-only and --apply are mutually exclusive")

    poems_path = Path(args.poems)
    overrides_path = Path(args.tts_overrides)
    registry_path = Path(args.proxy_registry)

    poem_ids = age_poem_ids(poems_path, args.age)
    payload = read_json(overrides_path)
    rules = load_rules(registry_path)

    scoped_items = [
        item
        for item in payload.get("items", [])
        if int(item["poem_id"]) in poem_ids
    ]

    seen_rules: Counter[RuleKey] = Counter()
    unclassified_examples: dict[RuleKey, list[str]] = defaultdict(list)
    fully_classified_assets = 0
    proxy_assets = 0
    canonical_only_assets = 0
    unclassified_assets = 0
    changed_assets = 0
    errors: list[str] = []

    for item in scoped_items:
        for pronunciation in item.get("pronunciations", []):
            key = (
                str(pronunciation["character"]),
                str(pronunciation["reading"]),
            )
            seen_rules[key] += 1

        try:
            prepared, _classified, unclassified = prepare_asset_text(
                item,
                rules,
            )
        except Exception as exc:
            errors.append(
                f"{item['poem_id']}|{item['audio_type']}|"
                f"{item.get('scene_no', 0)}: {exc}"
            )
            continue

        if unclassified:
            unclassified_assets += 1
            label = (
                f"p{int(item['poem_id']):03d} "
                f"{item['audio_type']} "
                f"s{int(item.get('scene_no', 0)):02d}"
            )
            for key in set(unclassified):
                if len(unclassified_examples[key]) < 3:
                    unclassified_examples[key].append(label)
            continue

        fully_classified_assets += 1
        if prepared == str(item["source_text"]):
            canonical_only_assets += 1
        else:
            proxy_assets += 1
            existing = str(item.get("synthesis_text") or "")
            if existing and existing != prepared:
                errors.append(
                    f"{item['poem_id']}|{item['audio_type']}|"
                    f"{item.get('scene_no', 0)}: existing synthesis_text "
                    f"does not match registry: {existing!r} != {prepared!r}"
                )
                continue
            if args.apply:
                if existing != prepared:
                    changed_assets += 1
                item["synthesis_text"] = prepared
                item["synthesis_text_source"] = (
                    "tts_pronunciation_proxy_registry_v1"
                )

    unclassified_rules = sorted(
        [key for key in seen_rules if key not in rules],
        key=lambda key: (key[0], key[1]),
    )
    classified_rules = sorted(
        [key for key in seen_rules if key in rules],
        key=lambda key: (key[0], key[1]),
    )

    print("TTS text-layer pronunciation preparation")
    print(f"age={args.age}")
    print(f"sensitive_assets={len(scoped_items)}")
    print(f"unique_rules={len(seen_rules)}")
    print(f"classified_rules={len(classified_rules)}")
    print(f"unclassified_rules={len(unclassified_rules)}")
    print(f"fully_classified_assets={fully_classified_assets}")
    print(f"canonical_only_assets={canonical_only_assets}")
    print(f"proxy_assets={proxy_assets}")
    print(f"unclassified_assets={unclassified_assets}")
    print(f"errors={len(errors)}")

    if unclassified_rules:
        print("\nUnclassified text rules:")
        for character, reading in unclassified_rules:
            examples = ", ".join(
                unclassified_examples.get((character, reading), [])
            )
            print(
                f"  {character} {reading} "
                f"occurrences={seen_rules[(character, reading)]} "
                f"examples={examples}"
            )

    if errors:
        print("\nERRORS:")
        for error in errors:
            print(f"  {error}")

    if args.apply and not errors:
        write_json(overrides_path, payload)
        print(f"\nupdated={overrides_path.as_posix()}")
        print(f"changed_proxy_assets={changed_assets}")

    if errors:
        return 1
    if args.require_classified and unclassified_rules:
        return 2

    print("AUDIT PASS" if not args.apply else "APPLY PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
