"""Sortero Pro: the ready-to-run app and one-click updates, and the licence key."""
import datetime, webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

from .. import ui, licence, settings, store
from .base import Screen, APP


SUPPORT = ("Sortero is an independent project. Its author paid to build and release it, "
           "with the help of Claude Code. If Sortero has been useful to you, please "
           "consider getting Sortero Pro: it's what keeps Sortero going.")


def _date(ts):
    dt = datetime.datetime.fromtimestamp(ts)
    return f"{dt.day} {dt.strftime('%B %Y')}"


class ProScreen(Screen):
    title = "Sortero Pro"
    summary = ("Sortero is free and open source. Pro gets you the ready-to-run app and "
               "one-click updates.")
    details = ("Anyone can build Sortero from its source code on GitHub, free, with every "
               "feature. Sortero Pro is for everyone who'd rather not: the app ready to run "
               "on Mac, Windows and Linux, and updates you install with one click from "
               "Help → Check for Updates.\n\nPayment happens on Stripe's own secure page, so "
               "Sortero never sees your card. Afterwards you get a licence key and download "
               "links. Paste the key below to turn on updates. A one-time licence includes "
               "every future update; a subscription includes updates while it's active.")

    def build(self):
        self.state = ttk.Frame(self)
        self.state.pack(fill="x", pady=(0, ui.SECTION))
        self.plans = ttk.Frame(self)
        self.plans.pack(fill="x")
        self.support = ui.WrapLabel(self, style="Muted.TLabel", text=SUPPORT)

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

        self.bar.set_more([("Download the app…", self.open_downloads),
                           ("Check for updates now", lambda: self.app.check_updates(quiet=False)),
                           None,
                           ("Check subscription now", self.check),
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
                text = (f"One-click updates are on. Your subscription is confirmed until "
                        f"{_date(s.until)}." if s.until else
                        "One-click updates are on. Your subscription will be confirmed with "
                        "the Sortero server shortly.")
            elif s.kind == "gift":
                text = "You have a gift licence, so one-click updates are on."
            else:
                text = "One-click updates are on, for good. Thank you!"
            ui.Card(self.state, "Sortero Pro is on", text, tone="good").pack(fill="x")
        else:
            ui.Card(self.state, "Updates aren't turned on",
                    s.note or "This copy of Sortero works fully. Add a licence key to "
                              "install new versions with one click.",
                    tone="warn" if s.note else None).pack(fill="x")

        if not s.pro or s.kind == "sub":
            for plan in store.PLANS:
                on_sale = licence.buy_url_ok(plan.get("url"))
                title = plan["name"] + (f"  ·  {plan['price']}" if plan.get("price") else "")
                ui.Card(self.plans, title, plan.get("note", ""),
                        "Buy…" if on_sale else "Not on sale yet",
                        lambda p=plan: self.buy(p)).pack(fill="x", pady=(0, ui.GAP))
        if s.pro:
            self.support.pack_forget()
        elif not self.support.winfo_ismapped():
            self.support.pack(fill="x", pady=(ui.GAP, 0), after=self.plans)
        self.bar.enable("Download the app…", bool(licence.downloads_page()))
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
                                     "afterwards has your licence key and the downloads: "
                                     "paste the key here.")

    def open_downloads(self):
        url = licence.downloads_page()
        if not url:
            messagebox.showinfo(APP, "Downloads aren't set up yet.")
            return
        webbrowser.open(url)

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
                messagebox.showinfo(APP, "Sortero Pro is on. Updates will install with one "
                                         "click. Thank you!")
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
                                        "keeps working; updates stop installing with one "
                                        "click. Keep a copy of your key to use it again."):
            return
        licence.deactivate()
        self.render()
