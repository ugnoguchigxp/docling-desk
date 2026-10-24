import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Last line of defence for a whole entry point (main, embed, editor). A render
 * exception that no `FeatureBoundary` caught would otherwise unmount the root
 * and leave a white page. It only reports and offers a reload; features keep
 * using `FeatureBoundary` for recoverable, local failures.
 */
export class AppErrorBoundary extends Component<
  { children: ReactNode; reload?: () => void },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error(
      "画面の描画中にエラーが発生しました",
      error,
      info.componentStack,
    );
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main
        role="alert"
        style={{ padding: "2rem", maxWidth: "40rem", margin: "0 auto" }}
      >
        <h1>画面を表示できませんでした</h1>
        <p>
          予期しないエラーが発生しました。再読み込みしても解決しない場合は、
          管理者に連絡してください。保存前の入力は失われる可能性があります。
        </p>
        <button onClick={this.props.reload ?? (() => location.reload())}>
          再読み込み
        </button>
      </main>
    );
  }
}
