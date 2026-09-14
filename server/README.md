# Selling Sortero Pro

Sortero's free version changes up to 50 tracks per action; a licence key lifts
that. This folder is the small server that turns a Stripe payment into a key.

```
buyer ── Stripe Payment Link ── pays ──▶ redirected to  /success   shows the key
Stripe ─────────────── webhook ────────▶                /webhook   saves key on the payment
Sortero (subscriptions, every few days) ▶               /check     signed "paid until …"
```

There's no database. Keys are signed with a private key only you and the
server hold; the app has the public half and checks keys offline.

Do everything in Stripe **test mode** first, then repeat the Stripe steps in
live mode.

## 1. The signing key

```bash
python tools/licence_admin.py genkey --install
```

This saves the signing key to `~/.config/sortero/licence-signing-key`, readable
only by you, and writes the public key into `sortero/licence.py` and
`server/wrangler.toml`. **Back the file up in a password manager.** If it's
lost, no new keys can be made; if it leaks, anyone can make keys.

## 2. The Stripe products

In the Stripe dashboard:

1. **Product catalogue → Add product**: "Sortero Pro". Give it a one-time price
   and a recurring price (monthly or yearly, or add both).
2. **Payment Links → New** for each price. On the **After payment** tab choose
   *Don't show confirmation page* and redirect to

   ```
   https://sortero-pro.<your-subdomain>.workers.dev/success?session_id={CHECKOUT_SESSION_ID}
   ```

   Type `{CHECKOUT_SESSION_ID}` exactly like that; Stripe fills it in. You'll get
   the real Worker address in step 3, so you can come back and fix this.
3. Note each link's id (`plink_…`, shown in the link's details) and URL
   (`https://buy.stripe.com/…`).

If you'll sell outside your own country, look at **Stripe Tax**, which can
collect VAT and sales tax on Payment Links.

## 3. The server

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
```

For `STRIPE_SECRET_KEY` use a **restricted key** (Developers → API keys →
Create restricted key) with only: *Checkout Sessions: Read*, *Subscriptions:
Write*, *PaymentIntents: Write*. Wrangler prompts for the value, so it never
lands in your shell history.

## 4. The webhook

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

## 5. The app

Edit `sortero/store.py`:

- `SERVER`: your Worker address
- each plan's `price` (the label shown, e.g. `"$29"`) and `url` (its
  `https://buy.stripe.com/…` link)

## 6. Try it

1. Run Sortero, open **Sortero Pro**, press **Buy…** and pay with Stripe's test
   card `4242 4242 4242 4242`, any future date, any CVC.
2. The page you land on shows a key. Paste it into Sortero and press
   **Activate**.
3. For the subscription, cancel it in the dashboard and choose **More → Check
   subscription now**: Pro stays on until the paid period ends, then turns off.

Then switch Stripe to live mode, repeat steps 2–4 with live links and a live
restricted key, and update `store.py`.

## Your own key

```bash
python tools/licence_admin.py gift --note "Elle"
python tools/licence_admin.py show SRT1.…      # check any key
```

Gift keys never expire and need no network. Make as many as you like.
