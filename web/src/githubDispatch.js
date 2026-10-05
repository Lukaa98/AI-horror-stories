/* One workflow_dispatch, shared.
 *
 * The build form and the Research tab both start workflows, and a copy of
 * this in each was one copy too many -- App imports ResearchPanel, so
 * ResearchPanel cannot import App back without a cycle.
 */
export async function dispatchWorkflow({ owner, repo, branch, token, workflow, inputs }) {
  const res = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: branch, inputs }),
    }
  );
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Dispatch failed (${res.status}): ${body}`);
  }
}

/* A file on the output branch, cache-busted.
 *
 * raw.githubusercontent caches hard, and a batch that just finished reading
 * would otherwise come back as the previous one for minutes.
 */
export async function readOutputJson({ owner, repo, path, branch = "cars-output" }) {
  const url = `https://raw.githubusercontent.com/${owner}/${repo}/${branch}/${path}?t=${Date.now()}`;
  const res = await fetch(url);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Could not read ${path} (${res.status})`);
  return res.json();
}
