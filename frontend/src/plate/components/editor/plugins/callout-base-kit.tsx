import { BaseCalloutPlugin } from '@platejs/callout';

import { CalloutElementStatic } from '@/plate/ui/callout-node-static';

export const BaseCalloutKit = [
  BaseCalloutPlugin.withComponent(CalloutElementStatic),
];
