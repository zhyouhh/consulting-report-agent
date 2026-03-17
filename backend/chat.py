from openai import OpenAI
from pathlib import Path
from typing import List, Dict
import json
import requests
import time
from .config import Settings
from .skill import SkillEngine

try:
    import tiktoken
    _encoding = tiktoken.get_encoding("cl100k_base")
except Exception:
    _encoding = None


class ChatHandler:
    """对话处理器"""

    def __init__(self, settings: Settings, skill_engine: SkillEngine):
        self.settings = settings
        self.skill_engine = skill_engine
        self.client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.api_base
        )

    def _estimate_tokens(self, messages: List[Dict]) -> int:
        """预估消息列表的token数"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                if _encoding:
                    total += len(_encoding.encode(content)) + 4
                else:
                    # 粗略估算：混合中英文约0.6 token/字符
                    total += int(len(content) * 0.6)
            elif isinstance(content, dict):
                text = json.dumps(content, ensure_ascii=False)
                if _encoding:
                    total += len(_encoding.encode(text)) + 4
                else:
                    total += int(len(text) * 0.6)
        return total

    def _compress_conversation(self, conversation: List[Dict]) -> List[Dict]:
        """压缩对话历史：保留system + LLM摘要 + 最近N条消息"""
        keep_n = self.settings.keep_recent_messages
        if len(conversation) <= keep_n + 2:
            return conversation

        system_msg = conversation[0]
        recent_msgs = conversation[-keep_n:]
        old_msgs = conversation[1:-keep_n]

        # 按消息数量截断，避免破坏JSON结构
        max_old_msgs = 50
        if len(old_msgs) > max_old_msgs:
            old_msgs = old_msgs[-max_old_msgs:]

        # 用LLM生成摘要
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
            {"role": "user", "content": json.dumps(old_msgs, ensure_ascii=False)}
        ]

        try:
            resp = self.client.chat.completions.create(
                model=self.settings.model,
                messages=summary_prompt,
                temperature=0.3,
                max_tokens=2000,
                timeout=30.0
            )
            summary = resp.choices[0].message.content
        except Exception:
            # 压缩失败，退回简单截断
            return [system_msg] + recent_msgs

        return [
            system_msg,
            {"role": "assistant", "content": f"[对话摘要]\n{summary}"},
            *recent_msgs
        ]

    def chat(self, project_name: str, user_message: str, max_iterations: int = 5) -> dict:
        """处理对话，返回 {content, token_usage}"""
        if len(user_message) > 10000:
            return {"content": "消息过长，请控制在10000字符以内。", "token_usage": None}

        conversation = self._load_conversation(project_name)
        conversation.append({"role": "user", "content": user_message})

        # 检查是否需要压缩
        compressed = False
        estimated = self._estimate_tokens(conversation)
        if estimated > self.settings.compress_threshold:
            conversation = self._compress_conversation(conversation)
            compressed = True
            estimated = self._estimate_tokens(conversation)  # 重新估算压缩后的token

        # 循环处理Function Calling
        total_tokens = 0
        iterations = 0
        assistant_message = ""
        while iterations < max_iterations:
            # API调用重试机制（最多3次）
            for retry in range(3):
                try:
                    timeout = 180.0 if "v3.2" in self.settings.model.lower() else 30.0
                    response = self.client.chat.completions.create(
                        model=self.settings.model,
                        messages=conversation,
                        temperature=0.7,
                        max_tokens=4096,
                        tools=self._get_tools(),
                        tool_choice="auto",
                        timeout=timeout
                    )
                    break  # 成功则跳出重试循环
                except Exception as e:
                    if retry < 2:  # 还有重试机会
                        time.sleep(2 ** retry)  # 指数退避：1s, 2s
                        continue
                    return {"content": f"API调用失败: {str(e)}", "token_usage": None}

            # 获取usage信息
            if hasattr(response, 'usage') and response.usage:
                total_tokens = getattr(response.usage, 'total_tokens', 0) or 0

            message = response.choices[0].message

            if message.tool_calls:
                # 安全地序列化message，避免非JSON对象
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
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                            } for tc in message.tool_calls
                        ]
                    }
                conversation.append(msg_dict)
                for tool_call in message.tool_calls:
                    result = self._execute_tool(project_name, tool_call)
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False)
                    })
                iterations += 1
            else:
                assistant_message = message.content
                break
        else:
            assistant_message = "抱歉，处理超时，请简化您的请求。"

        conversation.append({"role": "assistant", "content": assistant_message})
        self._save_conversation(project_name, conversation)

        # token统计：优先用API返回值，否则用预估
        if total_tokens == 0:
            total_tokens = self._estimate_tokens(conversation)

        return {
            "content": assistant_message,
            "token_usage": {
                "current_tokens": total_tokens,
                "max_tokens": self.settings.context_window,
                "compressed": compressed
            }
        }

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
                            "file_path": {"type": "string", "description": "文件路径，如plan/outline.md"},
                            "content": {"type": "string", "description": "文件内容"}
                        },
                        "required": ["file_path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取项目文件",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "文件路径"}
                        },
                        "required": ["file_path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "搜索互联网获取最新信息、数据、案例等",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]

    def _execute_tool(self, project_name: str, tool_call):
        """执行工具调用"""
        import logging
        try:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if func_name == "write_file":
                self.skill_engine.write_file(project_name, args["file_path"], args["content"])
                return {"status": "success", "message": f"已写入文件: {args['file_path']}"}
            elif func_name == "read_file":
                content = self.skill_engine.read_file(project_name, args["file_path"])
                return {"status": "success", "content": content}
            elif func_name == "web_search":
                results = self._web_search(args["query"])
                return {"status": "success", "results": results}
            else:
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

    def _web_search(self, query: str) -> str:
        """网络搜索（使用Tavily API）"""
        import logging
        try:
            # 使用Tavily API（专为AI设计的搜索）
            tavily_key = os.getenv("TAVILY_API_KEY", "")
            url = "https://api.tavily.com/search"
            payload = {
                "api_key": tavily_key,
                "query": query,
                "max_results": 3,
                "include_answer": True
            }
            response = requests.post(url, json=payload, timeout=15)

            if response.status_code != 200:
                return f"搜索服务暂时不可用（状态码：{response.status_code}）"

            data = response.json()

            # 优先返回AI生成的答案摘要
            if data.get("answer"):
                output = f"搜索摘要：\n{data['answer']}\n\n"
            else:
                output = "搜索结果：\n"

            # 添加具体搜索结果
            results = data.get("results", [])
            if results:
                for i, r in enumerate(results, 1):
                    title = r.get("title", "")
                    content = r.get("content", "")
                    output += f"{i}. {title}\n{content[:150]}...\n\n"
            else:
                return "未找到相关信息"

            return output.strip()
        except Exception as e:
            logging.error(f"搜索失败: {str(e)}")
            return "搜索功能暂时不可用，建议直接提供相关信息"

    def _load_conversation(self, project_name: str) -> List[Dict]:
        """加载对话历史"""
        project_path = self.skill_engine.get_project_path(project_name)
        if not project_path:
            return [{"role": "system", "content": self._build_system_prompt(project_name)}]

        conv_file = project_path / "conversation.json"
        if conv_file.exists():
            return json.loads(conv_file.read_text(encoding="utf-8"))

        return [{"role": "system", "content": self._build_system_prompt(project_name)}]

    def _save_conversation(self, project_name: str, conversation: List[Dict]):
        """保存对话历史"""
        project_path = self.skill_engine.get_project_path(project_name)
        if project_path:
            conv_file = project_path / "conversation.json"
            conv_file.write_text(json.dumps(conversation, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_system_prompt(self, project_name: str) -> str:
        """构建系统提示"""
        skill_prompt = self.skill_engine.get_skill_prompt()
        project_context = ""
        project_path = self.skill_engine.get_project_path(project_name)
        if project_path:
            info_file = project_path / "plan" / "project-info.md"
            if info_file.exists():
                project_context += f"\n\n## 当前项目信息\n{info_file.read_text(encoding='utf-8')}"
            outline_file = project_path / "plan" / "outline.md"
            if outline_file.exists():
                project_context += f"\n\n## 当前大纲\n{outline_file.read_text(encoding='utf-8')}"
        return f"{skill_prompt}\n\n{project_context}"
