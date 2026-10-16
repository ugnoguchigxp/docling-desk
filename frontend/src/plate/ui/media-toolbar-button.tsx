'use client';

import * as React from 'react';

import type { DropdownMenuProps } from '@radix-ui/react-dropdown-menu';

import { PlaceholderPlugin } from '@platejs/media/react';
import {
  AudioLinesIcon,
  FileUpIcon,
  FilmIcon,
  ImageIcon,
  LinkIcon,
  ImagesIcon,
} from 'lucide-react';
import { isUrl, KEYS } from 'platejs';
import { useEditorRef } from 'platejs/react';
import { toast } from 'sonner';
import { useFilePicker } from 'use-file-picker';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/plate/ui-primitives/alert-dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/plate/ui-primitives/dropdown-menu';
import { Input } from '@/plate/ui-primitives/input';
import { ImageLibraryDialog } from '@/editor/ImageLibraryDialog';

import {
  ToolbarSplitButton,
  ToolbarSplitButtonPrimary,
  ToolbarSplitButtonSecondary,
} from './toolbar';

const MEDIA_CONFIG: Record<
  string,
  {
    accept: string[];
    icon: React.ReactNode;
    title: string;
    tooltip: string;
  }
> = {
  [KEYS.audio]: {
    accept: ['audio/*'],
    icon: <AudioLinesIcon className="size-4" />,
    title: 'Insert Audio',
    tooltip: 'Audio',
  },
  [KEYS.file]: {
    accept: ['*'],
    icon: <FileUpIcon className="size-4" />,
    title: 'Insert File',
    tooltip: 'File',
  },
  [KEYS.img]: {
    accept: ['image/*'],
    icon: <ImageIcon className="size-4" />,
    title: 'Insert Image',
    tooltip: 'Image',
  },
  [KEYS.video]: {
    accept: ['video/*'],
    icon: <FilmIcon className="size-4" />,
    title: 'Insert Video',
    tooltip: 'Video',
  },
};

export function MediaToolbarButton({
  nodeType,
  ...props
}: DropdownMenuProps & { nodeType: string }) {
  const currentConfig = MEDIA_CONFIG[nodeType];

  const editor = useEditorRef();
  const [open, setOpen] = React.useState(false);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [libraryOpen, setLibraryOpen] = React.useState(false);
  const libraryRange = React.useRef<ReturnType<
    typeof editor.api.rangeRef
  > | null>(null);
  React.useEffect(
    () => () => {
      libraryRange.current?.unref();
    },
    [],
  );
  function openMenu(value: boolean) {
    if (value && nodeType === KEYS.img) {
      // Slate's selectionchange is deferred. Prefer the live DOM caret before
      // opening the menu, using Plate's own DOM-to-document conversion.
      const selection = window.getSelection();
      const range =
        selection?.anchorNode &&
        selection.focusNode &&
        editor.api.hasDOMNode(selection.anchorNode, { editable: true }) &&
        editor.api.hasDOMNode(selection.focusNode, { editable: true })
          ? editor.api.toSlateRange(selection, {
              exactMatch: false,
              suppressThrow: true,
            })
          : null;
      libraryRange.current?.unref();
      const at = range ?? editor.selection;
      libraryRange.current = at ? editor.api.rangeRef(at) : null;
    }
    setOpen(value);
  }

  const { openFilePicker } = useFilePicker({
    accept: currentConfig.accept,
    multiple: true,
    onFilesSelected: ({ plainFiles: updatedFiles }) => {
      editor.getTransforms(PlaceholderPlugin).insert.media(updatedFiles);
    },
  });

  return (
    <>
      <ToolbarSplitButton
        aria-label={currentConfig.tooltip}
        title={currentConfig.title}
        onClick={() => {
          openFilePicker();
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            openMenu(true);
          }
        }}
        pressed={open}
      >
        <ToolbarSplitButtonPrimary>
          {currentConfig.icon}
        </ToolbarSplitButtonPrimary>

        <DropdownMenu
          open={open}
          onOpenChange={openMenu}
          modal={false}
          {...props}
        >
          <DropdownMenuTrigger asChild>
            <ToolbarSplitButtonSecondary
              aria-label={`${currentConfig.tooltip}の挿入方法`}
            />
          </DropdownMenuTrigger>

          <DropdownMenuContent
            onClick={(e) => e.stopPropagation()}
            align="start"
            alignOffset={-32}
          >
            <DropdownMenuGroup>
              {nodeType === KEYS.img && (
                <DropdownMenuItem
                  onSelect={() => {
                    setLibraryOpen(true);
                  }}
                >
                  <ImagesIcon />
                  資料一覧から選ぶ
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={() => openFilePicker()}>
                {currentConfig.icon}
                Upload from computer
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setDialogOpen(true)}>
                <LinkIcon />
                Insert via URL
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </ToolbarSplitButton>

      <AlertDialog
        open={dialogOpen}
        onOpenChange={(value) => {
          setDialogOpen(value);
        }}
      >
        <AlertDialogContent className="gap-6">
          <MediaUrlDialogContent
            currentConfig={currentConfig}
            nodeType={nodeType}
            setOpen={setDialogOpen}
          />
        </AlertDialogContent>
      </AlertDialog>
      {libraryOpen && (
        <ImageLibraryDialog
          onClose={() => {
            const range = libraryRange.current?.unref();
            libraryRange.current = null;
            if (range) editor.tf.select(range);
            setLibraryOpen(false);
          }}
          onRestoreFocus={() => {
            // Radix restores focus after unmount. Do not steal it from a
            // toolbar menu the user has already opened in the meantime.
            if (document.activeElement === document.body) editor.tf.focus();
          }}
          onSelect={(asset) => {
            const range = libraryRange.current?.unref();
            libraryRange.current = null;
            editor.tf.insertNodes(
              {
                type: KEYS.img,
                url: asset.reference,
                alt: asset.default_alt,
                children: [{ text: '' }],
              },
              { at: range ?? editor.selection ?? undefined, select: true },
            );
            setLibraryOpen(false);
          }}
        />
      )}
    </>
  );
}

function MediaUrlDialogContent({
  currentConfig,
  nodeType,
  setOpen,
}: {
  currentConfig: (typeof MEDIA_CONFIG)[string];
  nodeType: string;
  setOpen: (value: boolean) => void;
}) {
  const editor = useEditorRef();
  const [url, setUrl] = React.useState('');

  const embedMedia = React.useCallback(() => {
    if (!isUrl(url)) return toast.error('Invalid URL');

    setOpen(false);
    editor.tf.insertNodes({
      children: [{ text: '' }],
      name: nodeType === KEYS.file ? url.split('/').pop() : undefined,
      type: nodeType,
      url,
    });
  }, [url, editor, nodeType, setOpen]);

  return (
    <>
      <AlertDialogHeader>
        <AlertDialogTitle>{currentConfig.title}</AlertDialogTitle>
      </AlertDialogHeader>

      <AlertDialogDescription className="group relative w-full">
        <label
          className="-translate-y-1/2 absolute top-1/2 block cursor-text px-1 text-muted-foreground/70 text-sm transition-all group-focus-within:pointer-events-none group-focus-within:top-0 group-focus-within:cursor-default group-focus-within:font-medium group-focus-within:text-foreground group-focus-within:text-xs has-[+input:not(:placeholder-shown)]:pointer-events-none has-[+input:not(:placeholder-shown)]:top-0 has-[+input:not(:placeholder-shown)]:cursor-default has-[+input:not(:placeholder-shown)]:font-medium has-[+input:not(:placeholder-shown)]:text-foreground has-[+input:not(:placeholder-shown)]:text-xs"
          htmlFor="url"
        >
          <span className="inline-flex bg-background px-2">URL</span>
        </label>
        <Input
          id="url"
          className="w-full"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') embedMedia();
          }}
          placeholder=""
          type="url"
          autoFocus
        />
      </AlertDialogDescription>

      <AlertDialogFooter>
        <AlertDialogCancel>Cancel</AlertDialogCancel>
        <AlertDialogAction
          onClick={(e) => {
            e.preventDefault();
            embedMedia();
          }}
        >
          Accept
        </AlertDialogAction>
      </AlertDialogFooter>
    </>
  );
}
