"""骨架冒烟: health 可用、错误信封格式正确。"""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] is True


def test_unknown_route_envelope(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_validation_error_envelope(client):
    # containers 路由存在但需要认证: 认证实现后应 401/403/503,骨架期 501,均为信封格式
    r = client.get("/api/v1/containers")
    assert r.status_code in (200, 401, 403, 503, 501)
    assert "error" in r.json()


def test_router_registration_regression(client):
    """路由级回归: 所有端点必须真实注册(防装饰器被吞/粘贴类事故)。

    用 HTTP 探测: 端点存在 → 401/403/503/422/405 之一;不存在 → 404 + not_found。
    (FastAPI 0.141 的 include_router 不再扁平化 app.routes,不能靠遍历路由表。)
    """
    expected = [
        "/api/health",
        "/api/v1/auth/login",
        "/api/v1/auth/setup",
        "/api/v1/auth/me",
        "/api/v1/auth/password",
        "/api/v1/keys",
        "/api/v1/keys/1",
        "/api/v1/containers",
        "/api/v1/containers/abc",
        "/api/v1/containers/abc/logs",
        "/api/v1/containers/abc/logs/stream",
        "/api/v1/containers/abc/stats",
        "/api/v1/containers/abc/start",
        "/api/v1/stacks",
        "/api/v1/stacks/demo",
        "/api/v1/stacks/demo/logs",
        "/api/v1/jobs",
        "/api/v1/jobs/j_x",
        "/api/v1/jobs/j_x/stream",
        "/api/v1/audit",
        "/api/v1/version",
    ]
    for p in expected:
        r = client.get(p)
        absent = r.status_code == 404 and r.json().get("error", {}).get("code") == "not_found"
        assert not absent, f"路由未注册: {p} -> {r.status_code} {r.text[:80]}"
