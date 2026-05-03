import React from "react";

/** Top-level error boundary so a render error in one panel doesn't blank the
 *  entire app. Shows a recovery button so the user can retry without F5. */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("UI crashed:", error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            padding: 24,
            color: "var(--text-0, #1d2433)",
            background: "var(--bg-0, #f6f8fb)",
            minHeight: "100vh",
            fontFamily:
              '-apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif',
          }}
        >
          <h2 style={{ marginTop: 0 }}>Something went wrong</h2>
          <pre
            style={{
              background: "var(--bg-2, #f2f4f8)",
              padding: 12,
              borderRadius: 8,
              overflow: "auto",
              fontSize: 12,
            }}
          >
            {String(this.state.error)}
          </pre>
          <button
            onClick={this.reset}
            style={{
              padding: "8px 14px",
              borderRadius: 6,
              border: "1px solid #c8d0dc",
              background: "#3b6ee8",
              color: "white",
              cursor: "pointer",
            }}
          >
            Reload UI state
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
