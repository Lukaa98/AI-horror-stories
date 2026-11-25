import { useState } from "react";
import BlurEditor from "./BlurEditor.jsx";
import {
  COMPARE_SLOTS, MAX_COMPARE_PHOTOS, compareTabLabel, overPhotoLimit, photoCount,
} from "./compareCars.js";
import "./CompareCars.css";

const SLOT_LABELS = {
  front: "Front", side: "Side", rear: "Rear", engine: "Engine bay", interior: "Interior",
};

/**
 * One tab per car, with the same fields a single-car build takes.
 *
 * Tabs rather than three stacked forms because the three are identical and
 * a page of fifteen identical boxes is a page nobody can see their place
 * in -- the tab row is also the only thing that says, at a glance, which
 * cars are filled in and which are not.
 */
export default function CompareCars({ cars, count, onChange, disabled }) {
  const [active, setActive] = useState(0);
  const [editing, setEditing] = useState("");
  const shown = Math.min(active, count - 1);
  const car = cars[shown] || {};

  const update = (fields) => onChange(cars.map((entry, index) =>
    index === shown ? { ...entry, ...fields } : entry));
  const setPhoto = (slot, url) => update({ photos: { ...(car.photos || {}), [slot]: url } });
  const setBlur = (slot, box) => update({ blur: { ...(car.blur || {}), [slot]: box } });

  return (
    <div className="compare-cars">
      <div className="compare-tabs" role="tablist">
        {cars.slice(0, count).map((entry, index) => {
          const filled = photoCount(entry) > 0 && ((entry.url || "").trim() || (entry.label || "").trim());
          return (
            <button
              key={index}
              type="button"
              role="tab"
              aria-selected={index === shown}
              className={`compare-tab${index === shown ? " active" : ""}${filled ? " filled" : ""}`}
              onClick={() => { setActive(index); setEditing(""); }}
              disabled={disabled}
            >
              <span className="compare-tab-rank">{index + 1}</span>
              <span className="compare-tab-name">{compareTabLabel(entry, index)}</span>
            </button>
          );
        })}
      </div>

      <div className="compare-panel">
        <label className="compare-field">
          <span>Listing link</span>
          <input
            type="url"
            value={car.url || ""}
            placeholder="https://carsandbids.com/auctions/xxxxxxxx/..."
            onChange={(event) => update({ url: event.target.value })}
            disabled={disabled}
          />
        </label>
        <label className="compare-field">
          <span>Name</span>
          <input
            type="text"
            value={car.label || ""}
            placeholder="Mercedes E55 AMG W211 (used if there is no listing)"
            onChange={(event) => update({ label: event.target.value })}
            disabled={disabled}
          />
        </label>
        <label className="compare-field">
          <span>Worth today</span>
          <input
            type="text"
            value={car.worth || ""}
            placeholder="What it's worth today, e.g. 8k (optional)"
            onChange={(event) => update({ worth: event.target.value })}
            disabled={disabled}
          />
        </label>
        <label className="compare-field">
          <span>Things to cover</span>
          <textarea
            rows={3}
            value={car.notes || ""}
            placeholder={"Things you want it to cover, best first (optional)\n"
                         + "e.g. a pulley and a map takes it to around 600 horsepower"}
            onChange={(event) => update({ notes: event.target.value })}
            disabled={disabled}
          />
        </label>

        <div className="compare-photos">
          <div className="compare-photos-head">
            <strong>Photos</strong>
            <span className={overPhotoLimit(car) ? "compare-warn" : "hint"}>
              {overPhotoLimit(car)
                ? `${photoCount(car)} filled in - only the first ${MAX_COMPARE_PHOTOS} are used`
                : `Two or three is plenty. Each car is on screen for about fifteen seconds.`}
            </span>
          </div>
          {COMPARE_SLOTS.map((slot) => {
            const url = (car.photos || {})[slot] || "";
            const key = `${shown}-${slot}`;
            const hasBox = Boolean((car.blur || {})[slot]);
            return (
              <div className="compare-photo-row" key={slot}>
                <span className="compare-photo-slot">{SLOT_LABELS[slot]}</span>
                <input
                  type="url"
                  value={url}
                  placeholder="https://..."
                  onChange={(event) => setPhoto(slot, event.target.value)}
                  disabled={disabled}
                />
                {url.trim() ? (
                  <button
                    type="button"
                    className={`blur-open${hasBox ? " set" : ""}`}
                    onClick={() => setEditing(editing === key ? "" : key)}
                    disabled={disabled}
                  >
                    {hasBox ? "Plate hidden" : "Hide plate"}
                  </button>
                ) : null}
                {editing === key ? (
                  <BlurEditor
                    url={url}
                    box={(car.blur || {})[slot]}
                    alt={`${SLOT_LABELS[slot]} photo`}
                    onChange={(box) => setBlur(slot, box)}
                    onClose={() => setEditing("")}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
