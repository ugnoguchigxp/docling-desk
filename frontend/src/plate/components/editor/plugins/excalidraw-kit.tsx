'use client';

import { ExcalidrawPlugin } from '@platejs/excalidraw/react';

import { ExcalidrawElement } from '@/plate/ui/excalidraw-node';

export const ExcalidrawKit = [
  ExcalidrawPlugin.withComponent(ExcalidrawElement),
];
