# Daily Briefing · 每日早報

AI-curated bilingual daily news digest for AI engineers. Runs automatically via Claude Cowork Scheduled Tasks, deployed to GitHub Pages.

由 Claude Cowork 每日自動策展，部署於 GitHub Pages 的雙語新聞早報。

## Stack

- **Data layer**: JSON files in `data/`
- **Presentation layer**: vanilla HTML/CSS/JS — no build step, no framework
- **Automation**: Claude Cowork Scheduled Tasks (daily 07:00 Asia/Taipei)
- **Content sources**: RSS feeds (primary backbone) + Firecrawl MCP (enrichment)
- **Deployment**: GitHub Pages (auto-deploy from `main`)

## Project structure

See [`docs/setup-guide.md`](docs/setup-guide.md) for full architecture and setup.

## Quick start

1. Read [`docs/setup-guide.md`](docs/setup-guide.md)
2. Enable GitHub Pages on this repo
3. Connect GitHub + Firecrawl MCP in Claude Desktop
4. Create a Cowork scheduled task with prompt from [`docs/cowork-task-prompt.md`](docs/cowork-task-prompt.md)
5. Edit [`config/sources.yaml`](config/sources.yaml) to customize news sources

## Live

Once deployed: `https://<your-user>.github.io/<this-repo>/`

## Customization

- Sources → edit `config/sources.yaml`
- Categories → edit first issue's `categories[]`
- Visual style → edit `assets/styles.css` CSS variables
- Editorial voice → edit `docs/cowork-task-prompt.md`
