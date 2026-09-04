import { useEffect, useState } from "react";
import { RollList } from "./components/RollList";
import { RollPage } from "./components/RollPage";

/** One page per roll, chosen from the hash (`#/rolls/<key>`) so a reload keeps its place. */
function keyFromHash(): string | null {
  const m = location.hash.match(/^#\/rolls\/(.+)$/);
  return m ? decodeURIComponent(m[1]) : null;
}

export function App() {
  const [key, setKey] = useState<string | null>(keyFromHash);
  useEffect(() => {
    const on = () => setKey(keyFromHash());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  const open = (k: string | null) => {
    location.hash = k ? `#/rolls/${encodeURIComponent(k)}` : "";
  };
  return key ? <RollPage rollKey={key} onBack={() => open(null)} /> : <RollList onOpen={open} />;
}
