// api/upload.js — manage raw inventory files in imports/ via the GitHub Contents API
//
// GET                          → list files currently in imports/
// POST { filename, content }   → upload (base64 content) to imports/
// DELETE { filename }          → remove a file from imports/

const GITHUB_OWNER = "jbsolutionslabs";
const GITHUB_REPO  = "copierInventory";
const IMPORTS_PATH = "imports";

function ghHeaders(token) {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "Content-Type": "application/json",
  };
}

function dirUrl() {
  return `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${IMPORTS_PATH}`;
}

function fileUrl(safeName) {
  return `${dirUrl()}/${safeName}`;
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  if (req.method === "OPTIONS") return res.status(200).end();

  const token = process.env.GITHUB_TOKEN;
  if (!token) return res.status(500).json({ error: "GITHUB_TOKEN not configured on server" });

  // ── GET: list files in imports/ ───────────────────────────────────────────
  if (req.method === "GET") {
    const r = await fetch(dirUrl(), { headers: ghHeaders(token) });
    if (r.status === 404) return res.status(200).json({ files: [] });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      return res.status(r.status).json({ error: err.message || `GitHub error: ${r.status}` });
    }
    const items = await r.json();
    const files = items
      .filter(i => i.type === "file")
      .map(i => ({ name: i.name, size: i.size, sha: i.sha }));
    return res.status(200).json({ files });
  }

  // ── POST: upload a file to imports/ ──────────────────────────────────────
  if (req.method === "POST") {
    const { filename, content } = req.body || {};
    if (!filename || !content) return res.status(400).json({ error: "filename and content are required" });

    const safeName = filename.replace(/[^a-zA-Z0-9._\-() ]/g, "_");
    const url = fileUrl(safeName);

    // Need existing SHA to overwrite
    let sha = null;
    const check = await fetch(url, { headers: ghHeaders(token) });
    if (check.ok) sha = (await check.json()).sha;

    const r = await fetch(url, {
      method: "PUT",
      headers: ghHeaders(token),
      body: JSON.stringify({
        message: `chore: upload manual source ${safeName}`,
        content,
        branch: "main",
        ...(sha ? { sha } : {}),
      }),
    });

    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      return res.status(r.status).json({ error: err.message || `GitHub write failed: ${r.status}` });
    }
    return res.status(200).json({ ok: true, path: `${IMPORTS_PATH}/${safeName}` });
  }

  // ── DELETE: remove a file from imports/ ──────────────────────────────────
  if (req.method === "DELETE") {
    const { filename } = req.body || {};
    if (!filename) return res.status(400).json({ error: "filename is required" });

    const safeName = filename.replace(/[^a-zA-Z0-9._\-() ]/g, "_");
    const url = fileUrl(safeName);

    // Must have the file's SHA to delete
    const check = await fetch(url, { headers: ghHeaders(token) });
    if (!check.ok) return res.status(404).json({ error: "File not found in imports/" });
    const { sha } = await check.json();

    const r = await fetch(url, {
      method: "DELETE",
      headers: ghHeaders(token),
      body: JSON.stringify({
        message: `chore: remove manual source ${safeName}`,
        sha,
        branch: "main",
      }),
    });

    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      return res.status(r.status).json({ error: err.message || `GitHub delete failed: ${r.status}` });
    }
    return res.status(200).json({ ok: true });
  }

  return res.status(405).json({ error: "Method not allowed" });
}
