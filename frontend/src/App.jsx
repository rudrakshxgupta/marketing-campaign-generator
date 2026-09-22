import { useEffect, useRef, useState } from "react";
import PhoneFrame from "./PhoneFrame";
import ReviewPanel from "./ReviewPanel";

/**
 * Campaign generator UI.
 *
 * Three things shape this screen.
 *
 * Image quota is scarce and billed per call, so the cost of an action is
 * shown *before* it is taken and the action bar is pinned rather than
 * scrolled past. Nothing here can spend a credit by accident.
 *
 * Generation is never synchronous -- the model allows 2-15 requests per
 * minute and a render takes tens of seconds -- so every campaign is a job the
 * client polls, and the waiting state has to be worth looking at.
 *
 * And the reference-image mode is the most consequential control in the form,
 * so it is the largest. As a pair of small toggles it was routinely missed,
 * and the default quietly regenerated a photograph of a real building into an
 * invented one -- an advertisement for a property that does not exist.
 */

// Mirrors RUNNING_STATES in backend/app/main.py.
const RUNNING_STATES = ["queued", "compiling", "generating", "compositing"];

const PROGRESS_LABEL = {
  queued: "Queued",
  compiling: "Writing the brief",
  generating: "Generating the image",
  compositing: "Typesetting every language",
};

/** What went wrong, in words the person reading it can act on. */
const FAILURE = {
  blocked: {
    title: "The image service refused the prompt",
    body:
      "A content filter rejected the wording of the generated brief before " +
      "anything was drawn. No image was billed. It is usually one phrase — " +
      "try describing the product more plainly, or give the brand name and " +
      "occasion yourself so the AI invents less.",
  },
  budget_exceeded: {
    title: "Stopped before spending",
    body:
      "The image budget is used up. Nothing is broken and nothing was " +
      "billed — raise the limit, or come back tomorrow.",
  },
  rate_limited: {
    title: "The model is busy",
    body: "The deployment hit its requests-per-minute ceiling. Try again shortly.",
  },
  failed: {
    title: "The campaign did not finish",
    body: "The details are in the log below.",
  },
};

const FORMAT_LABELS = {
  portrait: "Feed 4:5",
  grid: "Grid 3:4",
  story: "Story 9:16",
  square: "Square 1:1",
  landscape: "Landscape",
};

/** A file picker that shows you what it accepted. */
function Drop({ label, hint, file, onPick, onClear, accept = "image/*" }) {
  const input = useRef(null);
  return (
    <div className={`drop ${file ? "filled" : ""}`}
         role="group" aria-label={label}>
      <div className="drop-thumb"
           style={file ? { backgroundImage: `url(${file.url})` } : undefined}
           aria-hidden="true" />
      <button type="button" className="drop-pick"
              onClick={() => input.current?.click()}>
        <strong>{file ? file.name : label}</strong>
        <span>{file ? "Click to replace" : hint}</span>
      </button>
      {file && (
        <button type="button" className="drop-clear" onClick={onClear}
                aria-label={`Remove ${label}`}>×</button>
      )}
      <input ref={input} type="file" accept={accept} hidden
             onChange={(e) => {
               const picked = e.target.files?.[0];
               if (picked) onPick(picked);
               // Reset so picking the same file twice still fires a change.
               e.target.value = "";
             }} />
    </div>
  );
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const [health, setHealth] = useState(null);
  const [usage, setUsage] = useState(null);

  const [product, setProduct] = useState("");
  const [factsText, setFactsText] = useState("");
  const [brandName, setBrandName] = useState("");
  const [occasion, setOccasion] = useState("");
  const [formats, setFormats] = useState(["portrait"]);
  const [locales, setLocales] = useState(["en", "hi", "hi-Latn", "ta"]);
  const [economy, setEconomy] = useState(false);
  const [showSafe, setShowSafe] = useState(true);

  const [logoFile, setLogoFile] = useState(null);
  const [referenceFile, setReferenceFile] = useState(null);
  const [referenceId, setReferenceId] = useState(null);
  const [referenceStyle, setReferenceStyle] = useState(null);
  const [referenceMode, setReferenceMode] = useState("inspiration");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [subject, setSubject] = useState(null);

  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/meta").then((r) => r.json()).then(setMeta).catch(() => {});
    fetch("/healthz").then((r) => r.json()).then(setHealth).catch(() => {});
    refreshUsage();
  }, []);

  // Classify as you type. Local keyword matching on the server, no model and
  // no cost, so it can run on a keystroke -- and it is what lets the warning
  // about regenerating a real building appear before an image is spent.
  useEffect(() => {
    if (!product.trim()) return setSubject(null);
    const id = setTimeout(() => {
      fetch(`/api/classify?product=${encodeURIComponent(product)}`)
        .then((r) => r.json()).then(setSubject).catch(() => {});
    }, 300);
    return () => clearTimeout(id);
  }, [product]);

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
    setReferenceFile({ name: file.name, url: URL.createObjectURL(file) });
  }

  function clearReference() {
    setReferenceId(null);
    setReferenceStyle(null);
    setReferenceFile(null);
    setReferenceMode("inspiration");
    setRightsConfirmed(false);
  }

  async function uploadLogo(file) {
    const body = new FormData();
    body.append("file", file);
    const response = await fetch("/api/uploads/logo", { method: "POST", body });
    if (!response.ok) return setError("Could not read that logo.");
    const data = await response.json();
    // Keep the id. Without it the backend fell back to "whichever logo file
    // sorts first on disk", which is not necessarily the one just uploaded.
    setLogoFile({ id: data.logo_id, name: file.name, url: URL.createObjectURL(file) });
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
          logo_id: logoFile?.id || null,
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
      // Stop on anything that is not still running. Listing the endings
      // instead would mean a new backend state spins forever.
      if (!RUNNING_STATES.includes(data.state)) {
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

  const failure = job && !RUNNING_STATES.includes(job.state) ? FAILURE[job.state] : null;
  const ready = product.trim().length > 1 && formats.length > 0 && locales.length > 0;
  const overBudget = usage && !usage.mock && usage.today.remaining < imageCalls;
  // Inspiration regenerates from scratch. For a real building, a real face or
  // a specific piece of jewellery that means advertising something that does
  // not exist, so the choice has to be put in front of the user.
  const shouldUseExact =
    subject?.prefer_exact && referenceId && referenceMode === "inspiration";

  const variantsByLocale = {};
  (job?.variants || []).forEach((v) => {
    (variantsByLocale[v.locale] ||= []).push(v);
  });

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <div className="mark" aria-hidden="true">C</div>
          <h1>Campaign Generator</h1>
          {health && (
            <span className={`pill ${health.mock ? "mock" : "ok"}`}>
              {health.mock ? "mock" : health.image_model || "live"}
            </span>
          )}
        </header>

        <div className="panel-scroll">
          {/* 1 ------------------------------------------------------ */}
          <section className="step">
            <h2>What do you sell?</h2>
            <p className="lede">
              The only thing you have to write. Everything else — the
              photograph, the message, the occasion, the copy in every
              language — is worked out from it.
            </p>
            <textarea value={product} onChange={(e) => setProduct(e.target.value)}
                      placeholder="e.g. handmade silver jhumka earrings"
                      aria-describedby="product-hint" />
            {subject?.kind && subject.kind !== "generic" && (
              <div className="hint" id="product-hint">
                Read as <b>{subject.kind}</b>
                {subject.prefer_exact
                  ? ", so an uploaded photo of it will be kept rather than reinvented."
                  : "."}
              </div>
            )}

            <div className="row" style={{ marginTop: 16 }}>
              <div>
                <label htmlFor="brand">Brand name</label>
                <input id="brand" type="text" value={brandName}
                       placeholder="optional"
                       onChange={(e) => setBrandName(e.target.value)} />
              </div>
              <div>
                <label htmlFor="occasion">Occasion</label>
                <input id="occasion" type="text" value={occasion}
                       placeholder="AI decides"
                       onChange={(e) => setOccasion(e.target.value)} />
              </div>
            </div>

            <label htmlFor="facts" style={{ marginTop: 16 }}>
              Offers &amp; facts <span style={{ color: "var(--text-3)" }}>— one per line</span>
            </label>
            <textarea id="facts" value={factsText} style={{ minHeight: 56 }}
                      onChange={(e) => setFactsText(e.target.value)}
                      placeholder={"30% off\nfree delivery over 999"} />
            <div className="hint">
              The <b>only</b> claims the copy may make. A model that helpfully
              adds “50% off” has written a false advertisement.
            </div>
          </section>

          {/* 2 ------------------------------------------------------ */}
          <section className="step">
            <h2>Your assets <span className="opt">optional</span></h2>
            <p className="lede">
              A logo is composited from your real file. A reference photo can
              be kept exactly, or used only for its colours.
            </p>

            <Drop label="Brand logo" hint="PNG with transparency works best"
                  file={logoFile} onPick={uploadLogo}
                  onClear={() => setLogoFile(null)} />
            <div className="hint">
              Composited from your real file, never drawn by the model — a
              generated logo is a <b>wrong</b> logo. Placed inside the safe
              zone, sized to the mark itself, with a scrim if the background
              behind it would swallow it.
            </div>

            <div style={{ marginTop: 16 }}>
              <Drop label="Reference photo" hint="A photo of the actual thing"
                    file={referenceFile} onPick={uploadReference}
                    onClear={clearReference} />
            </div>

            {referenceStyle && (
              <>
                <div className="hint">
                  Read locally, no model call:{" "}
                  <b>{referenceStyle.palette.join(", ")}</b>, {referenceStyle.brightness} and{" "}
                  {referenceStyle.temperature}
                </div>

                <div className="modes" role="group" aria-label="How to use the reference photo">
                  <button type="button" className="mode"
                          aria-pressed={referenceMode === "edit"}
                          onClick={() => setReferenceMode("edit")}>
                    <span className="dot" aria-hidden="true" />
                    <span>
                      <strong>
                        Use this exact image
                        {subject?.prefer_exact && <span className="rec">RECOMMENDED</span>}
                      </strong>
                      <span className="body">
                        Keeps your subject pixel-accurate and restages what is
                        around it — sky, background, light, season. A building
                        keeps its floors, windows and roofline; the result is
                        measured against your original and flagged if it drifts.
                      </span>
                    </span>
                  </button>

                  <button type="button" className="mode"
                          aria-pressed={referenceMode === "inspiration"}
                          onClick={() => setReferenceMode("inspiration")}>
                    <span className="dot" aria-hidden="true" />
                    <span>
                      <strong>Use as inspiration only</strong>
                      <span className="body">
                        Borrows the palette and lighting, then generates a new
                        picture. The subject will <b>not</b> be yours.
                      </span>
                    </span>
                  </button>
                </div>

                {shouldUseExact && (
                  <div className="note warn">
                    This reads as <b>{subject.kind}</b>. On inspiration mode the
                    model invents a new one, so the advertisement would show a{" "}
                    {subject.kind === "architecture" ? "property" : "subject"}{" "}
                    that does not exist. Pick <b>Use this exact image</b> unless
                    you only want the mood.
                  </div>
                )}

                {referenceMode === "edit" && (
                  <div className="note bad">
                    <label className="check">
                      <input type="checkbox" checked={rightsConfirmed}
                             onChange={(e) => setRightsConfirmed(e.target.checked)} />
                      <span>I have the rights to this image</span>
                    </label>
                    <div style={{ marginTop: 6 }}>
                      This reproduces your photograph. It is restaged, never
                      redesigned.
                    </div>
                  </div>
                )}
              </>
            )}
          </section>

          {/* 3 ------------------------------------------------------ */}
          <section className="step">
            <h2>Output</h2>

            <label>Formats</label>
            <div className="chips">
              {(meta?.formats || []).map((f) => (
                <button key={f.key} type="button" className="chip"
                        aria-pressed={formats.includes(f.key)}
                        onClick={() => toggle(setFormats, f.key)}>
                  {FORMAT_LABELS[f.key] || f.key}
                </button>
              ))}
            </div>
            <div className="hint">
              Each format costs one image — unless economy mode is on.
              {meta?.backend && (
                <>
                  {" "}On <b>{meta.backend.name}</b>
                  {(meta.formats || []).every((f) => f.upscale <= 1.05)
                    ? ", every format renders at native size."
                    : ", smaller formats are upscaled to fit Instagram."}
                </>
              )}
            </div>

            <label style={{ marginTop: 18 }}>Languages</label>
            <div className="langs">
              {(meta?.locales || []).map((l) => (
                <button key={l.key} type="button" className="lang"
                        aria-pressed={locales.includes(l.key)}
                        onClick={() => toggle(setLocales, l.key)}>
                  {/* The script is the label. Setting lang here is not
                      cosmetic: it selects the right shaping and locale forms
                      for the browser, which is the same correctness the
                      renderer depends on. */}
                  <span className="native" lang={l.bcp47}>{l.native}</span>
                  <span className="name">{l.label}</span>
                </button>
              ))}
            </div>
            <div className="hint">
              Languages are <b>free</b> — all composited from the same image.
              Copy is authored in each one rather than translated, so the
              festival changes and not just the words.
            </div>

            <label className="check" style={{ marginTop: 18 }}>
              <input type="checkbox" checked={economy}
                     onChange={(e) => setEconomy(e.target.checked)} />
              <span>Economy mode</span>
            </label>
            <div className="hint">
              One tall master cropped down to every format. Saves calls, costs
              a little composition control.
            </div>
          </section>
        </div>

        <div className="action">
          {error && <div className="note bad" style={{ marginTop: 0 }}>{error}</div>}
          <div className="cost">
            <span>
              <b>{imageCalls}</b> image{imageCalls === 1 ? "" : "s"} →{" "}
              <b>{deliverables}</b> deliverable{deliverables === 1 ? "" : "s"}
            </span>
            {usage && !usage.mock && (
              <span className={usage.today.remaining <= 1 ? "low" : ""}>
                {usage.today.remaining} left today
              </span>
            )}
          </div>
          <button className="go" onClick={generate}
                  disabled={busy || !ready || overBudget}>
            {busy
              ? "Generating…"
              : overBudget
              ? "Not enough budget today"
              : !ready
              ? "Describe what you sell"
              : `Generate — ${imageCalls} image${imageCalls === 1 ? "" : "s"}`}
          </button>
        </div>
      </aside>

      <main className="stage">
        <div className="stage-bar">
          {job?.brief_summary?.category && (
            <span className="pill">{job.brief_summary.category}</span>
          )}
          {job?.image_calls != null && (
            <span className="pill">
              {job.image_calls} image call{job.image_calls === 1 ? "" : "s"}
              {job.calls_saved > 0 && `, ${job.calls_saved} saved`}
            </span>
          )}
          {job?.fidelity && (
            <span className={`pill ${job.fidelity.passed ? "ok" : "mock"}`}>
              subject {job.fidelity.passed ? "preserved" : "drifted"}
            </span>
          )}
          <span className="spacer" />
          {job?.variants?.length > 0 && (
            <>
              <label className="toggle">
                <input type="checkbox" checked={showSafe}
                       onChange={(e) => setShowSafe(e.target.checked)} />
                Safe zones
              </label>
              {job.bundle && (
                <a className="ghost" href={job.bundle} download
                   style={{ textDecoration: "none" }}>Download all</a>
              )}
            </>
          )}
        </div>

        {busy ? (
          <div className="working">
            <div className="bar"><i style={{ width: `${job?.progress || 5}%` }} /></div>
            <p>{PROGRESS_LABEL[job?.state] || "Starting"}…</p>
            <div className="step-log">{(job?.logs || []).slice(-1)[0]}</div>
          </div>
        ) : failure ? (
          /* A run that failed used to render the same placeholder as a page
             nobody had touched yet, with the reason buried in a log box. The
             stage is where you are looking, so the stage has to say it. */
          <div className="failed-card">
            <h3>{failure.title}</h3>
            <p>{failure.body}</p>
            {job.error && <pre className="why">{job.error}</pre>}
            {job.prompt && (
              <details>
                <summary>The prompt that was sent</summary>
                <pre className="why">{job.prompt}</pre>
              </details>
            )}
          </div>
        ) : !job?.variants?.length ? (
          <div className="empty">
            Describe what you sell, and your campaign appears here — framed
            exactly as Instagram will show it.
          </div>
        ) : (
          <>
            {job.brief_summary && (
              <div className="brief">
                <h3>What the AI decided</h3>
                <dl>
                  <dt>Photograph</dt>
                  <dd>{job.brief_summary.subject}</dd>
                  <dt>Message</dt>
                  <dd>{job.brief_summary.proposition}</dd>
                  {job.brief_summary.occasion && (
                    <>
                      <dt>Occasion</dt>
                      <dd>{job.brief_summary.occasion}</dd>
                    </>
                  )}
                  {Object.keys(job.brief_summary.occasion_by_locale || {}).length > 0 && (
                    <>
                      <dt>Per region</dt>
                      <dd>
                        {Object.entries(job.brief_summary.occasion_by_locale)
                          .map(([l, o]) => `${l} ${o}`).join(", ")}
                      </dd>
                    </>
                  )}
                  <dt>Reserved space</dt>
                  <dd>{job.brief_summary.reserved_space}</dd>
                </dl>
                {(job.notes || []).map((note, i) => (
                  <div className="note warn" key={i}>{note}</div>
                ))}
              </div>
            )}

            <div className="frames">
              {Object.entries(variantsByLocale).map(([locale, list]) =>
                list.map((v) => {
                  const copy = job.copy?.[locale];
                  const localeMeta = (meta?.locales || []).find((l) => l.key === locale);
                  const flags = (v.review_reasons || [])
                    .filter((r) => !r.includes("not been reviewed"));
                  return (
                    <article className="card" key={`${locale}-${v.format}`}>
                      <div className="card-head">
                        <span className="who">{localeMeta?.label || locale}</span>
                        <span className="what">
                          {FORMAT_LABELS[v.format] || v.format}, {v.headline_px}px
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
                        <ReviewPanel jobId={job.job_id} locale={locale}
                                     copy={copy} onReviewed={refreshJob} />
                      )}
                      {flags.length > 0 && <div className="flag">{flags.join(". ")}</div>}
                    </article>
                  );
                })
              )}
            </div>

            {job.logs?.length > 0 && (
              <details style={{ marginTop: 24 }}>
                <summary style={{ cursor: "pointer", fontSize: 11.5,
                                  color: "var(--text-3)" }}>
                  Run log
                </summary>
                <div className="log">{job.logs.join("\n")}</div>
              </details>
            )}
          </>
        )}
      </main>
    </div>
  );
}
