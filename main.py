import os, json, time, threading, datetime as dt
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

FIELDS = [("KOTAK_CONSUMER_KEY", "Consumer key"), ("KOTAK_MOBILE", "Mobile (+91...)"), ("KOTAK_UCC", "UCC"),
          ("KOTAK_MPIN", "MPIN"), ("KOTAK_TOTP_SECRET", "TOTP secret")]

class Root(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(orientation="vertical", padding=8, spacing=6, **kw)
        self.app, self.stop, self.inputs = app, False, {}
        saved = app.load()
        self.form = BoxLayout(orientation="vertical", spacing=4, size_hint_y=None, height=330)
        for k, hint in FIELDS:
            t = TextInput(text=saved.get(k, ""), hint_text=hint, multiline=False, password=k in ("KOTAK_MPIN", "KOTAK_TOTP_SECRET"))
            self.inputs[k] = t; self.form.add_widget(t)
        self.index = Spinner(text=saved.get("INDEX", "NIFTY"), values=("NIFTY", "BANKNIFTY"))
        self.form.add_widget(self.index)
        self.add_widget(self.form)
        row = BoxLayout(size_hint_y=None, height=48, spacing=6)
        for txt, fn in (("Call now", self.call_now), ("Run today (9:20 + tracker)", self.run_day), ("Stop", self.stop_all)):
            row.add_widget(Button(text=txt, on_release=fn))
        self.add_widget(row)
        self.verdict = Label(text="Not started", font_size="22sp", size_hint_y=None, height=60)
        self.add_widget(self.verdict)
        self.log = Label(text="", size_hint_y=None, halign="left", valign="top", text_size=(Window.width - 20, None))
        self.log.bind(texture_size=lambda i, s: setattr(i, "height", s[1]))
        sv = ScrollView(); sv.add_widget(self.log); self.add_widget(sv)

    def say(self, msg, big=None):
        def f(_):
            self.log.text = f"{dt.datetime.now():%H:%M} {msg}\n" + self.log.text
            if big: self.verdict.text = big
        Clock.schedule_once(f)
        if big:
            try:
                from plyer import notification
                notification.notify(title="OI Desk", message=big, timeout=10)
            except Exception: pass

    def prepare(self):
        cfg = {k: t.text.strip() for k, t in self.inputs.items()}; cfg["INDEX"] = self.index.text
        self.app.save(cfg); os.environ.update(cfg)
        os.chdir(self.app.user_data_dir)
        import oi_desk
        return oi_desk

    def call_now(self, *a): self.start(False)
    def run_day(self, *a): self.start(True)
    def stop_all(self, *a): self.stop = True; self.say("Stopping...", "Stopped")

    def start(self, full):
        self.stop = False
        try: od = self.prepare()
        except Exception as e: return self.say(f"Setup error: {e}")
        threading.Thread(target=self.work, args=(od, full), daemon=True).start()

    def work(self, od, full):
        try:
            self.say("Logging in..."); c = od.login(); self.say("Logged in.")
            if full:
                t = dt.datetime.combine(dt.date.today(), dt.time(9, 20))
                while dt.datetime.now() < t and not self.stop:
                    self.say("Waiting for 9:20...") if dt.datetime.now().second < 6 else None; time.sleep(5)
            if self.stop: return
            a, exp, heads = od.snapshot(c)
            self.say(f"{od.strike_line(a)} | PCR {a['pcr']:.2f} S {a['sup']:.0f} R {a['res']:.0f} spot {a['spot']:.0f}",
                     f"{a['bias']} ({a['conf']}%)")
            for h in heads: self.say("news: " + h[:90])
            if not full: return
            last = ""
            while dt.datetime.now().time() < dt.time(15, 15) and not self.stop:
                for _ in range(36):
                    if self.stop: return
                    time.sleep(5)
                try:
                    n, exp, heads = od.snapshot(c); v, f = od.check(a, n)
                    self.say(f"spot {n['spot']:.0f} {v} {'; '.join(f)}", v if v != last else None); last = v
                except Exception as e: self.say(f"Re-check failed (session may have expired): {e}")
        except Exception as e:
            self.say(f"Error: {e}", "Error")

class OIDeskApp(App):
    def build(self): return Root(self)
    def path(self): return os.path.join(self.user_data_dir, "settings.json")
    def load(self):
        try: return json.load(open(self.path()))
        except Exception: return {}
    def save(self, cfg):
        os.makedirs(self.user_data_dir, exist_ok=True); json.dump(cfg, open(self.path(), "w"))
        import shutil; d = os.path.join(self.user_data_dir, "oi_desk.py")
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oi_desk.py")
        if not os.path.exists(d): shutil.copy(src, d)
        import sys
        if self.user_data_dir not in sys.path: sys.path.insert(0, self.user_data_dir)

if __name__ == "__main__":
    OIDeskApp().run()
