import { useEffect, useState } from "react";
import App from "./App";
import Landing from "./Landing";

/**
 * Landing page, or the studio.
 *
 * Switched on the URL hash rather than a router, because one boolean does
 * not justify a routing dependency — and the hash means the studio has a
 * real address. Someone who bookmarks `#studio`, or reloads mid-campaign,
 * lands back where they were instead of at the marketing page.
 *
 * Back and forward work for the same reason: the browser is doing the
 * navigation, we are only reading it.
 */
const STUDIO = "#studio";

export default function Site() {
  const [studio, setStudio] = useState(
    () => window.location.hash === STUDIO
  );

  useEffect(() => {
    const sync = () => setStudio(window.location.hash === STUDIO);
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  if (studio) return <App />;

  return (
    <Landing
      onStart={() => {
        window.location.hash = STUDIO;
        // The hashchange listener does the state change, so there is one
        // path into the studio rather than two that can disagree.
        window.scrollTo({ top: 0 });
      }}
    />
  );
}
