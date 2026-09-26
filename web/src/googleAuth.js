/* Signing in to Google from the browser.
 *
 * The dashboard is a static page on GitHub Pages, so it can hold no secret
 * -- which is why every YouTube question has gone through a workflow: the
 * refresh token lives in Actions secrets and deliberately never reaches
 * here.
 *
 * This is the implicit flow, not authorization-code with PKCE. PKCE was
 * tried first and cannot work here: Google treats a "Web application"
 * client as confidential and its token endpoint demands the client secret
 * even when a code verifier is supplied, and putting that secret in a
 * public page is the one thing this is avoiding. The implicit flow never
 * calls the token endpoint -- Google returns the access token in the URL
 * fragment, which the browser never sends anywhere.
 *
 * The cost is that there is no refresh token: the grant is good for about
 * an hour and then you sign in again. That is the right trade for a page
 * whose job is answering questions on demand.
 *
 * The workflow path stays exactly as it was. It is what uploads on a
 * schedule, with nobody signed in.
 */
const CLIENT_ID =
  "809577058313-kdvnf2r0mej4e9jmgfls4odelapgjv24.apps.googleusercontent.com";
const AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth";
// Read and write, because unscheduling and deleting are the same trip.
const SCOPE = "https://www.googleapis.com/auth/youtube";

const STATE_KEY = "yt.oauth.state";
const RETURN_KEY = "yt.oauth.return";
const TOKEN_KEY = "yt.access";
// Google's tokens last an hour. Treating one as spent a minute early means
// a request never dies halfway because it aged mid-flight.
const EXPIRY_MARGIN_MS = 60_000;

function randomState() {
  return base64url(crypto.getRandomValues(new Uint8Array(24)));
}

function base64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
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
  const state = randomState();
  sessionStorage.setItem(STATE_KEY, state);
  // Which tab was open. Without it, signing in from the YouTube tab lands
  // you back on whichever tab is the default.
  sessionStorage.setItem(RETURN_KEY, window.location.hash || "");
  const params = new URLSearchParams({
    client_id: CLIENT_ID,
    redirect_uri: redirectUri(),
    response_type: "token",
    scope: SCOPE,
    state,
    include_granted_scopes: "true",
  });
  window.location.assign(`${AUTH_ENDPOINT}?${params}`);
}

/** Read the token Google left in the fragment. Safe to call always. */
export async function completeSignIn() {
  // The fragment, not the query: an implicit grant comes back after the #,
  // which browsers never send to a server. But the return-to-tab hash
  // lives there too, so a hash without a token is just a route.
  const raw = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(raw);
  const token = params.get("access_token");
  const failure = params.get("error") || new URLSearchParams(window.location.search).get("error");
  if (!token && !failure) return null;

  const expected = sessionStorage.getItem(STATE_KEY);
  sessionStorage.removeItem(STATE_KEY);
  const back = sessionStorage.getItem(RETURN_KEY) || "";
  sessionStorage.removeItem(RETURN_KEY);
  // Take the grant out of the address bar either way, so a refresh or a
  // shared link does not carry a live token with it.
  window.history.replaceState({}, "", window.location.pathname + back);

  if (failure) throw new Error(`Google refused the sign-in (${failure}).`);
  if (!expected || params.get("state") !== expected) {
    throw new Error("This sign-in did not start in this tab; try again.");
  }

  const kept = {
    access_token: token,
    expires_at: Date.now() + Number(params.get("expires_in") || 3600) * 1000,
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
