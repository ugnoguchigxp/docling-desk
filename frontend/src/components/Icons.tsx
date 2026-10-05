export function IconDefinitions() {
  return (
    <svg
      className="icon-definitions"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <defs>
        <symbol id="i-back" viewBox="0 0 24 24">
          <path d="m14 6-6 6 6 6" />
        </symbol>
        <symbol id="i-next" viewBox="0 0 24 24">
          <path d="m10 6 6 6-6 6" />
        </symbol>
        <symbol id="i-plus" viewBox="0 0 24 24">
          <path d="M12 5v14M5 12h14" />
        </symbol>
        <symbol id="i-minus" viewBox="0 0 24 24">
          <path d="M5 12h14" />
        </symbol>
        <symbol id="i-thumbnails" viewBox="0 0 24 24">
          <rect x="3" y="4" width="5" height="6" rx="1" />
          <rect x="3" y="14" width="5" height="6" rx="1" />
          <path d="M12 5h9v14h-9z" />
        </symbol>
        <symbol id="i-fit" viewBox="0 0 24 24">
          <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />
          <rect x="8" y="8" width="8" height="8" rx="1" />
        </symbol>
        <symbol id="i-open" viewBox="0 0 24 24">
          <path d="M14 3h7v7m0-7L11 13M10 5H4a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-6" />
        </symbol>
        <symbol id="i-download" viewBox="0 0 24 24">
          <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
        </symbol>
        <symbol id="i-copy" viewBox="0 0 24 24">
          <rect x="8" y="8" width="13" height="13" rx="1" />
          <path d="M16 8V3H3v13h5" />
        </symbol>
        <symbol id="i-more" viewBox="0 0 24 24">
          <circle cx="5" cy="12" r="1" />
          <circle cx="12" cy="12" r="1" />
          <circle cx="19" cy="12" r="1" />
        </symbol>
        <symbol id="i-search" viewBox="0 0 24 24">
          <circle cx="10" cy="10" r="6" />
          <path d="m15 15 5 5" />
        </symbol>
        <symbol id="i-file" viewBox="0 0 24 24">
          <path d="M13 3H5v18h14V9l-6-6Zm0 0v6h6M8 13h8M8 17h6" />
        </symbol>
        <symbol id="i-folder" viewBox="0 0 24 24">
          <path d="M3 7V5h7l2 2h9v13H3z" />
        </symbol>
        <symbol id="i-delete" viewBox="0 0 24 24">
          <path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7" />
        </symbol>
      </defs>
    </svg>
  );
}
export function Icon({
  name,
  className = "",
}: {
  name: string;
  className?: string;
}) {
  return (
    <svg className={`icon ${className}`} aria-hidden="true">
      <use href={`#i-${name}`} />
    </svg>
  );
}
