export const paths: Record<string, string> = {
  down: "m6 9 6 6 6-6",
  right: "m9 6 6 6-6 6",
  left: "m15 6-6 6 6 6",
  asc: "M12 19V5m-5 5 5-5 5 5",
  desc: "M12 5v14m-5-5 5 5 5-5",
  sort: "M8 19V5m-4 4 4-4 4 4m4-4v14m-4-4 4 4 4-4",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  expand: "M14 3h7v7m0-7-7 7M10 21H3v-7m0 7 7-7",
  close: "m6 6 12 12M6 18 18 6",
  filter: "M4 5h16l-6 7v6l-4 2v-8Z",
  move: "M12 3v18M3 12h18m-12-6 3-3 3 3m-6 12 3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3",
  pin: "m16 3 5 5-4 1-3 5v3l-7-7h3l5-3ZM3 21l7-7",
  hide: "m3 3 18 18M10 5c5-1 9 3 12 7-1 2-3 4-5 5M6 6c-2 2-4 4-5 6 3 4 7 7 12 7l2-1",
  blocked: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M6 6l12 12",
};
export function TableIcon({
  name,
  className = "",
}: {
  name: string;
  className?: string;
}) {
  return (
    <svg
      className={`table-svg-icon ${className}`}
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={paths[name]} />
    </svg>
  );
}
export function gridIcon(name: string) {
  const ns = "http://www.w3.org/2000/svg",
    svg = document.createElementNS(ns, "svg");
  for (const [k, v] of Object.entries({
    viewBox: "0 0 24 24",
    width: "16",
    height: "16",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "1.75",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "aria-hidden": "true",
    focusable: "false",
  }))
    svg.setAttribute(k, v);
  svg.classList.add("table-svg-icon");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", paths[name]);
  svg.append(path);
  const span = document.createElement("span");
  span.className = "table-grid-icon";
  span.setAttribute("aria-hidden", "true");
  span.append(svg);
  return span;
}
