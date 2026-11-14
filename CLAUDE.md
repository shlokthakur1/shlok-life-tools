# shlok-life-tools

Personal tools site, published with GitHub Pages at https://shlokthakur1.github.io/shlok-life-tools/.
It's plain static HTML with no build step and no dependencies to install.

## Managing the to-do list

Most sessions in this repo are me on my phone asking you to change my to-do list. The list is
[data/todo.md](data/todo.md), a Markdown checklist that the website at `/todo/` reads and writes live.

When I ask you to add, finish, reopen, reword or remove tasks:

1. `git pull --rebase origin main` first. The website commits to this file directly, so your copy may be stale.
2. Edit `data/todo.md`:
   - Open task: `- [ ] text`. Done: `- [x] text`. Keep one task per line.
   - `## Heading` lines are sections (e.g. `## Shopping`). Put new tasks at the end of the section that fits,
     or in the top list if none does. Create a section only if I ask for one or I'm adding several related tasks.
   - Don't reformat or reorder lines you weren't asked to touch.
3. Commit with a message like `todo: add "Book dentist"` and **push straight to `main`**. Don't open a branch or PR
   for to-do changes, because the website only reads `main`.
4. Reply with the current open tasks as a short list.

If I just ask "what's on my list", read the file and answer; don't commit anything.

## BearShare Australia (buy signals)

The BearShare tab (`bearshare/index.html`) shows buy signals from `data/bearshare.json`. The **BearShare signals**
workflow (`.github/workflows/bearshare-signals.yml`) rebuilds that file every weekday after the ASX close by running
`bearshare/build_signals.py`, and commits it to `main`. Don't edit `data/bearshare.json` by hand.

- **"What's a buy?"**: `git pull --rebase origin main`, read `data/bearshare.json` and list the items whose `signal`
  is `strong-buy` or `buy`, best `score` first, each with its `summary`. Mention the `generated` date. Don't commit anything.
- **Add or remove a share/ETF**: edit `bearshare/watchlist.json`. Each entry needs `code` (ASX code without `.AX`),
  `name`, `type` (`share` or `etf`), `sector` and a Google News `news` query; ETFs also get a `topic` (what the news
  is about, e.g. "the S&P 500"). Commit like `bearshare: add PLS` and push to `main`. The push re-runs the workflow,
  so the new code appears a few minutes later.
- Signals are general information, not financial advice. Keep the disclaimer on the page, and say so when you list buys.

## Adding or changing tools

- Each tool is a folder with an `index.html` (`todo/`, `budget/`, ...).
- Shared colours, buttons and the top tab bar live in `assets/base.css` and `assets/nav.js`.
  Register a new tool in the `LIFE_TOOLS` list in `assets/nav.js` so it gets a tab and a home-page card.
- Each page starts its `<body>` with `data-tool="<id>"` and `<script src="../assets/nav.js"></script>`.
- Pushing code to `main` redeploys the site through `.github/workflows/pages.yml`. Changes under `data/` don't redeploy.
- The repo and site are public. Never commit statements, tokens or other private data.
