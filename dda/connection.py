"""TCP 通信辅助 —— 与 rookieDB 的连接、读取、命令执行。

被 PollingMonitor、RollbackExecutor、scenarios 共用。
"""

import asyncio
from typing import Optional


async def open_connection(
    host: str = "localhost", port: int = 18600
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """连接到 rookieDB，跳过欢迎 banner + 初始 '=> ' 提示符。"""
    reader, writer = await asyncio.open_connection(host, port)

    # 跳过 banner 行
    for _ in range(10):
        line = await read_line(reader, timeout=0.15)
        if line is None:
            break

    # rookieDB '=> ' 提示符无换行，readline 读不到，用 read() 清掉
    try:
        await asyncio.wait_for(reader.read(256), timeout=0.15)
    except asyncio.TimeoutError:
        pass

    return reader, writer


async def read_line(
    reader: asyncio.StreamReader, timeout: float
) -> Optional[str]:
    """读一行，超时返回 None。"""
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return line.decode().strip() if line else None
    except asyncio.TimeoutError:
        return None


async def flush(reader: asyncio.StreamReader) -> None:
    """清空残留数据（banner 行 + 无换行的 '=> ' 提示符）。"""
    for _ in range(10):
        line = await read_line(reader, timeout=0.15)
        if line is None:
            break
    try:
        await asyncio.wait_for(reader.read(256), timeout=0.15)
    except asyncio.TimeoutError:
        pass


async def execute_sql(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    sql: str,
    timeout: float = 10.0,
) -> str:
    """发送 SQL/metacommand，读取并返回响应。

    rookieDB 的 PrintStream(autoFlush=true) 在每次 println 时 flush，
    但 '=> ' 提示符无换行会跟下一条输出一起 flush，导致响应开头有
    '=> ' 前缀——本函数负责去掉。
    """
    if not sql.rstrip().endswith(';') and not sql.startswith('\\'):
        sql = sql.rstrip() + ';'
    writer.write((sql + '\n').encode())
    await writer.drain()

    # 读第一行（含前置的 => 提示符 + 实际输出）
    first_line = await read_line(reader, timeout=timeout)
    if first_line is None:
        return ''

    # 去掉开头的 '=> ' 前缀（可能有多个堆积）
    cleaned = first_line.strip()
    while cleaned.startswith('=> '):
        cleaned = cleaned[3:]
    cleaned = cleaned.strip()

    # 读后续行（多行错误/堆栈跟踪）
    more_lines: list[str] = []
    while True:
        line = await read_line(reader, timeout=0.3)
        if line is None or line.strip() == '=>':
            break
        more_lines.append(line)

    if more_lines:
        return cleaned + '\n' + '\n'.join(more_lines)
    return cleaned


def is_error(response: str) -> bool:
    """判断 rookieDB 响应是否为错误。"""
    lower = response.lower()
    return any(
        kw in lower
        for kw in [
            'error', 'rollback', 'exception', 'cannot commit',
            'not in running state', 'failed',
        ]
    )
