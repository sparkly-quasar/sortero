"""Where the Supporter licence is sold. Fill these in once Stripe and the server are set up.

server/README.md walks through it. Nothing here is secret: Payment Link URLs
and the server's address are public by design.
"""

# The Cloudflare Worker from server/, for example
# "https://sortero-pro.yourname.workers.dev". Updates, downloads and
# subscription checks all go through it.
SERVER = "https://sortero-pro.habituatingtowholeness.workers.dev"

# One entry per Stripe Payment Link. Leave "url" empty to show a plan as not on
# sale yet. Only https://buy.stripe.com/ links are opened. The Supporter licence
# is a one-time, pay-what-you-want price; "price" is only the label shown.
PLANS = [
    {"id": "life", "name": "Supporter licence", "price": "pay what you want, from $15",
     "note": "One payment, no subscription. Includes the ready-to-run app for Mac, "
             "Windows and Linux, and one-click updates for good.",
     "url": "https://buy.stripe.com/3cI4gA7SQd5FeuQfPy83C00"},
]
