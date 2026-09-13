import "./PhotoSections.css";

const sections = [["exterior", "Exterior"], ["interior", "Interior"], ["engine", "Engine bay"]];

export default function PhotoSections({ photos, onChange, disabled }) {
  const update = (id, fields) => onChange(photos.map(p => {
    if (p.id === id) return { ...p, ...fields };
    const target = photos.find(p => p.id === id);
    if (fields.role === "hero" && p.section === (fields.section || target.section) && p.role === "hero") return { ...p, role: "angle" };
    return p;
  }));
  const add = (section, role) => onChange([...photos, {
    id: `photo-${Date.now()}-${photos.length}`, section, role, label: "", url: "", note: "",
  }]);
  const move = (id, direction) => {
    const index = photos.findIndex(p => p.id === id);
    const siblings = photos.map((p, i) => p.section === photos[index].section ? i : -1).filter(i => i >= 0);
    const target = siblings[siblings.indexOf(index) + direction];
    if (target === undefined) return;
    const next = [...photos];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  return <div className="photo-sections">
    <p>Main photo stays above the narration. Named close-ups appear below as they’re discussed. Add a main photo first; use angles for other full views.</p>
    {[...sections, ...photos.some(p => !p.section) ? [["", "Other photos (legacy)"]] : []].map(([section, title]) => {
      const items = photos.filter(p => (p.section || "") === section);
      return <details key={section} open className="photo-section">
        <summary>{title} <span>{items.length} photos</span></summary>
        <div className="photo-section-content">
          {!!items.length && section && !items.some(p => p.role === "hero" && p.url.trim()) && <p className="photo-warning">Choose a main image to keep an overview above these close-ups.</p>}
          {items.map((photo, index) => <fieldset key={photo.id} disabled={disabled} className="grouped-photo">
            <legend>{photo.label || `${title} photo ${index + 1}`}</legend>
            <div className="photo-fields">
              <label>Role<select value={photo.role || "detail"} onChange={e => update(photo.id, { role: e.target.value })}>
                <option value="hero">Main photo</option><option value="angle">Alternate angle</option><option value="detail">Close-up</option>
              </select></label>
              <label>Section<select value={photo.section || ""} onChange={e => update(photo.id, { section: e.target.value, role: "detail" })}>
                {!section && <option value="">Other</option>}{sections.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
              </select></label>
              <label className="photo-full-row">Name<input value={photo.label} onChange={e => update(photo.id, { label: e.target.value })} placeholder="e.g. Steering wheel, gauges, rear diffuser" maxLength={120} /></label>
              <label className="photo-full-row">Image URL<input type="url" value={photo.url} onChange={e => update(photo.id, { url: e.target.value })} placeholder="https://… direct image URL" /></label>
              <label className="photo-full-row">Narration note (optional)<input value={photo.note || ""} onChange={e => update(photo.id, { note: e.target.value })} placeholder="What should we look at? Facts will still be verified." maxLength={500} /></label>
            </div>
            {/^https?:\/\//i.test(photo.url) && <img className="photo-thumb" src={photo.url} alt={photo.label || "Photo preview"} loading="lazy" referrerPolicy="no-referrer" />}
            <div className="photo-actions">
              <button type="button" disabled={disabled || index === 0} onClick={() => move(photo.id, -1)} aria-label={`Move ${photo.label || "photo"} up`}>↑</button>
              <button type="button" disabled={disabled || index === items.length - 1} onClick={() => move(photo.id, 1)} aria-label={`Move ${photo.label || "photo"} down`}>↓</button>
              <button type="button" onClick={() => onChange(photos.filter(p => p.id !== photo.id))}>Remove</button>
            </div>
          </fieldset>)}
          <div className="photo-actions">
            {!items.some(p => p.role === "hero") && section && <button type="button" disabled={disabled} onClick={() => add(section, "hero")}>+ Main photo</button>}
            {section && <button type="button" disabled={disabled} onClick={() => add(section, "angle")}>+ Angle</button>}
            <button type="button" disabled={disabled} onClick={() => add(section, "detail")}>+ Close-up</button>
          </div>
        </div>
      </details>;
    })}
  </div>;
}
