// Shared top navigation. Every page includes this script as the first thing
// inside <body data-tool="...">. Register a new tool here and it shows up as
// a tab on every page and as a card on the home page.
window.LIFE_TOOLS = [
  { id: 'todo', label: 'To-do', path: 'todo/', mark: '✓',
    description: 'Tasks kept in this repo. Tick them off here, or ask Claude to add them.' },
  { id: 'budget', label: 'Budget', path: 'budget/', mark: '$',
    description: 'Drop in ANZ statement PDFs and get monthly budget categories. Runs entirely in your browser.' },
  { id: 'vote', label: 'Vote', path: 'vote/', mark: '☑',
    description: 'Labor, Liberal and One Nation compared for the Victorian election, with sources and a ballot to rate them.' },
];

(function () {
  const root = new URL('..', document.currentScript.src);
  const current = document.body.dataset.tool;

  const nav = document.createElement('nav');
  nav.className = 'lt-nav';
  nav.setAttribute('aria-label', 'Tools');
  const inner = document.createElement('div');
  inner.className = 'lt-nav-inner';

  const brand = document.createElement('a');
  brand.className = 'lt-brand';
  brand.href = root.href;
  brand.innerHTML = '<span class="lt-brand-mark" aria-hidden="true">LT</span><span class="lt-brand-name">Life Tools</span>';
  if (current === 'home') brand.setAttribute('aria-current', 'page');

  const tabs = document.createElement('div');
  tabs.className = 'lt-tabs';
  for (const tool of window.LIFE_TOOLS) {
    const a = document.createElement('a');
    a.className = 'lt-tab';
    a.href = new URL(tool.path, root).href;
    a.textContent = tool.label;
    if (tool.id === current) a.setAttribute('aria-current', 'page');
    tabs.append(a);
  }

  inner.append(brand, tabs);
  nav.append(inner);
  document.body.prepend(nav);
})();
