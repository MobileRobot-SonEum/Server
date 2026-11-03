"""AIHub 한국수어(KSL-Guide 문장 데이터) 원본 → data/examples.jsonl 내부 형식 변환기 (자리만).

내부 형식 (한 줄에 하나):
    {"glosses": ["나", "감기", "진료", "원하다"], "sentence": "감기 때문에 진료를 받고 싶어요.",
     "type": "declarative", "scenario": "hospital", "source": "aihub:<원본 파일/ID>"}

TODO (원본 형식 확인 후):
  1. 원본 파일 형식 확인 (JSON/XML/CSV, 인코딩, 문장 단위 vs 형태소 단위 주석)
  2. gloss 필드 추출 · 정규화 (동형이의어 번호 '감기1' 같은 표기 처리 방침 정하기)
  3. 한국어 문장 필드 추출
  4. 의문문 여부: 비수지 주석이 있으면 사용, 없으면 문장부호('?')로 추정
  5. 시나리오 태그가 없으면 "general"
  6. 중복 제거 후 저장. 직접 작성 예시(source=manual)와 섞을지 결정

사용 예 (구현 후):
    python -m app.data_tools.aihub_converter --src <AIHub 폴더> --out data/examples_aihub.jsonl
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterator


def iter_aihub_records(src: Path) -> Iterator[dict]:
    raise NotImplementedError("AIHub 원본 형식을 확인한 뒤 구현합니다.")


def convert(src: Path, out: Path) -> int:
    raise NotImplementedError("AIHub 원본 형식을 확인한 뒤 구현합니다.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("data/examples_aihub.jsonl"))
    args = ap.parse_args()
    n = convert(args.src, args.out)
    print(f"{n}개 변환 → {args.out}")


if __name__ == "__main__":
    main()
