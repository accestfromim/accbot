const paths = {
  puzzle:
    '<path d="M8 3H4v6a3 3 0 1 1 0 6v5h5a3 3 0 1 1 6 0h5v-5a3 3 0 1 1 0-6V4h-6a3 3 0 1 0-6-1Z"/>',
  brain:
    '<path d="M12 18V5a3 3 0 0 0-5.8-1 4 4 0 0 0-3 6 4 4 0 0 0 0 6 4 4 0 0 0 6.8 4M12 5a3 3 0 0 1 5.8-1 4 4 0 0 1 3 6 4 4 0 0 1 0 6 4 4 0 0 1-6.8 4M8 8c0 2-2 3-4 3m12-3c0 2 2 3 4 3M7 15c2-1 4 0 5 2m5-2c-2-1-4 0-5 2"/>',
  cable:
    '<path d="M7 2v4m4-4v4M5 6h8v3a4 4 0 0 1-8 0V6Zm4 7v3a4 4 0 0 0 8 0v-3m-4-3h8m-6 0V7m4 3V7"/>',
  key: '<circle cx="8" cy="9" r="5"/><path d="m12 13 8 8m-4-4 3-3m-1 5 3-3"/>',
  server:
    '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M11 6.5h6m-6 11h6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  moon: '<path d="M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10Z"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  "chevron-right": '<path d="m9 6 6 6-6 6"/>',
  "arrow-up-right": '<path d="M7 17 17 7M7 7h10v10"/>',
  "arrow-right": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  "arrow-left": '<path d="M20 12H4m6-6-6 6 6 6"/>',
  refresh:
    '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  folder:
    '<path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/>',
  "folder-open":
    '<path d="M3 9V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v1M3 9h19l-3 11H4L2 9"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  circlecheck: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
  alert: '<path d="m12 3 10 17H2L12 3Z"/><path d="M12 9v4m0 3h.01"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  box: '<path d="m12 3 9 5v9l-9 5-9-5V8l9-5Zm0 10 9-5m-9 5L3 8m9 5v9M7 6l9 5"/>',
  activity: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  message:
    '<path d="M21 15a3 3 0 0 1-3 3H8l-5 4V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3v9Z"/><path d="M7 8h10M7 12h6"/>',
  terminal:
    '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3m6 0h4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  file: '<path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 13h8m-8 4h6"/>',
  book: '<path d="M12 5c-3-2-6-2-10-1v15c4-1 7-1 10 1 3-2 6-2 10-1V4c-4-1-7-1-10 1Zm0 0v15"/>',
  edit: '<path d="m16 3 5 5M4 20l5-1L21 7l-4-4L5 15l-1 5Z"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  eye: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  "eye-off":
    '<path d="m3 3 18 18M9 5a13 13 0 0 1 3 0c7 0 10 7 10 7a17 17 0 0 1-3 4M6 6a20 20 0 0 0-4 6s3 7 10 7a13 13 0 0 0 5-1M10 10a3 3 0 0 0 4 4"/>',
  link: '<path d="m10 13 4-4m-5 0-3 3a4 4 0 0 0 6 6l2-2m-4-8 2-2a4 4 0 0 1 6 6l-3 3"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  play: '<path d="m7 4 14 8-14 8V4Z"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v2"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
};
export function icon(name, cls = "") {
  return `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.box}</svg>`;
}
export function mountIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icon(el.dataset.icon);
    el.removeAttribute("data-icon");
  });
}
