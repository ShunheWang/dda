"""DFS 三色标记找死锁环。"""

from dda.models import Cycle, WaitForGraph


class CycleDetector:
    """在 WFG 中检测有向环（死锁）。"""

    def detect(self, wfg: WaitForGraph) -> list[Cycle]:
        """DFS 三色标记找环。返回所有环。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[int, int] = {node: WHITE for node in wfg.nodes}
        cycles: list[Cycle] = []

        # 构建邻接表
        adj: dict[int, list[int]] = {node: [] for node in wfg.nodes}
        for u, v in wfg.edges:
            adj.setdefault(u, []).append(v)

        def dfs(u: int, path: list[int]) -> None:
            color[u] = GRAY
            path.append(u)

            for v in adj.get(u, []):
                if color.get(v) == GRAY:
                    idx = path.index(v)
                    cycle_txns = path[idx:] + [v]
                    cycles.append(Cycle(transactions=cycle_txns))
                elif color.get(v) == WHITE:
                    dfs(v, path)

            path.pop()
            color[u] = BLACK

        for node in wfg.nodes:
            if color[node] == WHITE:
                dfs(node, [])

        return cycles
