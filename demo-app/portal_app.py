"""포털 데모 실행기: 변형 이름으로 as-is 앱(demo-app/portal_asis/)이나 to-be 앱(demo-app/portal_tobe/)을 띄운다. 표준 라이브러리만 사용.

python demo-app/portal_app.py <port> asis | asis-patched                                            → portal_asis/app.py
python demo-app/portal_app.py <port> tobe | tobe-fixed | tobe-renamed | tobe-custom | tobe-modern | tobe-wip   → portal_tobe/app.py

두 앱은 코드를 공유하지 않는다. 실제 전환처럼 as-is 와 to-be 가 각자 완결된 소스라서 `east2west routes` 가 각 폴더(east2west.json 의 소스 위치)의 주소만 뽑고,
지도의 회색(코드에만 있음)이 사실을 반영한다: as-is 에만 있는 공지사항(/notices)은 to-be 소스에 없고, tobe-wip 의 보고서(/reports)는 as-is 소스에 없다.
변형별 설명과 라우트는 각 앱의 첫 docstring.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ASIS = ("asis", "asis-patched")
TOBE = ("tobe", "tobe-fixed", "tobe-renamed", "tobe-custom", "tobe-modern", "tobe-wip")


def load(side: str):
    path = Path(__file__).parent / f"portal_{side}" / "app.py"
    spec = importlib.util.spec_from_file_location(f"portal_{side}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8820
    variant = sys.argv[2] if len(sys.argv) > 2 else "asis"
    assert variant in ASIS + TOBE, f"{variant}: 변형은 {ASIS + TOBE}"
    load("asis" if variant in ASIS else "tobe").serve(port, variant)
