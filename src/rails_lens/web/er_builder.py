"""ER図生成（Mermaid erDiagram形式）"""
from __future__ import annotations

from typing import Any


def generate_er_diagram(models: list[dict[str, Any]]) -> str:
    """モデルリストからMermaid erDiagramを生成。双方向重複除去。"""
    lines = ["erDiagram"]
    seen_relations: set[frozenset[str]] = set()

    for model in models:
        name = model.get("model_name", "")
        columns = model.get("columns", [])
        if columns:
            lines.append(f"    {name} {{")
            for col in columns[:10]:  # 最大10カラム表示
                col_type = col.get("type", "string").upper()
                col_name = col.get("name", "")
                lines.append(f"        {col_type} {col_name}")
            lines.append("    }")

        for assoc in model.get("associations", []):
            target = assoc.get("klass", "") or assoc.get("class_name", "")
            macro = assoc.get("macro", "") or assoc.get("type", "")
            if not target:
                continue
            rel_key: frozenset[str] = frozenset([name, target])
            if rel_key in seen_relations:
                continue
            seen_relations.add(rel_key)

            if macro in ("has_many", "has_and_belongs_to_many"):
                lines.append(f'    {name} ||--o{{ {target} : ""')
            elif macro == "belongs_to":
                lines.append(f'    {name} }}o--|| {target} : ""')
            else:
                lines.append(f'    {name} ||--|| {target} : ""')

    return "\n".join(lines)
