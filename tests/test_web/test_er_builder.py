"""tests/test_web/test_er_builder.py — er_builder.generate_er_diagram テスト"""
from __future__ import annotations

from rails_lens.web.er_builder import generate_er_diagram


def test_generate_er_basic() -> None:
    models = [
        {
            "model_name": "User",
            "columns": [
                {"type": "integer", "name": "id"},
                {"type": "string", "name": "name"},
            ],
            "associations": [{"macro": "has_many", "klass": "Post"}],
        }
    ]
    result = generate_er_diagram(models)
    assert result.startswith("erDiagram")
    assert "User" in result
    assert "Post" in result


def test_generate_er_dedup() -> None:
    models = [
        {
            "model_name": "User",
            "columns": [],
            "associations": [{"macro": "has_many", "klass": "Post"}],
        },
        {
            "model_name": "Post",
            "columns": [],
            "associations": [{"macro": "belongs_to", "klass": "User"}],
        },
    ]
    result = generate_er_diagram(models)
    lines = result.splitlines()
    # User<->Post のリレーション行は重複除去で1行のみ
    rel_lines = [
        line for line in lines if "User" in line and "Post" in line
    ]
    assert len(rel_lines) == 1


def test_generate_er_empty() -> None:
    result = generate_er_diagram([])
    assert result == "erDiagram"
