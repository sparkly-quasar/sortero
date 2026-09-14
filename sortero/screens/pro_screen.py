"""Sortero Pro: what it is, how to buy it, and where the licence key goes."""
import datetime, webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui, licence, settings, store
from .base import Screen, APP


def _date(ts):
    dt = datetime.datetime.fromtimestamp(ts)
    return f"{dt.day} {dt.strftime('%B %Y')}"


class ProScreen(Screen):
    title = "Sortero Pro"
    summary = (f"The free version changes up to {licence.FREE_CAP} tracks at a time. "
               "Pro takes the limit away.")
    details = ("Everything in Sortero works in both versions. Previews, undo and the "
               "safety net are never limited; the only difference is how many tracks a "
               "single action can change.\n\nPayment happens on Stripe's own secure page, "
               "so Sortero never sees your card. Afterwards you get a licence key to paste "
               "below. A one-time licence works offline for good. A subscription is "
               "checked with the Sortero server every few days and keeps working offline "
               "for two weeks at a time.")

    def build(self):
        self.state = ttk.Frame(self)
        self.state.pack(fill="x", pady=(0, ui.SECTION))
        self.plans = ttk.Frame(self)
        self.plans.pack(fill="x")

        ttk.Label(self, text="Licence key", style="Heading.TLabel").pack(
            anchor="w", pady=(ui.SECTION + 4, 4))
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.key_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.key_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.activate())
        ttk.Button(row, text="Activate", command=self.activate).pack(side="left",
                                                                     padx=(ui.GAP, 0))
        self.key_note = ui.WrapLabel(self, style="Muted.TLabel",
                                     text="Paste the key from the page Stripe sends you to "
                                          "after paying. It starts with SRT1.")
        self.key_note.pack(fill="x", pady=(4, 0))

        self.bar.set_more([("Check subscription now", self.check),
                           None,
                           ("Remove licence from this computer…", self.remove)])
        self.render()

    def shown(self):
        self.render()

    def render(self):
        s = licence.status()
        for w in self.state.winfo_children() + self.plans.winfo_children():
            w.destroy()
        if s.pro:
            if s.kind == "sub":
                text = (f"Your subscription is confirmed until {_date(s.until)}."
                        if s.until else "Your subscription will be confirmed with the "
                                        "Sortero server shortly.")
            elif s.kind == "gift":
                text = "You have a gift licence. There's no limit on any action."
            else:
                text = "Yours for good. There's no limit on any action. Thank you!"
            ui.Card(self.state, "Sortero Pro is on", text, tone="good").pack(fill="x")
        else:
            ui.Card(self.state, "You're using the free version",
                    s.note or f"Each action changes up to {licence.FREE_CAP} tracks. Run "
                              "it again for the next batch.",
                    tone="warn" if s.note else None).pack(fill="x")

        if not s.pro or s.kind == "sub":
            for plan in store.PLANS:
                on_sale = licence.buy_url_ok(plan.get("url"))
                title = plan["name"] + (f"  ·  {plan['price']}" if plan.get("price") else "")
                ui.Card(self.plans, title, plan.get("note", ""),
                        "Buy…" if on_sale else "Not on sale yet",
                        lambda p=plan: self.buy(p)).pack(fill="x", pady=(0, ui.GAP))
        self.bar.enable("Check subscription now", s.kind == "sub")
        self.bar.enable("Remove licence from this computer…",
                        bool(settings.get("licence_key")))

    def buy(self, plan):
        url = plan.get("url")
        if not licence.buy_url_ok(url):
            messagebox.showinfo(APP, "This plan isn't on sale yet.")
            return
        webbrowser.open(url)
        self.key_note.configure(text="Finish paying in your browser. The page you land on "
                                     "afterwards shows your licence key: paste it here.")

    def activate(self):
        key = licence.normalise(self.key_var.get())
        if not key:
            messagebox.showinfo(APP, "Paste your licence key first.")
            return

        def work(progress, log):
            try:
                return ("ok", licence.activate(key))
            except licence.LicenceError as e:
                return ("error", str(e))

        def done(res):
            if res[0] == "error":
                messagebox.showwarning(APP, res[1])
                return
            self.key_var.set("")
            self.render()
            if res[1].pro:
                messagebox.showinfo(APP, "Sortero Pro is on. Thank you!")
            else:
                messagebox.showwarning(APP, res[1].note or "That key didn't turn Pro on.")

        self.app.task.run(work, done, "Checking licence")

    def check(self):
        def work(progress, log):
            try:
                return ("ok", licence.refresh())
            except licence.LicenceError as e:
                return ("error", str(e))

        def done(res):
            self.render()
            if res[0] == "error":
                messagebox.showwarning(APP, res[1])
            else:
                messagebox.showinfo(APP, "Subscription checked.")

        self.app.task.run(work, done, "Checking subscription")

    def remove(self):
        if not messagebox.askyesno(APP, "Remove the licence from this computer?\n\nSortero "
                                        "goes back to the free version. Keep a copy of your "
                                        "key if you want to use it again."):
            return
        licence.deactivate()
        self.render()
