// MCP client configuration snippets. Keep in sync with the "Using the MCP server" section of README.md.
// The key is never embedded: every snippet reads it from the TRUSTDESK_API_KEY environment variable.

export const CLIENTS = [
  {
    id: "claude-code", name: "Claude Code", where: "Run in a terminal:",
    snippet: (u) => `claude mcp add --transport http trustdesk ${u} \\\n  --header "Authorization: Bearer $TRUSTDESK_API_KEY"`,
  },
  {
    id: "vscode", name: "VS Code (GitHub Copilot)", where: "Save as .vscode/mcp.json in your workspace (VS Code asks for the key once and stores it securely):",
    snippet: (u) => JSON.stringify({
      servers: { trustdesk: { type: "http", url: u, headers: { Authorization: "Bearer ${input:trustdesk-key}" } } },
      inputs: [{ type: "promptString", id: "trustdesk-key", description: "TrustDesk API key", password: true }],
    }, null, 2),
  },
  {
    id: "opencode", name: "OpenCode", where: "Add to opencode.json (project) or ~/.config/opencode/opencode.json:",
    snippet: (u) => JSON.stringify({
      $schema: "https://opencode.ai/config.json",
      mcp: { trustdesk: { type: "remote", url: u, enabled: true, headers: { Authorization: "Bearer {env:TRUSTDESK_API_KEY}" } } },
    }, null, 2),
  },
  {
    id: "codex", name: "Codex CLI", where: "Add to ~/.codex/config.toml:",
    snippet: (u) => `[mcp_servers.trustdesk]\nurl = "${u}"\nbearer_token_env_var = "TRUSTDESK_API_KEY"`,
  },
  {
    id: "cursor", name: "Cursor", where: "Add to ~/.cursor/mcp.json (or .cursor/mcp.json in a project):",
    snippet: (u) => JSON.stringify({
      mcpServers: { trustdesk: { url: u, headers: { Authorization: "Bearer ${env:TRUSTDESK_API_KEY}" } } },
    }, null, 2),
  },
  {
    id: "windsurf", name: "Windsurf", where: "Add to ~/.codeium/windsurf/mcp_config.json:",
    snippet: (u) => JSON.stringify({
      mcpServers: { trustdesk: { serverUrl: u, headers: { Authorization: "Bearer ${env:TRUSTDESK_API_KEY}" } } },
    }, null, 2),
  },
  {
    id: "gemini", name: "Gemini CLI", where: "Add to ~/.gemini/settings.json:",
    snippet: (u) => JSON.stringify({
      mcpServers: { trustdesk: { httpUrl: u, headers: { Authorization: "Bearer $TRUSTDESK_API_KEY" } } },
    }, null, 2),
  },
  {
    id: "cline", name: "Cline", where: "Add to cline_mcp_settings.json (Cline: MCP Servers, then Configure):",
    snippet: (u) => JSON.stringify({
      mcpServers: { trustdesk: { type: "streamableHttp", url: u, headers: { Authorization: "Bearer <your key>" } } },
    }, null, 2),
  },
  {
    id: "zed", name: "Zed", where: "Add to Zed settings.json:",
    snippet: (u) => JSON.stringify({
      context_servers: { trustdesk: { url: u, headers: { Authorization: "Bearer <your key>" } } },
    }, null, 2),
  },
  {
    id: "claude-desktop", name: "Claude Desktop", where: "Claude Desktop needs a local bridge for bearer-key servers. Add to claude_desktop_config.json (requires Node.js):",
    snippet: (u) => JSON.stringify({
      mcpServers: { trustdesk: { command: "npx", args: ["-y", "mcp-remote", u, "--header", "Authorization:${AUTH}"], env: { AUTH: "Bearer <your key>" } } },
    }, null, 2),
  },
];
