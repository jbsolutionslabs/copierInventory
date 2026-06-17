// api/watchlist.js — read/write docs/data/watchlist.json via GitHub Contents API
// GET             → return full watchlist array
// POST { watchlist: [...] } → save full watchlist
// DELETE          → clear watchlist

const GITHUB_OWNER = "jbsolutionslabs";
const GITHUB_REPO  = "copierInventory";
const FILE_PATH    = "docs/data/watchlist.json";

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();

  const token = process.env.GITHUB_TOKEN;
  if (!token) return res.status(500).json({ error: "GITHUB_TOKEN not configured on server" });

  const ghHeaders = {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
  };
  const apiUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${FILE_PATH}`;

  async function readFile() {
    const r = await fetch(apiUrl, { headers: ghHeaders });
    if (r.status === 404) return { data: [], sha: null };
    if (!r.ok) throw new Error(`GitHub read failed: ${r.status}`);
    const file = await r.json();
    const content = Buffer.from(file.content.replace(/\n/g, ""), "base64").toString("utf-8");
    return { data: JSON.parse(content), sha: file.sha };
  }

  async function writeFile(data, sha) {
    const content = Buffer.from(JSON.stringify(data)).toString("base64");
    const body = {
      message: "chore: update customer watchlist",
      content,
      branch: "main",
      ...(sha ? { sha } : {}),
    };
    const r = await fetch(apiUrl, { method: "PUT", headers: ghHeaders, body: JSON.stringify(body) });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.message || `GitHub write failed: ${r.status}`);
    }
  }

  try {
    if (req.method === "GET") {
      const { data } = await readFile();
      return res.status(200).json({ watchlist: Array.isArray(data) ? data : [] });
    }

    if (req.method === "POST") {
      const { watchlist } = req.body;
      if (!Array.isArray(watchlist))
        return res.status(400).json({ error: "watchlist array is required" });
      const { sha } = await readFile();
      await writeFile(watchlist, sha);
      return res.status(200).json({ ok: true, count: watchlist.length });
    }

    if (req.method === "DELETE") {
      const { sha } = await readFile();
      await writeFile([], sha);
      return res.status(200).json({ ok: true });
    }

    return res.status(405).json({ error: "Method not allowed" });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
