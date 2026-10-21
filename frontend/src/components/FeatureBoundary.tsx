import {
  Component,
  lazy,
  type ComponentProps,
  type ComponentType,
  type LazyExoticComponent,
  type ReactNode,
} from "react";

type Retry = () => void;

/**
 * A failed optional feature must not remove the surrounding screen. `fallback`
 * may be a function that receives `retry`: it clears the failure (and calls
 * `onRetry`, e.g. `lazyFeature(...).reset`) so only this feature mounts again.
 */
export class FeatureBoundary extends Component<
  {
    children: ReactNode;
    fallback: ReactNode | ((retry: Retry) => ReactNode);
    onRetry?: () => void;
  },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  retry: Retry = () => {
    this.props.onRetry?.();
    this.setState({ failed: false });
  };
  render() {
    if (!this.state.failed) return this.props.children;
    const { fallback } = this.props;
    return typeof fallback === "function" ? fallback(this.retry) : fallback;
  }
}

/**
 * `lazy()` remembers a rejected import for good, so a feature loaded with it
 * can never be retried. This keeps the same component identity for callers but
 * replaces the inner `lazy()` on `reset()`, which a `FeatureBoundary` calls from
 * its retry. The module is requested again only after a failure.
 */
export function lazyFeature<
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  C extends ComponentType<any>,
>(load: () => Promise<{ default: C }>) {
  const current: { Inner: LazyExoticComponent<C> } = { Inner: lazy(load) };
  function Feature(props: ComponentProps<C>) {
    const { Inner } = current;
    return <Inner {...props} />;
  }
  return Object.assign(Feature, {
    reset() {
      current.Inner = lazy(load);
    },
  });
}
