import { useEffect, useState } from "react";
import PhoneFrame from "./PhoneFrame";

/**
 * Campaign generator UI.
 *
 * Two things shape this screen.
 *
 * Image quota is scarce and billed per call, so the cost of an action is shown
 * *before* it is taken -- the button says how many images it will spend, and
 * economy mode says what it saves. Nothing here can spend a credit by accident.
 *
 * And generation is never synchronous: the model allows 2-12 requests per
 * minute and a render takes tens of seconds, so every campaign is a job the
 * client polls.
 */

const FORMAT_LABELS = {
  portrait: "Feed 4:5",
  grid: "Grid 3:4",
  story: "Story 9:16",
  square: "Square 1:1",
  landscape: "Landscape",
};

export default function App() {
  const [meta, setMeta] = useState(null);
  const [health, setHealth] = useState(null);
  const [usage, setUsage] = useState(null);

  const [brief, setBrief] = useState(
    "a clear glass bottle of cold-pressed coconut oil on dark walnut, warm festive bokeh behind"
  );
  const [proposition, setProposition] = useState("Purity you can taste");
  const [benefit, setBenefit] = useState("Cold-pressed, nothing added");
  const [brandName, setBrandName] = useState("ACME");
  const [occasion, setOccasion] = useState("Diwali");
  const [formats, setFormats] = useState(["portrait"]);
  const [locales, setLocales] = useState(["en", "hi", "hi-Latn", "ta"]);
  const [economy, setEconomy] = useState(false);
  const [showSafe, setShowSafe] = useState(true);

  const [referenceId, setReferenceId] = useState(null);
  const [referenceStyle, setReferenceStyle] = useState(null);
  const [referenceMode, setReferenceMode] = useState("inspiration");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);

  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/meta").then((r) => r.json()).then(setMeta).catch(() => {});
    fetch("/healthz").then((r) => r.json()).then(setHealth).catch(() => {});
    refreshUsage();
  }, []);

  const refreshUsage = () =>
    fetch("/api/usage").then((r) => r.json()).then(setUsage).catch(() => {});

  // One generation per format -- or exactly one for the whole campaign in
  // economy mode, where a tall master is cropped down to everything else.
  const imageCalls = economy && formats.length > 1 ? 1 : formats.length;
  const deliverables = formats.length * locales.length;

  // Functional update, not `list` from the enclosing render.
  //
  // Reading the array directly means three quick chip clicks all see the same
  // stale value and the last one wins, so selecting several languages in a row
  // silently keeps only the final pick. Deriving from `prev` makes each toggle
  // independent of render timing.
  const toggle = (setList, value) =>
    setList((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]
    );

  async function uploadReference(file) {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch("/api/uploads/reference", { method: "POST", body });
    if (!response.ok) return setError("Could not read that image.");
    const data = await response.json();
    setReferenceId(data.reference_id);
    setReferenceStyle(data.style);
  }

  async function uploadLogo(file) {
    const body = new FormData();
    body.append("file", file);
    await fetch("/api/uploads/logo", { method: "POST", body });
  }

  async function generate() {
    setBusy(true);
    setError(null);
    setJob(null);
    try {
      const response = await fetch("/api/campaigns", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          brief, proposition, benefit,
          brand_name: brandName,
          occasion,
          // The right festival differs by region: a Bengali audience's gifting
          // peak is Durga Puja, not Diwali.
          occasion_by_locale: occasion === "Diwali" ? { bn: "Durga Puja" } : {},
          formats, locales, economy,
          reference_id: referenceId,
          reference_mode: referenceMode,
          rights_confirmed: rightsConfirmed,
        }),
      });
      const created = await response.json();
      if (!response.ok) throw new Error(created.detail || "Request rejected");
      poll(created.job_id);
    } catch (e) {
      setError(String(e.message || e));
      setBusy(false);
    }
  }

  function poll(jobId) {
    const tick = async () => {
      const data = await fetch(`/api/jobs/${jobId}`).then((r) => r.json());
      setJob(data);
      if (["complete", "failed", "rate_limited", "budget_exceeded"].includes(data.state)) {
        setBusy(false);
        refreshUsage();
        return;
      }
      setTimeout(tick, 900);
    };
    tick();
  }

  const variantsByLocale = {};
  (job?.variants || []).forEach((v) => {
    (variantsByLocale[v.locale] ||= []).push(v);
  });

  return (
    <div className="app">
      <aside className="panel">
        <h1>
          Campaign Generator
          {health && (
            <span className={`pill ${health.mock ? "mock" : "ok"}`}>
              {health.mock ? "mock images" : "live"}
            </span>
          )}
        </h1>
        <p className="sub">
          One text-free image, every language composited on top.
        </p>

        <h2>The picture</h2>
        <textarea value={brief} onChange={(e) => setBrief(e.target.value)} />
        <div className="hint">
          Describe what the <b>photo</b> shows — not the caption. Text is never
          drawn by the model; it is typeset on top afterwards.
        </div>

        <h2>The message</h2>
        <label>Proposition</label>
        <input type="text" value={proposition} onChange={(e) => setProposition(e.target.value)} />
        <label>Benefit</label>
        <input type="text" value={benefit} onChange={(e) => setBenefit(e.target.value)} />
        <div className="row">
          <div>
            <label>Brand</label>
            <input type="text" value={brandName} onChange={(e) => setBrandName(e.target.value)} />
          </div>
          <div>
            <label>Occasion</label>
            <input type="text" value={occasion} onChange={(e) => setOccasion(e.target.value)} />
          </div>
        </div>
        {occasion === "Diwali" && locales.includes("bn") && (
          <div className="note">
            Bengali copy will be written to <b>Durga Puja</b>, not Diwali — that
            is the gifting peak for that audience. The referent changes, not
            just the words.
          </div>
        )}

        <h2>Brand logo</h2>
        <input type="file" accept="image/*"
               onChange={(e) => e.target.files[0] && uploadLogo(e.target.files[0])} />
        <div className="hint">
          Composited from your real file, never drawn by the model — a
          generated logo is a <b>wrong</b> logo.
        </div>

        <h2>Reference image <span style={{ textTransform: "none" }}>(optional)</span></h2>
        <input type="file" accept="image/*"
               onChange={(e) => e.target.files[0] && uploadReference(e.target.files[0])} />
        {referenceStyle && (
          <>
            <div className="hint" style={{ marginTop: 8 }}>
              Read locally, no model call: <b>{referenceStyle.palette.join(", ")}</b> ·{" "}
              {referenceStyle.brightness}, {referenceStyle.temperature}
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button className={`ghost ${referenceMode === "inspiration" ? "on" : ""}`}
                      onClick={() => setReferenceMode("inspiration")}>
                Use as inspiration
              </button>
              <button className={`ghost ${referenceMode === "edit" ? "on" : ""}`}
                      onClick={() => setReferenceMode("edit")}>
                Edit this image
              </button>
            </div>
            {referenceMode === "edit" && (
              <div className="note bad">
                <label style={{ color: "inherit", margin: "0 0 6px" }}>
                  <input type="checkbox" checked={rightsConfirmed}
                         onChange={(e) => setRightsConfirmed(e.target.checked)} />{" "}
                  I have the rights to this image
                </label>
                Edit mode reproduces your photo pixel-for-pixel. A real building
                or product is <b>restaged, never redesigned</b> — the result is
                measured against your original.
              </div>
            )}
          </>
        )}

        <h2>Formats</h2>
        <div className="chips">
          {(meta?.formats || []).map((f) => (
            <span key={f.key}
                  className={`chip ${formats.includes(f.key) ? "on" : ""}`}
                  onClick={() => toggle(setFormats, f.key)}>
              {FORMAT_LABELS[f.key] || f.key}
            </span>
          ))}
        </div>
        <div className="hint">Each format costs one image — unless economy mode is on.</div>

        <h2>Languages</h2>
        <div className="chips">
          {(meta?.locales || []).map((l) => (
            <span key={l.key}
                  className={`chip ${locales.includes(l.key) ? "on" : ""}`}
                  onClick={() => toggle(setLocales, l.key)}>
              {l.label}
              {l.native !== l.label && <span className="native">{l.native}</span>}
            </span>
          ))}
        </div>
        <div className="hint">
          Languages are <b>free</b> — all composited from the same image.
          Hinglish is Hindi in Latin script, and is how a lot of Indian
          Instagram copy is actually written.
        </div>

        <h2>Options</h2>
        <label style={{ color: "var(--text)" }}>
          <input type="checkbox" checked={economy} onChange={(e) => setEconomy(e.target.checked)} />{" "}
          Economy mode
        </label>
        <div className="hint">
          One tall master cropped down to every format. Saves calls, costs a
          little sharpness.
        </div>
        <label style={{ color: "var(--text)", marginTop: 10 }}>
          <input type="checkbox" checked={showSafe} onChange={(e) => setShowSafe(e.target.checked)} />{" "}
          Show Instagram safe zones
        </label>

        <div className="cost">
          <span>
            <b>{imageCalls}</b> image{imageCalls === 1 ? "" : "s"} →{" "}
            {deliverables} deliverable{deliverables === 1 ? "" : "s"}
          </span>
          {usage && !usage.mock && (
            <span style={{ color: "var(--muted)" }}>
              {usage.today.remaining} left today
            </span>
          )}
        </div>

        <button onClick={generate} disabled={busy || !formats.length || !locales.length}>
          {busy ? "Generating…" : `Generate — ${imageCalls} image${imageCalls === 1 ? "" : "s"}`}
        </button>

        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}

        {job && (
          <>
            <div className="bar"><i style={{ width: `${job.progress || 0}%` }} /></div>
            <div className="log">
              state: {job.state}
              {job.image_calls != null && `\nimage calls: ${job.image_calls}`}
              {job.calls_saved > 0 && `  (saved ${job.calls_saved})`}
              {job.fidelity && `\nsubject: ${job.fidelity.summary}`}
              {(job.logs || []).map((l) => `\n${l}`)}
              {job.error && `\n${job.error}`}
            </div>
          </>
        )}
      </aside>

      <main className="stage">
        {!job?.variants?.length ? (
          <div className="empty">
            {busy ? "Working…" : "Your campaign will appear here, framed as Instagram will show it."}
          </div>
        ) : (
          <div className="frames">
            {Object.entries(variantsByLocale).map(([locale, list]) =>
              list.map((v) => {
                const copy = job.copy?.[locale];
                const localeMeta = (meta?.locales || []).find((l) => l.key === locale);
                return (
                  <div key={`${locale}-${v.format}`}>
                    <div className="frame-head">
                      <span className="who">{localeMeta?.label || locale}</span>
                      <span className="what">
                        {FORMAT_LABELS[v.format] || v.format} · {v.headline_px}px
                      </span>
                    </div>
                    <PhoneFrame
                      src={`/api/jobs/${job.job_id}/image/${v.file}`}
                      format={v.format}
                      showSafe={showSafe}
                    />
                    {copy && (
                      <div className="caption" lang={localeMeta?.bcp47}>
                        {copy.caption}
                        <div className="tags">{(copy.hashtags || []).join(" ")}</div>
                      </div>
                    )}
                    {v.needs_review && (
                      <div className="review">
                        {v.review_reasons.join(" · ")}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        )}
      </main>
    </div>
  );
}
