"""rails_lens_migration_context ツール"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rails_lens.errors import RailsRunnerExecutionError, RailsRunnerTimeoutError
from rails_lens.models import (
    ColumnInfo,
    ErrorResponse,
    ForeignKeyInfo,
    IndexInfo,
    MigrationContextInput,
    MigrationContextOutput,
    MigrationHistoryItem,
    MigrationTemplate,
    MigrationWarning,
    SchemaInfo,
)

_MIGRATION_CLASS_RE = re.compile(r'class\s+(\w+)\s*<\s*ActiveRecord::Migration')
_SCHEMA_CREATE_TABLE_RE = re.compile(r'create_table\s+"(\w+)"')
_SCHEMA_COLUMN_RE = re.compile(
    r't\.(string|integer|bigint|text|boolean|float|decimal|datetime|date|time|binary|json|jsonb|uuid|references)'
    r'\s+"(\w+)"([^#\n]*)'
)
_SCHEMA_NULL_FALSE_RE = re.compile(r'null:\s*false')
_SCHEMA_INDEX_RE = re.compile(r'add_index\s+"(\w+)",\s+(\[.*?\]|"(\w+)")')
_SCHEMA_INDEX_COLS_RE = re.compile(r'"(\w+)"')
_SCHEMA_UNIQUE_RE = re.compile(r'unique:\s*true')
_SCHEMA_INDEX_NAME_RE = re.compile(r'name:\s*"([^"]+)"')
_SCHEMA_FK_RE = re.compile(
    r'add_foreign_key\s+"(\w+)",\s+"(\w+)"(?:.*?column:\s*"(\w+)")?'
)
_OPERATION_RE = re.compile(
    r'(create_table|add_column|remove_column|add_index|remove_index|'
    r'change_column|add_reference|rename_column|rename_table)\b[^#\n]*'
)


def _parse_schema_for_table(schema_path: Path, table_name: str) -> dict[str, Any]:
    """db/schema.rb から指定テーブルのカラム・インデックス・外部キー情報を抽出する"""
    try:
        content = schema_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"columns": [], "indexes": [], "foreign_keys": []}

    columns = []
    indexes = []
    foreign_keys = []

    # Find create_table block for the target table
    blocks = re.split(r'(?=create_table\s+")', content)
    for block in blocks:
        m = _SCHEMA_CREATE_TABLE_RE.match(block)
        if not m or m.group(1) != table_name:
            continue
        for col_m in _SCHEMA_COLUMN_RE.finditer(block):
            col_type, col_name, options = col_m.group(1), col_m.group(2), col_m.group(3)
            null_allowed = not bool(_SCHEMA_NULL_FALSE_RE.search(options))
            columns.append({
                "name": col_name, "type": col_type,
                "null": null_allowed, "default": None, "limit": None,
            })
        break

    # Indexes for this table (from full content)
    for idx_m in _SCHEMA_INDEX_RE.finditer(content):
        if idx_m.group(1) != table_name:
            continue
        cols_str = idx_m.group(2)
        idx_columns = _SCHEMA_INDEX_COLS_RE.findall(cols_str)
        unique = bool(_SCHEMA_UNIQUE_RE.search(idx_m.group(0)))
        name_m = _SCHEMA_INDEX_NAME_RE.search(idx_m.group(0))
        idx_name = name_m.group(1) if name_m else f"index_{table_name}_on_{'_'.join(idx_columns)}"
        indexes.append({"name": idx_name, "columns": idx_columns, "unique": unique})

    # Foreign keys where from_table == table_name
    for fk_m in _SCHEMA_FK_RE.finditer(content):
        if fk_m.group(1) != table_name:
            continue
        to_table = fk_m.group(2)
        from_col = fk_m.group(3) or f"{to_table.rstrip('s')}_id"
        foreign_keys.append({"from_column": from_col, "to_table": to_table, "to_column": "id"})

    return {"columns": columns, "indexes": indexes, "foreign_keys": foreign_keys}


def _parse_migration_file(path: Path, table_name: str) -> MigrationHistoryItem | None:
    """マイグレーションファイルをパースしてMigrationHistoryItemを返す"""
    filename = path.name
    # filename format: 20260101000000_create_users.rb
    match = re.match(r'^(\d{14})_(.+)\.rb$', filename)
    if not match:
        return None

    version = match.group(1)
    name_snake = match.group(2)
    name_camel = "".join(w.capitalize() for w in name_snake.split("_"))

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Extract operation summary from file content
    ops = []
    for m in _OPERATION_RE.finditer(content):
        op_line = m.group(0).strip()
        if table_name in op_line or name_snake.replace("_", "") in op_line.replace("_", ""):
            ops.append(op_line[:80])
            if len(ops) >= 2:
                break

    operation_summary = "; ".join(ops) if ops else f"{name_camel}"

    return MigrationHistoryItem(
        version=version,
        name=name_camel,
        file=str(path),
        operation_summary=operation_summary,
    )


def _generate_warnings(
    output_data: dict[str, Any],
    operation: str,
) -> list[MigrationWarning]:
    """警告ルールに基づいてwarningsを生成する"""
    warnings: list[MigrationWarning] = []
    estimated_rows = output_data.get("estimated_row_count")
    indexes = output_data.get("indexes", [])
    foreign_keys = output_data.get("foreign_keys", [])

    large_table = estimated_rows is not None and estimated_rows > 1_000_000

    if large_table and operation in ("add_column", "change_column"):
        warnings.append(MigrationWarning(
            type="large_table",
            message=(
                f"テーブルは推定{estimated_rows:,}行以上です。"
                "NOT NULL制約付きのカラム追加はテーブルロックを引き起こす可能性があります"
            ),
            suggestion=(
                "デフォルト値を設定するか、NULLを許可してバックフィルする"
                "2段階マイグレーションを検討してください"
            ),
        ))

    if large_table and operation == "add_index":
        warnings.append(MigrationWarning(
            type="large_table",
            message=(
                f"テーブルは推定{estimated_rows:,}行以上です。"
                "インデックス追加は長時間ロックを引き起こす可能性があります"
            ),
            suggestion=(
                "PostgreSQLでは `add_index ..., algorithm: :concurrently` を使用してください"
            ),
        ))

    # Check for foreign keys without indexes
    indexed_columns = {col for idx in indexes for col in idx.get("columns", [])}
    for fk in foreign_keys:
        fk_col = fk.get("from_column", "")
        if fk_col and fk_col not in indexed_columns:
            warnings.append(MigrationWarning(
                type="missing_index",
                message=f"外部キー '{fk_col}' にインデックスがありません",
                suggestion=(
                    f"add_index :{output_data.get('table_name', 'table')}, :{fk_col}"
                    " を追加することを推奨します"
                ),
            ))

    return warnings


def _generate_template(table_name: str, operation: str) -> MigrationTemplate | None:
    """operationに基づいてマイグレーションテンプレートを生成する"""
    class_name_base = "".join(w.capitalize() for w in table_name.split("_"))

    templates = {
        "add_column": (
            f"Add<ColumnName>To{class_name_base}",
            f"class Add<ColumnName>To{class_name_base} < ActiveRecord::Migration[7.1]\n"
            f"  def change\n"
            f"    add_column :{table_name}, :<column_name>, :<type>\n"
            f"  end\n"
            f"end",
        ),
        "remove_column": (
            f"Remove<ColumnName>From{class_name_base}",
            f"class Remove<ColumnName>From{class_name_base} < ActiveRecord::Migration[7.1]\n"
            f"  def change\n"
            f"    remove_column :{table_name}, :<column_name>, :<type>\n"
            f"  end\n"
            f"end",
        ),
        "add_index": (
            f"AddIndexTo{class_name_base}",
            f"class AddIndexTo{class_name_base} < ActiveRecord::Migration[7.1]\n"
            f"  def change\n"
            f"    add_index :{table_name}, :<column_name>\n"
            f"  end\n"
            f"end",
        ),
        "remove_index": (
            f"RemoveIndexFrom{class_name_base}",
            f"class RemoveIndexFrom{class_name_base} < ActiveRecord::Migration[7.1]\n"
            f"  def change\n"
            f"    remove_index :{table_name}, :<column_name>\n"
            f"  end\n"
            f"end",
        ),
        "change_column": (
            f"Change<ColumnName>In{class_name_base}",
            f"class Change<ColumnName>In{class_name_base} < ActiveRecord::Migration[7.1]\n"
            f"  def change\n"
            f"    change_column :{table_name}, :<column_name>, :<new_type>\n"
            f"  end\n"
            f"end",
        ),
        "add_reference": (
            f"Add<RefName>To{class_name_base}",
            f"class Add<RefName>To{class_name_base} < ActiveRecord::Migration[7.1]\n"
            f"  def change\n"
            f"    add_reference :{table_name}, :<ref_name>, null: false, foreign_key: true\n"
            f"  end\n"
            f"end",
        ),
    }

    if operation not in templates:
        return None

    desc_base, code = templates[operation]
    return MigrationTemplate(
        description=f"{table_name}テーブルへの{operation}マイグレーション",
        code=code,
    )


def register(mcp: FastMCP, get_deps: Callable[[], Any]) -> None:
    @mcp.tool(
        name="rails_lens_migration_context",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def migration_context(params: MigrationContextInput) -> str:
        """テーブルのスキーマ・インデックス・外部キー・マイグレーション履歴を返し、適切な警告とテンプレートを提供する"""
        try:
            config, bridge, cache, grep = get_deps()
        except Exception as e:
            return ErrorResponse(
                code="INITIALIZATION_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        file_analysis_fallback = False
        try:
            # ランタイム解析 (Ruby)
            raw_data = await bridge.execute(
                "migration_context.rb",
                args=[params.table_name, params.operation],
            )
        except (RailsRunnerExecutionError, RailsRunnerTimeoutError, FileNotFoundError, OSError):
            schema_path = config.rails_project_path / "db" / "schema.rb"
            raw_data = _parse_schema_for_table(schema_path, params.table_name)
            file_analysis_fallback = True
        except Exception as e:
            return ErrorResponse(
                code="RUNTIME_ANALYSIS_ERROR", message=str(e)
            ).model_dump_json(indent=2)

        try:
            # Build SchemaInfo from raw data
            columns = [
                ColumnInfo(
                    name=col["name"],
                    type=col.get("type", "string"),
                    null=col.get("null", True),
                    default=col.get("default"),
                    limit=col.get("limit"),
                )
                for col in raw_data.get("columns", [])
            ]
            indexes = [
                IndexInfo(
                    name=idx["name"],
                    columns=idx.get("columns", []),
                    unique=idx.get("unique", False),
                )
                for idx in raw_data.get("indexes", [])
            ]
            foreign_keys = [
                ForeignKeyInfo(
                    from_column=fk["from_column"],
                    to_table=fk["to_table"],
                    to_column=fk.get("to_column", "id"),
                )
                for fk in raw_data.get("foreign_keys", [])
            ]
            schema = SchemaInfo(columns=columns, indexes=indexes, foreign_keys=foreign_keys)

            # Parse migration history: enrich from files

            migration_history: list[MigrationHistoryItem] = []
            migrate_dir = config.rails_project_path / "db" / "migrate"
            if migrate_dir.exists():
                migration_files = sorted(
                    migrate_dir.glob("*.rb"), reverse=True
                )[:20]
                for mf in migration_files:
                    item = _parse_migration_file(mf, params.table_name)
                    if item:
                        migration_history.append(item)

            # Find related models (models using this table)
            related_models: list[str] = []
            try:
                table_refs = grep.search(params.table_name, scope="models", search_type="any")
                seen: set[str] = set()
                for ref in table_refs[:10]:
                    # Extract model class names from file paths
                    fpath = ref.file
                    if "app/models" in fpath and fpath.endswith(".rb"):
                        model_file = Path(fpath).stem
                        model_name = "".join(w.capitalize() for w in model_file.split("_"))
                        if model_name not in seen:
                            seen.add(model_name)
                            related_models.append(model_name)
            except Exception:
                pass

            # Generate warnings
            warnings = _generate_warnings(
                {
                    "table_name": params.table_name,
                    "estimated_row_count": raw_data.get("estimated_row_count"),
                    "indexes": raw_data.get("indexes", []),
                    "foreign_keys": raw_data.get("foreign_keys", []),
                },
                params.operation,
            )

            # Generate template for the requested operation
            template = _generate_template(params.table_name, params.operation)

            output = MigrationContextOutput(
                table_name=params.table_name,
                operation=params.operation,
                schema=schema,
                migration_history=migration_history,
                warnings=warnings,
                template=template,
                related_models=related_models,
                estimated_row_count=raw_data.get("estimated_row_count"),
            )
            if file_analysis_fallback:
                result = output.model_dump()
                result["_metadata"] = {
                    "source": "file_analysis",
                    "note": "Rails runner unavailable",
                }
                return json.dumps(result, ensure_ascii=False, indent=2)
            return output.model_dump_json(indent=2)

        except Exception as e:
            return ErrorResponse(
                code="MIGRATION_CONTEXT_ERROR", message=str(e)
            ).model_dump_json(indent=2)
