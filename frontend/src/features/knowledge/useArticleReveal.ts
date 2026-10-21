import { useEffect, type RefObject } from "react";

/**
 * Title and scroll position for the Wiki screen: the tab title follows the
 * open article, and a deep link (`section`) is scrolled to and focused once its
 * heading exists. Without a section the body goes back to the top.
 */
export function useArticleReveal(
  body: RefObject<HTMLDivElement | null>,
  {
    hidden,
    title,
    revision,
    source,
    section,
  }: {
    hidden: boolean;
    title: string | undefined;
    revision: string | undefined;
    source: string | null;
    section: string | null;
  },
) {
  useEffect(() => {
    if (hidden) return;
    document.title = `${title || "Wiki"} · Docling Desk`;
    if (section && revision) {
      const target = body.current;
      const reveal = () => {
        const heading = [
          ...(target?.querySelectorAll<HTMLElement>("[id]") || []),
        ].find((node) => node.id === section);
        if (!heading) return false;
        heading.scrollIntoView({ block: "start" });
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
        return true;
      };
      if (!reveal() && target) {
        // Rich article rendering is lazy; resolve a deep link after its nodes mount.
        const observer = new MutationObserver(() => {
          if (reveal()) observer.disconnect();
        });
        observer.observe(target, { childList: true, subtree: true });
        return () => observer.disconnect();
      }
    } else body.current?.scrollTo({ top: 0 });
  }, [hidden, revision, title, source, section, body]);
}
