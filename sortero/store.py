"""Where Sortero Pro is sold. Fill these in once Stripe and the server are set up.

server/README.md walks through it. Nothing here is secret: Payment Link URLs
and the server's address are public by design.
"""

# The Cloudflare Worker from server/, for example
# "https://sortero-pro.yourname.workers.dev". Subscriptions are checked here.
SERVER = ""

# One entry per Stripe Payment Link. Leave "url" empty to show a plan as not on
# sale yet. Only https://buy.stripe.com/ links are opened.
PLANS = [
    {"id": "life", "name": "Pro, pay once", "price": "",
     "note": "Unlock Pro for good on this and future versions.", "url": ""},
    {"id": "sub", "name": "Pro, subscription", "price": "",
     "note": "Pay as you go and cancel any time.", "url": ""},
]
