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
