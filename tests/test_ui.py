"""Tables select a run of rows from the keyboard with Shift+↑ and Shift+↓."""
import tkinter as tk
import unittest

from sortero import ui


class ShiftArrows(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"Tk can't open a window here ({e})")
        self.addCleanup(self.root.destroy)
        frame, self.tv = ui.tree(self.root, [("name", "Name", 200)])
        frame.pack()
        for i in range(10):
            self.tv.insert("", "end", iid=str(i), values=(f"Track {i}",))
        self.root.update()

    def click(self, row, shift=False):
        x, y, w, h = self.tv.bbox(row, "#1")
        state = 1 if shift else 0
        self.tv.event_generate("<ButtonPress-1>", x=x + 5, y=y + h // 2, state=state)
        self.tv.event_generate("<ButtonRelease-1>", x=x + 5, y=y + h // 2, state=state)
        self.root.update()

    def key(self, name, times=1):
        for _ in range(times):
            self.tv.event_generate(f"<{name}>")
        self.root.update()

    def test_shift_down_then_back_up(self):
        self.click("2")
        self.key("Shift-Down", 3)
        self.assertEqual(self.tv.selection(), ("2", "3", "4", "5"))
        self.assertEqual(self.tv.focus(), "5")
        self.key("Shift-Up", 2)
        self.assertEqual(self.tv.selection(), ("2", "3"))

    def test_shift_up_past_the_start_flips_the_range(self):
        self.click("5")
        self.key("Shift-Up", 2)
        self.assertEqual(self.tv.selection(), ("3", "4", "5"))
        self.key("Shift-Down", 4)
        self.assertEqual(self.tv.selection(), ("5", "6", "7"))

    def test_stops_at_the_ends(self):
        self.click("8")
        self.key("Shift-Down", 5)
        self.assertEqual(self.tv.selection(), ("8", "9"))

    def test_a_plain_arrow_moves_the_starting_point(self):
        self.click("2")
        self.key("Down", 2)
        self.key("Shift-Down")
        self.assertEqual(self.tv.selection(), ("4", "5"))

    def test_shift_click_still_works_and_keeps_the_start(self):
        self.click("1")
        self.click("4", shift=True)
        self.assertEqual(self.tv.selection(), ("1", "2", "3", "4"))
        self.key("Shift-Down")
        self.assertEqual(self.tv.selection(), ("1", "2", "3", "4", "5"))


if __name__ == "__main__":
    unittest.main()
