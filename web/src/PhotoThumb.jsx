import { useState } from "react";
import "./PhotoThumb.css";

// A thumbnail of whatever is in the field next to it, the same size as the
// row's × button, growing on hover. It exists to answer "is that the photo I
// meant?" without opening the link -- run #208 shipped a Corvette story built
// from the wrong photos and nothing on screen said so.
//
// It doubles as a liveness check: a URL that will not load here is one the
// build cannot download either, so a blank slot is a warning before a
// seventeen-minute run rather than after it.
export default function PhotoThumb({ url, flipped = false, alt = "" }) {
  const [broken, setBroken] = useState(false);
  const clean = (url || "").trim();
  if (!clean) return <span className="photo-thumb photo-thumb-empty" aria-hidden="true" />;
  if (broken) {
    return (
      <span className="photo-thumb photo-thumb-broken"
            title="This link did not load. The build will not be able to download it either.">!</span>
    );
  }
  return (
    <span className="photo-thumb">
      <img
        src={clean}
        alt={alt}
        loading="lazy"
        onError={() => setBroken(true)}
        style={flipped ? { transform: "scaleX(-1)" } : undefined}
      />
    </span>
  );
}
