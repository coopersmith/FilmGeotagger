import React, { Component, type ReactNode } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import "maplibre-gl/dist/maplibre-gl.css";
import "./styles.css";

/** A render error shows itself instead of a black page; the likeliest cause is named. */
class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="crash">
        <h1 className="brand">filmgeo</h1>
        <p>The page hit an error it could not recover from.</p>
        <pre className="mono">{String(this.state.error.message || this.state.error)}</pre>
        <p className="muted">
          If you rebuilt the UI after the server started, the server is older than the page: stop `filmgeo serve` (Ctrl-C) and run it again, then reload.
        </p>
        <button className="btn" onClick={() => location.reload()}>
          reload
        </button>
      </main>
    );
  }
}

const client = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <Boundary>
        <App />
      </Boundary>
    </QueryClientProvider>
  </React.StrictMode>,
);
