"""
LLM API客户端（OpenAI兼容格式）

本模块封装了 DeepSeek 和 MiMo（OpenAI 兼容格式）大语言模型调用。

学习要点：
  - OpenAI 兼容 API 的使用方式
  - 工厂模式（根据配置选择不同的 LLM 提供商）
  - 类的 __init__ 构造方法
  - 同步调用 vs 流式调用（Streaming）
  - yield 生成器的使用
  - 模块级单例对象
  - sys.path 动态修改 Python 模块搜索路径
"""

# ============================================================
# 一、标准库导入
# ============================================================

import sys
import logging
import time
import threading
# sys 模块提供了对 Python 解释器的控制
# 这里用 sys.path 修改模块搜索路径

from pathlib import Path
# Path 用于文件路径操作

# ============================================================
# 二、动态修改模块搜索路径
# ============================================================

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# 【为什么需要这行？】
# 本文件位于 app/llm/client.py，而 config.py 在 app/ 目录下。
# 当直接运行本文件时，Python 默认只在当前文件所在目录搜索模块，
# 导致 import config 找不到 config.py。
#
# 解析过程：
#   Path(__file__)                  → app/llm/client.py（当前文件路径）
#   .resolve()                      → 转为绝对路径
#   .parent                         → app/llm/
#   .parent.parent                  → app/
#   str(...)                        → 转为字符串
#   sys.path.insert(0, ...)         → 插入到模块搜索路径的最前面
#
# sys.path 是一个列表，Python 按顺序在这些目录中查找模块。
# insert(0, ...) 把 app/ 放在最前面，确保优先找到我们的 config.py。

# ============================================================
# 三、项目内部模块导入
# ============================================================

import config
# 【批量导入】从 config 模块一次导入多个变量
# 等价于：from config import LLM_PROVIDER
#         from config import DEEPSEEK_API_KEY
#         ...（但更简洁）

import httpx
from openai import OpenAI
# OpenAI 官方 Python SDK
# 虽然叫 "openai"，但很多国产大模型（DeepSeek、通义千问等）
# 都兼容 OpenAI 的 API 格式，所以可以直接用这个库调用
# 这种设计叫"OpenAI 兼容接口"（OpenAI-compatible API）

logger = logging.getLogger("llm.client")


# ============================================================
# 四、LLM 客户端类
# ============================================================

class LLMClient:
    """
    文本生成客户端，按 DeepSeek → MiMo 的顺序自动故障转移。

    封装了两种调用方式：
      1. chat()       - 同步调用，等待完整响应后返回
      2. chat_stream() - 流式调用，边生成边返回（打字机效果）

    所有文本生成接口统一优先使用 DeepSeek；请求失败时才使用 MiMo。
    两个提供商都未配置时，由业务层决定是否使用规则兜底。
    """

    def __init__(self):
        """
        构造方法，初始化 LLM 客户端。

        【__init__ 是什么？】
        Python 类的构造方法，在创建对象时自动调用。
        例如 client = LLMClient() 会执行这里的代码。

        【self 是什么？】
        self 代表"当前实例对象"。
        通过 self.client 和 self.model 把配置绑定到实例上，
        这样其他方法（如 chat）就能通过 self.client 使用它们。
        """
        self.provider = None
        self.model = None
        self.last_provider = None
        self._providers = []
        self.thinking_settings = {
            "deepseek": {
                "enabled": bool(getattr(config, "DEEPSEEK_THINKING_ENABLED", False)),
                "capability": str(getattr(config, "DEEPSEEK_THINKING_CAPABILITY", "unknown")),
                "hint": str(getattr(config, "DEEPSEEK_THINKING_HINT", "能力尚未检测")),
            },
            "mimo": {
                "enabled": bool(getattr(config, "MIMO_THINKING_ENABLED", True)),
                "capability": str(getattr(config, "MIMO_THINKING_CAPABILITY", "unknown")),
                "hint": str(getattr(config, "MIMO_THINKING_HINT", "能力尚未检测")),
            },
        }

        provider_configs = {
            "deepseek": (config.DEEPSEEK_API_KEY, config.DEEPSEEK_MODEL, config.DEEPSEEK_BASE_URL, config.DEEPSEEK_VERIFY_SSL),
            "mimo": (config.MIMO_API_KEY, config.MIMO_MODEL, config.MIMO_BASE_URL, config.MIMO_VERIFY_SSL),
        }
        provider_order = ["deepseek", "mimo"]
        # 只有配置了密钥的提供商才会加入候选列表，避免空密钥请求遮蔽真正的备用模型。
        for provider in provider_order:
            api_key, model, base_url, verify_ssl = provider_configs[provider]
            if api_key:
                self._providers.append((
                    provider, model,
                    OpenAI(
                        api_key=api_key,
                        base_url=base_url,
                        http_client=httpx.Client(verify=verify_ssl, timeout=60.0),
                    ),
                ))

        if self._providers:
            self.provider, self.model, self.client = self._providers[0]
            logger.info(
                "文本生成模型已配置: 主模型=%s/%s%s",
                self.provider,
                self.model,
                ", 备用模型=mimo" if len(self._providers) > 1 else "",
            )
        else:
            self.client = None
            logger.warning("未配置 DeepSeek 或 MiMo API 密钥，文本生成将使用业务兜底")

    def reload(self):
        """Reload provider clients after a runtime settings change."""
        for _, _, client in self._providers:
            try:
                client.close()
            except Exception:
                pass
        self.__init__()

    def _completion_options(self, model: str, provider: str | None = None, thinking_enabled: bool | None = None) -> dict:
        """Build provider options from the independently configured thinking switch."""
        if thinking_enabled is None and provider:
            settings = getattr(self, "thinking_settings", {})
            setting = settings.get(provider, {})
            capability = str(setting.get("capability", "configurable"))
            if capability in {"unsupported", "unknown"}:
                return {}
            thinking_enabled = bool(setting.get("enabled", False))
        if thinking_enabled is None:
            thinking_enabled = str(model or "").lower().startswith("qwen3") is False
        return {"extra_body": {"enable_thinking": bool(thinking_enabled)}}

    def _chat_once(self, messages, max_tokens):
        if not self._providers:
            raise RuntimeError("未配置 DeepSeek 或 MiMo API 密钥")
        errors = []
        for provider, model, client in self._providers:
            try:
                response = client.chat.completions.create(
                    model=model, messages=messages,
                    max_tokens=max_tokens, temperature=0.3,
                    **self._completion_options(model, provider),
                )
                content = response.choices[0].message.content
                if not str(content or "").strip():
                    raise RuntimeError("模型未返回文本")
                self.last_provider = provider
                return content
            except Exception as exc:
                if provider == "deepseek" and len(self._providers) > 1:
                    logger.warning("DeepSeek 文本生成失败，将切换 MiMo 备用模型: %s", exc)
                errors.append(f"{provider}: {exc}")
        raise RuntimeError("; ".join(errors))

    def chat(self, prompt: str, system: str = "", max_tokens: int = 4000) -> str:
        """
        同步聊天调用——发送消息，等待完整回复后一次性返回。

        参数：
          prompt     : 用户的提问/指令（必填）
          system     : 系统提示词，设定 AI 的角色和行为（可选）
          max_tokens : 回复的最大 token 数（默认 4000）

        返回值：AI 回复的文本字符串

        【OpenAI Chat API 的消息格式】
        消息是一个列表，每个元素是字典，包含：
          - role: 消息角色
            "system"  → 系统指令（设定 AI 行为）
            "user"    → 用户消息
            "assistant" → AI 回复
          - content: 消息内容

        对话示例：
          [
            {"role": "system", "content": "你是一个药品分析师"},
            {"role": "user", "content": "分析银黄口服液的成本"}
          ]

        学习要点：
          - 类型标注 (prompt: str) 和返回值标注 (-> str)
          - 默认参数 (system: str = "")
          - 列表的 append 方法
          - API 返回值的结构
        """
        # 构建消息列表
        messages = []
        if system:
            # 如果提供了系统提示词，作为第一条消息
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        return self._chat_once(messages, max_tokens)

    def chat_provider(self, provider_name: str, prompt: str, system: str = "", max_tokens: int = 4000) -> str:
        """Request one named provider without automatic failover."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        selected = next((item for item in self._providers if item[0] == provider_name), None)
        if selected is None:
            raise RuntimeError(f"未配置模型提供商: {provider_name}")
        provider, model, client = selected
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=0.3, **self._completion_options(model, provider),
            )
            content = response.choices[0].message.content
            if not str(content or "").strip():
                raise RuntimeError("模型未返回文本")
            self.last_provider = provider
            return content
        except Exception as exc:
            raise RuntimeError(f"{provider} 文本生成失败: {exc}") from exc

    def chat_stream_provider(
        self, provider_name: str, prompt: str, system: str = "", max_tokens: int = 4000,
        include_reasoning: bool = False, cancel_event: threading.Event | None = None,
    ):
        """Stream from one named provider without automatically trying another.

        This is used by latency-sensitive flows that need a bounded primary
        attempt before explicitly switching to the configured standby model.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        selected = next((item for item in self._providers if item[0] == provider_name), None)
        if selected is None:
            raise RuntimeError(f"未配置模型提供商: {provider_name}")

        provider, model, client = selected
        stream = None
        try:
            stream = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens,
                temperature=0.3, stream=True,
                **self._completion_options(model, provider),
            )
            if cancel_event is not None:
                def close_when_cancelled():
                    cancel_event.wait()
                    close = getattr(stream, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            pass
                threading.Thread(target=close_when_cancelled, daemon=True, name=f"{provider}-stream-canceller").start()
            for chunk in stream:
                if cancel_event is not None and cancel_event.is_set():
                    return
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta else None
                reasoning = getattr(delta, "reasoning_content", None) if delta else None
                if include_reasoning and reasoning:
                    yield ("activity", str(reasoning))
                if content:
                    yield ("content", str(content)) if include_reasoning else content
            self.last_provider = provider
        except Exception as exc:
            raise RuntimeError(f"{provider} 流式生成失败: {exc}") from exc
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def chat_stream(self, prompt: str, system: str = "", max_tokens: int = 4000):
        """
        流式聊天调用——边生成边返回，实现"打字机"效果。

        与 chat() 的区别：
          - chat()        : 等待 AI 完整生成后，一次性返回全部文本
          - chat_stream() : AI 每生成一小段就立即返回，前端可以实时显示

        返回值：生成器（generator），每次 yield 一小段文本

        【yield 是什么？】
        yield 是 Python 生成器的关键字。
        普通函数用 return 返回一次就结束；
        生成器函数用 yield 可以多次返回，每次暂停并保存状态。

        调用方式：
          for chunk in client.chat_stream("你好"):
              print(chunk)  # 每次打印一小段文本

        【stream=True 的工作原理】
        设置 stream=True 后，服务器不会等生成完毕再返回，
        而是通过 SSE（Server-Sent Events）持续发送数据块。
        每个 chunk 包含一小段新增的文本。

        学习要点：
          - yield 生成器
          - for 循环迭代流式响应
          - delta（增量）vs message（完整）
        """
        if not self._providers:
            raise RuntimeError("未配置 DeepSeek 或 MiMo API 密钥")
        errors = []
        for provider, _, _ in self._providers:
            yielded = False
            try:
                for content in self.chat_stream_provider(provider, prompt, system, max_tokens):
                    yielded = True
                    yield content
                if not yielded:
                    raise RuntimeError("模型未返回文本")
                return
            except Exception as exc:
                # 已输出的流不能安全地由备用模型续写。
                if yielded:
                    raise
                if provider == "deepseek" and len(self._providers) > 1:
                    logger.warning("DeepSeek 流式生成失败，将切换 MiMo 备用模型: %s", exc)
                errors.append(f"{provider}: {exc}")
        raise RuntimeError("; ".join(errors))

    def probe_thinking_capability(self, provider_name: str, timeout: float = 12.0) -> dict:
        """Probe whether one provider honors enable_thinking on/off."""
        selected = next((item for item in self._providers if item[0] == provider_name), None)
        if selected is None:
            raise RuntimeError(f"未配置模型提供商: {provider_name}")
        provider, model, client = selected
        observations = {}
        for enabled in (False, True):
            started = time.monotonic()
            reasoning_seen = False
            stream = None
            try:
                stream = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "只回复：测试"}],
                    max_tokens=32,
                    temperature=0,
                    stream=True,
                    extra_body={"enable_thinking": enabled},
                )
                for chunk in stream:
                    choices = getattr(chunk, "choices", None) or []
                    if not choices:
                        continue
                    delta = getattr(choices[0], "delta", None)
                    if delta is not None and getattr(delta, "reasoning_content", None):
                        reasoning_seen = True
                    if time.monotonic() - started > timeout:
                        raise TimeoutError("能力探测超时")
                observations[enabled] = reasoning_seen
            except Exception as exc:
                observations[enabled] = exc
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
        off, on = observations[False], observations[True]
        errors = [value for value in (off, on) if isinstance(value, Exception)]
        if errors:
            descriptions = "；".join(str(error) for error in errors)
            lowered = descriptions.lower()
            parameter_rejected = any(token in lowered for token in ("enable_thinking", "extra_body", "unknown parameter", "unsupported parameter", "unrecognized"))
            if not parameter_rejected:
                raise RuntimeError(f"深度思考能力探测失败: {descriptions}")
            capability = "unsupported"
            hint = "接口不接受深度思考参数，已禁用开关"
        elif off is False and on is True:
            capability = "configurable"
            hint = "支持独立控制深度思考"
        elif off is True:
            capability = "always_on"
            hint = "该模型固定启用深度思考，开关不可关闭"
        else:
            capability = "unsupported"
            hint = "该模型未返回深度思考内容，已禁用开关"
        return {"capability": capability, "hint": hint, "observations": {"off": bool(off) if not isinstance(off, Exception) else None, "on": bool(on) if not isinstance(on, Exception) else None}}


# ============================================================
# 五、模块级单例
# ============================================================

llm_client = LLMClient()
# 【模块级单例模式】
# 在模块加载时创建一个全局的 LLMClient 实例。
# 因为 Python 模块只会被 import 一次（后续 import 复用缓存），
# 所以整个应用中只有一个 llm_client 对象。
#
# 使用方式（在其他文件中）：
#   from llm.client import llm_client
#   result = llm_client.chat("分析一下银黄口服液的成本")
#
# 【为什么不每次都 new LLMClient()？】
# 1. LLMClient 内部持有 OpenAI 客户端，创建开销较大
# 2. 复用同一个实例可以共享 HTTP 连接池，性能更好
# 3. 全局统一配置，避免不一致
#
# 这种模式叫"单例模式"（Singleton）的一种简单实现。
