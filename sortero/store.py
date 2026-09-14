"""Where Sortero Pro is sold. Fill these in once Stripe and the server are set up.

server/README.md walks through it. Nothing here is secret: Payment Link URLs
and the server's address are public by design.
"""

# The Cloudflare Worker from server/, for example
# "https://sortero-pro.yourname.workers.dev". Updates, downloads and
# subscription checks all go through it.
SERVER = ""

# One entry per Stripe Payment Link. Leave "url" empty to show a plan as not on
# sale yet. Only https://buy.stripe.com/ links are opened.
PLANS = [
    {"id": "life", "name": "Pro, pay once", "price": "",
     "note": "The ready-to-run app for Mac, Windows and Linux, with every future update.",
     "url": ""},
    {"id": "sub", "name": "Pro, subscription", "price": "",
     "note": "The ready-to-run app and its updates, for as long as you subscribe.",
     "url": ""},
]
