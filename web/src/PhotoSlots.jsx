import "./PhotoSlots.css";
import { SLOTS, MAX_CLOSEUPS, closeupsFor } from "./photoSections";

// The five slots the pipeline has always had, each with its one main photo
// and up to four close-ups nested under it. The main photo is what a scene
// is about; the close-ups ride along underneath it on screen for that whole
// chapter, so they need a link and (optionally) a name, nothing more.
export default function PhotoSlots({ photoUrls, onUrlsChange, closeups, onCloseupsChange, disabled }) {
  const addCloseup = (slot) => onCloseupsChange([...closeups, {
    id: `closeup-${Date.now()}-${closeups.length}`, slot, label: "", url: "", note: "",
  }]);
  const update = (id, fields) => onCloseupsChange(closeups.map(p => p.id === id ? { ...p, ...fields } : p));
  const remove = (id) => onCloseupsChange(closeups.filter(p => p.id !== id));

  return <div className="photo-slots">
    <p className="photo-slots-intro">
      One main photo per slot — that’s what the narration talks about. Nested close-ups appear
      underneath it on screen for that whole chapter and get a passing mention at most.
    </p>
    {SLOTS.map(([slot, title]) => {
      const nested = closeupsFor(closeups, slot);
      const hasMain = !!(photoUrls[slot] || "").trim();
      return <div key={slot} className="photo-slot">
        <label className="photo-slot-main">
          <span className="photo-slot-title">{title}</span>
          <input
            value={photoUrls[slot] || ""}
            onChange={(e) => onUrlsChange({ ...photoUrls, [slot]: e.target.value })}
            placeholder="Main photo — direct image URL"
            disabled={disabled}
          />
        </label>
        {!!nested.length && !hasMain &&
          <p className="photo-slot-warning">Add a main {title.toLowerCase()} photo — close-ups are shown underneath one, never on their own.</p>}
        {nested.map((photo, index) => <div key={photo.id} className="photo-closeup">
          <span className="photo-closeup-index">{index + 1}</span>
          <input
            className="photo-closeup-url"
            value={photo.url}
            onChange={(e) => update(photo.id, { url: e.target.value })}
            placeholder="Close-up — direct image URL"
            disabled={disabled}
          />
          <input
            className="photo-closeup-label"
            value={photo.label}
            onChange={(e) => update(photo.id, { label: e.target.value })}
            placeholder="Name (e.g. exhaust tip)"
            maxLength={120}
            disabled={disabled}
          />
          <button type="button" onClick={() => remove(photo.id)} disabled={disabled}
                  aria-label={`Remove ${title} close-up ${index + 1}`}>×</button>
        </div>)}
        <div className="photo-slot-actions">
          <button type="button" disabled={disabled || nested.length >= MAX_CLOSEUPS}
                  onClick={() => addCloseup(slot)}>
            + Close-up
          </button>
          <span className="photo-slot-count">{nested.length}/{MAX_CLOSEUPS}</span>
        </div>
      </div>;
    })}
  </div>;
}
