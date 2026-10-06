import { Component, type ErrorInfo, type ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { reportError } from '../lib/report';

interface Props {
  children: ReactNode;
  resetKey: string;
}

interface State {
  error: Error | null;
  retried: boolean;
}

// Without a boundary, an error while drawing a page unmounts the whole app and leaves a blank
// screen until a reload. With one, the page redraws itself once (most such errors are a passing
// race), and if it fails again it says so with a Reload button. Every error is reported.
class Boundary extends Component<Props, State> {
  state: State = { error: null, retried: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    reportError(error, info.componentStack ?? undefined);
    if (!this.state.retried) setTimeout(() => this.setState({ error: null, retried: true }), 50);
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && (this.state.error || this.state.retried)) {
      this.setState({ error: null, retried: false });
    }
  }

  render() {
    const { error, retried } = this.state;
    if (!error) return this.props.children;
    if (!retried) return null;
    return (
      <div className="empty">
        <h1>This page hit an error</h1>
        <p className="error-text">{error.message}</p>
        <p>
          <button type="button" className="btn btn-primary" onClick={() => window.location.reload()}>Reload page</button>
        </p>
      </div>
    );
  }
}

/** Resets when the route changes, so moving to another page always gets a fresh start. */
export function ErrorBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return <Boundary resetKey={pathname}>{children}</Boundary>;
}
