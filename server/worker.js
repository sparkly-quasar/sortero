/**
 * Sortero Pro server: a Cloudflare Worker with three routes.
 *
 *   GET  /success?session_id=cs_...   Stripe sends buyers here after paying. It
 *                                     confirms the payment with Stripe and shows
 *                                     their licence key.
 *   POST /check  {"key": "SRT1..."}   Sortero asks whether a subscription is paid
 *                                     up, and gets a signed answer back.
 *   POST /webhook                     Stripe's checkout.session.completed event.
 *                                     Writes the key into the payment's metadata,
 *                                     so you can find and resend it from the
 *                                     Stripe dashboard.
 *
 * There is no database. Keys are signed, and Ed25519 signing is deterministic,
 * so the success page and the webhook always make the same key for a purchase.
 *
 * Secrets (npx wrangler secret put NAME):
 *   STRIPE_SECRET_KEY      a restricted key, see README.md
 *   STRIPE_WEBHOOK_SECRET  whsec_... from the webhook endpoint
 *   LICENCE_SIGNING_KEY    the hex seed from tools/licence_admin.py genkey
 * Vars (wrangler.toml):
 *   LICENCE_PUBLIC_KEY     hex, the same one built into the app
 *   PAYMENT_LINKS          plink_... ids that sell Sortero Pro, comma separated
 */
const STRIPE = "https://api.stripe.com/v1";
const STRIPE_VERSION = "2024-06-20";
const KEY_LABEL = "sortero-licence:";
const STATUS_LABEL = "sortero-status:";
const PKCS8_ED25519 = "302e020100300506032b657004220420";
const PAID_UP = new Set(["active", "trialing", "past_due"]);
const enc = new TextEncoder();

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    try {
      if (request.method === "GET" && url.pathname === "/success") return await success(url, env);
      if (request.method === "POST" && url.pathname === "/check") return await check(request, env);
      if (request.method === "POST" && url.pathname === "/webhook") return await webhook(request, env);
      return text("Not found", 404);
    } catch (err) {
      console.error(err);
      return text("Something went wrong. Please try again in a minute.", 500);
    }
  },
};

// ------------------------------------------------------------------ routes
async function success(url, env) {
  const id = url.searchParams.get("session_id") || "";
  if (!/^cs_(test|live)_[A-Za-z0-9]{10,200}$/.test(id)) {
    return page("That link isn't complete",
      "<p>Open the link from your payment confirmation again. If it keeps happening, reply to your receipt email.</p>", 400);
  }
  const session = await stripe(env, `/checkout/sessions/${id}`);
  if (!session) {
    return page("We couldn't find that purchase",
      "<p>Check you opened the whole link. If it keeps happening, reply to your receipt email.</p>", 404);
  }
  const key = await licenceFor(env, session);
  if (!key) {
    return page("Payment not confirmed yet",
      "<p>Stripe hasn't confirmed this payment. If you've just paid, wait a moment and reload this page.</p>", 402);
  }
  return page("Thanks for getting Sortero Pro", `
    <p>This is your licence key. Keep a copy somewhere safe.</p>
    <textarea id="key" readonly rows="4" spellcheck="false">${escape(key)}</textarea>
    <p><button id="copy" type="button">Copy key</button> <span id="copied"></span></p>
    <ol>
      <li>Open Sortero and choose <b>Sortero Pro</b> in the sidebar.</li>
      <li>Paste the key under <b>Licence key</b> and press <b>Activate</b>.</li>
    </ol>
    <script>
      document.getElementById("copy").onclick = async () => {
        const box = document.getElementById("key");
        try { await navigator.clipboard.writeText(box.value); }
        catch { box.select(); document.execCommand("copy"); }
        document.getElementById("copied").textContent = "Copied";
      };
    </script>`);
}

async function check(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "Send JSON with a key." }, 400);
  }
  const lic = await open(env, body && body.key, "SRT1", KEY_LABEL);
  if (!lic || lic.k !== "sub" || !/^sub_[A-Za-z0-9]+$/.test(lic.id || "")) {
    return json({ error: "That isn't a subscription licence." }, 400);
  }
  const sub = await stripe(env, `/subscriptions/${lic.id}`);
  let active = false;
  let until = 0;
  if (sub) {
    active = PAID_UP.has(sub.status);
    const periodEnd = sub.current_period_end
      ?? (sub.items && sub.items.data && sub.items.data[0] && sub.items.data[0].current_period_end) ?? 0;
    until = active ? periodEnd : (sub.ended_at || sub.canceled_at || 0);
  }
  const status = await seal(env, "SRS1", STATUS_LABEL, { v: 1, id: lic.id, until, active });
  return json({ status });
}

async function webhook(request, env) {
  const raw = await request.text();
  const ok = await verifyStripe(raw, request.headers.get("Stripe-Signature"), env.STRIPE_WEBHOOK_SECRET);
  if (!ok) return text("Bad signature", 400);
  const event = JSON.parse(raw);
  if (event.type === "checkout.session.completed" || event.type === "checkout.session.async_payment_succeeded") {
    const session = event.data.object;
    const key = await licenceFor(env, session);
    if (key) {
      const target = session.mode === "subscription"
        ? session.subscription && `/subscriptions/${idOf(session.subscription)}`
        : session.payment_intent && `/payment_intents/${idOf(session.payment_intent)}`;
      if (target) await stripe(env, target, { "metadata[sortero_licence]": key });
    }
  }
  return json({ received: true });
}

// ----------------------------------------------------------------- licences
function idOf(v) {
  return typeof v === "string" ? v : v && v.id;
}

async function licenceFor(env, session) {
  const links = String(env.PAYMENT_LINKS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!links.length || !links.includes(session.payment_link)) return null;   // not a Sortero sale
  if (session.status !== "complete") return null;
  if (!["paid", "no_payment_required"].includes(session.payment_status)) return null;
  const payload = session.mode === "subscription"
    ? { v: 1, k: "sub", id: idOf(session.subscription), t: session.created }
    : { v: 1, k: "life", id: session.id, t: session.created };
  if (!payload.id) return null;
  return seal(env, "SRT1", KEY_LABEL, payload);
}

async function seal(env, prefix, label, payload) {
  const raw = enc.encode(JSON.stringify(payload));
  const key = await crypto.subtle.importKey(
    "pkcs8", hex(PKCS8_ED25519 + String(env.LICENCE_SIGNING_KEY).trim()), { name: "Ed25519" }, false, ["sign"]);
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, key, join(enc.encode(label), raw));
  return `${prefix}.${b64url(raw)}.${b64url(new Uint8Array(sig))}`;
}

async function open(env, token, prefix, label) {
  const parts = String(token || "").replace(/\s+/g, "").split(".");
  if (parts.length !== 3 || parts[0] !== prefix) return null;
  try {
    const raw = unb64url(parts[1]);
    const key = await crypto.subtle.importKey(
      "raw", hex(env.LICENCE_PUBLIC_KEY), { name: "Ed25519" }, false, ["verify"]);
    const ok = await crypto.subtle.verify({ name: "Ed25519" }, key, unb64url(parts[2]), join(enc.encode(label), raw));
    return ok ? JSON.parse(new TextDecoder().decode(raw)) : null;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------------- Stripe
async function stripe(env, path, form) {
  const res = await fetch(STRIPE + path, {
    method: form ? "POST" : "GET",
    headers: {
      Authorization: `Bearer ${env.STRIPE_SECRET_KEY}`,
      "Stripe-Version": STRIPE_VERSION,
      ...(form ? { "Content-Type": "application/x-www-form-urlencoded" } : {}),
    },
    body: form ? new URLSearchParams(form) : undefined,
  });
  if (res.status === 404) return null;
  const data = await res.json();
  if (!res.ok) throw new Error(`Stripe ${res.status}: ${data && data.error && data.error.message}`);
  return data;
}

async function verifyStripe(payload, header, secret) {
  if (!header || !secret) return false;
  let t = null;
  const sigs = [];
  for (const item of header.split(",")) {
    const at = item.indexOf("=");
    const k = item.slice(0, at).trim();
    const v = item.slice(at + 1).trim();
    if (k === "t") t = v;
    else if (k === "v1") sigs.push(v);
  }
  if (!t || !sigs.length || Math.abs(Date.now() / 1000 - Number(t)) > 300) return false;
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const expected = toHex(await crypto.subtle.sign("HMAC", key, enc.encode(`${t}.${payload}`)));
  return sigs.some((s) => sameString(s, expected));
}

// ------------------------------------------------------------------ helpers
function sameString(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function hex(s) {
  const clean = String(s || "").trim();
  if (!/^([0-9a-f]{2})+$/i.test(clean)) throw new Error("bad hex");
  return Uint8Array.from(clean.match(/../g).map((b) => parseInt(b, 16)));
}

function toHex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function join(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a);
  out.set(b, a.length);
  return out;
}

function b64url(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function unb64url(s) {
  let t = s.replace(/-/g, "+").replace(/_/g, "/");
  while (t.length % 4) t += "=";
  return Uint8Array.from(atob(t), (c) => c.charCodeAt(0));
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const SECURITY_HEADERS = {
  "Cache-Control": "no-store",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
};

function text(body, status = 200) {
  return new Response(body, { status, headers: { ...SECURITY_HEADERS, "Content-Type": "text/plain; charset=utf-8" } });
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { ...SECURITY_HEADERS, "Content-Type": "application/json" } });
}

function page(title, body, status = 200) {
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(title)} · Sortero</title>
<style>
  :root { color-scheme: light dark; --fg:#1d1d1f; --muted:#6e6e73; --bg:#f5f5f7; --card:#fff; --line:#d6d6db; --accent:#0a62c9; }
  @media (prefers-color-scheme: dark) { :root { --fg:#f2f2f7; --muted:#a1a1a6; --bg:#1e1e1e; --card:#2c2c2e; --line:#3d3d40; --accent:#5aa9ff; } }
  body { margin:0; padding:48px 16px; background:var(--bg); color:var(--fg); font:16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { max-width:560px; margin:0 auto; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:28px; }
  h1 { font-size:24px; margin:0 0 12px; }
  p, li { color:var(--muted); }
  b { color:var(--fg); }
  textarea { width:100%; box-sizing:border-box; font:13px/1.4 ui-monospace, Menlo, monospace; padding:10px; border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--fg); resize:none; word-break:break-all; }
  button { font:inherit; padding:8px 16px; border:0; border-radius:8px; background:var(--accent); color:#fff; cursor:pointer; }
</style></head><body><main><h1>${escape(title)}</h1>${body}</main></body></html>`;
  return new Response(html, { status, headers: { ...SECURITY_HEADERS, "Content-Type": "text/html; charset=utf-8" } });
}
