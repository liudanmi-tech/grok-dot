"""工具定义与入参校验（技术方案 7.1 / 7.3）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

TYPES = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict}


class ToolError(Exception):
    """统一错误码：SCOPE_DENIED / NOT_FOUND / RATE_LIMITED / PENDING_APPROVAL / FORBIDDEN_ACTION /
    INVALID_ARGS / UPSTREAM_ERROR"""

    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


@dataclass
class ToolContext:
    bot_id: str
    trace_id: str
    scope: dict
    task_id: str | None = None
    requester: str | None = None
    outputs: list[str] = field(default_factory=list)
    kb_visibility: list[str] = field(default_factory=lambda: ["internal", "external"])


@dataclass
class Tool:
    name: str
    action_class: str
    schema: dict
    handler: Callable[[dict, ToolContext], Any]
    description: str = ""
    scope_keys: tuple[str, ...] = ()           # 入参中受作用域约束的维度（如 region、brand）
    describe: Callable[[dict, ToolContext], dict] | None = None  # 审批卡片用：target / 预览 / 依据


def validate(schema: dict, args: dict) -> None:
    if not isinstance(args, dict):
        raise ToolError("INVALID_ARGS", "入参必须是对象")
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args or args[key] in (None, ""):
            raise ToolError("INVALID_ARGS", f"缺少必填参数 {key}")
    if schema.get("additionalProperties") is False:
        extra = set(args) - set(props)
        if extra:
            raise ToolError("INVALID_ARGS", f"不允许的参数：{', '.join(sorted(extra))}")
    for key, val in args.items():
        spec = props.get(key)
        if not spec or val is None:
            continue
        t = spec.get("type")
        if t and not isinstance(val, TYPES[t]) or (t in ("number", "integer") and isinstance(val, bool)):
            raise ToolError("INVALID_ARGS", f"参数 {key} 应为 {t}")
        if "enum" in spec and val not in spec["enum"]:
            raise ToolError("INVALID_ARGS", f"参数 {key} 取值不在允许范围内")
        if t == "array" and "items" in spec and "enum" in spec["items"]:
            bad = [v for v in val if v not in spec["items"]["enum"]]
            if bad:
                raise ToolError("INVALID_ARGS", f"参数 {key} 含不允许的取值：{bad}")
        if t == "string" and "maxLength" in spec and len(val) > spec["maxLength"]:
            raise ToolError("INVALID_ARGS", f"参数 {key} 超过最大长度")
        if t in ("number", "integer") and "minimum" in spec and val < spec["minimum"]:
            raise ToolError("INVALID_ARGS", f"参数 {key} 小于最小值")
        if t == "array" and "minItems" in spec and len(val) < spec["minItems"]:
            raise ToolError("INVALID_ARGS", f"参数 {key} 至少需要 {spec['minItems']} 项")


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self.tools:
            raise ToolError("NOT_FOUND", f"工具不存在：{name}")
        return self.tools[name]

    def specs(self, names: list[str] | None = None) -> list[dict]:
        return [
            {"name": t.name, "action_class": t.action_class, "description": t.description, "input_schema": t.schema}
            for n, t in self.tools.items() if names is None or n in names
        ]
