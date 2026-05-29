// api/manual.js — read/write docs/data/manual_sources.json via GitHub Contents API
// POST  { source, filename, records }  → upsert a source
// DELETE { source }                    → remove a source

const GITHUB_OWNER = "jbsolutionslabs";
const GITHUB_REPO  = "copierInventory";
const FILE_PATH    = "docs/data/manual_sources.json";

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, DELETE, OPTIONS");
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
    if (r.status === 404) return { data: { sources: {} }, sha: null };
    if (!r.ok) throw new Error(`GitHub read failed: ${r.status}`);
    const file = await r.json();
    const content = Buffer.from(file.content.replace(/\n/g, ""), "base64").toString("utf-8");
    return { data: JSON.parse(content), sha: file.sha };
  }

  async function writeFile(data, sha) {
    const content = Buffer.from(JSON.stringify(data)).toString("base64");
    const body = {
      message: "chore: update manual sources",
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
    if (req.method === "POST") {
      const { source, filename, records } = req.body;
      if (!source || !Array.isArray(records))
        return res.status(400).json({ error: "source and records are required" });

      const { data, sha } = await readFile();
      data.sources = data.sources || {};
      data.sources[source] = { filename, uploadedAt: new Date().toISOString(), records };
      data.updated = new Date().toISOString();
      await writeFile(data, sha);
      return res.status(200).json({ ok: true, count: records.length });
    }

    if (req.method === "DELETE") {
      const { source } = req.body;
      if (!source) return res.status(400).json({ error: "source is required" });

      const { data, sha } = await readFile();
      delete data.sources[source];
      data.updated = new Date().toISOString();
      await writeFile(data, sha);
      return res.status(200).json({ ok: true });
    }

    return res.status(405).json({ error: "Method not allowed" });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
