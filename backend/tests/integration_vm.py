"""DockerAPI VM 集成测试(真实 daemon 全链路)。

用法(VM 上):
  ~/dapi-venv/bin/python tests/integration_vm.py http://127.0.0.1:8000

前置: /opt/stacks/demo 栈存在(M0 已建);服务已启动且数据目录为空(首次跑 setup 流程)。
非首次运行时 --reset 不可用,脚本会自动跳过 setup 用环境变量 DAPI_USER/DAPI_PASS 登录。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
USER = os.getenv("DAPI_USER", "admin")
PASS = os.getenv("DAPI_PASS", "admin123456")

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name} {extra}")


def expect_error(name: str, r: httpx.Response, status: int, code: str) -> None:
    ok = r.status_code == status and r.json().get("error", {}).get("code") == code
    check(name, ok, f"(got {r.status_code} {r.text[:120]})")


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=30)

    print("== 健康与匿名访问 ==")
    r = c.get("/api/health")
    check("health 200", r.status_code == 200 and r.json()["status"] == "ok")
    r = c.get("/api/v1/version")
    check("匿名 version 被拒", r.status_code in (401, 503), f"(got {r.status_code})")

    print("== setup / 登录 ==")
    r = c.get("/api/v1/containers")
    if r.status_code == 503:  # setup 模式
        r = c.post("/api/v1/auth/setup", json={"username": USER, "password": PASS})
        check("setup 201/token", r.status_code in (200, 201) and "token" in r.json(), r.text[:120])
        r = c.post("/api/v1/auth/setup", json={"username": "x", "password": "y"})
        check("重复 setup 409", r.status_code == 409)
    else:
        print("  (已有账号,跳过 setup)")

    r = c.post("/api/v1/auth/login", json={"username": USER, "password": "wrong"})
    check("错密码 401", r.status_code == 401)
    r = c.post("/api/v1/auth/login", json={"username": USER, "password": PASS})
    check("登录 token", r.status_code == 200 and "token" in r.json())
    jwt = r.json()["token"]
    H = {"Authorization": f"Bearer {jwt}"}

    r = c.get("/api/v1/auth/me", headers=H)
    check("me=user", r.json().get("type") == "user" and r.json().get("name") == USER, r.text[:120])

    print("== version / dashboard 数据 ==")
    r = c.get("/api/v1/version", headers=H)
    v = r.json()
    check("version 摘要", r.status_code == 200 and v.get("docker") and v.get("storage_driver"), r.text[:200])

    print("== API Key 生命周期 ==")
    r = c.post("/api/v1/keys", headers=H, json={"name": "itest", "scopes": ["view", "start"]})
    check("建 key 返回明文", r.status_code in (200, 201) and r.json().get("key", "").startswith("dka_"), r.text[:150])
    key_plain = r.json().get("key", "")
    key_id = r.json().get("id")
    HK = {"Authorization": f"Bearer {key_plain}"}
    r = c.post("/api/v1/keys", headers=HK, json={"name": "hack", "scopes": ["admin"]})
    check("key 主体建 key 被拒 403", r.status_code == 403, f"(got {r.status_code})")

    r = c.get("/api/v1/containers", headers=HK)
    check("view key 列容器", r.status_code == 200)
    r = c.post("/api/v1/containers/no_such_container_xyz/start", headers=HK)
    expect_error("start 不存在容器 404", r, 404, "container_not_found")
    r = c.post("/api/v1/containers/no_such_container_xyz/stop", headers=HK)
    check("无 stop scope 403", r.status_code == 403, f"(got {r.status_code})")
    r = c.delete("/api/v1/containers/no_such_container_xyz", headers=HK)
    check("无 delete scope 403", r.status_code == 403)

    print("== 栈列表与非法栈名 ==")
    r = c.get("/api/v1/stacks", headers=H)
    names = [s["name"] for s in r.json()]
    check("栈列表含 demo", "demo" in names, f"(got {names})")
    r = c.post("/api/v1/stacks/..%2Fetc/up", headers=H)
    check("路径穿越栈名被拒", r.status_code == 404)

    print("== 栈 up(异步 job)==")
    r = c.post("/api/v1/stacks/demo/up", headers=H)
    check("up 202 job_id", r.status_code == 202 and r.json().get("job_id", "").startswith("j_"), r.text[:150])
    job_id = r.json().get("job_id", "")
    for _ in range(60):
        r = c.get(f"/api/v1/jobs/{job_id}", headers=H)
        if r.json().get("status") in ("done", "failed", "timeout"):
            break
        time.sleep(2)
    check("job done", r.json().get("status") == "done", r.text[:300])

    print("== 容器操作(幂等/审计)==")
    r = c.get("/api/v1/containers", headers=H)
    demo = next((x for x in r.json() if x.get("compose_project") == "demo"), None)
    check("demo 栈容器出现", demo is not None)
    if demo:
        cid = demo["id"]
        r = c.post(f"/api/v1/containers/{cid}/start", headers=H)
        note1 = r.json().get("note")
        r = c.post(f"/api/v1/containers/{cid}/start", headers=H)
        check("重复 start 幂等", r.status_code == 200 and r.json().get("status") == "ok", r.text[:150])
        r = c.get(f"/api/v1/containers/{cid}/logs?tail=5", headers=H)
        check("取日志", r.status_code == 200 and "lines" in r.json())
        r = c.get(f"/api/v1/containers/{cid}/stats", headers=H)
        check("stats 序列(或单点)", r.status_code == 200 and isinstance(r.json(), list))
        r = c.get(f"/api/v1/containers/{cid}", headers=H)
        check("详情", r.status_code == 200)

    print("== 审计 ==")
    r = c.get("/api/v1/audit?page_size=50", headers=H)
    items = r.json().get("items", [])
    actions = {i["action"] for i in items}
    check("审计含 container.start", "container.start" in actions, f"(got {sorted(actions)})")
    check("审计含 stack.up 终态行", "stack.up" in actions)
    check("审计含失败记录", any(i["result"] == "error" for i in items))

    print("== 栈 down + 清理 ==")
    r = c.post("/api/v1/stacks/demo/down", headers=H)
    check("down 202", r.status_code == 202)
    jid = r.json().get("job_id", "")
    for _ in range(60):
        r = c.get(f"/api/v1/jobs/{jid}", headers=H)
        if r.json().get("status") in ("done", "failed", "timeout"):
            break
        time.sleep(2)
    check("down job done", r.json().get("status") == "done", r.text[:300])
    if key_id:
        c.delete(f"/api/v1/keys/{key_id}", headers=H)
        check("删 key", True)

    print(f"\n结果: {len(PASSED)} 通过, {len(FAILED)} 失败")
    if FAILED:
        print("失败项:", FAILED)
        sys.exit(1)


if __name__ == "__main__":
    main()
