"""统一错误出口：所有接口失败都返回 {"code", "message"}，message 为中文。"""

import fastapi
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """业务错误：携带 HTTP 状态码、错误码和中文信息，由全局处理器统一输出。"""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def install_error_handlers(app: FastAPI) -> None:
    """把三类异常都接住，保证响应体永远是 {code, message} 中文结构。"""

    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status,
                            content={"code": exc.code, "message": exc.message})

    @app.exception_handler(fastapi.exceptions.RequestValidationError)
    async def handle_validation_error(_: Request, exc):
        # FastAPI 自带的 422 校验错误换成 400 + 中文信息
        return JSONResponse(status_code=400,
                            content={"code": "INVALID_PARAM", "message": "请求参数格式不正确"})

    @app.exception_handler(Exception)
    async def handle_unknown_error(_: Request, exc: Exception):
        # 兜底：任何未预期异常都不向客户端泄露堆栈
        return JSONResponse(status_code=500,
                            content={"code": "INTERNAL_ERROR", "message": "服务器内部错误，请联系管理员"})
