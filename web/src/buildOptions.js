/* Which settings a build was actually run with.
 *
 * Every one of these is already in result.json -- build_inputs is a verbatim
 * snapshot of what was dispatched -- but it was only readable by opening the
 * raw JSON and knowing which keys matter. Four builds of the same car run
 * back to back with different settings are indistinguishable in the list,
 * which makes comparing them guesswork about which was which.
 *
 * So: a short badge per setting that was turned on, and one for the script
 * style whether or not it was changed, because "this was the plain style" is
 * the thing you most want to know when a dense build sits next to it.
 *
 * A build whose result.json predates build_inputs gets an empty list rather
 * than a row of "off" badges -- nothing was recorded, so nothing is claimed.
 */
import { splitAngles } from "./angles.js";
import { parseExtraPhotos } from "./photoSections.js";

const STYLE_LABELS = {
  dense: "Dense script",
  short: "35s short",
  classic: "Classic script",
};

const PHOTO_SLOTS = ["photo_front", "photo_side", "photo_rear", "photo_engine", "photo_interior"];

function filled(value) {
  return Boolean(String(value ?? "").trim());
}

/**
 * [{key, label, title}] -- the settings worth seeing at a glance.
 *
 * `title` is the hover text: a badge saying "Notes" is only half the answer
 * when the question is which notes.
 */
export function buildOptions(result) {
  const inputs = result?.build_inputs;
  if (!inputs) return [];
  const badges = [];

  const style = String(inputs.script_style || "classic").trim().toLowerCase();
  badges.push({
    key: "style",
    label: STYLE_LABELS[style] || `${style} script`,
    title: style === "dense"
      ? "Dense script: ~160 words over about 63 seconds, in short sentences."
      : style === "short"
      ? "35-second short: 80-100 words over 30-40 seconds."
      : "Classic script: ~147 words over about 55 seconds.",
  });

  if (filled(inputs.auction_url)) {
    badges.push({ key: "listing", label: "Listing URL", title: String(inputs.auction_url).trim() });
  }

  const slots = PHOTO_SLOTS.filter((slot) => filled(inputs[slot])).length;
  // extra_photos is a JSON array of {slot, url, label}, not a list of lines:
  // splitting it on commas counted the punctuation inside seven objects and
  // reported thirty-odd photos for seven. parseExtraPhotos is the reader the
  // create form already restores them with.
  const extra = parseExtraPhotos(inputs.extra_photos).length;
  if (slots || extra) {
    // Slots and extras are counted together because the question is how much
    // of the imagery was chosen by hand, not which box each one went in.
    const total = slots + extra;
    badges.push({
      key: "photos",
      label: `${total} manual photo${total === 1 ? "" : "s"}`,
      title: extra
        ? `${slots} in named slots, ${extra} pasted as extras.`
        : `${slots} pasted into named slots.`,
    });
  }

  const bullets = splitAngles(inputs.angles);
  if (bullets.length) {
    badges.push({
      key: "angles",
      label: `${bullets.length} note${bullets.length === 1 ? "" : "s"}`,
      // Numbered, because the order is the priority -- that is the whole
      // point of the list and it is invisible in the badge.
      title: bullets.map((bullet, index) => `${index + 1}. ${bullet}`).join("\n"),
    });
  }

  if (String(inputs.disable_comparison) === "true") {
    badges.push({
      key: "no-comparison",
      label: "No comparison",
      title: "The script was told not to measure the car against a rival.",
    });
  }

  if (filled(inputs.rival_car) || filled(inputs.photo_rival)) {
    badges.push({
      key: "rival",
      label: filled(inputs.rival_car) ? `vs ${String(inputs.rival_car).trim()}` : "Rival photo",
      title: "A rival was named for the comparison beat.",
    });
  }

  if (filled(inputs.current_price)) {
    badges.push({
      key: "price",
      label: "Price given",
      title: `Told the script this car is worth ${String(inputs.current_price).trim()}.`,
    });
  }

  return badges;
}

/** The same thing on one line, for places too tight for badges. */
export function buildOptionsLine(result) {
  return buildOptions(result).map((badge) => badge.label).join(" · ");
}
