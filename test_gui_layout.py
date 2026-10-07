"""Off-screen Tk geometry checks; no media is loaded and no desktop input is used."""
import os
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import local_transcriber as app


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class LayoutTests(unittest.TestCase):
    def check_layout(self, scaling):
        real_tk = tk.Tk

        def make_root():
            root = real_tk()
            root.tk.call('tk', 'scaling', scaling)
            return root

        def inspect(root):
            # Place our test window outside the desktop; never capture other apps.
            root.geometry('900x600+10000+10000')
            root.update()
            root.update_idletasks()
            widgets = list(descendants(root))
            buttons = {str(w.cget('text')): w for w in widgets if isinstance(w, ttk.Button)}
            for text in ('开始转成文字', '停止', '打开输出目录', '打开主要结果'):
                self.assertIn(text, buttons)
                self.assert_inside(root, buttons[text])
            self.assertIn('选择视频 / 音频文件', buttons)
            self.assertIn('选择素材文件夹', buttons)
            # The active task page must swap the visible settings and footer action.
            audio_tab = next(w for w in widgets if isinstance(w, ttk.Button) and str(w.cget('text')) == '提取音频')
            audio_tab.invoke()
            root.update_idletasks()
            active_button = next(w for w in descendants(root) if isinstance(w, ttk.Button)
                                 and str(w.cget('text')) == '开始提取音频')
            self.assert_inside(root, active_button)
            app_window = next(w for w in widgets if isinstance(w, tk.Canvas))
            self.assertTrue(app_window.winfo_ismapped())
            form_scroll = next(w for w in widgets if isinstance(w, ttk.Scrollbar))
            self.assertEqual(form_scroll.cget('style'), 'Visible.Vertical.TScrollbar')
            scrollbar_style = ttk.Style(root)
            self.assertEqual(scrollbar_style.lookup(form_scroll.cget('style'), 'background'), '#B8795F')
            self.assertGreaterEqual(int(scrollbar_style.lookup(form_scroll.cget('style'), 'width')), 14)
            self.assertNotEqual(
                scrollbar_style.lookup(form_scroll.cget('style'), 'background'),
                scrollbar_style.lookup(form_scroll.cget('style'), 'troughcolor'),
            )
            transcribe_tab = next(w for w in widgets if isinstance(w, ttk.Button) and str(w.cget('text')) == '转成文字')
            transcribe_tab.invoke()
            root.update_idletasks()
            self.assertTrue(any(isinstance(w, ttk.LabelFrame) and '转成文字' in str(w.cget('text'))
                                and w.winfo_ismapped() for w in descendants(root)))
            progress = next(w for w in widgets if isinstance(w, ttk.Progressbar))
            self.assert_inside(root, progress)
            log_toggle = next(w for w in widgets if isinstance(w, ttk.Button)
                              and '详细日志' in str(w.cget('text')))
            log_toggle.invoke()
            root.update_idletasks()
            self.assert_inside(root, progress)
            self.assert_inside(root, log_toggle)
            self.assert_inside(root, buttons['开始转成文字'])
            advanced_toggle = next(w for w in widgets if isinstance(w, ttk.Button)
                                   and '高级设置' in str(w.cget('text')))
            advanced_toggle.invoke()
            root.update_idletasks()
            self.assert_inside(root, progress)
            self.assert_inside(root, buttons['打开输出目录'])
            for callback in root.tk.splitlist(root.tk.call('after', 'info')):
                root.after_cancel(callback)
            root.destroy()

        with patch.object(tk, 'Tk', make_root), patch.object(real_tk, 'mainloop', inspect), \
                patch.dict(os.environ, {'LOCAL_TRANSCRIBER_GUI_SMOKE_TEST': '0'}):
            app.launch_gui()

    def assert_inside(self, root, widget):
        x = widget.winfo_rootx() - root.winfo_rootx()
        y = widget.winfo_rooty() - root.winfo_rooty()
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + widget.winfo_width(), root.winfo_width())
        self.assertLessEqual(y + widget.winfo_height(), root.winfo_height())
        self.assertGreater(widget.winfo_width(), 1, str(widget.cget('text')) if isinstance(widget, ttk.Button) else str(widget))

    def test_default_scale_footer(self):
        self.check_layout(1.333)

    def test_large_scale_footer(self):
        self.check_layout(2.0)


if __name__ == '__main__':
    unittest.main()
