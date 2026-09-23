# Selling the Supporter licence

Sortero is free and open source. A **Supporter licence** is a one-time,
pay-what-you-want payment; as a thank-you it brings the ready-to-run app for
Mac, Windows and Linux, plus one-click updates. (The code still calls this
"Sortero Pro".) This folder is the small server that turns a Stripe payment
into a licence key and hands out the builds.

```
buyer ── Stripe Payment Link ── pays ──▶ /success     licence key + download buttons
returning buyer ── pastes key ─────────▶ /downloads   download buttons
Sortero's updater ── key ──────────────▶ /update      newest build for this computer
Sortero (subscriptions, every few days) ▶ /check      signed "paid until …"
Stripe ─────────────── webhook ────────▶ /webhook    saves the key on the payment
```

There's no database. Keys are signed with a private key only you and the
server hold; the app has the public half and checks keys offline. The builds
live as releases on a **private GitHub repository**; the server fetches them
with a read-only token and gives buyers a download link that expires in minutes.

Do everything in Stripe **test mode** first, then repeat the Stripe steps in
live mode.

## 1. The signing key

Already done: `tools/licence_admin.py genkey --install` saved it to
`~/.config/sortero/licence-signing-key` and wrote the public key into
`sortero/licence.py` and `server/wrangler.toml`. **Back that file up in a
password manager.** If it's lost, no new keys can be made; if it leaks, anyone
can make keys.

## 2. The private builds repository

```bash
gh repo create sparkly-quasar/sortero-builds --private --add-readme \
  --description "Sortero Pro builds"
```

Then make two **fine-grained personal access tokens** (GitHub → Settings →
Developer settings → Fine-grained tokens), each with *Repository access: Only
select repositories → sortero-builds*:

| Token | Permission | Goes to |
|---|---|---|
| sortero release uploads | Contents: **Read and write** | the `BUILDS_TOKEN` secret of `sparkly-quasar/sortero` (repo Settings → Secrets and variables → Actions) |
| sortero pro server | Contents: **Read-only** | the Worker, in step 4 |

Tokens expire; set a reminder to renew them. When the upload token lapses the
release workflow fails loudly rather than publishing nothing.

From then on, pushing a `v*` tag builds all three platforms, uploads the zips to
`sortero-builds`, and publishes only the release notes on the public repository.

## 3. The Stripe products

In the Stripe dashboard:

1. **Product catalogue → Add product**: "Sortero Supporter licence". Give it a
   one-time price and choose **Customer chooses price**, with a minimum (e.g.
   $15) and a suggested amount (e.g. $25). No recurring price.
2. **Payment Links → New** for that price. On the **After payment** tab choose
   *Don't show confirmation page* and redirect to

   ```
   https://sortero-pro.<your-subdomain>.workers.dev/success?session_id={CHECKOUT_SESSION_ID}
   ```

   Type `{CHECKOUT_SESSION_ID}` exactly like that; Stripe fills it in. You get
   the real Worker address in step 4, so come back and fix this.
3. Note the link's id (`plink_…`, in the link's details) and URL
   (`https://buy.stripe.com/…`).

If you'll sell outside your own country, look at **Stripe Tax**, which can
collect VAT and sales tax on Payment Links.

## 4. The server

Needs a free Cloudflare account and Node.js.

```bash
cd server
npx wrangler login
```

Put your Payment Link ids in `wrangler.toml` under `PAYMENT_LINKS`, comma
separated. A purchase from any other link gets no key. Then:

```bash
npx wrangler deploy
npx wrangler secret put LICENCE_SIGNING_KEY < ~/.config/sortero/licence-signing-key
npx wrangler secret put STRIPE_SECRET_KEY
npx wrangler secret put GITHUB_TOKEN
```

For `STRIPE_SECRET_KEY` use a **restricted key** (Developers → API keys →
Create restricted key) with only: *Checkout Sessions: Read*, *Subscriptions:
Write*, *PaymentIntents: Write*. `GITHUB_TOKEN` is the read-only token from
step 2. Wrangler prompts for each value, so none land in your shell history.

## 5. The webhook

Stripe dashboard → **Developers → Webhooks → Add endpoint**:

- URL: `https://sortero-pro.<your-subdomain>.workers.dev/webhook`
- Events: `checkout.session.completed` and `checkout.session.async_payment_succeeded`

Copy the endpoint's signing secret (`whsec_…`), then:

```bash
npx wrangler secret put STRIPE_WEBHOOK_SECRET
```

The webhook saves each key into the payment's or subscription's metadata as
`sortero_licence`, so if a buyer loses theirs you can find it in the dashboard
and send it again.

## 6. The app

Edit `sortero/store.py`:

- `SERVER`: your Worker address
- the Supporter licence's `price` (the label shown, e.g.
  `"pay what you want, from $15"`) and `url` (its `https://buy.stripe.com/…` link)

Release a new version so the built app knows where to go.

## 7. Try it

1. Push a tag and check its zips appear on `sortero-builds`.
2. Open **Support Sortero** in the app, press **Buy…** and pay with Stripe's test
   card `4242 4242 4242 4242`, any future date, any CVC.
3. The page you land on shows a key and download buttons. Download a build.
4. Paste the key into Sortero and press **Activate**. **Help → Check for
   Updates…** now offers to install newer versions.
Then switch Stripe to live mode, repeat steps 3–5 with live links and a live
restricted key, and update `store.py`.

## Your own key

```bash
python3 tools/licence_admin.py gift --note "Elle"
python3 tools/licence_admin.py show SRT1.…      # check any key
```

Gift keys never expire, and get downloads and updates like a one-time licence.
