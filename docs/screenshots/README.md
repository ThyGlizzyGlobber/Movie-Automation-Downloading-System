# Screenshots

Drop images here and uncomment the matching `<!-- SCREENSHOT: ... -->` block in the
root [`README.md`](../../README.md). Each placeholder there names the shot it wants and
the filename it expects, so they can be filled in any order.

## Slots the README is waiting on

| File | Shot |
|---|---|
| `home.png` | Hero shot — the Home page on desktop, full width. This is the first image anyone sees. |
| `home-mobile.png` | Optional companion — the same page on a phone. Worth having: the responsive pass is a selling point, and a desktop-only gallery hides it. |
| `request.png` | The request flow — a detail page with the Request action. |
| `requests.png` | The Requests page, ideally with a mix of statuses visible. |
| `settings.png` | Settings, showing the pipeline or dashboard panel. |
| `activity.png` | The activity dashboard mid-download. |
| `install-compose.png` | Terminal after a successful `docker compose up -d --build`. |
| `install-truenas.png` | The TrueNAS Custom App form with the compose config pasted in. |
| `setup-1-token.png` | Setup wizard — setup code. |
| `setup-2-tmdb.png` | Setup wizard — TMDB key. |
| `setup-3-qbittorrent.png` | Setup wizard — qBittorrent connection. |
| `setup-4-plex.png` | Setup wizard — Plex server link. |

## Notes

- **PNG**, and keep each one under ~500 KB — they load on every visit to the repo front
  page. Resize rather than ship a 4K capture.
- Capture at a consistent width so the README doesn't jump between shots. Desktop shots
  above the 860px breakpoint (where top-bar nav replaces the tab bar); phone shots below
  640px, so each tier looks like itself rather than something caught mid-reflow.
- Blur or crop anything identifying: real usernames, server names, local IPs, domains,
  and the setup code itself (it is a credential — use a fake one for the shot).
- The app is dark-only, so screenshots sit better against GitHub's dark theme. Check the
  light theme too before committing.
