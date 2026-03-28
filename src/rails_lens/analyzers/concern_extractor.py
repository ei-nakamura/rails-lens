"""Concern分割候補抽出（モデルファイル解析・クラスタリング）"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from rails_lens.config import RailsLensConfig
from rails_lens.models import ConcernCandidate, MethodNode


class ConcernExtractor:
    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config
        self.project_root = Path(config.rails_project_path)

    def extract(
        self,
        model_name: str,
        min_cluster_size: int = 3,
        existing_concerns: list[str] | None = None,
    ) -> tuple[list[ConcernCandidate], int, int, list[str]]:
        """モデルファイルを解析し、Concern分割候補を返す。

        Returns (candidates, total_methods, total_lines, unclustered_methods).
        """
        snake_name = _to_snake_case(model_name)
        model_file = self.project_root / f"app/models/{snake_name}.rb"
        if not model_file.exists():
            return [], 0, 0, []

        source = model_file.read_text(encoding="utf-8")
        total_lines = len(source.splitlines())
        nodes = self._parse_methods(source, str(model_file))
        candidates, unclustered = self._cluster(nodes, min_cluster_size, existing_concerns or [])
        return candidates, len(nodes), total_lines, unclustered

    def _parse_methods(self, source: str, file_path: str) -> list[MethodNode]:
        """メソッド定義と本体を抽出してMethodNodeリストを返す"""
        nodes: list[MethodNode] = []
        lines = source.splitlines()

        for m in re.finditer(
            r"^( *)def (self\.)?(\w+)(?:\([^)]*\))?\s*$",
            source,
            re.MULTILINE,
        ):
            is_class_method = bool(m.group(2))
            method_name = m.group(3)
            start_line = source[: m.start()].count("\n") + 1

            # メソッド本体を抽出（次のdef またはendまで）
            body_lines: list[str] = []
            depth = 1
            for line in lines[start_line:]:
                if re.match(r"\s*(def |class |module |do\b)", line):
                    depth += 1
                if re.match(r"\s*end\b", line):
                    depth -= 1
                    if depth <= 0:
                        break
                body_lines.append(line)

            body = "\n".join(body_lines)

            # カラム参照抽出（self.xxx, xxx= パターン）
            columns = list(set(
                re.findall(r"self\.(\w+)", body)
                + re.findall(r"\b(\w+)=\s", body)
            ))
            # 呼び出しメソッド抽出
            calls = [
                c for c in set(re.findall(r"\b(\w+)\s*\(", body))
                if c != method_name and not c[0].isupper()
            ]

            nodes.append(MethodNode(
                name=method_name,
                type="class_method" if is_class_method else "instance_method",
                accesses_columns=columns,
                calls_methods=calls,
                source_file=file_path,
                source_line=start_line,
                line_count=len(body_lines),
            ))

        return nodes

    def _cluster(
        self,
        nodes: list[MethodNode],
        min_cluster_size: int,
        existing_concerns: list[str],
    ) -> tuple[list[ConcernCandidate], list[str]]:
        """共通カラムアクセスに基づいてクラスタリングし、Concern候補を返す"""
        # カラム→メソッドのマッピング
        col_to_nodes: dict[str, list[MethodNode]] = defaultdict(list)
        for node in nodes:
            for col in node.accesses_columns:
                col_to_nodes[col].append(node)

        # Union-Find でメソッドをグループ化（共通カラム経由）
        parent: dict[str, str] = {n.name: n.name for n in nodes}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for _col, col_nodes in col_to_nodes.items():
            for i in range(len(col_nodes) - 1):
                if col_nodes[i].name in parent and col_nodes[i + 1].name in parent:
                    union(col_nodes[i].name, col_nodes[i + 1].name)

        # グループ集約
        groups: dict[str, list[MethodNode]] = defaultdict(list)
        for node in nodes:
            if node.name in parent:
                groups[find(node.name)].append(node)

        candidates: list[ConcernCandidate] = []
        clustered_names: set[str] = set()

        for _root, members in groups.items():
            if len(members) < min_cluster_size:
                continue

            all_cols = list({c for n in members for c in n.accesses_columns})
            shared_cols = [
                c for c in all_cols
                if sum(1 for n in members if c in n.accesses_columns) >= 2
            ]
            if not shared_cols:
                continue

            cohesion = len(shared_cols) / max(len(all_cols), 1)
            name = _suggest_concern_name(shared_cols)

            # 既存Concernとの重複チェック
            overlap = [c for c in existing_concerns if c.lower() in name.lower()]

            candidates.append(ConcernCandidate(
                suggested_name=name,
                methods=[n.name for n in members],
                shared_columns=shared_cols,
                cohesion_score=round(min(cohesion, 1.0), 3),
                rationale=f"Methods share columns: {', '.join(shared_cols[:3])}",
                existing_concern_overlap=overlap,
            ))
            clustered_names.update(n.name for n in members)

        unclustered = [n.name for n in nodes if n.name not in clustered_names]
        return candidates, unclustered


def _to_snake_case(name: str) -> str:
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z])([A-Z])", r"\1_\2", s).lower()


def _suggest_concern_name(columns: list[str]) -> str:
    if not columns:
        return "ExtractedConcern"
    key = columns[0].title().replace("_", "")
    return f"{key}Concern"
