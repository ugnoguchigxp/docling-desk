'use client';

import {
  FootnoteDefinitionPlugin,
  FootnoteInputPlugin,
  FootnoteReferencePlugin,
} from '@platejs/footnote/react';

import {
  FootnoteDefinitionElement,
  FootnoteInputElement,
  FootnoteReferenceElement,
} from '@/plate/ui/footnote-node';

export const FootnoteKit = [
  FootnoteInputPlugin.withComponent(FootnoteInputElement),
  FootnoteReferencePlugin.withComponent(FootnoteReferenceElement),
  FootnoteDefinitionPlugin.withComponent(FootnoteDefinitionElement),
];
