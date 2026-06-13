"""通过 TCP 向 rookieDB 发送 \\kill 命令。"""

import asyncio

from dda.connection import flush, read_line


class RollbackExecutor:
    """通过 TCP 向 rookieDB 发送 \\kill 命令。"""

    def __init__(self, host: str = "localhost", port: int = 18600):
        self.host = host
        self.port = port

    async def kill(self, trans_num: int) -> bool:
        """回滚指定事务。打开新连接执行 \\kill 后关闭。"""
        try:
            reader, writer = await asyncio.open_connection(
                self.host, self.port
            )
            await flush(reader)

            cmd = f"\\kill {trans_num}"
            writer.write((cmd + '\n').encode())
            await writer.drain()

            response = await read_line(reader, timeout=3.0)
            writer.close()
            await writer.wait_closed()

            if response and 'rolled back' in response.lower():
                return True
            return False
        except Exception as e:
            print(f"  [Rollback] kill T{trans_num} 失败: {e}")
            return False
