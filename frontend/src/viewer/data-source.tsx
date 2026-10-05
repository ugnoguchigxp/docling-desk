import { createContext, useContext } from "react";
import { withBase } from "../lib/base";

/** One provider per isolated viewer; never changes application-wide fetch. */
export interface ViewerDataSource {
  url(path: string): string;
}
export const localViewerSource: ViewerDataSource = { url: withBase };
export const ViewerSource = createContext(localViewerSource);
export const useViewerSource = () => useContext(ViewerSource);

export function sessionViewerSource(base: string): ViewerDataSource {
  return {
    url(path) {
      if (path.startsWith("/files/"))
        return base + "file/" + path.split("/").slice(3).join("/");
      if (path.startsWith("/view/"))
        return base + "view/" + path.split("/").slice(3).join("/");
      const match = /^\/api\/jobs\/[^/]+\/(.*)$/.exec(path);
      if (match) return base + "api/" + match[1];
      throw new Error("閲覧専用の資料取得先ではありません。");
    },
  };
}
