// The two- and three-car countdown's form logic, kept out of the component
// for the same reason clipTimes.js is: this is the part that decides
// whether a fifteen-minute build is worth starting, and it is worth being
// able to test it without a browser.

export const COMPARE_SLOTS = ["front", "side", "rear", "engine", "interior"];

// Matches MAX_PHOTOS_PER_CAR in compare_short.py. Three cars share one
// runtime, so each is on screen for about fifteen seconds and a fourth
// photo is never reached -- the build trims to this and says so, and the
// form should say the same thing before anybody waits for a run to find
// out which three it kept.
export const MAX_COMPARE_PHOTOS = 3;

export const MIN_COMPARE_CARS = 2;
export const MAX_COMPARE_CARS = 3;

export function emptyCompareCar() {
  return { url: "", label: "", worth: "", notes: "", photos: {} };
}

export function emptyCompareCars(count = MAX_COMPARE_CARS) {
  return Array.from({ length: count }, emptyCompareCar);
}

function filledPhotos(car) {
  return COMPARE_SLOTS
    .map((slot) => [slot, ((car?.photos || {})[slot] || "").trim()])
    .filter(([, url]) => url);
}

export function photoCount(car) {
  return filledPhotos(car).length;
}

/** Whether this car has more photos than a countdown can show. */
export function overPhotoLimit(car) {
  return photoCount(car) > MAX_COMPARE_PHOTOS;
}

/**
 * Why this countdown cannot be built yet, in the words the user needs, or
 * null when it can.
 *
 * Checked here as well as in the build because the build finds out after
 * the credits check, the scrape and the script -- and a car with no photo
 * is not a thing to learn about fifteen minutes in.
 */
export function compareProblem(cars, count) {
  if (count < MIN_COMPARE_CARS || count > MAX_COMPARE_CARS) {
    return `A countdown takes ${MIN_COMPARE_CARS} or ${MAX_COMPARE_CARS} cars.`;
  }
  const used = (cars || []).slice(0, count);
  if (used.length < count) return `Car ${used.length + 1} is missing.`;
  for (let index = 0; index < used.length; index += 1) {
    const car = used[index] || {};
    const url = (car.url || "").trim();
    const label = (car.label || "").trim();
    if (!url && !label) {
      return `Car ${index + 1} needs a listing link or a name.`;
    }
    if (url && !/^https:\/\/carsandbids\.com\/auctions\//i.test(url)) {
      return `Car ${index + 1}'s link is not a Cars & Bids auction URL.`;
    }
    if (!photoCount(car)) {
      return `Car ${index + 1} needs at least one photo -- it has to be on screen while it is talked about.`;
    }
  }
  return null;
}

/**
 * The `cars` workflow input for a countdown.
 *
 * An object rather than a bare list so the theme can ride inside it. The
 * research workflow has used all 25 inputs workflow_dispatch allows since
 * before this mode existed, so there was none left to give the theme one
 * of its own -- and a 26th would not have broken the countdown, it would
 * have stopped every mode on that workflow from dispatching.
 */
export function comparePayload(cars, count, theme = "") {
  if (compareProblem(cars, count)) return null;
  return {
    theme: (theme || "").trim(),
    cars: (cars || []).slice(0, count).map((car) => {
      const photos = {};
      for (const [slot, url] of filledPhotos(car).slice(0, MAX_COMPARE_PHOTOS)) {
        photos[slot] = url;
      }
      if (car.blur && Object.keys(car.blur).length) photos.blur = car.blur;
      const entry = {
        url: (car.url || "").trim(),
        label: (car.label || "").trim(),
        worth: (car.worth || "").trim(),
        notes: (car.notes || "").trim(),
        photos,
      };
      if (car.engineClip) entry.engine_clip = car.engineClip;
      return entry;
    }),
  };
}

// How wide a tab's name may get before it is cut. The cut has to include
// the ellipsis or it is not a cap at all -- slicing to the limit and then
// appending three dots is three characters over it, every time.
const TAB_LABEL_LIMIT = 18;

function cut(name) {
  return name.length > TAB_LABEL_LIMIT
    ? `${name.slice(0, TAB_LABEL_LIMIT - 3).trimEnd()}...`
    : name;
}

/** A short name for a car, for the tab itself. */
export function compareTabLabel(car, index) {
  const label = (car?.label || "").trim();
  if (label) return cut(label);
  const url = (car?.url || "").trim();
  if (url) {
    // The slug a Cars & Bids URL ends with, which is the car's name with
    // hyphens in it -- better on a tab than "Car 2" when three listings
    // are open and they all look alike.
    const slug = url.split("/").filter(Boolean).pop() || "";
    const words = slug.replace(/[?#].*$/, "").split("-").filter(Boolean).slice(0, 3);
    if (words.length) {
      return cut(words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" "));
    }
  }
  return `Car ${index + 1}`;
}
