[日本語](README_ja.md)

# rails-lens

MCP server that reveals implicit Rails dependencies for AI coding tools.

## Overview

rails-lens is an MCP (Model Context Protocol) server that extracts and exposes
Ruby on Rails application structure to AI coding tools like Claude Code and Cursor.
It helps AI tools understand Rails implicit dependencies such as callbacks, associations,
concerns, and dynamic method generation.

## Installation

pip install rails-lens

## Usage

Add to your MCP client configuration (~/.claude/claude_desktop_config.json):

{
  "mcpServers": {
    "rails-lens": {
      "command": "rails-lens",
      "env": {
        "RAILS_LENS_PROJECT_PATH": "/path/to/your/rails/project"
      }
    }
  }
}

## Configuration

Create .rails-lens.toml in your Rails project root:

[rails]
project_path = "/path/to/rails/project"
timeout = 30

[cache]
auto_invalidate = true

[search]
command = "rg"

## License

MIT
