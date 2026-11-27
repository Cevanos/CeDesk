"""The MCP server AI apps (Claude, ChatGPT, Codex, Cursor ...) reach at `<issuer>/mcp`.

It sits outside the helpdesk layers and calls the service layer only (an import-linter
contract keeps it off the repo and api layers): every tool runs as the member who
connected the app, through the same `caller.can(...)` checks as the web app.
"""
