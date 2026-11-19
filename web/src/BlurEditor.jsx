import { useRef } from "react";
import "./BlurEditor.css";
import { cropFractions, dragCropBox, moveCropBox, cropHandleAt, cropAnchor } from "./clipTimes";

/* One photo with one box drawn on it, for hiding a number plate.
 *
 * The automatic pass reads each photo with a vision model and fails open,
 * which is the right trade for a pass nobody asked for and the wrong one
 * when you can see the plate and it did not.
 *
 * The arithmetic is the same the clip's crop box uses and lives in
 * clipTimes, so a box here means exactly what a box there means: four
 * percentages of the picture. The pointer handling is its own, because
 * this surface has one box and no shape lock, where the clip's has two
 * boxes, a shape lock and an auto-fill -- sharing them would mean passing
 * five flags to turn most of it off.
 */
export default function BlurEditor({ url, box, onChange, onClose, alt = "" }) {
  const frame = useRef(null);
  const drag = useRef(null);
  const clean = (url || "").trim();
  if (!clean) return null;

  const pointAt = (event) => {
    const el = frame.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return null;
    return { x: event.clientX - r.left, y: event.clientY - r.top,
             rect: { width: r.width, height: r.height } };
  };

  const onDown = (event) => {
    const point = pointAt(event);
    if (!point) return;
    const current = cropFractions(box);
    const grip = cropHandleAt(point, current, point.rect);
    drag.current = grip === "inside"
      ? { mode: "move", from: point, box: current }
      : { mode: "draw", from: cropAnchor(current, grip, point.rect) || point };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };

  const onMove = (event) => {
    const moving = drag.current;
    if (!moving) return;
    const point = pointAt(event);
    if (!point) return;
    const next = moving.mode === "move"
      ? moveCropBox(moving.box, point.x - moving.from.x, point.y - moving.from.y, point.rect)
      : dragCropBox(moving.from, point, point.rect, 0);
    if (next) { moving.drew = true; onChange(next); }
  };

  const onUp = (event) => {
    const moving = drag.current;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    // Pressed and let go on bare photo: no box, same as the clip's.
    if (moving && moving.mode === "draw" && !moving.drew) {
      onChange({ left: "", top: "", width: "", height: "" });
    }
  };

  const drawn = cropFractions(box);
  return (
    <div className="blur-editor">
      <div className="blur-editor-head">
        <span>Drag over the number plate. Click the photo to clear it.</span>
        <button type="button" onClick={onClose}>Done</button>
      </div>
      <div className="blur-editor-frame" ref={frame}
           onPointerDown={onDown} onPointerMove={onMove}
           onPointerUp={onUp} onPointerCancel={onUp}>
        <img src={clean} alt={alt} draggable={false} />
        {drawn && (
          <span className="blur-box" style={{
            left: `${drawn.x * 100}%`, top: `${drawn.y * 100}%`,
            width: `${drawn.w * 100}%`, height: `${drawn.h * 100}%`,
          }}>
            <i className="crop-grip nw" /><i className="crop-grip ne" />
            <i className="crop-grip sw" /><i className="crop-grip se" />
          </span>
        )}
      </div>
    </div>
  );
}
