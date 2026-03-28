"""bridge/runner.py のテスト"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rails_lens.bridge.runner import RailsBridge
from rails_lens.config import RailsLensConfig
from rails_lens.errors import (
    RailsProjectNotFoundError,
    RailsRunnerExecutionError,
    RailsRunnerOutputError,
)


def test_build_command(config: RailsLensConfig) -> None:
    """コマンド組み立ての確認"""
    bridge = RailsBridge(config)
    # ruby_scripts_path が存在するスクリプトを仮想的にテスト
    # _build_command はスクリプトファイルの存在確認をするため、tmp_path にスクリプトを置く
    script_path = config.ruby_scripts_path / "introspect_model.rb"
    if not script_path.is_file():
        # スクリプトが存在しない場合はコマンド組み立て部分だけをテスト
        command_parts = config.ruby_command.split()
        assert command_parts == ["bundle", "exec", "rails", "runner"]
    else:
        cmd = bridge._build_command("introspect_model.rb", ["User"])
        assert "introspect_model.rb" in " ".join(cmd)
        assert "User" in cmd


def test_validate_project_success(config: RailsLensConfig) -> None:
    """Gemfile 存在時はエラーなし"""
    bridge = RailsBridge(config)
    # sample_rails_app には Gemfile がある
    bridge._validate_project()  # 例外が発生しなければOK


def test_validate_project_no_gemfile(tmp_path: Path) -> None:
    """Gemfile なしでエラー"""
    no_gemfile_dir = tmp_path / "no_gemfile_app"
    no_gemfile_dir.mkdir()
    cfg = RailsLensConfig(rails_project_path=no_gemfile_dir)
    bridge = RailsBridge(cfg)
    with pytest.raises(RailsProjectNotFoundError):
        bridge._validate_project()


def test_parse_output_success(config: RailsLensConfig) -> None:
    """正常 JSON 解析"""
    bridge = RailsBridge(config)
    data = {"model_name": "User", "associations": []}
    result = bridge._parse_output(json.dumps(data), "")
    assert result == data


def test_parse_output_invalid_json(config: RailsLensConfig) -> None:
    """不正 JSON で RailsRunnerOutputError"""
    bridge = RailsBridge(config)
    with pytest.raises(RailsRunnerOutputError):
        bridge._parse_output("not json at all {{{", "")


def test_parse_output_ruby_error(config: RailsLensConfig) -> None:
    """status:error で RailsRunnerExecutionError"""
    bridge = RailsBridge(config)
    error_payload = json.dumps({
        "status": "error",
        "error": {"message": "Model not found", "details": {}}
    })
    with pytest.raises(RailsRunnerExecutionError):
        bridge._parse_output(error_payload, "")


def test_parse_output_empty(config: RailsLensConfig) -> None:
    """空出力で RailsRunnerOutputError"""
    bridge = RailsBridge(config)
    with pytest.raises(RailsRunnerOutputError):
        bridge._parse_output("", "")


@pytest.mark.asyncio
async def test_execute_mocked(config: RailsLensConfig) -> None:
    """subprocess をモックして execute の正常系をテスト"""
    bridge = RailsBridge(config)
    expected_data = {"model_name": "User", "associations": []}

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(
        return_value=(json.dumps(expected_data).encode(), b"")
    )

    with (
        patch("asyncio.create_subprocess_exec", return_value=mock_process),
        patch.object(bridge, "_build_command", return_value=["echo", "test"]),
    ):
        result = await bridge.execute("introspect_model.rb", ["User"])

    assert result == expected_data
