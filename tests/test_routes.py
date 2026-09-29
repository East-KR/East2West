"""소스 라우트 추출(eastshift routes)과 지도의 '코드에만 있음' 회색 카드. 임시 폴더와 저장소의 demo-app 만 읽는다."""
import json
from pathlib import Path

from eastshift import routes
from eastshift.pwtest import map as screen_map


def test_normalize_matches_map_route_keys():
    assert routes.normalize("/orders/<int:id>/") == "/orders/{id}"
    assert routes.normalize(r"^orders/(?P<pk>\d+)/$") == "/orders/{id}"
    assert routes.normalize("/users/:userId/posts/:postId") == "/users/{id}/posts/{id}"
    assert routes.normalize("/blog/[slug]") == "/blog/{id}"
    assert routes.normalize("orders") == "/orders" and routes.normalize("/") == "/" and routes.normalize("") == "/"
    assert routes.normalize("/v2/orders?x=1#top") == "/v2/orders"
    assert routes.normalize("/orders/17") == "/orders/{id}"  # 지도와 같은 규칙 (숫자 조각)


def test_classify_screen_action_api_asset():
    assert routes.classify("/orders", ["GET"]) == "screen" and routes.classify("/orders", []) == "screen"
    assert routes.classify("/orders/{id}/cancel", ["POST"]) == "action"
    assert routes.classify("/api/orders", ["GET"]) == "api" and routes.classify("/orders.json", ["GET"]) == "api"
    assert routes.classify("/static/app.css", ["GET"]) == "asset" and routes.classify("/favicon.ico", []) == "asset"


def _src(tmp_path: Path) -> Path:
    src = tmp_path / "app"
    (src / "py").mkdir(parents=True)
    (src / "py" / "views.py").write_text(
        '@app.route("/orders", methods=["GET", "POST"])\ndef a(): pass\n'
        '@router.get("/items/{item_id}")\ndef b(): pass\n'
        '@bp.post("/items")\ndef c(): pass\n', encoding="utf-8")
    (src / "py" / "urls.py").write_text('urlpatterns = [path("customers/<int:pk>/", v), re_path(r"^reports/$", v)]\n', encoding="utf-8")
    (src / "java").mkdir()
    (src / "java" / "OrderController.java").write_text(
        '@RestController\n@RequestMapping("/admin")\npublic class OrderController {\n'
        '  @GetMapping("/orders")\n  public String list() {}\n'
        '  @PostMapping(value = "/orders")\n  public String save() {}\n'
        '  @GetMapping\n  public String home() {}\n}\n', encoding="utf-8")
    (src / "js").mkdir()
    (src / "js" / "server.js").write_text("app.get('/health', h);\nrouter.post('/login', h);\napp.get('/users/:id', h);\n", encoding="utf-8")
    (src / "js" / "App.tsx").write_text('<Route path="/dashboard" element={<D/>} />\nconst r = [{ path: "/profile/:id", component: P }];\n', encoding="utf-8")
    (src / "web" / "pages").mkdir(parents=True)
    (src / "web" / "pages" / "orders" / "[id].tsx").parent.mkdir(parents=True)
    (src / "web" / "pages" / "orders" / "[id].tsx").write_text("export default () => null\n", encoding="utf-8")
    (src / "web" / "pages" / "_app.tsx").write_text("export default () => null\n", encoding="utf-8")
    (src / "webapp").mkdir()
    (src / "webapp" / "list.jsp").write_text("<html/>", encoding="utf-8")
    (src / "webapp" / "WEB-INF").mkdir()
    (src / "webapp" / "WEB-INF" / "inc.jsp").write_text("<html/>", encoding="utf-8")  # forward 조각: 주소가 아니다
    (src / "webapp" / "WEB-INF" / "struts-config.xml").write_text('<action path="/saveOrder" type="x"/>\n<url-pattern>/legacy/menu</url-pattern>\n<url-pattern>*.do</url-pattern>\n', encoding="utf-8")
    (src / "tests").mkdir()
    (src / "tests" / "test_views.py").write_text('client.get("/never-a-route")\n', encoding="utf-8")
    (src / "node_modules" / "x").mkdir(parents=True)
    (src / "node_modules" / "x" / "index.js").write_text("app.get('/vendor-route', h)\n", encoding="utf-8")
    return src


def test_extract_frameworks_and_skips(tmp_path):
    rs, scanned = routes.extract(_src(tmp_path))
    by = {r.path: r for r in rs}
    assert by["/orders"].methods == ["GET", "POST"] and by["/orders"].kind == "screen" and by["/orders"].source == "flask/fastapi"
    assert by["/items/{id}"].methods == ["GET"] and by["/items"].kind == "action"
    assert by["/customers/{id}"].source == "django" and "/reports" in by
    assert by["/admin/orders"].methods == ["GET", "POST"] and by["/admin"].methods == ["GET"]  # 클래스 접두 + 빈 @GetMapping
    assert by["/health"].source == "express" and by["/login"].kind == "action" and "/users/{id}" in by
    assert by["/dashboard"].source == "router" and "/profile/{id}" in by
    assert by["/orders/{id}"].source == "file route" and "/_app" not in by
    assert by["/list.jsp"].source == "page file" and not any("inc.jsp" in p for p in by)
    assert by["/saveOrder"].source == "struts" and "/legacy/menu" in by and not any("*" in p for p in by)
    assert "/never-a-route" not in by and "/vendor-route" not in by  # tests/, node_modules/ 는 건너뛴다
    assert scanned >= 7
    assert by["/orders"].file == "py/views.py" and by["/orders"].line == 1


def test_extract_stdlib_hand_routing_of_demo_apps():
    """표준 라이브러리 손 라우팅: 포털 데모 as-is 의 화면 7개와 동작 2개. 파일 하나만 넘겨도 된다."""
    rs, _ = routes.extract(Path("demo-app/portal_asis/app.py"))
    screens = {r.path for r in rs if r.kind == "screen"}
    actions = {r.path: r.methods for r in rs if r.kind == "action"}
    assert {"/", "/orders", "/orders/{id}", "/customers", "/customers/{id}", "/settings", "/notices"} <= screens
    assert actions == {"/orders/{id}/cancel": ["POST"], "/customers/{id}/memo": ["POST"]}
    assert all(r.file == "app.py" for r in rs)
    # as-is 와 to-be 는 소스가 따로다: 폴더를 넘기면 그쪽 주소만 나온다 (공지사항은 as-is 에만, 보고서는 to-be 에만)
    asis = {r.path for r in routes.extract(Path("demo-app/portal_asis"))[0] if r.kind == "screen"}
    tobe = {r.path for r in routes.extract(Path("demo-app/portal_tobe"))[0] if r.kind == "screen"}
    assert "/notices" in asis and "/notices" not in tobe and "/reports" in tobe and "/reports" not in asis
    assert not {r.path for r in routes.extract(Path("demo-app/portal_app.py"))[0]}  # 실행기에는 라우트가 없다
    legacy = {r.path for r in routes.extract(Path("demo-app/legacy_app.py"))[0]}
    assert {"/", "/menu.jsp", "/order.jsp", "/list.jsp", "/done.jsp", "/app/orders/{id}"} <= legacy
    reservation = {r.path: r.kind for r in routes.extract(Path("demo-app/app.py"))[0]}
    assert reservation["/reservations/{id}"] == "screen" and reservation["/reserve"] == "action"


def test_write_marks_new_dir_and_summary(tmp_path):
    out = tmp_path / "crawl" / "shop" / "routes.json"
    data = routes.write(Path("demo-app/portal_tobe"), out)
    assert out.exists() and (out.parent / ".crawl-output").exists()  # 뒤에 오는 crawl 이 이 폴더를 산출물 폴더로 본다
    assert data["counts"]["screen"] >= 6 and "portal_tobe" in data["src"]
    assert "/orders/{id}" in routes.summary(data)


def _crawl_dir(tmp_path: Path, urls: list[str]) -> Path:
    """탐색 그래프 흉내: 시작(/)에서 urls 각각으로 가는 전이 하나씩."""
    d = tmp_path / "crawl" / "shop"
    d.mkdir(parents=True)
    nodes = [{"id": 0, "url": "http://x/", "title": "홈", "label": "홈", "snapshot": '- heading "홈"', "screenshot": "", "loc": "/", "filled": False, "path": []}]
    edges = []
    for i, u in enumerate(urls, 1):
        nodes.append({"id": i, "url": f"http://x{u}", "title": u, "label": u, "snapshot": f'- heading "{u}"', "screenshot": "", "loc": u, "filled": False, "path": [i - 1]})
        edges.append({"id": i - 1, "src": 0, "dst": i, "kind": "transition", "mode": "as-is", "steps": [{"role": "link", "name": u, "action": "click"}], "dialogs": [], "http_errors": []})
    (d / "graph.json").write_text(json.dumps({"start": "/", "start_url": "http://x/", "nodes": nodes, "edges": edges}), encoding="utf-8")
    return d


def test_map_marks_code_only_routes_gray(tmp_path):
    d = _crawl_dir(tmp_path, ["/orders", "/orders/3"])
    routes_json = {"src": "app", "generated_at": "now", "files_scanned": 1, "counts": {"screen": 4, "action": 1},
                   "routes": [{"path": "/", "raw": "/", "methods": ["GET"], "kind": "screen", "file": "a.py", "line": 1, "source": "stdlib", "also": []},
                              {"path": "/orders", "raw": "/orders", "methods": ["GET"], "kind": "screen", "file": "a.py", "line": 2, "source": "stdlib", "also": []},
                              {"path": "/orders/{id}", "raw": "/orders/{id}", "methods": ["GET"], "kind": "screen", "file": "a.py", "line": 3, "source": "stdlib", "also": []},
                              {"path": "/settings", "raw": "/settings", "methods": ["GET"], "kind": "screen", "file": "a.py", "line": 9, "source": "stdlib", "also": ["b.py:4"]},
                              {"path": "/orders/{id}/cancel", "raw": "…", "methods": ["POST"], "kind": "action", "file": "a.py", "line": 5, "source": "stdlib", "also": []}]}
    (d / "routes.json").write_text(json.dumps(routes_json), encoding="utf-8")
    g = screen_map.build_from_crawl("shop", d, "asis")
    assert g["code_routes"]["screens"] == 4 and g["code_routes"]["reached"] == 3 and g["code_routes"]["unreached"] == ["/settings"]
    r = g["routes"]["/settings"]
    assert r["status"] == "unreached" and r["states"] == [] and r["evidence"] == ["a.py:9", "b.py:4"]
    assert "/orders/{id}/cancel" not in g["routes"]  # 동작(POST)은 화면이 아니다
    assert g["counts"]["unreached"] == 1
    page = screen_map.render(g)
    assert "data-st='unreached'" in page and "코드에만 있음<b>1</b>" in page and "소스 화면 4 중 3 도달" in page and "node unreached" in page and "a.py:9" in page
    assert "화면<b>3</b>" in page  # 찾은 화면 수에는 회색을 세지 않는다
    # 잣대가 없으면 회색도 없다
    (d / "routes.json").unlink()
    g2 = screen_map.build_from_crawl("shop", d, "asis")
    assert g2["code_routes"] is None and "unreached" not in g2["counts"] and "/settings" not in g2["routes"]
    page2 = screen_map.render(g2)
    assert "node unreached" not in page2 and "소스 화면" not in page2 and "legend'><span><i class='unreached'" not in page2
