import * as React from "react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { withBase } from "@/lib/base";
import { object } from "@/lib/api";
import { matchesFormat } from "@/lib/formats";
import { uploadFolder } from "@/lib/upload-folder";
import { isWikiImage } from "@/editor/raw-markdown";

export type UploadedFile = {
  url: string;
  name: string;
  size: number;
  type: string;
  key: string;
};
export const UploadActivity = React.createContext<(delta: number) => void>(
  () => {},
);

/** Only the storage adapter changes; Plate's standard media controls own insertion. */
export function useUploadFile({
  onUploadComplete,
  onUploadError,
}: {
  onUploadComplete?: (file: UploadedFile) => void;
  onUploadError?: (error: unknown) => void;
} = {}) {
  const [uploadedFile, setUploadedFile] = React.useState<UploadedFile>();
  const [uploadingFile, setUploadingFile] = React.useState<File>();
  const [progress, setProgress] = React.useState(0);
  const [isUploading, setIsUploading] = React.useState(false);
  const activity = React.useContext(UploadActivity);
  const cache = useQueryClient();
  async function uploadFile(file: File) {
    setIsUploading(true);
    setUploadingFile(file);
    activity(1);
    try {
      setUploadedFile(undefined);
      const isImage =
        file.type.startsWith("image/") || matchesFormat(file.name, "image");
      const data = new FormData();
      data.append("file", file);
      if (isImage) data.set("folder_id", await uploadFolder());
      const route = isImage ? "/api/upload" : "/api/wiki/media";
      const result = await new Promise<unknown>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", withBase(route));
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable)
            setProgress(Math.round((event.loaded / event.total) * 100));
        };
        xhr.onerror = () =>
          reject(new Error("ファイルをアップロードできませんでした。"));
        xhr.onload = () => {
          try {
            const value: unknown = JSON.parse(xhr.responseText);
            if (xhr.status < 200 || xhr.status >= 300)
              throw new Error(
                object(value) && typeof value.detail === "string"
                  ? value.detail
                  : "ファイルを保存できませんでした。",
              );
            resolve(value);
          } catch (error) {
            reject(error);
          }
        };
        xhr.send(data);
      });
      const image =
        object(result) && object(result.image) ? result.image : undefined;
      const url = isImage
        ? image?.reference
        : object(result)
          ? result.url
          : undefined;
      if (
        typeof url !== "string" ||
        (isImage &&
          (!object(result) || result.state !== "success" || !isWikiImage(url)))
      )
        throw new Error(
          "保存した画像・ファイルの参照先を取得できませんでした。",
        );
      if (isImage) await cache.invalidateQueries({ queryKey: ["library"] });
      const saved = {
        url,
        key: url,
        name: file.name,
        size: file.size,
        type:
          image && typeof image.media_type === "string"
            ? image.media_type
            : file.type,
      };
      setUploadedFile(saved);
      onUploadComplete?.(saved);
      return saved;
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "ファイルを保存できませんでした。",
      );
      onUploadError?.(error);
      return undefined;
    } finally {
      setIsUploading(false);
      setUploadingFile(undefined);
      setProgress(0);
      activity(-1);
    }
  }
  return { uploadedFile, uploadingFile, progress, isUploading, uploadFile };
}
