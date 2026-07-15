// Cloudflare Pages Function — POST /api/request-title
// Required env var (set in Cloudflare Pages → Settings → Environment variables):
//   GITHUB_TOKEN  — fine-grained PAT with Issues: write on onxtane/f1-lifx-bridge

const GITHUB_OWNER = "onxtane";
const GITHUB_REPO  = "f1-lifx-bridge";
const MAX_REQUESTS_PER_IP = 3; // per invocation — Cloudflare WAF handles broader abuse

export async function onRequestPost({ request, env }) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "Invalid request body" }, 400);
  }

  const { title, platform, notes, contact, _trap } = body;

  // Honeypot — bots fill hidden fields, humans don't
  if (_trap) return json({ ok: true });

  if (!title || typeof title !== "string" || !title.trim()) {
    return json({ error: "Game title is required" }, 400);
  }

  if (!env.GITHUB_TOKEN) {
    return json({ error: "Server misconfiguration — contact the maintainer" }, 500);
  }

  const t = s => (s || "").toString().trim();
  const issueBody = buildBody(t(title), t(platform), t(notes), t(contact));

  const ghRes = await fetch(`https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/issues`, {
    method: "POST",
    headers: {
      Authorization:        `Bearer ${env.GITHUB_TOKEN}`,
      Accept:               "application/vnd.github+json",
      "Content-Type":       "application/json",
      "User-Agent":         "GridGlow-Website/1.0",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({
      title:  `Title request: ${t(title).slice(0, 180)}`,
      body:   issueBody,
      labels: ["title-request"],
    }),
  });

  if (!ghRes.ok) {
    const detail = await ghRes.text().catch(() => "");
    console.error("GitHub API error", ghRes.status, detail);
    return json({ error: "Could not create issue — please try again later" }, 502);
  }

  const issue = await ghRes.json();
  return json({ ok: true, issueUrl: issue.html_url });
}

function buildBody(title, platform, notes, contact) {
  const lines = [`## Title Request: ${title}`, ""];
  if (platform) lines.push(`**Platform:** ${platform}`, "");
  if (notes)    lines.push(`**Why / notes:**`, notes, "");
  if (contact)  lines.push(`**Contact:** ${contact}`, "");
  lines.push("---", "*Submitted via the GridGlow website*");
  return lines.join("\n");
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
