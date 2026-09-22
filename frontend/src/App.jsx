import { useEffect, useState } from "react";
import PhoneFrame from "./PhoneFrame";
import ReviewPanel from "./ReviewPanel";

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

  const [product, setProduct] = useState("cold-pressed coconut oil, 500ml glass bottle");
  const [factsText, setFactsText] = useState("");
  const [brandName, setBrandName] = useState("ACME");
  const [occasion, setOccasion] = useState("");
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
          product,
          brand_name: brandName,
          // Blank means "you decide" -- the compiler picks an occasion that
          // suits the product and season, including per-region festivals.
          occasion,
          // The only claims the copy may make. Anything not listed here is
          // never invented.
          facts: factsText.split(/\r?\n/).map((f) => f.trim()).filter(Boolean),
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

  async function refreshJob() {
    if (!job?.job_id) return;
    const data = await fetch(`/api/jobs/${job.job_id}`).then((r) => r.json());
    // Bust the browser cache: the file name is unchanged but the pixels are not.
    setJob({ ...data, _v: Date.now() });
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
              {health.mock ? "mock images" : health.image_model || "live"}
            </span>
          )}
        </h1>
        <p className="sub">
          One text-free image, every language composited on top.
        </p>

        <h2>What do you sell?</h2>
        <textarea value={product} onChange={(e) => setProduct(e.target.value)}
                  placeholder="e.g. handmade silver jhumka earrings" />
        <div className="hint">
          That is the only thing you have to write. The AI works out the
          photograph, the message, the occasion and the copy in every language.
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <div>
            <label>Brand name</label>
            <input type="text" value={brandName} onChange={(e) => setBrandName(e.target.value)} />
          </div>
          <div>
            <label>Occasion <span style={{ opacity: 0.6 }}>(optional)</span></label>
            <input type="text" value={occasion} placeholder="AI decides"
                   onChange={(e) => setOccasion(e.target.value)} />
          </div>
        </div>

        <label style={{ marginTop: 12 }}>Offers &amp; facts <span style={{ opacity: 0.6 }}>(one per line, optional)</span></label>
        <textarea value={factsText} onChange={(e) => setFactsText(e.target.value)}
                  placeholder={"30% off\nfree delivery over 999"}
                  style={{ minHeight: 52 }} />
        <div className="hint">
          The <b>only</b> claims the copy may make. Anything not listed here is
          never invented — a model that helpfully adds "50% off" has written a
          false advertisement.
        </div>

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
        <div className="hint">
          Each format costs one image — unless economy mode is on.
          {meta?.backend && (
            <>
              {" "}Generating on <b>{meta.backend.name}</b>
              {(meta.formats || []).every((f) => f.upscale <= 1.05)
                ? " — every format renders at native size, no upscaling."
                : " — smaller formats are upscaled to fit Instagram."}
            </>
          )}
        </div>

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
            <span style={{ color: usage.today.remaining <= 1 ? "var(--bad)" : "var(--muted)" }}>
              {usage.today.remaining}/{usage.today.limit} left today
              {usage.cache?.calls_saved > 0 && ` · ${usage.cache.calls_saved} cached`}
            </span>
          )}
        </div>

        {usage && !usage.mock && usage.today.remaining < imageCalls && (
          <div className="note bad">
            This needs {imageCalls} image{imageCalls === 1 ? "" : "s"} but only{" "}
            {usage.today.remaining} remain today. Raise <b>MAI_DAILY_LIMIT</b>, drop a
            format, or turn on economy mode.
          </div>
        )}

        <button
          onClick={generate}
          disabled={
            busy || !formats.length || !locales.length ||
            (usage && !usage.mock && usage.today.remaining < imageCalls)
          }>
          {busy ? "Generating…" : `Generate — ${imageCalls} image${imageCalls === 1 ? "" : "s"}`}
        </button>

        {error && <div className="note bad" style={{ marginTop: 12 }}>{error}</div>}

        {job && (
          <>
            <div className="bar"><i style={{ width: `${job.progress || 0}%` }} /></div>
            {job.brief_summary && (
              <div className="brief-card">
                <div className="brief-head">What the AI decided</div>
                <b>Category</b> {job.brief_summary.category}
                <b>Photograph</b> {job.brief_summary.subject}
                <b>Message</b> {job.brief_summary.proposition}
                {job.brief_summary.occasion && (
                  <><b>Occasion</b> {job.brief_summary.occasion}</>
                )}
                {Object.keys(job.brief_summary.occasion_by_locale || {}).length > 0 && (
                  <>
                    <b>Per region</b>
                    <span>
                      {Object.entries(job.brief_summary.occasion_by_locale)
                        .map(([l, o]) => `${l}: ${o}`).join(" · ")}
                    </span>
                  </>
                )}
                <b>Reserved for text</b> {job.brief_summary.reserved_space}
              </div>
            )}
            {job.review_count > 0 && (
              <div className="note">
                <b>{job.review_count}</b> locale{job.review_count === 1 ? "" : "s"} awaiting
                review. Approving re-renders from the image already generated — no
                image cost.
              </div>
            )}
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
                      src={`/api/jobs/${job.job_id}/image/${v.file}${job._v ? `?v=${job._v}` : ""}`}
                      format={v.format}
                      showSafe={showSafe}
                    />
                    {copy && (
                      <div className="caption" lang={localeMeta?.bcp47}>
                        {copy.caption}
                        <div className="tags">{(copy.hashtags || []).join(" ")}</div>
                      </div>
                    )}
                    {copy && (
                      <ReviewPanel
                        jobId={job.job_id}
                        locale={locale}
                        copy={copy}
                        onReviewed={refreshJob}
                      />
                    )}
                    {v.needs_review &&
                      v.review_reasons.filter((r) => !r.includes("not been reviewed"))
                        .length > 0 && (
                        <div className="review">
                          {v.review_reasons
                            .filter((r) => !r.includes("not been reviewed"))
                            .join(" · ")}
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
