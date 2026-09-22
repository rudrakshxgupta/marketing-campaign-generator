/**
 * An Instagram post rendered inside a phone frame, with the platform's own
 * safe zones drawn over it.
 *
 * This is the part of the UI that earns its keep. A creative looks fine in a
 * file browser and then loses its call-to-action under the Reels caption tray,
 * and no amount of describing that in a tooltip lands the way seeing it does.
 *
 * The margins are Meta's unified 9:16 spec from March 2026 -- top 14%, bottom
 * 35% for Reels, 6% each side -- and are drawn to scale, so what looks clear
 * here is genuinely clear on a phone.
 */

// Matches backend/app/imaging/safezones.py. Kept in sync deliberately: these
// numbers are a platform fact, not a styling choice.
const INSETS = {
  story: { top: 0.14, right: 0.06, bottom: 0.35, left: 0.06 },
  feed: { top: 0.06, right: 0.06, bottom: 0.08, left: 0.06 },
};

const LABELS = {
  story: "Reels safe zone — bottom 35% is caption tray + controls",
  feed: "Feed safe zone",
};

export default function PhoneFrame({ src, format, showSafe }) {
  const isStory = format === "story";
  const inset = isStory ? INSETS.story : INSETS.feed;
  const pct = (n) => `${(n * 100).toFixed(1)}%`;

  return (
    <div className="phone">
      <div className="screen">
        {src ? (
          <img src={src} alt="" />
        ) : (
          <div style={{ aspectRatio: isStory ? "9/16" : "4/5", background: "#15171d" }} />
        )}

        {showSafe && src && (
          <>
            {/* Hatched areas are where the platform's own UI sits. Anything
                placed there is not "slightly cropped" -- it is invisible. */}
            <div className="unsafe" style={{ top: 0, height: pct(inset.top) }} />
            <div className="unsafe" style={{ bottom: 0, height: pct(inset.bottom) }} />
            <div
              className="safe"
              style={{
                top: pct(inset.top),
                bottom: pct(inset.bottom),
                left: pct(inset.left),
                right: pct(inset.right),
                inset: "unset",
                position: "absolute",
              }}
            />
            <div className="safe-label" style={{ top: 4, left: 6 }}>
              {LABELS[isStory ? "story" : "feed"]}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
