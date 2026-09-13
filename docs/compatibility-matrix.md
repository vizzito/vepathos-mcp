# Client compatibility matrix

Filled only from official documentation (cited) and from our own tests. "Tested" stays empty until a
real client run passes the smoke prompts.

Last documentation review: 2026-09-13.

| Client | Remote MCP | Streamable HTTP | OAuth | Tasks extension | Protocol version observed | Tested |
|---|---|---|---|---|---|---|
| Claude (web, Desktop, mobile) | Yes: custom connectors and Connectors Directory [1] | Yes [1] | Yes: DCR, CIMD (requires `client_id_metadata_document_supported` + `none` auth method), Anthropic-held credentials; static headers in beta [1] | Not listed in the extension matrix [3] | — | — |
| Claude Code | Yes (`claude mcp add --transport http`) | Yes | Yes: own CIMD, loopback redirect on any port [1] | Not listed [3] | — | Planned (step 2, `service` mode) |
| Cursor | Yes (`url` in `mcp.json`) [4] | Yes [4] | Yes: browser OAuth; static client configuration supported [4] | Not listed [3] | — | — |
| VS Code (GitHub Copilot) | Yes (`type: http`) | Yes | Yes (verify DCR/CIMD at release) | Not listed [3] | — | — |
| Codex (CLI / IDE) | Yes (`url` in `config.toml`) [5] | Yes [5] | Yes (`codex mcp login`; may need `oauth_resource`) [5] | Not listed [3] | — | — |
| ChatGPT (developer mode / apps) | Yes, public HTTPS [6] | Yes (and SSE) [6] | Yes: CIMD supported, DCR [6] | Not listed [3] | — | — |
| MCP Inspector 2.6.0 | Yes | Yes | Yes | — | 2026-07-28 client | Yes (fake Core, 2026-09-13) |

Notes:

- No mainstream client listed support for `io.modelcontextprotocol/tasks` as of 2026-09-13; the official
  Python and TypeScript SDKs had not shipped it either. Vepathos works through explicit
  `optimization_id` polling.
- "Protocol version observed" comes from `mcp_http_requests_total{protocol_version}` once traffic
  exists.

Sources:

1. Claude connectors — authentication, submission and review criteria: https://claude.com/docs/connectors/building/authentication, https://claude.com/docs/connectors/building/submission
2. MCP authorization specification 2026-07-28: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/index.md
3. MCP extension support matrix: https://modelcontextprotocol.io/extensions/client-matrix
4. Cursor MCP documentation: https://cursor.com/docs/mcp
5. Codex MCP documentation: https://developers.openai.com/codex/mcp
6. ChatGPT developer mode: https://developers.openai.com/api/docs/guides/developer-mode
