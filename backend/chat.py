import base64
import ipaddress
import json
import re
import requests
import socket
import time
from typing import Dict, List
from html import unescape
from urllib.parse import urlparse

from openai import OpenAI

from .config import Settings
from .skill import SkillEngine

try:
    import tiktoken
    _encoding = tiktoken.get_encoding("cl100k_base")
except Exception:
    _encoding = None


class ChatHandler:
    """对话处理器"""

    INTERCEPT_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")
    FETCH_URL_MAX_BYTES = 600_000
    FETCH_URL_MAX_CHARS = 12_000
    FETCH_URL_ALLOWED_CONTENT_TYPES = (
        "text/html",
        "application/xhtml+xml",
        "text/plain",
    )
    BLOCKED_HOSTNAMES = {
        "localhost",
        "host.docker.internal",
    }

    NON_PLAN_WRITE_ALLOW_KEYWORDS = [
        "确认大纲",
        "按这个大纲",
        "就按这个",
        "继续写",
        "继续下一章",
        "开始正文",
        "开始写正文",
        "写第一章",
        "写第二章",
        "写执行摘要",
        "继续完善",
        "继续撰写",
    ]
    def __init__(self, settings: Settings, skill_engine: SkillEngine):
        self.settings = settings
        self.skill_engine = skill_engine
        self._turn_context = {
            "can_write_non_plan": True,
            "web_search_disabled": False,
        }
        import httpx

        http_client = httpx.Client(timeout=120.0)
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base,
            http_client=http_client,
        )

    def _estimate_tokens(self, messages: List[Dict]) -> int:
        """预估消息列表的token数"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self._estimate_text_tokens(content)
            else:
                total += self._estimate_text_tokens(json.dumps(content, ensure_ascii=False))
        return total

    def _estimate_text_tokens(self, text: str) -> int:
        if _encoding:
            return len(_encoding.encode(text)) + 4
        return int(len(text) * 0.6)

    def _compress_conversation(self, conversation: List[Dict]) -> List[Dict]:
        """压缩对话历史：保留system + LLM摘要 + 最近N条消息"""
        keep_n = self.settings.keep_recent_messages
        if len(conversation) <= keep_n + 2:
            return conversation

        system_msg = conversation[0]
        recent_msgs = conversation[-keep_n:]
        old_msgs = conversation[1:-keep_n]

        max_old_msgs = 50
        if len(old_msgs) > max_old_msgs:
            old_msgs = old_msgs[-max_old_msgs:]

        summary_prompt = [
            {"role": "system", "content": (
                "你是一个对话摘要助手。请将以下对话历史压缩为简洁摘要，必须保留：\n"
                "1. 项目名称、报告类型、主题\n"
                "2. 已完成的章节和内容要点\n"
                "3. 用户提出的修改意见和偏好\n"
                "4. 当前工作进度和下一步计划\n"
                "5. 所有精确的名称、路径、数据、引用来源\n"
                "只输出摘要内容，不要加前缀。"
            )},
            {"role": "user", "content": json.dumps(old_msgs, ensure_ascii=False)},
        ]

        try:
            resp = self.client.chat.completions.create(
                model=self.settings.model,
                messages=summary_prompt,
                temperature=0.3,
                max_tokens=2000,
                timeout=30.0,
            )
            summary = resp.choices[0].message.content
        except Exception:
            return [system_msg] + recent_msgs

        return [
            system_msg,
            {"role": "assistant", "content": f"[对话摘要]\n{summary}"},
            *recent_msgs,
        ]

    def chat_stream(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str] | None = None,
        max_iterations: int = 10,
    ):
        """流式处理对话，yield 每个 chunk"""
        if len(user_message) > 10000:
            yield {"type": "error", "data": "消息过长，请控制在10000字符以内。"}
            return

        history = self._load_conversation(project_id)
        current_user_message = {
            "role": "user",
            "content": user_message,
            "attached_material_ids": attached_material_ids or [],
        }
        self._turn_context = self._build_turn_context(project_id, user_message)
        conversation = self._build_provider_conversation(project_id, history, current_user_message)

        compressed = False
        estimated = self._estimate_tokens(conversation)
        if estimated > self.settings.compress_threshold:
            conversation = self._compress_conversation(conversation)
            compressed = True

        total_tokens = 0
        iterations = 0
        assistant_message = ""

        while iterations < max_iterations:
            for retry in range(2):
                try:
                    timeout = 120.0 if "v3.2" in self.settings.model.lower() else 30.0
                    response = self.client.chat.completions.create(
                        model=self.settings.model,
                        messages=conversation,
                        temperature=0.7,
                        max_tokens=4096,
                        tools=self._get_tools(),
                        tool_choice="auto",
                        timeout=timeout,
                        stream=True,
                    )
                    break
                except Exception as e:
                    if retry < 1:
                        time.sleep(2)
                        continue
                    yield {"type": "error", "data": f"API调用失败: {str(e)}"}
                    return

            collected_message = {"role": "assistant", "content": "", "tool_calls": []}

            for chunk in response:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta
                if delta.content:
                    collected_message["content"] += delta.content
                    yield {"type": "content", "data": delta.content}

                if delta.tool_calls:
                    for tc_chunk in delta.tool_calls:
                        if tc_chunk.index >= len(collected_message["tool_calls"]):
                            collected_message["tool_calls"].append({
                                "id": tc_chunk.id or "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })

                        tc = collected_message["tool_calls"][tc_chunk.index]
                        if tc_chunk.id:
                            tc["id"] = tc_chunk.id
                        if tc_chunk.function:
                            if tc_chunk.function.name:
                                tc["function"]["name"] = tc_chunk.function.name
                            if tc_chunk.function.arguments:
                                tc["function"]["arguments"] += tc_chunk.function.arguments

            if collected_message["tool_calls"]:
                conversation.append(collected_message)
                for tool_call in collected_message["tool_calls"]:
                    func_name = tool_call["function"]["name"]
                    func_args = tool_call["function"]["arguments"]
                    yield {"type": "tool", "data": f"🔧 调用工具: {func_name}({func_args[:50]}...)"}

                    class ToolCall:
                        def __init__(self, data):
                            self.id = data["id"]
                            self.function = type("obj", (object,), {
                                "name": data["function"]["name"],
                                "arguments": data["function"]["arguments"],
                            })()

                    result = self._execute_tool(project_id, ToolCall(tool_call))
                    result_icon = "✅" if result.get("status") == "success" else "⚠️"
                    yield {"type": "tool", "data": f"{result_icon} 结果: {str(result)[:160]}..."}
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                iterations += 1
            else:
                assistant_message = collected_message["content"]
                break
        else:
            assistant_message = "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。"
            yield {"type": "content", "data": assistant_message}

        history.extend([current_user_message, {"role": "assistant", "content": assistant_message}])
        self._save_conversation(project_id, history)
        self._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

        if total_tokens == 0:
            total_tokens = self._estimate_tokens(conversation)

        yield {
            "type": "usage",
            "data": {
                "current_tokens": total_tokens,
                "max_tokens": self.settings.context_window,
                "compressed": compressed,
            },
        }

    def chat(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str] | None = None,
        max_iterations: int = 5,
    ) -> dict:
        """处理对话，返回 {content, token_usage}"""
        if len(user_message) > 10000:
            return {"content": "消息过长，请控制在10000字符以内。", "token_usage": None}

        history = self._load_conversation(project_id)
        current_user_message = {
            "role": "user",
            "content": user_message,
            "attached_material_ids": attached_material_ids or [],
        }
        self._turn_context = self._build_turn_context(project_id, user_message)
        conversation = self._build_provider_conversation(project_id, history, current_user_message)

        compressed = False
        estimated = self._estimate_tokens(conversation)
        if estimated > self.settings.compress_threshold:
            conversation = self._compress_conversation(conversation)
            compressed = True

        total_tokens = 0
        iterations = 0
        assistant_message = ""
        while iterations < max_iterations:
            for retry in range(2):
                try:
                    timeout = 120.0 if "v3.2" in self.settings.model.lower() else 30.0
                    response = self.client.chat.completions.create(
                        model=self.settings.model,
                        messages=conversation,
                        temperature=0.7,
                        max_tokens=4096,
                        tools=self._get_tools(),
                        tool_choice="auto",
                        timeout=timeout,
                    )
                    break
                except Exception as e:
                    if retry < 1:
                        time.sleep(2)
                        continue
                    return {"content": f"API调用失败: {str(e)}", "token_usage": None}

            if hasattr(response, "usage") and response.usage:
                total_tokens = getattr(response.usage, "total_tokens", 0) or 0

            message = response.choices[0].message
            if message.tool_calls:
                try:
                    msg_dict = message.model_dump()
                except Exception:
                    msg_dict = {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                            }
                            for tc in message.tool_calls
                        ],
                    }
                conversation.append(msg_dict)
                for tool_call in message.tool_calls:
                    result = self._execute_tool(project_id, tool_call)
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                iterations += 1
            else:
                assistant_message = message.content
                break
        else:
            assistant_message = "抱歉，工具调用轮次过多，已停止本轮，请缩小检索范围或改成分步提问。"

        history.extend([current_user_message, {"role": "assistant", "content": assistant_message}])
        self._save_conversation(project_id, history)
        self._turn_context = {"can_write_non_plan": True, "web_search_disabled": False}

        if total_tokens == 0:
            total_tokens = self._estimate_tokens(conversation)

        return {
            "content": assistant_message,
            "token_usage": {
                "current_tokens": total_tokens,
                "max_tokens": self.settings.context_window,
                "compressed": compressed,
            },
        }

    def _build_provider_conversation(self, project_id: str, history: List[Dict], current_user_message: Dict) -> List[Dict]:
        conversation = [{"role": "system", "content": self._build_system_prompt(project_id)}]
        for message in history:
            provider_message = self._to_provider_message(project_id, message, include_images=False)
            if provider_message:
                conversation.append(provider_message)
        conversation.append(self._to_provider_message(project_id, current_user_message, include_images=True))
        return conversation

    def _to_provider_message(self, project_id: str, message: Dict, include_images: bool) -> Dict | None:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            return None
        if role == "assistant":
            return {"role": "assistant", "content": message.get("content", "")}

        attached_material_ids = message.get("attached_material_ids") or []
        if attached_material_ids:
            return {
                "role": "user",
                "content": self._build_user_content(
                    project_id,
                    message.get("content", ""),
                    attached_material_ids,
                    include_images=include_images,
                ),
            }
        return {"role": "user", "content": message.get("content", "")}

    def _build_user_content(
        self,
        project_id: str,
        user_message: str,
        attached_material_ids: List[str],
        include_images: bool = True,
    ) -> List[Dict]:
        materials = [self.skill_engine.get_material(project_id, material_id) for material_id in attached_material_ids]
        note_lines = [user_message]
        if materials:
            note_lines.extend(["", "[本轮附带材料]"])
            for material in materials:
                note_lines.append(
                    f"- {material['id']} | {material['display_name']} | {material['source_type']} | {material['file_type']}"
                )
            note_lines.append("需要读取文本材料时，请调用 read_material_file。")

        content = [{"type": "text", "text": "\n".join(note_lines).strip()}]
        if include_images:
            for material in materials:
                if material["media_kind"] != "image_like":
                    continue
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": self._build_material_data_url(project_id, material["id"]),
                    },
                })
        return content

    def _build_material_data_url(self, project_id: str, material_id: str) -> str:
        material = self.skill_engine.get_material(project_id, material_id)
        material_path = self.skill_engine.get_material_path(project_id, material_id)
        encoded = base64.b64encode(material_path.read_bytes()).decode("ascii")
        mime_type = material.get("mime_type") or "application/octet-stream"
        return f"data:{mime_type};base64,{encoded}"

    def _get_tools(self):
        """定义可用工具"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写入或更新项目文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件路径，如 plan/outline.md"},
                            "content": {"type": "string", "description": "文件内容"},
                        },
                        "required": ["file_path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取项目文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件路径"},
                        },
                        "required": ["file_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_material_file",
                    "description": "读取项目材料的文本内容，适用于文档、表格、文本材料",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "material_id": {"type": "string", "description": "材料ID"},
                        },
                        "required": ["material_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "搜索互联网获取最新信息、数据、案例等",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_url",
                    "description": "读取指定网页正文内容，适合对搜索结果链接继续深入阅读",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "description": "网页链接，必须是 http 或 https"},
                        },
                        "required": ["url"],
                    },
                },
            },
        ]

    def _execute_tool(self, project_id: str, tool_call):
        """执行工具调用"""
        import logging

        try:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if func_name == "write_file":
                if self._should_block_non_plan_write(args["file_path"]):
                    return {
                        "status": "error",
                        "message": "当前轮次还不能开始写正文，请先确认大纲或明确说“继续写正文”。",
                    }
                self.skill_engine.write_file(project_id, args["file_path"], args["content"])
                return {"status": "success", "message": f"已写入文件: {args['file_path']}"}
            if func_name == "read_file":
                content = self.skill_engine.read_file(project_id, args["file_path"])
                return {"status": "success", "content": content}
            if func_name == "read_material_file":
                content = self.skill_engine.read_material_file(project_id, args["material_id"])
                return {"status": "success", "content": content}
            if func_name == "web_search":
                if self._turn_context.get("web_search_disabled"):
                    return {
                        "status": "error",
                        "message": "本轮 web_search 已因搜索服务错误被停用，请不要继续重试。",
                    }
                result = self._web_search(args["query"])
                if result.get("disable_for_turn"):
                    self._turn_context["web_search_disabled"] = True
                return {key: value for key, value in result.items() if key != "disable_for_turn"}
            if func_name == "fetch_url":
                return self._fetch_url(args["url"])
            return {"status": "error", "message": f"未知工具: {func_name}"}
        except json.JSONDecodeError as e:
            logging.error(f"工具参数解析失败: {func_name}, 错误: {str(e)}")
            return {"status": "error", "message": f"参数解析失败: {str(e)}"}
        except ValueError as e:
            logging.error(f"工具参数验证失败: {func_name}, 错误: {str(e)}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"工具执行异常: {func_name}, 错误: {str(e)}")
            return {"status": "error", "message": f"工具执行失败: {str(e)}"}

    def _web_search(self, query: str) -> Dict[str, str | bool]:
        """网络搜索（使用 SearXNG JSON API）"""
        import logging

        try:
            response = requests.get(
                self.settings.managed_search_api_url,
                params={"q": query, "format": "json"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )

            if response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"搜索服务暂时不可用（状态码：{response.status_code}）",
                    "disable_for_turn": True,
                }

            payload = response.json()
            raw_results = payload.get("results") or []
            if not isinstance(raw_results, list):
                return {
                    "status": "error",
                    "message": "搜索服务返回了不可识别的数据格式。",
                    "disable_for_turn": True,
                }

            results = []
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                title = self._strip_html(str(item.get("title", "")).strip())
                snippet = self._strip_html(str(item.get("content", "")).strip())
                url = str(item.get("url", "")).strip()
                if not title or not url:
                    continue
                results.append({
                    "title": title,
                    "snippet": snippet or "无摘要",
                    "url": url,
                })

            if not results:
                return {"status": "success", "results": "未找到相关信息"}

            output = "搜索结果：\n"
            for index, result in enumerate(results[:5], 1):
                output += (
                    f"{index}. {result['title']}\n"
                    f"{result['snippet'][:180]}\n"
                    f"链接: {result['url']}\n\n"
                )

            return {"status": "success", "results": output.strip()}
        except Exception as e:
            logging.error(f"搜索失败: {str(e)}")
            return {
                "status": "error",
                "message": "搜索功能暂时不可用，本轮已暂停继续搜索，请稍后重试。",
                "disable_for_turn": True,
            }

    def _fetch_url(self, url: str) -> Dict[str, str | bool]:
        parsed = self._validate_fetch_url(url)
        response = None

        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
                stream=True,
                allow_redirects=True,
            )

            final_url = response.url if isinstance(getattr(response, "url", None), str) else url
            self._validate_fetch_url(final_url)

            if response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"网页抓取失败（状态码：{response.status_code}）",
                }

            content_type = str(response.headers.get("Content-Type", "")).lower()
            if content_type and not any(token in content_type for token in self.FETCH_URL_ALLOWED_CONTENT_TYPES):
                return {
                    "status": "error",
                    "message": f"暂不支持读取该类型网页内容：{content_type}",
                }

            raw_text = self._read_response_text(response)
            if not raw_text.strip():
                return {"status": "error", "message": "网页内容为空，无法提取正文。"}

            title = self._extract_html_title(raw_text) or parsed.hostname or final_url
            if "text/plain" in content_type:
                content = raw_text.strip()
            else:
                content = self._extract_readable_text(raw_text)

            if not content.strip():
                return {"status": "error", "message": "网页正文提取失败。"}

            content, truncated = self._truncate_text(content)
            return {
                "status": "success",
                "title": title,
                "url": final_url,
                "content": content,
                "truncated": truncated,
            }
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "message": f"网页抓取失败: {str(exc)}"}
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    def _validate_fetch_url(self, raw_url: str):
        parsed = urlparse((raw_url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("fetch_url 只支持 http/https 链接。")
        if not parsed.hostname:
            raise ValueError("链接无效，缺少主机名。")
        if parsed.username or parsed.password:
            raise ValueError("不支持带账号凭据的链接。")
        if parsed.hostname.lower() in self.BLOCKED_HOSTNAMES:
            raise ValueError("不允许访问本地或内网地址。")

        self._ensure_public_hostname(parsed.hostname)
        return parsed

    def _ensure_public_hostname(self, hostname: str):
        try:
            ip = ipaddress.ip_address(hostname)
            self._ensure_public_ip(ip)
            return
        except ValueError:
            pass

        try:
            resolved = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"域名解析失败: {hostname}") from exc

        if not resolved:
            raise ValueError(f"域名解析失败: {hostname}")

        for entry in resolved:
            ip = ipaddress.ip_address(entry[4][0])
            if ip in self.INTERCEPT_PROXY_NETWORK:
                continue
            self._ensure_public_ip(ip)

    def _ensure_public_ip(self, ip):
        carrier_grade_nat = ip in ipaddress.ip_network("100.64.0.0/10")
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or carrier_grade_nat
            or getattr(ip, "is_site_local", False)
        ):
            raise ValueError("不允许访问本地或内网地址。")

    def _read_response_text(self, response) -> str:
        chunks = []
        total = 0
        response_encoding = getattr(response, "encoding", None)
        encoding = response_encoding if isinstance(response_encoding, str) and response_encoding else "utf-8"

        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            remaining = self.FETCH_URL_MAX_BYTES - total
            if remaining <= 0:
                break
            piece = chunk[:remaining]
            chunks.append(piece)
            total += len(piece)
            if total >= self.FETCH_URL_MAX_BYTES:
                break

        return b"".join(chunks).decode(encoding, errors="ignore")

    def _extract_html_title(self, html_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return self._strip_html(match.group(1))

    def _extract_readable_text(self, html_text: str) -> str:
        try:
            import trafilatura

            extracted = trafilatura.extract(
                html_text,
                include_comments=False,
                include_tables=False,
                include_links=False,
                favor_precision=True,
            )
            if extracted:
                return extracted.strip()
        except Exception:
            pass

        return self._strip_html(html_text)

    def _truncate_text(self, text: str) -> tuple[str, bool]:
        normalized = text.strip()
        if len(normalized) <= self.FETCH_URL_MAX_CHARS:
            return normalized, False
        return normalized[: self.FETCH_URL_MAX_CHARS].rstrip(), True

    def _strip_html(self, value: str) -> str:
        text = re.sub(r"<[^>]+>", "", value)
        text = unescape(text)
        return " ".join(text.split())

    def _load_conversation(self, project_id: str) -> List[Dict]:
        """加载对话历史。仅持久化 user/assistant 显示消息。"""
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return []

        conv_file = project_path / "conversation.json"
        if not conv_file.exists():
            return []

        raw_messages = json.loads(conv_file.read_text(encoding="utf-8"))
        normalized = []
        for message in raw_messages:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            normalized.append({
                "role": role,
                "content": self._extract_message_text(message.get("content", "")),
                "attached_material_ids": message.get("attached_material_ids", []),
            })
        return normalized

    def _save_conversation(self, project_id: str, conversation: List[Dict]):
        """保存对话历史"""
        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return

        conv_file = project_path / "conversation.json"
        conv_file.write_text(
            json.dumps(conversation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _extract_message_text(self, content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(item.get("text", ""))
            return "\n".join(part for part in texts if part).strip()
        return json.dumps(content, ensure_ascii=False)

    def _build_system_prompt(self, project_id: str) -> str:
        """构建系统提示"""
        skill_prompt = self.skill_engine.get_skill_prompt()
        project_context = self.skill_engine.build_project_context(project_id)
        if self._turn_context.get("can_write_non_plan", True):
            turn_rule = "本轮如用户明确要求继续正文，可在更新 plan 后撰写正文。"
        else:
            turn_rule = (
                "本轮只能做两类事：1）继续问清关键信息；2）更新 `plan/` 内文件。"
                "在用户明确确认大纲或明确要求继续正文前，禁止写正文、章节草稿、report_draft 或最终报告。"
                "如果信息不足，提出问题后就停止本轮，不要擅自继续。"
            )
        return f"{skill_prompt}\n\n## 当前轮次约束\n{turn_rule}\n\n{project_context}"

    def _build_turn_context(self, project_id: str, user_message: str) -> Dict[str, bool]:
        return {
            "can_write_non_plan": self._should_allow_non_plan_write(project_id, user_message),
            "web_search_disabled": False,
        }

    def _should_allow_non_plan_write(self, project_id: str, user_message: str) -> bool:
        normalized = (user_message or "").strip()
        if not normalized:
            return False

        if any(keyword in normalized for keyword in self.NON_PLAN_WRITE_ALLOW_KEYWORDS):
            return True

        project_path = self.skill_engine.get_project_path(project_id)
        if not project_path:
            return False

        report_candidates = [
            project_path / "report_draft_v1.md",
            project_path / "content" / "report.md",
            project_path / "content" / "draft.md",
            project_path / "output" / "final-report.md",
        ]
        if any(path.exists() for path in report_candidates) and any(
            keyword in normalized for keyword in ["继续", "补充", "完善", "修改", "调整"]
        ):
            return True

        return False

    def _should_block_non_plan_write(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/").lstrip("/")
        return not normalized.startswith("plan/") and not self._turn_context.get("can_write_non_plan", True)
