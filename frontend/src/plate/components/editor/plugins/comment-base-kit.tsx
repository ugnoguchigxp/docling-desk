import { BaseCommentPlugin } from '@platejs/comment';

import { CommentLeafStatic } from '@/plate/ui/comment-node-static';

export const BaseCommentKit = [
  BaseCommentPlugin.withComponent(CommentLeafStatic),
];
