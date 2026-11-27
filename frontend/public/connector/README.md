# CeDesk connector logos

The CeDesk mark for AI-app directories (Claude, ChatGPT / OpenAI apps, Antigravity and
others), made from `frontend/public/logo-mark.png`: square, transparent background, the
mark centred with about 8% padding on each side.

| File | Size | Use it for |
|---|---|---|
| `cedesk-512.png` | 512 x 512 | Directory listings that take one large icon; the source of the others |
| `cedesk-256.png` | 256 x 256 | Listings that ask for 256 px or less |
| `cedesk-128.png` | 128 x 128 | Antigravity's MCP store (square SVG or PNG, at least 128 px) |
| `cedesk-48.png` | 48 x 48 | Small icons; the least OpenAI accepts |
| `cedesk.svg` | 512 x 512 view box | Where an SVG is asked for |

**`cedesk.svg` is a raster in an SVG**: it embeds `cedesk-512.png` as a base64 `<image>`.
It scales no better than the PNG. It stands in until a designer supplies a true vector
mark; replace it then, and make the PNGs again from the vector.

What each directory asks for:

| Directory | Logo |
|---|---|
| Antigravity | Square SVG or PNG, at least 128 x 128 px. |
| OpenAI (ChatGPT apps) | Square PNG, at least 48 x 48 px, at most 5 MiB. |
| Claude (connectors directory) | Square; send `cedesk-512.png`, or the SVG when an SVG is asked for. |

The source mark has a soft light halo around it, so it reads best on light backgrounds.
Check how it looks on a dark one before submitting.

These files are served by the web app at `/connector/<file>` (for example
`https://app.cedesk.in/connector/cedesk-512.png`) when a directory wants a URL instead of
an upload.
