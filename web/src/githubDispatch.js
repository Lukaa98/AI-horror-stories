/* One workflow_dispatch, shared.
 *
 * The build form and the Research tab both start workflows, and a copy of
 * this in each was one copy too many -- App imports ResearchPanel, so
 * ResearchPanel cannot import App back without a cycle.
 */
// The branch every workflow is dispatched to when this browser has no
// saved settings. It lived as a literal in three files and sat on v12 for
// three weeks after the work moved to v13, so a browser that lost its
// settings aimed builds at a stale workflow file. GitHub refuses a
// dispatch carrying inputs the workflow does not declare, and v12 had
// never heard of script_style -- so the dispatch 422'd, no run was
// created, nothing appeared in Actions, and the build simply did not
// happen. One copy, and it moves with the branch being developed on.
export const DEFAULT_BRANCH = "v13";


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
    // GitHub refuses a dispatch carrying an input the workflow file on that
    // ref does not declare. The raw 422 names the input but not the reason,
    // and the reason is always the same one: the branch is older than this
    // dashboard. Said plainly, it is a settings change; said as JSON, it
    // reads like the build broke.
    if (res.status === 422 && body.includes("Unexpected inputs provided")) {
      const named = (body.match(/Unexpected inputs provided: \[([^\]]*)\]/) || [])[1] || "";
      const inputs = named.replace(/[\\"]/g, "") || "an input it was sent";
      throw new Error(
        `The workflow on branch "${branch}" does not accept ${inputs}. That branch's `
        + `workflow file is older than this dashboard, so nothing was started and there is `
        + `no build log. Set Branch to ${DEFAULT_BRANCH} in Settings and try again.`
      );
    }
    throw new Error(`Dispatch failed (${res.status}): ${body}`);
  }
}
