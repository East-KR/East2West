"""소스에서 라우트(화면 주소) 목록을 뽑는다: 탐색 지도의 잣대. 코드에는 있는데 탐색·시나리오가 닿지 않은 화면을 지도가 회색으로 표시한다.

east2west routes <소스 폴더> --out crawl/<app>/routes.json

실행하지 않고 파일을 정규식으로 읽는다. 잡는 것:
  Python   Flask/FastAPI 데코레이터(@app.route, @router.get …), Django path()/re_path(), 표준 라이브러리 손 라우팅(path == "/x", path.startswith("/x/"), parts == ["x"], parts[0] == "x" and len(parts) == n)
  Java/Kotlin  Spring @RequestMapping/@GetMapping …(클래스 접두 포함), JAX-RS @Path
  JS/TS    Express·Koa(app.get("/x")), React/Vue/Angular 라우터(path: "/x", <Route path="/x">), Next/Nuxt 파일 라우트(pages/, app/)
  기타     JSP·PHP·ASP 파일(웹 루트 기준 주소), Struts/web.xml(<action path>, <url-pattern>), Rails routes.rb, Go(HandleFunc, r.GET), C#([Route], [HttpGet], MapGet)

주소는 지도와 같은 규칙으로 정규화한다: 자리표시자(<int:id>, {id}, :id, [id], 정규식 그룹)와 숫자 조각은 {id}, 끝 슬래시는 뺀다.
종류: screen(GET 화면) · action(POST 등 동작만) · api(/api/…, .json) · asset(정적 파일). 지도의 잣대는 screen 이다.

못 잡는 것: 데이터 테이블에서 만드는 주소(메뉴 DB, SCREENS 배열), 런타임 조건(변형·권한)으로 갈리는 주소, 문자열을 이어 붙인 주소.
그래서 결과는 '코드가 선언한 주소의 하한'이다. 여기 있는데 지도에 없으면 확실히 놓친 것이고, 여기 없다고 없는 것은 아니다.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pwtest.mutation import route_key

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "dist", "build", "target", "out", ".next", ".nuxt", "__pycache__", "coverage",
             "vendor", "bower_components", ".idea", ".vscode", "test", "tests", "__tests__", "spec", "e2e", "cypress", "fixtures", "migrations"}
SKIP_FILES = re.compile(r"(^test_.*\.py$|_test\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|Test\.java$|Tests?\.kt$|_test\.go$)")
WEB_ROOTS = ("webapp", "WebContent", "web", "public", "www", "htdocs", "wwwroot", "html", "static-pages")
PAGE_EXT = {".jsp", ".jspx", ".php", ".asp", ".aspx", ".cfm"}
HTML_EXT = {".html", ".htm"}
ASSET_EXT = {".css", ".js", ".mjs", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".txt", ".xml", ".pdf", ".webp"}
ASSET_PREFIX = ("/static/", "/assets/", "/public/", "/resources/", "/dist/", "/_next/", "/favicon")
API_PREFIX = ("/api/", "/rest/", "/ajax/", "/graphql", "/ws/")
MAX_BYTES = 2_000_000

# 자리표시자 → {id}
PLACEHOLDER = re.compile(r"<[^>/]*>|\{[^}/]*\}|\[[^\]/]*\]|:[A-Za-z_]\w*\??|\(\?P<\w+>[^)]*\)|\([^)]*\)|\\d\+|\\w\+|\.\*\??|\.\+\??|\*+")
REGEX_LEFTOVER = re.compile(r"[\\()\[\]+?^$|]")


@dataclass
class Route:
    path: str
    raw: str
    methods: list[str]
    kind: str
    file: str
    line: int
    source: str
    also: list[str] = field(default_factory=list)  # 같은 주소를 선언한 다른 자리 (file:line)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "raw": self.raw, "methods": sorted(set(self.methods)), "kind": self.kind,
                "file": self.file, "line": self.line, "source": self.source, "also": self.also}


def normalize(raw: str) -> str:
    """선언된 주소 → 지도의 라우트 열쇠. 쿼리·해시·정규식 표식을 벗기고 자리표시자와 숫자 조각을 {id}로."""
    s = raw.strip().split("?", 1)[0].split("#", 1)[0]
    s = s.replace("\\.", ".").replace("\\/", "/")
    s = s.lstrip("^").rstrip("$")
    s = PLACEHOLDER.sub("{id}", s)
    segs = []
    for seg in s.split("/"):
        if not seg:
            continue
        if "{id}" in seg or REGEX_LEFTOVER.search(seg):
            seg = "{id}"
        segs.append(seg)
    return route_key("/" + "/".join(segs))


def classify(path: str, methods: list[str]) -> str:
    low = path.lower()
    ext = Path(low.split("{id}")[0] if low.endswith("{id}") else low).suffix
    if low.startswith(ASSET_PREFIX) or ext in ASSET_EXT:
        return "asset"
    if low.startswith(API_PREFIX) or ext == ".json" or low.endswith("/api"):
        return "api"
    if methods and not any(m in ("GET", "ANY", "HEAD") for m in methods):
        return "action"
    return "screen"


# -- 파일별 추출기: (raw_path, methods, source) 를 낸다 -------------------------------------------------------
_PY_DECOR = re.compile(r"@(\w+)\.(route|get|post|put|patch|delete|api_route|head|options)\(\s*[rf]?['\"]([^'\"]+)['\"]")
_PY_METHODS = re.compile(r"methods\s*=\s*[\[(]([^\])]*)[\])]")
_DJANGO = re.compile(r"\b(?:path|re_path|url)\(\s*r?['\"]([^'\"]*)['\"]\s*,")
_STD_EQ = re.compile(r"\b(?:u\.path|url\.path|self\.path|parsed\.path|path|p)\s*==\s*['\"](/[^'\"]*)['\"]")
_STD_IN = re.compile(r"\b(?:u\.path|url\.path|self\.path|parsed\.path|path|p)\s+in\s+[\[(]([^\])]*)[\])]")
_STD_STARTS = re.compile(r"\b(?:u\.path|url\.path|self\.path|parsed\.path|path|p)\.startswith\(\s*['\"](/[^'\"]+)['\"]\s*\)")
_STD_PARTS_EQ = re.compile(r"\bparts\s*==\s*\[([^\]]*)\]")
_STD_PARTS_IDX = re.compile(r"\bparts\[(\d+)\]\s*==\s*['\"]([^'\"]+)['\"]")
_STD_PARTS_LEN = re.compile(r"len\(parts\)\s*==\s*(\d+)")
_STD_NOT_PARTS = re.compile(r"\bif\s+not\s+parts\s*:")
_STD_HANDLER = re.compile(r"def\s+do_(GET|POST|PUT|PATCH|DELETE|HEAD)\b")
_STR = re.compile(r"['\"]([^'\"]*)['\"]")

_JAVA_MAP = re.compile(r"@(Request|Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*\"([^\"]*)\"")
_JAVA_MAP_BARE = re.compile(r"@(Request|Get|Post|Put|Delete|Patch)Mapping\s*(?:\(\s*\))?\s*$")
_JAVA_METHOD = re.compile(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH)")
_JAXRS_PATH = re.compile(r"@Path\s*\(\s*\"([^\"]*)\"")
_JAXRS_VERB = re.compile(r"@(GET|POST|PUT|DELETE|PATCH|HEAD)\b")
_CLASS = re.compile(r"^\s*(?:public\s+|final\s+|abstract\s+|open\s+)*(?:class|interface|object)\s+\w+")

_JS_EXPRESS = re.compile(r"\b(?:app|router|server|route[rs]?|api|r|fastify|koa)\.(get|post|put|patch|delete|all|head|options)\(\s*[`'\"](/[^`'\"]*)[`'\"]")
_JS_ROUTE_ATTR = re.compile(r"<Route\b[^>]*\bpath\s*=\s*[{]?[`'\"]([^`'\"]+)[`'\"]")
_JS_PATH_KEY = re.compile(r"\bpath\s*:\s*[`'\"]([^`'\"]*)[`'\"]")
_NEXT_FILE = re.compile(r"(?:^|/)(?:pages|app)/(.+?)(?:/page|/index|/route)?\.(?:[jt]sx?|vue|svelte|mdx?)$")

_XML_ACTION = re.compile(r"<action\b[^>]*\bpath\s*=\s*\"([^\"]+)\"")
_XML_URL = re.compile(r"<url-pattern>\s*([^<\s]+)\s*</url-pattern>")
_RAILS = re.compile(r"^\s*(get|post|put|patch|delete|match)\s+['\"]([^'\"]+)['\"]")
_RAILS_RES = re.compile(r"^\s*resources?\s+:(\w+)")
_GO = re.compile(r"\.(?:HandleFunc|Handle|GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch|Any)\(\s*\"(/[^\"]*)\"")
_GO_VERB = re.compile(r"\.(GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch)\(")
_CS_ATTR = re.compile(r"\[(Route|HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch)\s*\(\s*\"([^\"]*)\"")
_CS_MAP = re.compile(r"\.Map(Get|Post|Put|Delete|Patch)\(\s*\"([^\"]*)\"")


def _join(prefix: str, p: str) -> str:
    if not prefix:
        return p if p.startswith("/") else "/" + p
    if not p or p == "/":
        return prefix
    return prefix.rstrip("/") + "/" + p.lstrip("/")


def _py(lines: list[str]) -> list[tuple[int, str, list[str], str]]:
    out = []
    handler = ""
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        m = _STD_HANDLER.search(line)
        if m:
            handler = m.group(1)
        for m in _PY_DECOR.finditer(line):
            verb = m.group(2).upper()
            methods = ([x.strip().strip("'\"").upper() for x in _PY_METHODS.search(line).group(1).split(",") if x.strip()] if _PY_METHODS.search(line)
                       else ([] if verb in ("ROUTE", "API_ROUTE") else [verb]))
            out.append((i, m.group(3), methods or ["GET"], "flask/fastapi"))
        for m in _DJANGO.finditer(line):
            out.append((i, m.group(1) or "/", [], "django"))
        hm = [handler] if handler else []
        for m in _STD_EQ.finditer(line):
            out.append((i, m.group(1), hm, "stdlib"))
        for m in _STD_IN.finditer(line):
            out += [(i, x, hm, "stdlib") for x in _STR.findall(m.group(1)) if x.startswith("/")]
        for m in _STD_STARTS.finditer(line):
            out.append((i, m.group(1).rstrip("/") + "/{id}", hm, "stdlib"))
        for m in _STD_PARTS_EQ.finditer(line):
            out.append((i, "/" + "/".join(_STR.findall(m.group(1))), hm, "stdlib"))
        if _STD_NOT_PARTS.search(line):
            out.append((i, "/", hm, "stdlib"))
        idx = {int(a): b for a, b in _STD_PARTS_IDX.findall(line)}
        if 0 in idx and not _STD_PARTS_EQ.search(line):
            ln = _STD_PARTS_LEN.search(line)
            n = int(ln.group(1)) if ln else max(idx) + 1
            out.append((i, "/" + "/".join(idx.get(k, "{id}") for k in range(n)), hm, "stdlib"))
    return out


def _java(lines: list[str]) -> list[tuple[int, str, list[str], str]]:
    out = []
    prefix, seen_class, pending_verb = "", False, []
    for i, line in enumerate(lines, 1):
        if _CLASS.match(line):
            seen_class = True
        m = _JAXRS_VERB.search(line)
        if m and not _JAVA_MAP.search(line):
            pending_verb = [m.group(1)]
        for m in _JAVA_MAP.finditer(line):
            kind, p = m.group(1), m.group(2)
            methods = [kind.upper()] if kind != "Request" else [x for x in _JAVA_METHOD.findall(line)]
            if not seen_class and kind == "Request":
                prefix = p
                continue
            out.append((i, _join(prefix, p), methods, "spring"))
        if _JAVA_MAP_BARE.search(line) and seen_class and prefix:  # @GetMapping 만 있으면 클래스 접두가 곧 주소
            verb = _JAVA_MAP_BARE.search(line).group(1)
            out.append((i, prefix, [verb.upper()] if verb != "Request" else [], "spring"))
        for m in _JAXRS_PATH.finditer(line):
            if not seen_class:
                prefix = m.group(1) if m.group(1).startswith("/") else "/" + m.group(1)
                continue
            out.append((i, _join(prefix, m.group(1)), pending_verb, "jax-rs"))
            pending_verb = []
    return out


def _js(lines: list[str]) -> list[tuple[int, str, list[str], str]]:
    out = []
    for i, line in enumerate(lines, 1):
        for m in _JS_EXPRESS.finditer(line):
            verb = m.group(1).upper()
            out.append((i, m.group(2), [] if verb == "ALL" else [verb], "express"))
        for m in _JS_ROUTE_ATTR.finditer(line):
            out.append((i, m.group(1), ["GET"], "router"))
        for m in _JS_PATH_KEY.finditer(line):
            p = m.group(1)
            if p and not p.startswith(("http", ".")) and "$" not in p[:1]:
                out.append((i, p, ["GET"], "router"))
    return out


def _xml(lines: list[str]) -> list[tuple[int, str, list[str], str]]:
    out = []
    for i, line in enumerate(lines, 1):
        out += [(i, m, [], "struts") for m in _XML_ACTION.findall(line)]
        out += [(i, m, [], "web.xml") for m in _XML_URL.findall(line) if m.startswith("/") and "*" not in m]
    return out


def _rb(lines: list[str]) -> list[tuple[int, str, list[str], str]]:
    out = []
    for i, line in enumerate(lines, 1):
        m = _RAILS.match(line)
        if m:
            out.append((i, m.group(2), [] if m.group(1) == "match" else [m.group(1).upper()], "rails"))
        m = _RAILS_RES.match(line)
        if m:
            out += [(i, f"/{m.group(1)}", ["GET"], "rails"), (i, f"/{m.group(1)}/{{id}}", ["GET"], "rails")]
    return out


def _go(lines: list[str]) -> list[tuple[int, str, list[str], str]]:
    out = []
    for i, line in enumerate(lines, 1):
        for m in _GO.finditer(line):
            v = _GO_VERB.search(line)
            out.append((i, m.group(1), [v.group(1).upper()] if v else [], "go"))
    return out


def _cs(lines: list[str]) -> list[tuple[int, str, list[str], str]]:
    out = []
    prefix = ""
    for i, line in enumerate(lines, 1):
        for kind, p in _CS_ATTR.findall(line):
            if kind == "Route" and "class " in "".join(lines[i:i + 3]):
                prefix = p if p.startswith("/") else "/" + p
                continue
            out.append((i, _join(prefix, p), [] if kind == "Route" else [kind[4:].upper()], "aspnet"))
        for kind, p in _CS_MAP.findall(line):
            out.append((i, p, [kind.upper()], "aspnet"))
    return out


EXTRACTORS = {".py": _py, ".java": _java, ".kt": _java, ".js": _js, ".mjs": _js, ".cjs": _js, ".ts": _js, ".jsx": _js, ".tsx": _js, ".vue": _js,
              ".xml": _xml, ".rb": _rb, ".go": _go, ".cs": _cs}


def _web_path(rel: Path) -> str | None:
    """JSP·PHP 같은 페이지 파일의 주소: 웹 루트 폴더 아래 상대 경로. 웹 루트가 없으면 소스 루트 기준."""
    parts = rel.parts
    for j in range(len(parts) - 1, -1, -1):
        if parts[j] in WEB_ROOTS:
            return "/" + "/".join(parts[j + 1:])
    return "/" + "/".join(parts)


def _next_route(rel: Path) -> str | None:
    m = _NEXT_FILE.search(rel.as_posix())
    if not m:
        return None
    p = m.group(1)
    if "/_" in "/" + p or p.startswith("api/"):  # _app, _document, API 라우트는 화면이 아니다
        return None
    p = re.sub(r"\([^/)]*\)/?", "", p)  # (group) 폴더는 주소에 없다
    return "/" + p.replace("[...", "[").replace("[[", "[")


def extract(src: Path) -> tuple[list[Route], int]:
    """소스 폴더 전체에서 라우트를 모은다. 같은 주소는 하나로 합치고(메서드 합집합), 첫 자리를 근거로 남긴다."""
    src = src.resolve()
    files = [src] if src.is_file() else sorted(src.rglob("*"))  # 파일 하나(단일 파일 앱)도 받는다
    root = src.parent if src.is_file() else src
    found: dict[str, Route] = {}
    scanned = 0

    def add(raw: str, methods: list[str], rel: str, line: int, source: str) -> None:
        if not raw or raw.startswith(("http://", "https://", "mailto:", "javascript:")):
            return
        path = normalize(raw)
        if path in found:
            r = found[path]
            r.methods = sorted(set(r.methods) | set(methods))
            r.kind = classify(path, r.methods)
            if len(r.also) < 5 and f"{rel}:{line}" not in r.also:
                r.also.append(f"{rel}:{line}")
            return
        found[path] = Route(path, raw, sorted(set(methods)), classify(path, methods), rel, line, source)

    for f in files:
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]) or SKIP_FILES.search(f.name):
            continue
        ext = f.suffix.lower()
        if ext in PAGE_EXT or (ext in HTML_EXT and any(p in WEB_ROOTS for p in rel.parts[:-1])):
            if "WEB-INF" in rel.parts:  # WEB-INF 아래 페이지는 주소로 못 들어간다 (컨트롤러가 forward 하는 조각). 설정 XML 은 읽는다
                continue
            scanned += 1
            add(_web_path(rel) or "", ["GET"], rel.as_posix(), 1, "page file")
            continue
        nx = _next_route(rel) if ext in (".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".mdx", ".md") else None
        if nx:
            add(nx, ["GET"], rel.as_posix(), 1, "file route")
        fn = EXTRACTORS.get(ext)
        if fn is None:
            continue
        try:
            if f.stat().st_size > MAX_BYTES:
                continue
            lines = f.read_text(encoding="utf-8", errors="strict").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for line, raw, methods, source in fn(lines):
            add(raw, methods, rel.as_posix(), line, source)
    return sorted(found.values(), key=lambda r: (r.kind != "screen", r.path)), scanned


def write(src: Path, out: Path) -> dict[str, Any]:
    routes, scanned = extract(src)
    counts: dict[str, int] = {}
    for r in routes:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    data = {"src": str(src), "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "files_scanned": scanned, "counts": counts,
            "routes": [r.to_dict() for r in routes]}
    new_dir = not out.parent.exists()
    out.parent.mkdir(parents=True, exist_ok=True)
    if new_dir:  # 탐색 산출물 폴더로 표시해 두어야 뒤에 오는 east2west crawl 이 이 폴더를 덮어쓸 수 있다
        (out.parent / ".crawl-output").write_text("east2west crawl output: regenerated on every crawl. Move reviewed files out before editing them.\n", encoding="utf-8")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def summary(data: dict[str, Any]) -> str:
    c = data["counts"]
    L = [f"{data['src']} · 파일 {data['files_scanned']}개 · 라우트 {len(data['routes'])}개 "
         f"(화면 {c.get('screen', 0)} · 동작 {c.get('action', 0)} · API {c.get('api', 0)} · 정적 {c.get('asset', 0)})"]
    for r in data["routes"]:
        if r["kind"] == "screen":
            L.append(f"  {r['path']:<40} {','.join(r['methods']) or 'ANY':<10} {r['file']}:{r['line']}")
    return "\n".join(L)
