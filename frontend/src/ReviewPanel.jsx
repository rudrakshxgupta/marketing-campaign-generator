import { useEffect, useRef, useState } from "react";

/**
 * Edit and approve one locale's copy.
 *
 * Machine-written copy ships flagged, and this is how the flag gets cleared.
 * It matters most for Bengali, Tamil and Telugu, where Azure OCR cannot verify
 * rendered text at all -- for those scripts a human approving what they can
 * read is the only check that exists.
 *
 * Two deliberate choices:
 *
 * The English back-translation sits right under the headline, so a reviewer
 * who does not read Tamil can still tell whether the Tamil means the right
 * thing. Without it, "review" for most of our languages is just clicking
 * approve on something you cannot assess.
 *
 * And approving is free. The base image carries no text, so corrected copy is
 * re-typeset onto the image already on disk -- no generation, no quota. If
 * fixing a typo cost a rate-limited call, nobody would fix typos.
 *
 * Which is also what makes live preview possible. Because a re-render costs
 * nothing, edits are typeset onto the real creative as they are typed rather
 * than after a Save -- and that matters most for the scripts a reviewer
 * cannot read, where the only real check is seeing the words in place at the
 * size they will be published.
 */
export default function ReviewPanel({ jobId, locale, copy, onReviewed }) {
  const [headline, setHeadline] = useState(copy.headline || "");
  const [subhead, setSubhead] = useState(copy.subhead || "");
  const [cta, setCta] = useState(copy.cta || "");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState(false);
  const first = useRef(true);

  const edited =
    headline !== (copy.headline || "") ||
    subhead !== (copy.subhead || "") ||
    cta !== (copy.cta || "");

  // Re-typeset as you type, debounced.
  //
  // 450ms is chosen against the work, not by habit: a re-render is a
  // Chromium layout plus a composite, tens of milliseconds, so the delay
  // only has to outlast a normal inter-keystroke gap. Shorter queues renders
  // behind every letter; longer and the image visibly lags the field.
  useEffect(() => {
    if (first.current) {
      // Do not fire on mount: nothing has been edited yet, and re-rendering
      // identical copy would rewrite the file the user is looking at.
      first.current = false;
      return;
    }
    if (!edited) return;
    const timer = setTimeout(() => submit(false, { silent: true }), 450);
    return () => clearTimeout(timer);
  }, [headline, subhead, cta]);

  async function submit(approve, { silent = false } = {}) {
    if (silent) setPreview(true);
    else setBusy(true);
    try {
      const response = await fetch(`/api/jobs/${jobId}/copy/${locale}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ headline, subhead, cta, approve }),
      });
      if (response.ok) onReviewed?.(await response.json());
    } finally {
      if (silent) setPreview(false);
      else setBusy(false);
    }
  }

  if (!copy.needs_review && !open) {
    return (
      <div className="approved">
        ✓ approved
        <button className="link" onClick={() => setOpen(true)}>edit again</button>
      </div>
    );
  }

  return (
    <div className="review-panel">
      <div className="review-head">
        Review before publishing
        {preview && <span className="live">updating…</span>}
      </div>

      <label>Headline</label>
      <input type="text" lang={copy.bcp47} value={headline}
             onChange={(e) => setHeadline(e.target.value)} />

      {copy.back_translation && (
        <div className="backtrans">
          In English: <i>{copy.back_translation}</i>
        </div>
      )}

      <label>Subhead</label>
      <input type="text" lang={copy.bcp47} value={subhead}
             onChange={(e) => setSubhead(e.target.value)} />

      <label>Call to action</label>
      <input type="text" lang={copy.bcp47} value={cta}
             onChange={(e) => setCta(e.target.value)} />

      {/* A single button, so it spans the panel rather than sitting in a
          two-column grid with an empty cell beside it. */}
      <button className="go" style={{ marginTop: 12 }}
              onClick={() => submit(true)} disabled={busy}>
        {busy ? "Re-rendering…" : edited ? "Save & approve" : "Approve"}
      </button>
      <div className="hint" style={{ marginTop: 6 }}>
        The image above updates as you type — re-typeset onto the picture
        already generated, so <b>no image cost</b>.
      </div>
    </div>
  );
}
