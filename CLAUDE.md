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

## Adding or changing tools

- Each tool is a folder with an `index.html` (`todo/`, `budget/`, ...).
- Shared colours, buttons and the top tab bar live in `assets/base.css` and `assets/nav.js`.
  Register a new tool in the `LIFE_TOOLS` list in `assets/nav.js` so it gets a tab and a home-page card.
- Each page starts its `<body>` with `data-tool="<id>"` and `<script src="../assets/nav.js"></script>`.
- Pushing code to `main` redeploys the site through `.github/workflows/pages.yml`. Changes under `data/` don't redeploy.
- The repo and site are public. Never commit statements, tokens or other private data.
