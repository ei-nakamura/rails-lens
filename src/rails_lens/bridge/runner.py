"""Ruby実行ブリッジ"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from rails_lens.config import RailsLensConfig
from rails_lens.errors import (
    RailsBridgeError,
    RailsProjectNotFoundError,
    RailsRunnerExecutionError,
    RailsRunnerOutputError,
    RailsRunnerTimeoutError,
)

logger = logging.getLogger(__name__)


class RailsBridge:
    """rails runner を介してRubyスクリプトを実行するブリッジ"""

    def __init__(self, config: RailsLensConfig) -> None:
        self.config = config

    async def execute(
        self,
        script_name: str,
        args: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Rubyスクリプトを実行し、JSONレスポンスを返す。

        Args:
            script_name: 実行するスクリプト名（例: "introspect_model.rb"）
            args: スクリプトに渡す引数のリスト

        Returns:
            Rubyスクリプトが出力したJSONをパースしたdict

        Raises:
            RailsProjectNotFoundError: Railsプロジェクトが見つからない
            RailsRunnerTimeoutError: タイムアウト
            RailsRunnerExecutionError: rails runner の実行に失敗
            RailsRunnerOutputError: 出力のJSON解析に失敗
        """
        self._validate_project()

        command = self._build_command(script_name, args or [])
        logger.info("Executing: %s", " ".join(command))

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.config.rails_project_path),
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self.config.timeout,
            )

        except TimeoutError:
            process.kill()
            await process.wait()
            raise RailsRunnerTimeoutError(
                f"Rails runner timed out after {self.config.timeout} seconds. "
                f"The Rails application may be slow to boot. "
                f"Try increasing 'rails.timeout' in .rails-lens.toml."
            ) from None
        except FileNotFoundError as err:
            raise RailsRunnerExecutionError(
                "Failed to execute Rails runner. "
                "Ensure Ruby and Bundler are installed and available in PATH."
            ) from err

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if stderr:
            logger.debug("Rails runner stderr:\n%s", stderr)

        if process.returncode != 0:
            raise RailsRunnerExecutionError(
                f"Rails runner exited with code {process.returncode}. "
                f"Ensure 'bundle install' has been run in the Rails project.\n"
                f"stderr: {stderr[:500]}"
            )

        return self._parse_output(stdout, stderr)

    def _build_command(self, script_name: str, args: list[str]) -> list[str]:
        """実行コマンドを組み立てる"""
        script_path = self.config.ruby_scripts_path / script_name

        if not script_path.is_file():
            raise RailsBridgeError(
                f"Ruby script not found: {script_path}"
            )

        # "bundle exec rails runner" → ["bundle", "exec", "rails", "runner"]
        command_parts = self.config.ruby_command.split()
        return [*command_parts, str(script_path), *args]

    def _parse_output(self, stdout: str, stderr: str) -> dict[str, Any]:
        """標準出力のJSONを解析する"""
        stdout = stdout.strip()
        if not stdout:
            raise RailsRunnerOutputError(
                "Rails runner produced no output. "
                "Check stderr for details."
            )

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as e:
            # stdout の先頭部分をヒントとして含める
            preview = stdout[:200]
            raise RailsRunnerOutputError(
                f"Failed to parse JSON from Rails runner output. "
                f"This may be caused by debug output in the Rails application. "
                f"Output preview: {preview!r}\n"
                f"Parse error: {e}"
            ) from e

        # RailsLens::Serializer の status チェック
        if result.get("status") == "error":
            error_info = result.get("error", {})
            raise RailsRunnerExecutionError(
                error_info.get("message", "Unknown error from Rails runner"),
            )

        data = result.get("data", result)
        return dict(data) if isinstance(data, dict) else result

    def _validate_project(self) -> None:
        """Railsプロジェクトの存在を確認する"""
        project_path = self.config.rails_project_path

        if not project_path.is_dir():
            raise RailsProjectNotFoundError(
                f"Rails project directory not found: {project_path}"
            )

        gemfile = project_path / "Gemfile"
        if not gemfile.is_file():
            raise RailsProjectNotFoundError(
                f"No Gemfile found at {project_path}. "
                f"Ensure the path points to a Rails project root."
            )
