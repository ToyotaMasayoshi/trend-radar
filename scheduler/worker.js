const DISPATCH_URL =
  "https://api.github.com/repos/ToyotaMasayoshi/trend-radar/actions/workflows/daily.yml/dispatches";

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(
      fetch(DISPATCH_URL, {
        method: "POST",
        headers: {
          Accept: "application/vnd.github+json",
          Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
          "User-Agent": "trend-radar-scheduler",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main" }),
      }).then(async (response) => {
        if (!response.ok) {
          throw new Error(`GitHub dispatch failed: ${response.status} ${await response.text()}`);
        }
      }),
    );
  },
};
