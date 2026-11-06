"""阶段5（提示词8）评测运行器：逐条执行 30 条评测用例，产出 Markdown 评测报告。

用法（在 my-tool/ 目录下）：
    python3 scripts/run_eval.py                # 全量 30 条
    python3 scripts/run_eval.py --limit 5      # 快速试跑前 5 条
    python3 scripts/run_eval.py --strict       # 有失败时退出码为 1（可接 CI）

执行方式：进程内起 TestClient 直接调 FastAPI 应用（与 pytest 同一套路），
真机评测时 LLM_API_KEY 指向 glm-4-flash，即为真实模型评测；
评测库用临时 SQLite（跑完即删），不污染真实会话与整改单数据。

每条用例的执行流程：
1. 多轮类先依次发送 context 里的 user 轮（agent_mode=False 固定流程，
   让系统自己生成 assistant 回复进入会话历史——指代考核才真实）；
2. 发送正式 question（agent_mode=True 智能体模式）；
3. 解析 SSE：收集工具调用序列与回答全文；遇 confirm_request（建单 HITL
   挂起）自动点"确认"，模拟员工放行，继续消费恢复流；
4. 调 evaluation.judge_case 判定，最后汇总渲染 Markdown 报告。
"""

import argparse
import json
import os
import pathlib
import sys
import tempfile
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from my_tool.evaluation import judge_case, summarize  # noqa: E402

CASES_PATH = ROOT / "data" / "eval_cases.json"
# 报告必须放 docs/：知识库会索引 data/*.md，报告放 data/ 会污染检索结果
DEFAULT_REPORT = ROOT / "docs" / "eval_report.md"


def load_dotenv(path: pathlib.Path) -> None:
    """极简 .env 加载：KEY=VALUE 逐行 setdefault，不覆盖已有环境变量。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):                   # 兼容 source .env 的 export 格式
            line = line[7:].strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def parse_sse(text: str) -> list:
    """把 SSE 响应文本解析成 [(event, data_dict)] 列表。"""
    out = []
    for frame in text.strip().split("\n\n"):
        event, data = None, None
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if event and data is not None:
            out.append((event, data))
    return out


def consume_stream(events: list) -> tuple:
    """消费一段 SSE 事件：(工具名序列, delta 拼接文本, confirm_token 或 None)。"""
    tools, answer, confirm_token = [], "", None
    for e, d in events:
        if e == "tool":
            tools.append(d["name"])
        elif e == "delta":
            answer += d["text"]
        elif e == "confirm_request":
            confirm_token = d["token"]
    return tools, answer, confirm_token


def run_case(client, token: str, case: dict) -> dict:
    """执行单条用例，返回 {tools, answer}。多轮类先发上文，建单确认自动放行。"""
    headers = {"Authorization": "Bearer " + token}
    tools, answer = [], ""

    # 多轮上下文：先发 context 的 user 轮，让系统生成真实回答进入会话历史
    # （context 里的 assistant 内容只作文档说明，不发——那是我们拟的理想答案）
    for turn in case.get("context", []):
        if turn["role"] != "user":
            continue
        res = client.post("/api/chat", json={"question": turn["content"],
                                             "agent_mode": False}, headers=headers)
        assert res.status_code == 200, f"上文发送失败：{res.text}"
        answer += "".join(d["text"] for e, d in parse_sse(res.text) if e == "delta")

    # 正式提问：智能体模式。建单 HITL 挂起时自动点确认（最多 3 次防死循环）
    pending = {"question": case["question"], "agent_mode": True}
    for _ in range(3):
        res = client.post("/api/chat" if "question" in pending else "/api/chat/confirm",
                          json=pending, headers=headers)
        assert res.status_code == 200, f"评测请求失败：{res.text}"
        seg_tools, seg_answer, confirm_token = consume_stream(parse_sse(res.text))
        tools += seg_tools
        answer += seg_answer
        if not confirm_token:
            break
        pending = {"token": confirm_token, "approve": True}   # 评测员工：同意建单
    return {"tools": tools, "answer": answer}


def render_report(results: list, model_name: str) -> str:
    """把判定结果渲染成 Markdown 评测报告（汇总 + 未通过明细 + 全量一览）。"""
    by_cat = summarize(results)
    total = len(results)
    passed = sum(1 for r in results if r["verdict"]["ok"])
    lines = [
        "# 连锁巡店与运营改进 · 智能体评测报告",
        "",
        f"- 评测时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- 被测模型：{model_name}",
        f"- 用例总数：{total}，通过：{passed}，**通过率 {passed / total:.0%}**",
        "",
        "## 分类汇总",
        "",
        "| 分类 | 用例数 | 通过 | 通过率 |",
        "|---|---|---|---|",
    ]
    label = {"policy": "制度类（含陷阱题）", "tool": "工具调用类",
             "violation": "违规意图类", "multi_turn": "多轮上下文类"}
    for cat in ("policy", "tool", "violation", "multi_turn"):
        s = by_cat.get(cat, {"total": 0, "passed": 0, "rate": "-"})
        lines.append(f"| {label[cat]} | {s['total']} | {s['passed']} | {s['rate']} |")

    failed = [r for r in results if not r["verdict"]["ok"]]
    lines += ["", "## 未通过明细", ""]
    if not failed:
        lines.append("全部通过。")
    for r in failed:
        excerpt = (r["answer"][:120] + "…") if len(r["answer"]) > 120 else r["answer"]
        lines += [f"### 用例 {r['id']}（{label[r['category']]}）",
                  f"- **问题**：{r['question']}",
                  f"- **期望**：{r['expected_behavior']}",
                  f"- **实际工具**：{'、'.join(r['tools']) or '（无）'}",
                  f"- **实际回答**：{excerpt or '（空）'}",
                  f"- **失败原因**：{'；'.join(r['verdict']['failures'])}", ""]

    lines += ["", "## 全量一览", "",
              "| id | 分类 | 问题 | 实际工具调用 | 结果 |",
              "|---|---|---|---|---|"]
    short = {"policy": "制度", "tool": "工具", "violation": "违规", "multi_turn": "多轮"}
    for r in results:
        mark = "✅" if r["verdict"]["ok"] else "❌"
        lines.append(f"| {r['id']} | {short[r['category']]} | {r['question']} "
                     f"| {'、'.join(r['tools']) or '—'} | {mark} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="连锁巡店智能体评测运行器")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（试跑用）")
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_REPORT, help="报告输出路径")
    parser.add_argument("--strict", action="store_true", help="存在失败用例时退出码 1")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if "LLM_API_KEY" not in os.environ:
        print("未配置 LLM_API_KEY（环境变量或 my-tool/.env），无法评测", file=sys.stderr)
        return 2

    # 评测库隔离：临时 SQLite，进程结束即删，不污染真实数据
    os.environ["CHAT_DB"] = tempfile.mkstemp(suffix=".db")[1]

    from starlette.testclient import TestClient
    from my_tool.api.app import app

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[:args.limit]

    results = []
    with TestClient(app) as client:                      # startup 建临时库+种子数据
        login = client.post("/api/auth/login",
                            json={"username": "zhangsan", "password": "123456"}).json()
        token = login["token"]
        for i, case in enumerate(cases, 1):
            try:
                run = run_case(client, token, case)
                verdict = judge_case(case, run["tools"], run["answer"])
            except Exception as exc:                     # 单条崩了不拖垮全量
                verdict = {"ok": False,
                           "failures": [f"执行异常：{type(exc).__name__}: {exc}"]}
                run = {"tools": [], "answer": ""}
            results.append({**case, "tools": run["tools"],
                            "answer": run["answer"], "verdict": verdict})
            mark = "✅" if verdict["ok"] else "❌"
            print(f"[{i:>2}/{len(cases)}] {mark} 用例{case['id']} {case['question']}")

    model = os.environ.get("CHAT_MODEL", "glm-4-flash")
    args.out.write_text(render_report(results, model), encoding="utf-8")
    passed = sum(1 for r in results if r["verdict"]["ok"])
    print(f"\n评测完成：{passed}/{len(results)} 通过，报告已写入 {args.out}")
    return 1 if args.strict and passed < len(results) else 0


if __name__ == "__main__":
    sys.exit(main())
