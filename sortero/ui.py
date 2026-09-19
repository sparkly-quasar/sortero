"""The shared look for every Sortero window.

Colours come from a light and a dark palette. On macOS the app watches the
system appearance and repaints when it changes; on Windows and Linux Tk doesn't
follow the system theme, so the light palette is used. Backgrounds on macOS use
the system's own dynamic colours so hand-drawn areas match native ttk widgets.

Widgets that set a colour directly register it with paint(), so a switch
between light and dark reaches them too. ttk widgets use the named styles set up
here (Muted.TLabel and friends) and repaint for free.

The module attributes (TITLE, MUTED...) hold safe fallbacks until setup() runs,
so a dialog built without the main window still draws.
"""
import sys
import tkinter as tk
from tkinter import ttk, font as tkfont

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# fonts - replaced by named fonts in setup()
TITLE = ("Helvetica", 18, "bold")
HEADING = ("Helvetica", 13, "bold")
BODY = ("Helvetica", 12)
SMALL = ("Helvetica", 11)
MONO = ("Menlo", 11)

# spacing
GAP = 8           # between related controls
SECTION = 16      # between sections
PAD = 24          # screen edge

PALETTES = {
    "light": {
        "text": "#1d1d1f", "muted": "#6e6e73", "faint": "#8e8e93",
        "warn": "#9a5b00", "good": "#1e7a46", "bad": "#b3261e", "link": "#0a62c9",
        "banner": "#9a5b00", "on_banner": "#ffffff",
        "window": "#f5f5f7", "sidebar": "#e8e8ec", "card": "#ffffff",
        "border": "#d6d6db", "select": "#cddcf2", "on_select": "#1d1d1f",
    },
    "dark": {
        "text": "#f2f2f7", "muted": "#a1a1a6", "faint": "#7c7c80",
        "warn": "#f0a53a", "good": "#5fcf8e", "bad": "#ff7b72", "link": "#5aa9ff",
        "banner": "#8a5a00", "on_banner": "#ffffff",
        "window": "#1e1e1e", "sidebar": "#262626", "card": "#2c2c2e",
        "border": "#3d3d40", "select": "#3f638b", "on_select": "#ffffff",
    },
}
# macOS dynamic colours: they follow the appearance by themselves
MAC_DYNAMIC = {
    "window": "systemWindowBackgroundColor",
    "sidebar": "systemWindowBackgroundColor1",
    # Cards share the window colour: native buttons paint the window colour
    # around themselves, so a lighter card leaves a box around every button.
    "card": "systemWindowBackgroundColor",
    "select": "systemSelectedTextBackgroundColor",
    "text": "systemTextColor",
}

_root = None
_mode = "light"
_painted = {}          # {widget path: (widget, {option: role})}
_fonts = []            # keep named fonts alive


def dark():
    return _mode == "dark"


def c(role):
    """The current colour for a role such as 'muted' or 'card'."""
    if IS_MAC and role in MAC_DYNAMIC:
        return MAC_DYNAMIC[role]
    if not IS_MAC and role in ("window", "card") and _root is not None:
        bg = ttk.Style(_root).lookup(".", "background")
        if bg:
            return bg
    return PALETTES[_mode][role]


def _detect(root):
    if not IS_MAC:
        return "light"
    try:
        return "dark" if int(root.tk.call("tk::unsupported::MacWindowStyle",
                                          "isdark", ".")) else "light"
    except (tk.TclError, ValueError):
        return "light"


def setup(root):
    """Create fonts and styles. Call once, straight after creating the Tk root."""
    global _root, _mode, TITLE, HEADING, BODY, SMALL, MONO
    _root = root
    _mode = _detect(root)
    base = tkfont.nametofont("TkDefaultFont", root=root).actual()
    fam, size = base["family"], abs(int(base["size"])) or 12
    mono = "Menlo" if IS_MAC else ("Consolas" if IS_WIN else "DejaVu Sans Mono")
    for name, kw in (("SorteroTitle", dict(family=fam, size=size + 8, weight="bold")),
                     ("SorteroHeading", dict(family=fam, size=size + 3, weight="bold")),
                     ("SorteroBody", dict(family=fam, size=size)),
                     ("SorteroSmall", dict(family=fam, size=max(size - 1, 9))),
                     ("SorteroMono", dict(family=mono, size=size + 1))):
        try:
            f = tkfont.Font(root=root, name=name, exists=True)
            f.configure(**kw)
        except tk.TclError:
            f = tkfont.Font(root=root, name=name, **kw)
        _fonts.append(f)
    TITLE, HEADING, BODY, SMALL, MONO = ("SorteroTitle", "SorteroHeading", "SorteroBody",
                                         "SorteroSmall", "SorteroMono")
    _style()
    if IS_MAC:
        root.after(2000, _watch)


def _style():
    s = ttk.Style(_root)
    s.configure("Title.TLabel", font=TITLE)
    s.configure("Heading.TLabel", font=HEADING)
    s.configure("Muted.TLabel", foreground=c("muted"))
    s.configure("Small.TLabel", foreground=c("muted"), font=SMALL)
    s.configure("Warn.TLabel", foreground=c("warn"))
    s.configure("Good.TLabel", foreground=c("good"))
    s.configure("Bad.TLabel", foreground=c("bad"))
    s.configure("Link.TLabel", foreground=c("link"))
    s.configure("Treeview", rowheight=int(tkfont.Font(font=BODY).metrics("linespace") * 1.45))


def _watch():
    global _mode
    if _root is None:
        return
    try:
        mode = _detect(_root)
        if mode != _mode:
            _mode = mode
            _style()
            repaint()
        _root.after(2000, _watch)
    except tk.TclError:
        pass                # window gone


def paint(widget, **roles):
    """Colour a plain tk widget by role, and keep it right across theme changes.

    paint(label, bg="card", fg="muted")
    """
    opts = {}
    for opt, role in roles.items():
        opts[{"bg": "background", "fg": "foreground"}.get(opt, opt)] = role
    key = str(widget)
    if key in _painted:
        _painted[key][1].update(opts)      # re-painting (say, a selected row) replaces
    else:
        _painted[key] = (widget, opts)
    _apply(widget, opts)
    return widget


def _apply(widget, opts):
    for opt, role in opts.items():
        try:
            widget.configure(**{opt: c(role)})
        except tk.TclError:
            pass


def repaint():
    for key, (w, opts) in list(_painted.items()):
        try:
            if w.winfo_exists():
                _apply(w, opts)
                continue
        except tk.TclError:
            pass
        del _painted[key]


# ---------------------------------------------------------------- widgets
class WrapLabel(ttk.Label):
    """A label that re-wraps its text to whatever width it's given."""

    def __init__(self, parent, **kw):
        kw.setdefault("justify", "left")
        super().__init__(parent, **kw)
        self.bind("<Configure>", lambda e: self.configure(wraplength=max(e.width - 4, 200)))


def link(parent, text, command, style="Link.TLabel"):
    """Text that acts as a button, for quiet secondary actions."""
    lab = ttk.Label(parent, text=text, style=style,
                    cursor="pointinghand" if IS_MAC else "hand2")
    lab.bind("<Button-1>", lambda e: command())
    return lab


class Header(ttk.Frame):
    """Screen title, a one-line summary, and an ⓘ toggle for the longer version."""

    def __init__(self, parent, title, summary="", details=None, crumb=None):
        super().__init__(parent)
        if crumb:
            text, command = crumb
            link(self, "‹ " + text, command).pack(anchor="w", pady=(0, 2))
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.title = ttk.Label(row, text=title, style="Title.TLabel")
        self.title.pack(side="left")
        self.details_text = details
        self.info = None
        if details:
            self.info = link(row, "ⓘ", self.toggle, style="Muted.TLabel")
            self.info.configure(font=HEADING)
            self.info.pack(side="left", padx=(10, 0), pady=(4, 0))
        self.summary = WrapLabel(self, text=summary, style="Muted.TLabel")
        self.summary.pack(fill="x", pady=(2, 0))
        self.details = WrapLabel(self, text=details or "")
        self.open = False

    def toggle(self):
        self.open = not self.open
        if self.open:
            self.details.pack(fill="x", pady=(GAP, 0), after=self.summary)
            self.info.configure(style="Link.TLabel")
        else:
            self.details.pack_forget()
            self.info.configure(style="Muted.TLabel")

    def set_summary(self, text):
        self.summary.configure(text=text)


class ActionBar(ttk.Frame):
    """Bottom row of a screen: status on the left, More ▾ and one primary button right."""

    def __init__(self, parent):
        super().__init__(parent)
        # The right-hand side is packed first so its width is reserved before
        # the left side expands into what remains. Packed the other way round,
        # a long label or a wide control on the left squeezes the primary
        # button down to a sliver, which is how a long card explanation once
        # made a button disappear entirely.
        self.right = ttk.Frame(self)
        self.right.pack(side="right")
        self.left = ttk.Frame(self)
        self.left.pack(side="left", fill="x", expand=True)
        self.primary = ttk.Button(self.right, default="active")
        self.more_btn = None
        self.menu = None
        self._items = {}

    def set_primary(self, text=None, command=None, state="normal"):
        if text is None:
            self.primary.pack_forget()
            return
        self.primary.configure(text=text, command=command or (lambda: None), state=state)
        if not self.primary.winfo_ismapped():
            self.primary.pack(side="right")

    def set_more(self, items):
        """items: [(label, command) | None for a separator]"""
        if self.more_btn is None:
            self.more_btn = ttk.Menubutton(self.right, text="More")
            self.menu = tk.Menu(self.more_btn, tearoff=0)
            self.more_btn.configure(menu=self.menu)
            self.more_btn.pack(side="right", padx=(0, GAP))
        self.menu.delete(0, "end")
        self._items = {}
        for item in items:
            if item is None:
                self.menu.add_separator()
            else:
                label, command = item
                self.menu.add_command(label=label, command=command)
                self._items[label] = self.menu.index("end")

    def enable(self, label, on=True):
        if label in self._items:
            self.menu.entryconfigure(self._items[label], state="normal" if on else "disabled")


class Card(tk.Frame):
    """A raised row: a bold line, a quieter explanation, and one button."""

    def __init__(self, parent, title, text="", button=None, command=None,
                 tone=None, link_text=None, link_command=None):
        super().__init__(parent, highlightthickness=1, bd=0, padx=14, pady=10)
        paint(self, bg="card", highlightbackground="border", highlightcolor="border")
        # The button is packed before the body on purpose. The packer hands out
        # space in packing order, and the body expands, so packing it first let
        # a long explanation claim the whole card and squeeze the button down to
        # a sliver - four pixels wide in the smallest window, which reads as no
        # button at all. Reserving the button's width first leaves the body the
        # rest, which is also what lets the explanation wrap.
        if button:
            ttk.Button(self, text=button, command=command).pack(side="right",
                                                                padx=(12, 0))
        body = tk.Frame(self)
        paint(body, bg="card")
        body.pack(side="left", fill="x", expand=True)
        t = tk.Label(body, text=title, font=HEADING, anchor="w", justify="left")
        paint(t, bg="card", fg=tone or "text")
        t.pack(fill="x")
        if text:
            d = tk.Label(body, text=text, font=BODY, anchor="w", justify="left")
            paint(d, bg="card", fg="muted")
            d.pack(fill="x", pady=(2, 0))
            d.bind("<Configure>", lambda e: d.configure(wraplength=max(e.width - 4, 200)))
        if link_text:
            lk = tk.Label(body, text=link_text, font=BODY, anchor="w",
                          cursor="pointinghand" if IS_MAC else "hand2")
            paint(lk, bg="card", fg="link")
            lk.pack(fill="x", pady=(4, 0))
            lk.bind("<Button-1>", lambda e: link_command())


def tree(parent, columns, height=14, selectmode="extended"):
    """columns: [(id, heading, width)] - a scrolling table. Returns (frame, tv)."""
    frame = ttk.Frame(parent)
    ids = [cid for cid, _, _ in columns]
    tv = ttk.Treeview(frame, columns=ids, show="headings", height=height,
                      selectmode=selectmode)
    for cid, heading, width in columns:
        tv.heading(cid, text=heading, anchor="w")
        tv.column(cid, width=width, anchor="w", stretch=width >= 150)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    # Scrollbar first. A table asks for the sum of its column widths - 840 to
    # 986px here - which is wider than the screen gives it, so packing the
    # table first left nothing for the scrollbar: clipped in the default
    # window, gone entirely in the smallest one, on every table in the app.
    sb.pack(side="right", fill="y")
    tv.pack(side="left", fill="both", expand=True)
    return frame, tv


def plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"
