"""对话审计接口（阶段3）：按员工/时间筛选，疑似违规标红。仅管理员。"""

from fastapi import APIRouter, Depends, Query

from my_tool.api import db
from my_tool.api.security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/api/admin/audit")
async def audit(
    user_id: int = None,
    start: str = Query(None, description="起始日期 YYYY-MM-DD"),
    end: str = Query(None, description="结束日期 YYYY-MM-DD"),
    flagged: int = Query(None, description="传 1 只看疑似违规"),
):
    """会话审计列表：首问预览、消息数、疑似违规数。"""
    items = db.audit_query(user_id=user_id, start=start, end=end,
                           flagged_only=(flagged == 1))
    return {"code": "OK", "items": items}
