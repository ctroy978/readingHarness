from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.lessons import LessonBuildError, compile_lesson, dump_lesson, load_protocol


def build(source_dir: Path, output_dir: Path, protocol_path: Path, check: bool) -> int:
    try:
        protocol = load_protocol(protocol_path)
    except LessonBuildError as exc:
        print(exc, file=sys.stderr)
        return 1
    failures = 0
    output_dir.mkdir(parents=True, exist_ok=True)
    for source_path in sorted(source_dir.glob("*.source.json")):
        try:
            source = json.loads(source_path.read_text(encoding="utf-8"))
            lesson = compile_lesson(source, protocol)
        except (OSError, json.JSONDecodeError, LessonBuildError) as exc:
            print(f"{source_path}: {exc}", file=sys.stderr)
            failures += 1
            continue
        output_path = output_dir / f"{lesson['lesson_id']}.json"
        content = dump_lesson(lesson)
        if check:
            if not output_path.is_file() or output_path.read_bytes() != content:
                print(f"Generated lesson is stale: {output_path}", file=sys.stderr)
                failures += 1
        else:
            output_path.write_bytes(content)
            print(output_path)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile lesson sources into runtime JSON")
    parser.add_argument("--source-dir", type=Path, default=Path("lessons/src"))
    parser.add_argument("--output-dir", type=Path, default=Path("lessons"))
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "server/config/protocol.json",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return build(args.source_dir, args.output_dir, args.protocol, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
