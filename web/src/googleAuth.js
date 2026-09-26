/* Signing in to Google from the browser, with PKCE.
 *
 * The dashboard is a static page on GitHub Pages, so it can hold no secret
 * -- which is why every YouTube question has gone through a workflow: the
 * refresh token lives in Actions secrets and deliberately never reaches
 * here. PKCE needs no secret. The client id is public by design, and the
 * proof of possession is generated fresh per sign-in and never leaves this
 * tab, so the page can talk to YouTube as you without ever holding a
 * credential that would be worth stealing from it.
 *
 * The workflow path stays exactly as it was. It is what uploads on a
 * schedule, with nobody signed in.
 */
const CLIENT_ID =
  "809577058313-kdvnf2r0mej4e9jmgfls4odelapgjv24.apps.googleusercontent.com";
const AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth";
const TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token";
// Read and write, because unscheduling and deleting are the same trip.
const SCOPE = "https://www.googleapis.com/auth/youtube";

const VERIFIER_KEY = "yt.pkce.verifier";
const RETURN_KEY = "yt.pkce.return";
const TOKEN_KEY = "yt.access";
// Google's tokens last an hour. Treating one as spent a minute early means
// a request never dies halfway because it aged mid-flight.
const EXPIRY_MARGIN_MS = 60_000;

function randomVerifier() {
  const bytes = crypto.getRandomValues(new Uint8Array(64));
  return base64url(bytes);
}

function base64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function challengeFor(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

/** Where Google is told to come back to: this page, with nothing on it. */
export function redirectUri() {
  return window.location.origin + window.location.pathname;
}

function storedToken() {
  try {
    const raw = sessionStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const token = JSON.parse(raw);
    if (!token.access_token || token.expires_at - EXPIRY_MARGIN_MS < Date.now()) return null;
    return token;
  } catch {
    // A private window, or blocked site data. Not being signed in is a
    // correct answer to that, so it is not an error.
    return null;
  }
}

function keepToken(token) {
  // sessionStorage, not localStorage: an access token is worth an hour and
  // should not outlive the tab it was granted to.
  try {
    sessionStorage.setItem(TOKEN_KEY, JSON.stringify(token));
  } catch { /* nothing to do; the token still works for this page load */ }
}

export function signedIn() {
  return Boolean(storedToken());
}

export function signOut() {
  try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* already gone */ }
}

/** Send the browser to Google. Returns only if the redirect fails. */
export async function signIn() {
  const verifier = randomVerifier();
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  // The hash carries which tab was open; without it, signing in from the
  // YouTube tab lands you back on whichever tab is the default.
  sessionStorage.setItem(RETURN_KEY, window.location.hash || "");
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    redirect_uri: redirectUri(),
    response_type: "code",
    scope: SCOPE,
    code_challenge: await challengeFor(verifier),
    code_challenge_method: "S256",
    // Already signed in to Google in this browser: no second prompt.
    prompt: "",
    include_granted_scopes: "true",
  });
  window.location.assign(`${AUTH_ENDPOINT}?${params}`);
}

/** Swap the ?code= Google left in the URL for a token. Safe to call always. */
export async function completeSignIn() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const failure = params.get("error");
  if (!code && !failure) return null;

  const verifier = sessionStorage.getItem(VERIFIER_KEY);
  sessionStorage.removeItem(VERIFIER_KEY);
  const back = sessionStorage.getItem(RETURN_KEY) || window.location.hash || "";
  sessionStorage.removeItem(RETURN_KEY);
  // Take the code out of the address bar either way: leaving it there means
  // a refresh retries a code Google has already spent.
  window.history.replaceState({}, "", window.location.pathname + back);

  if (failure) throw new Error(`Google refused the sign-in (${failure}).`);
  if (!verifier) throw new Error("This sign-in did not start in this tab; try again.");

  const res = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: CLIENT_ID,
      code,
      code_verifier: verifier,
      grant_type: "authorization_code",
      redirect_uri: redirectUri(),
    }),
  });
  if (!res.ok) throw new Error(`Could not finish signing in (${res.status}): ${await res.text()}`);
  const token = await res.json();
  const kept = {
    access_token: token.access_token,
    expires_at: Date.now() + Number(token.expires_in || 3600) * 1000,
  };
  keepToken(kept);
  return kept;
}

/** One YouTube API call as the signed-in account. */
export async function youtube(path, { method = "GET", params = {}, body } = {}) {
  const token = storedToken();
  if (!token) throw new Error("Not signed in to YouTube.");
  const query = new URLSearchParams(params).toString();
  const res = await fetch(`https://www.googleapis.com/youtube/v3/${path}${query ? `?${query}` : ""}`, {
    method,
    headers: {
      Authorization: `Bearer ${token.access_token}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    // The token aged out mid-session. Saying so beats a raw 401, and the
    // panel can offer the button again.
    signOut();
    throw new Error("The YouTube sign-in expired; sign in again.");
  }
  if (!res.ok) throw new Error(`YouTube said ${res.status}: ${await res.text()}`);
  return res.status === 204 ? null : res.json();
}
