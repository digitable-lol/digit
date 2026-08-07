# Digit CLI Reference

Live sources when anything looks stale: `digit --help`, `digit <command> --help`,
https://docs.digitable.life/reference/cli-commands

### Global Flags

```
digit [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
digit chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
digit setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
digit model                Interactive model/provider picker
digit fallback [add|remove|list]  Fallback provider chain
digit config [show|edit|get|set|unset|path|env-path|check|migrate]
digit login / logout       OAuth sign-in / clear stored auth
digit doctor [--fix]       Check dependencies and config
digit status [--all]       Component status
```

### Tools & Skills

```
digit tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

digit skills list|browse|search QUERY|inspect ID
digit skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
digit skills config        Enable/disable skills per platform
digit skills check|update|uninstall|publish PATH
digit skills tap add REPO  Add a GitHub repo as a skill source
digit bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
digit mcp add NAME (--url or --command) | remove | list | test NAME
digit mcp catalog | install NAME     Curated catalog install
digit mcp configure NAME             Toggle tool selection
digit mcp serve                      Run Digit as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
digit gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `digit photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://docs.digitable.life/user-guide/messaging/

### Sessions

```
digit sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
digit cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
digit webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
digit profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
digit profile rename A B | alias NAME | export NAME | import FILE
```

### Credentials & Pools

```
digit auth                 Interactive credential manager
digit auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
digit auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
digit desktop / gui        Native desktop app
digit dashboard            Web admin panel + embedded chat (--stop / --status)
digit proxy                OpenAI-compatible local proxy backed by an OAuth provider
digit portal               Quick setup / sign in via Nous Portal
digit kanban <verb>        Multi-agent work-queue board
digit project              Named multi-folder workspaces
digit skin list|use|set    Switch/tweak skins (see references/themes.md)
digit pets <verb>          Pet mascots (see references/petdex.md)
digit memory setup|status|off|reset   Memory provider
digit secrets bitwarden|onepassword   External secret stores
digit moa                  Mixture-of-Agents slots
digit hooks / security / backup / import / checkpoints / console
digit logs [-f] [errors]   View agent/error logs
digit send                 One-off message through a gateway platform
digit pairing / plugins / insights / journey / computer-use
digit acp                  ACP server (IDE integration)
digit completion bash|zsh|fish
digit update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `digit photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `digit config edit` · [Configuration docs](https://docs.digitable.life/user-guide/configuration) |
| Tools / toolsets | `digit tools list` · [Tools reference](https://docs.digitable.life/reference/tools-reference) |
| Skills catalog | `digit skills browse` · [Skills catalog](https://docs.digitable.life/reference/skills-catalog) |
| Provider setup | `digit model` · [Providers guide](https://docs.digitable.life/integrations/providers) |
| Env variables | `digit config env-path` · [Env vars reference](https://docs.digitable.life/reference/environment-variables) |
| Gateway logs | `~/.digit/logs/gateway.log` (or `digit logs`) |
| Sessions | `digit sessions browse` (reads state.db) |
