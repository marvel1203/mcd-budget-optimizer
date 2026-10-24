"""MCP 客户端 + 能力自适应。

问题背景：麦当劳 MCP 暴露哪些工具、工具叫什么名字、参数怎么传，各家实现不一样
（而且大赛期间还可能调整）。与其把工具名硬编码进 Skill，不如在运行时先问一遍
`tools/list`，再把拿到的工具按语义映射到本 Skill 需要的四种能力上。

这样换一个 MCP 实现、或者对方改了工具名，Skill 不用改代码也能继续工作。
映射不上的能力会被标记缺失，调用方可以选择降级（比如读本地菜单缓存）。

传输支持两种：
  stdio —— 启动子进程，按行读写 JSON-RPC（MCP 默认方式）
  http  —— POST JSON-RPC 到指定 URL（部分远端实现）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 能力契约
# --------------------------------------------------------------------------

@dataclass
class Capability:
    """Skill 需要的一种能力。keywords 用于在 tools/list 结果里做语义匹配。"""
    key: str
    label: str
    keywords: tuple[str, ...]
    arg_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    required: bool = True


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="menu",
        label="获取门店菜单（含价格与可售状态）",
        keywords=("menu", "meal", "dish", "food", "catalog", "sku", "product",
                  "菜品", "菜单", "餐品", "商品", "单品", "list_dish", "get_menu",
                  "search_menu", "query_meals"),
        arg_hints={"store": ("store", "store_id", "storeId", "shop", "shopId", "poi", "门店")},
    ),
    Capability(
        key="coupon",
        label="获取可用优惠券与活动",
        keywords=("coupon", "promotion", "discount", "activity", "deal", "offer",
                  "优惠", "券", "活动", "随心配", "促销"),
        arg_hints={"store": ("store", "store_id", "storeId", "shop", "shopId", "门店")},
    ),
    Capability(
        key="quote",
        label="试算购物车总价（不实际下单）",
        keywords=("quote", "preview", "calc", "calculate", "price", "estimate",
                  "试算", "预估", "算价", "价格", "结算", "预算", "cart_preview"),
        required=False,
    ),
    Capability(
        key="nutrition",
        label="获取餐品营养成分",
        keywords=("nutrition", "nutrient", "calorie", "kcal",
                  "营养", "热量", "成分"),
        required=False,
    ),
    Capability(
        key="order",
        label="下单",
        keywords=("order", "下单", "create_order", "submit", "place", "支付", "pay"),
        required=False,
    ),
)


def score_tool(tool: Mapping[str, Any], cap: Capability) -> float:
    """给一个工具对某种能力的匹配度打分（0~1）。"""
    name = str(tool.get("name", "")).lower()
    desc = str(tool.get("description", "") or "").lower()
    hay = f"{name} {desc}"
    if not hay.strip():
        return 0.0

    hits = 0.0
    for kw in cap.keywords:
        k = kw.lower()
        if k == name:
            hits += 1.0
        elif k in name:
            hits += 0.9
        elif k in desc:
            hits += 0.5
    if hits <= 0:
        return 0.0
    # 归一化：命中数「够用」即可满分，避免关键词多的能力天然吃亏
    return min(1.0, hits / 1.5)


@dataclass
class ToolBinding:
    capability: str
    tool_name: str
    score: float
    input_schema: Mapping[str, Any] = field(default_factory=dict)


class MCPClient:
    """最小可用的 MCP 客户端。"""

    def __init__(self, transport: str = "stdio", command: Sequence[str] | None = None,
                 url: str | None = None, timeout: float = 20.0, env: Mapping[str, str] | None = None,
                 headers: Mapping[str, str] | None = None):
        self.transport = transport
        self.command = list(command or [])
        self.url = url
        self.timeout = timeout
        self.env = {**os.environ, **(env or {})}
        self.headers = dict(headers or {})
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._id = 0
        self._session_id: str | None = None
        self._tools_cache: list[Mapping[str, Any]] | None = None

    # ---------- 生命周期 ----------

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start(self) -> None:
        if self.transport == "stdio":
            if not self.command:
                raise MCPError("stdio 传输需要提供 command，例如 ['npx','-y','@mcd/mcp']")
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self.env, text=True, bufsize=1,
            )
            self._handshake()
        elif self.transport == "http":
            if not self.url:
                raise MCPError("http 传输需要提供 url")
            self._handshake()
        else:
            raise MCPError(f"不支持的传输方式: {self.transport}")

    def close(self) -> None:
        proc = self._proc
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream is not None and not stream.closed:
                        stream.close()
                except Exception:
                    pass
        self._proc = None

    # ---------- 协议 ----------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, payload: Mapping[str, Any], expect_response: bool = True) -> Mapping[str, Any] | None:
        if self.transport == "stdio":
            return self._send_stdio(payload, expect_response)
        return self._send_http(payload, expect_response)

    def _send_stdio(self, payload: Mapping[str, Any], expect_response: bool) -> Mapping[str, Any] | None:
        assert self._proc and self._proc.stdin and self._proc.stdout
        with self._lock:
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
            if not expect_response:
                return None
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    raise MCPError("MCP 服务已关闭 stdout，未收到响应")
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # 跳过服务端主动推送的 notification（没有 id 字段）
                if "id" not in msg:
                    continue
                if "error" in msg and msg.get("error"):
                    raise MCPError(f"MCP 返回错误: {msg['error']}")
                return msg
            raise MCPError("等待 MCP 响应超时")

    def _send_http(self, payload: Mapping[str, Any], expect_response: bool) -> Mapping[str, Any] | None:
        """Streamable HTTP：POST JSON-RPC，响应可能是 JSON 也可能是 SSE 流。

        麦当劳 MCP 用这种方式（https://mcp.mcd.cn），请求头必须带
        Authorization: Bearer <token>，服务端可能下发 Mcp-Session-Id，
        后续请求要原样带回。
        """
        import urllib.request
        assert self.url
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self.headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            raw = resp.read().decode("utf-8")

        if not expect_response:
            return None
        msg = _parse_sse_or_json(raw)
        if "error" in msg and msg.get("error"):
            raise MCPError(f"MCP 返回错误: {msg['error']}")
        return msg

    def _handshake(self) -> None:
        resp = self._send({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-budget-optimizer", "version": "1.0.0"},
            },
        })
        if resp is None:
            raise MCPError("initialize 无响应")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"},
                   expect_response=False)

    # ---------- 工具 ----------

    def list_tools(self, refresh: bool = False) -> list[Mapping[str, Any]]:
        if self._tools_cache is not None and not refresh:
            return self._tools_cache
        resp = self._send({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list"})
        if resp is None:
            raise MCPError("tools/list 无响应")
        tools = (resp.get("result") or {}).get("tools", [])
        self._tools_cache = list(tools)
        return self._tools_cache

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        resp = self._send({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": dict(arguments or {})},
        })
        if resp is None:
            raise MCPError(f"tools/call {name} 无响应")
        result = resp.get("result") or {}
        # MCP 规范：result.content 是文本/资源块数组
        content = result.get("content")
        if content:
            texts = []
            for block in content:
                if isinstance(block, Mapping) and block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
            joined = "\n".join(texts).strip()
            parsed = _try_json(joined)
            return parsed if parsed is not None else joined
        return result

    # ---------- 能力映射 ----------

    def bind_capabilities(self, min_score: float = 0.45) -> dict[str, ToolBinding]:
        """把已发现的工具映射到能力上。返回 {能力key: 绑定}。"""
        tools = self.list_tools()
        bindings: dict[str, ToolBinding] = {}
        for cap in CAPABILITIES:
            best: ToolBinding | None = None
            for t in tools:
                s = score_tool(t, cap)
                if s < min_score:
                    continue
                if best is None or s > best.score:
                    best = ToolBinding(
                        capability=cap.key,
                        tool_name=str(t.get("name", "")),
                        score=s,
                        input_schema=t.get("inputSchema") or t.get("input_schema") or {},
                    )
            if best:
                bindings[cap.key] = best
        return bindings

    def missing_required(self, bindings: Mapping[str, ToolBinding]) -> list[str]:
        return [c.label for c in CAPABILITIES if c.required and c.key not in bindings]

    def describe(self, bindings: Mapping[str, ToolBinding]) -> str:
        lines = [f"发现 {len(self.list_tools())} 个工具，能力映射如下："]
        for cap in CAPABILITIES:
            b = bindings.get(cap.key)
            if b:
                lines.append(f"  [ok] {cap.label} -> {b.tool_name} (score={b.score:.2f})")
            else:
                flag = "缺失" if cap.required else "可选"
                lines.append(f"  [{flag}] {cap.label} -> 未匹配到工具")
        return "\n".join(lines)


def _try_json(text: str) -> Any | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_sse_or_json(raw: str) -> Mapping[str, Any]:
    """响应可能是纯 JSON，也可能是 SSE 流（多行 data: ...）。"""
    raw = raw.strip()
    if not raw:
        raise MCPError("MCP 返回空响应")
    if raw.startswith("{"):
        return json.loads(raw)
    objs: list[Mapping[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body:
            continue
        try:
            objs.append(json.loads(body))
        except json.JSONDecodeError:
            continue
    if not objs:
        raise MCPError(f"无法解析 MCP 响应: {raw[:200]}")
    for o in reversed(objs):
        if "id" in o:
            return o
    return objs[-1]


def client_from_env() -> MCPClient:
    """按环境变量构造客户端。

    MCD_MCP_TRANSPORT = stdio | http | streamablehttp   （默认 stdio）
    MCD_MCP_COMMAND   = 命令行，用 shlex 解析（stdio）
    MCD_MCP_URL       = 端点地址（http，麦当劳官方为 https://mcp.mcd.cn）
    MCD_MCP_TOKEN     = MCP Token，会自动加上 Authorization: Bearer 头
    MCD_MCP_HEADERS   = 额外的请求头，JSON 字符串（可选）
    """
    import shlex
    transport = os.environ.get("MCD_MCP_TRANSPORT", "stdio").strip().lower()

    headers: dict[str, str] = {}
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    extra = os.environ.get("MCD_MCP_HEADERS", "").strip()
    if extra:
        try:
            headers.update(json.loads(extra))
        except json.JSONDecodeError:
            pass

    if transport in ("http", "streamablehttp", "streamable-http"):
        return MCPClient(transport="http",
                         url=os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn"),
                         headers=headers)
    cmd = os.environ.get("MCD_MCP_COMMAND", "")
    return MCPClient(transport="stdio", command=shlex.split(cmd) if cmd else None,
                     headers=headers)
