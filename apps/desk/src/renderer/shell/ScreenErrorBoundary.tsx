import { Component, type ErrorInfo, type ReactNode } from "react";

/** Keeps a crashing screen from taking down the shell (nav, palette, status bar). */
export class ScreenErrorBoundary extends Component<
  { screen: string; children: ReactNode },
  { error: Error | null }
> {
  override state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`screen ${this.props.screen} crashed`, error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.error === null) return this.props.children;
    return (
      <section className="state state-error" role="alert" aria-label="Screen error">
        <h2 className="state-title">This screen failed to render</h2>
        <p>
          <code>{this.state.error.message}</code>
        </p>
        <p className="dim">Other screens still work. Switch screens with Cmd/Ctrl+1..8.</p>
      </section>
    );
  }
}
