# Life Tools

Small personal tools, live at **https://shlokthakur1.github.io/shlok-life-tools/**

| Tool | What it does |
| --- | --- |
| [To-do](https://shlokthakur1.github.io/shlok-life-tools/todo/) | A checklist kept in [`data/todo.md`](data/todo.md). Edit it on the site, or ask Claude to change it. |
| [Budget](https://shlokthakur1.github.io/shlok-life-tools/budget/) | Statement Ledger: turns ANZ statement PDFs into monthly budget categories, entirely in the browser. |
| [Vote](https://shlokthakur1.github.io/shlok-life-tools/vote/) | Victorian election 2026: party policies compared by issue, with sources, links to the originals and a private rating ballot. |

## To-do sync

- **On the site:** open the To-do tab, tap ⚙︎ and paste a fine-grained GitHub token that has
  *Contents: Read and write* on this repo only. The token is stored only in that browser. Without a token, the list is view only.
- **From Claude:** start a session on this repo from the Code tab in the Claude app and say something like
  "add book dentist to my to-do". [CLAUDE.md](CLAUDE.md) tells Claude how to edit the file and push to `main`.

Both edit the same file, so the site and Claude always agree.
