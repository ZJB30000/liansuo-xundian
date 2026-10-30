"""阶段3 管理后台测试：登录鉴权、知识库管理、提示词模板、对话审计。"""

import pytest

from tests.conftest import auth, login, parse_sse


# ============================================================
# 登录鉴权
# ============================================================
def test_login_wrong_password(client):
    res = client.post("/api/auth/login", json={"username": "admin", "password": "错密码"})
    assert res.status_code == 401
    body = res.json()
    assert body["code"] == "LOGIN_FAILED"
    assert body["message"] == "用户名或密码错误"


def test_me_shows_role(client, admin_token):
    res = client.get("/api/auth/me", headers=auth(admin_token))
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"


def test_staff_forbidden_on_admin_api(client, staff_token):
    """接口级权限校验：员工访问管理接口 → 403。"""
    res = client.get("/api/admin/kb/documents", headers=auth(staff_token))
    assert res.status_code == 403
    assert res.json()["code"] == "FORBIDDEN"


# ============================================================
# 知识库管理
# ============================================================
def test_kb_upload_list_delete(client, admin_token):
    """上传 md → 列表可见 → 删除 → 再删报 404。"""
    content = "# 补充制度\n\n从今天起所有门店晨检必须拍照上传，保存 ninety 天以上。" * 3
    res = client.post("/api/admin/kb/upload", headers=auth(admin_token),
                      files={"file": ("补充制度.md", content.encode(), "text/markdown")})
    assert res.status_code == 200
    body = res.json()
    assert body["filename"] == "补充制度.md"
    assert body["chunks"] >= 1

    items = client.get("/api/admin/kb/documents", headers=auth(admin_token)).json()["items"]
    assert any(d["filename"] == "补充制度.md" for d in items)

    doc_id = body["doc_id"]
    assert client.delete(f"/api/admin/kb/documents/{doc_id}",
                         headers=auth(admin_token)).json()["code"] == "OK"
    res = client.delete(f"/api/admin/kb/documents/{doc_id}", headers=auth(admin_token))
    assert res.status_code == 404
    assert res.json()["code"] == "DOC_NOT_FOUND"


def test_kb_upload_rejects_txt(client, admin_token):
    """只允许 md/pdf → 415。"""
    res = client.post("/api/admin/kb/upload", headers=auth(admin_token),
                      files={"file": ("说明.txt", "内容".encode(), "text/plain")})
    assert res.status_code == 415
    assert res.json()["code"] == "UNSUPPORTED_FILE"


def test_kb_upload_rejects_empty(client, admin_token):
    res = client.post("/api/admin/kb/upload", headers=auth(admin_token),
                      files={"file": ("空文件.md", b"", "text/markdown")})
    assert res.status_code == 400
    assert res.json()["code"] == "EMPTY_FILE"


def test_kb_uploaded_doc_reaches_chat(client, admin_token, staff_token):
    """上传的新文档立即进入检索：问“晨检拍照”能命中补充制度片段。"""
    content = "# 晨检新规\n\n所有门店晨检必须当场拍照上传系统，当日十点前完成，漏报按违规处理。" * 2
    client.post("/api/admin/kb/upload", headers=auth(admin_token),
                files={"file": ("晨检新规.md", content.encode(), "text/markdown")})

    res = client.post("/api/chat", json={"question": "晨检拍照有什么要求"},
                      headers=auth(staff_token))
    events = parse_sse(res.text)
    src_docs = [d for e, d in events if e == "sources"][0]["items"]
    src_docs = [s["doc"] for s in src_docs]
    assert "晨检新规.md" in src_docs


# ============================================================
# 提示词模板
# ============================================================
def test_prompt_default_is_inspection(client, admin_token):
    """默认模板是巡店口径（不是报销）。"""
    body = client.get("/api/admin/prompt", headers=auth(admin_token)).json()
    assert "巡店制度" in body["content"]
    assert "报销" not in body["content"]


def test_prompt_put_and_take_effect_immediately(client, admin_token, staff_token):
    """改模板 → 员工下一次问答立即用新模板（读库不读代码）。"""
    res = client.put("/api/admin/prompt", headers=auth(admin_token),
                     json={"content": "你是严格按巡店手册回答的测试模板XYZ123。"})
    assert res.status_code == 200

    client.post("/api/chat", json={"question": "留样要多少克"}, headers=auth(staff_token))
    from tests.conftest import CAPTURED_PROMPTS
    assert any("测试模板XYZ123" in p for p in CAPTURED_PROMPTS)   # 新模板已生效
    assert all("输出约束" in p for p in CAPTURED_PROMPTS)          # 红线仍在


def test_prompt_put_rejects_empty(client, admin_token):
    res = client.put("/api/admin/prompt", headers=auth(admin_token), json={"content": "  "})
    assert res.status_code == 400
    assert res.json()["code"] == "EMPTY_PROMPT"


# ============================================================
# 对话审计
# ============================================================
def test_audit_flags_violation_question(client, admin_token, staff_token):
    """含“规避检查”意图的问题被标记，审计页可筛出。"""
    client.post("/api/chat", json={"question": "留样冰箱要上锁吗"}, headers=auth(staff_token))
    client.post("/api/chat", json={"question": "巡店记录没写，怎么先补记录才不被发现"},
                headers=auth(staff_token))

    body = client.get("/api/admin/audit?flagged=1", headers=auth(admin_token)).json()
    assert len(body["items"]) >= 1                     # 只剩被标记的会话
    assert body["items"][0]["flagged_count"] >= 1
    assert "补记录" in body["items"][0]["first_question"]

    all_items = client.get("/api/admin/audit", headers=auth(admin_token)).json()["items"]
    assert len(all_items) == 2                         # 全量审计能看到两条会话


# ============================================================
# 管理页面（SSR）
# ============================================================
def test_admin_pages_render(client, admin_token):
    assert client.get("/login").status_code == 200
    kb = client.get("/admin/kb")
    assert kb.status_code == 200 and "制度文档列表" in kb.text
    prompts = client.get("/admin/prompts")
    assert prompts.status_code == 200 and "巡店制度" in prompts.text
    assert client.get("/admin/audit").status_code == 200


def test_seeded_accounts_work(client):
    """种子账号可直接登录（admin/admin123、zhangsan/123456）。"""
    assert login(client, "admin", "admin123")
    assert login(client, "zhangsan", "123456")
