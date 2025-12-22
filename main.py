import lark_oapi as lark
from lark_oapi.api.im.v1 import *
import json
import subprocess
import threading
import time
import re
import os
import sys
import uuid
from typing import Optional, Dict
from datetime import datetime, timedelta


# 从环境变量获取配置
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")


class ClaudeCodeSession:
    """Claude Code 会话管理器 - 使用 claude --print + --session-id 维持上下文"""
    
    def __init__(self, session_id: str, workspace_path: str = ".", timeout: int = 300):
        self.session_id = session_id
        self.workspace_path = workspace_path
        self.timeout = timeout
        self.lock = threading.Lock()
        self.claude_path = "/opt/homebrew/bin/claude"
        
    def start(self):
        return True
    
    def send_and_wait(self, message: str, timeout: Optional[int] = None) -> str:
        """发送消息并等待完成"""
        if timeout is None:
            timeout = self.timeout
        with self.lock:
            try:
                res = self._run_claude_print(message, timeout=timeout)
            except subprocess.TimeoutExpired:
                return "⏱️ Claude 执行超时"
            except FileNotFoundError:
                return f"❌ 找不到 claude 命令: {self.claude_path}"
            except Exception as e:
                return f"❌ Claude 执行异常: {str(e)}"

        stdout = (res.stdout or "").strip()
        stderr = (res.stderr or "").strip()

        if res.returncode != 0:
            if stderr:
                return f"❌ Claude 运行失败\n\n{stderr}"
            return f"❌ Claude 运行失败 (exit={res.returncode})"

        if stderr:
            print(f"[{datetime.now()}] Claude stderr: {stderr[:300]}")

        if not stdout:
            return "✅ Claude 已执行，但无输出"

        stdout = self._clean_ansi(stdout)
        if len(stdout) > 3000:
            stdout = stdout[:3000] + f"\n\n... (输出过长，已截断，共 {len(stdout)} 字符)"
        return stdout

    def _run_claude_print(self, message: str, timeout: int):
        cmd_resume = [
            self.claude_path,
            "--print",
            message,
            "--dangerously-skip-permissions",
            "--resume",
            self.session_id,
        ]
        res = subprocess.run(
            cmd_resume,
            cwd=self.workspace_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        stdout = (res.stdout or "").strip()
        stderr = (res.stderr or "").strip()
        combined = f"{stdout}\n{stderr}".strip()

        if "No conversation found with session ID" in combined:
            cmd_create = [
                self.claude_path,
                "--print",
                message,
                "--dangerously-skip-permissions",
                "--session-id",
                self.session_id,
            ]
            created = subprocess.run(
                cmd_create,
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            created_stderr = (created.stderr or "").strip()
            if "is already in use" in created_stderr:
                return res
            return created

        return res
    
    def _clean_ansi(self, text: str) -> str:
        """去除 ANSI 转义序列"""
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)
    
    def is_alive(self) -> bool:
        """检查会话是否存活"""
        return True
    
    def stop(self):
        """停止会话"""
        return


# 全局会话管理
claude_sessions: Dict[str, ClaudeCodeSession] = {}
session_lock = threading.Lock()

# 处理过的消息 ID（带过期时间）
processed_messages: Dict[str, datetime] = {}
processed_lock = threading.Lock()


def cleanup_processed_messages():
    """清理1小时前的消息ID"""
    while True:
        time.sleep(300)
        with processed_lock:
            now = datetime.now()
            expired = [
                msg_id for msg_id, timestamp in processed_messages.items()
                if now - timestamp > timedelta(hours=1)
            ]
            for msg_id in expired:
                del processed_messages[msg_id]
            if expired:
                print(f"Cleaned up {len(expired)} expired message IDs")


threading.Thread(target=cleanup_processed_messages, daemon=True).start()


def get_or_create_session(chat_id: str) -> Optional[ClaudeCodeSession]:
    """获取或创建 Claude 会话"""
    with session_lock:
        session = claude_sessions.get(chat_id)
        if session is None:
            session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"feishu-chat:{chat_id}"))
            session = ClaudeCodeSession(session_id=session_id, workspace_path=".")
            if not session.start():
                return None
            claude_sessions[chat_id] = session
        return session


def process_claude_task(message_id: str, chat_id: str, prompt: str, is_p2p: bool):
    """处理 Claude 任务"""
    try:
        print(f"\n{'='*60}")
        print(f"Processing Claude task: {prompt[:100]}")
        print(f"Message ID: {message_id}")
        print(f"{'='*60}\n")
        
        # 获取会话
        session = get_or_create_session(chat_id)
        if not session:
            send_reply(message_id, chat_id, "❌ 无法启动 Claude Code 会话\n\n请确认：\n1. claude 命令已安装\n2. ANTHROPIC_API_KEY 已设置", is_p2p)
            return
        
        # 发送"处理中"的即时反馈
        send_reply(message_id, chat_id, f"🤖 正在处理您的请求...\n\n📝 任务: {prompt}", is_p2p)
        
        # 执行任务
        result = session.send_and_wait(prompt, timeout=180)  # 3分钟超时
        
        # 发送结果
        final_message = f"✅ 任务完成\n\n{result}"
        send_reply(message_id, chat_id, final_message, is_p2p)
        
    except Exception as e:
        print(f"Error in process_claude_task: {e}")
        import traceback
        traceback.print_exc()
        send_reply(message_id, chat_id, f"❌ 处理出错: {str(e)}", is_p2p)


def send_reply(message_id: str, chat_id: str, text: str, is_p2p: bool):
    """发送回复消息"""
    content = json.dumps({"text": text}, ensure_ascii=False)
    
    try:
        if is_p2p:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = client.im.v1.message.create(request)
        else:
            request = (
                ReplyMessageRequest.builder()
                .message_id(message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .content(content)
                    .msg_type("text")
                    .build()
                )
                .build()
            )
            response = client.im.v1.message.reply(request)

        if not response.success():
            print(f"Failed to send reply: {response.code}, {response.msg}")
             
    except Exception as e:
        print(f"Error sending reply: {e}")


def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    """处理接收到的消息"""
    message_id = data.event.message.message_id
    
    # 消息去重
    with processed_lock:
        if message_id in processed_messages:
            print(f"Message {message_id} already processed, skipping.")
            return
        processed_messages[message_id] = datetime.now()
    
    # 只处理文本消息
    if data.event.message.message_type != "text":
        return
    
    try:
        content = json.loads(data.event.message.content)["text"]
    except:
        return
    
    # 检查是否是 Claude Code 命令
    if "claude code" in content.lower():
        print(f"\n[{datetime.now()}] Received Claude Code command")
        
        # 提取 prompt
        prompt = content.lower().replace("claude code", "", 1).strip()
        if not prompt:
            prompt = "hello"
        
        # 异步处理
        is_p2p = data.event.message.chat_type == "p2p"
        chat_id = data.event.message.chat_id
        
        thread = threading.Thread(
            target=process_claude_task,
            args=(message_id, chat_id, prompt, is_p2p),
            daemon=True
        )
        thread.start()
        return
    


# 注册事件回调
event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1)
    .build()
)

# 创建客户端
client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()
wsClient = lark.ws.Client(
    APP_ID,
    APP_SECRET,
    event_handler=event_handler,
    log_level=lark.LogLevel.DEBUG,
)


def main():
    """主函数"""
    print(f"\n{'='*60}")
    print("飞书 Claude Code Bot 启动中...")
    print(f"启动时间: {datetime.now()}")
    print(f"Python 版本: {sys.version}")
    print(f"工作目录: {os.getcwd()}")
    
    # 检查环境
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        print(f"✅ ANTHROPIC_API_KEY 已设置 (前8位: {api_key[:8]}...)")
    else:
        print("⚠️  ANTHROPIC_API_KEY 未设置")
    
    print(f"{'='*60}\n")
    
    # 启动长连接
    wsClient.start()


if __name__ == "__main__":
    main()
