"""로또 번호 생성기 (Windows) — 실행 파일 진입점.

- 지금까지 1등으로 나온 조합을 제외하고 번호 생성 (포함할 번호 지정, 1~5게임)
- 6개 번호가 과거 당첨번호였는지 확인
- 가장 많이 나온 번호 TOP 6
- 토요일 오후 8시 이후 새 회차 자동 업데이트
- 카카오톡 '나에게 보내기'
"""

import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import lotto_core as core
from kakao import KakaoClient, KakaoError

APP_TITLE = "로또 번호 생성기"
FONT = "Malgun Gothic"
UPDATE_CHECK_MS = 5 * 60 * 1000          # 5분마다 새 회차가 나올 시간인지 확인

BG = "#F4F5F7"
CARD = "#FFFFFF"
SIDEBAR = "#1E2A43"
SIDEBAR_SUB = "#9AA6BD"
SIDEBAR_LINE = "#2E3B57"
TEXT = "#1F2937"
SUBTEXT = "#6B7280"
ACCENT = "#E8453C"
ACCENT_DARK = "#C9362E"
KAKAO = "#FEE500"
OK = "#15803D"
WARN = "#B91C1C"

GAME_LABELS = "ABCDE"
WEEKDAYS = "월화수목금토일"


def ball_color(n):
    if n <= 10:
        return "#F5B800"
    if n <= 20:
        return "#4FB3E8"
    if n <= 30:
        return "#F06262"
    if n <= 40:
        return "#9CA3AF"
    return "#8CC63F"


def short_date(d):
    return "%d/%d(%s)" % (d.month, d.day, WEEKDAYS[d.weekday()])


# ====================================================================== 공용 위젯
class Scaler:
    def __init__(self, root):
        try:
            self.k = max(1.0, root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self.k = 1.0

    def __call__(self, v):
        return int(round(v * self.k))


def draw_ball(cv, x, y, r, n, active=True, tags=(), size=None):
    if active:
        cv.create_oval(x - r, y - r, x + r, y + r, fill=ball_color(n), outline="", tags=tags)
        color, weight = "#FFFFFF", "bold"
    else:
        cv.create_oval(x - r, y - r, x + r, y + r, fill="#FFFFFF", outline="#CBD2DC", width=1, tags=tags)
        color, weight = "#4B5563", "normal"
    cv.create_text(x, y, text=str(n), fill=color, font=(FONT, size or 10, weight), tags=tags)


class NumberGrid(tk.Canvas):
    """1~45 번호를 눌러서 고르는 판."""
    COLS = 9

    def __init__(self, master, sc, max_select, on_change=None, on_limit=None):
        self.sc, self.max_select = sc, max_select
        self.on_change, self.on_limit = on_change, on_limit
        self.cell, self.r = sc(40), sc(16)
        rows = (45 + self.COLS - 1) // self.COLS
        super().__init__(master, width=self.cell * self.COLS + sc(8), height=self.cell * rows + sc(8),
                         bg=CARD, highlightthickness=0, cursor="hand2")
        self.sel = []
        self.bind("<Button-1>", self._click)
        self.redraw()

    def _pos(self, n):
        i = n - 1
        return (self.sc(4) + (i % self.COLS) * self.cell + self.cell // 2,
                self.sc(4) + (i // self.COLS) * self.cell + self.cell // 2)

    def redraw(self):
        self.delete("all")
        for n in core.NUMBERS:
            x, y = self._pos(n)
            draw_ball(self, x, y, self.r, n, n in self.sel, tags=("n%d" % n,), size=10)

    def _click(self, e):
        for item in self.find_overlapping(e.x - 1, e.y - 1, e.x + 1, e.y + 1):
            for t in self.gettags(item):
                if t.startswith("n") and t[1:].isdigit():
                    self.toggle(int(t[1:]))
                    return

    def toggle(self, n):
        if n in self.sel:
            self.sel.remove(n)
        elif len(self.sel) >= self.max_select:
            self.bell()
            if self.on_limit:
                self.on_limit()
            return
        else:
            self.sel.append(n)
        self.redraw()
        if self.on_change:
            self.on_change()

    @property
    def selected(self):
        return sorted(self.sel)

    def set(self, nums):
        self.sel = list(nums)[: self.max_select]
        self.redraw()
        if self.on_change:
            self.on_change()

    def clear(self):
        self.set([])


# ====================================================================== 메인 창
class App:
    def __init__(self, root):
        self.root = root
        self.sc = Scaler(root)
        self.data = core.LottoData()
        self.kakao = KakaoClient()
        self.settings = core.read_json("settings.json", {})
        self.last_generated = core.read_json("last_generated.json", None)
        self.current_sets = []
        self.updating = False
        self.kakao_busy = False
        self.jobs = queue.Queue()

        root.title(APP_TITLE)
        root.configure(bg=BG)
        root.geometry("%dx%d" % (self.sc(1060), self.sc(720)))
        root.minsize(self.sc(980), self.sc(660))
        self._styles()

        self.sidebar = tk.Frame(root, bg=SIDEBAR, width=self.sc(290))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        main = tk.Frame(root, bg=BG)
        main.pack(side="left", fill="both", expand=True, padx=self.sc(18), pady=self.sc(14))

        self._build_sidebar()
        self.nb = ttk.Notebook(main)
        self.nb.pack(fill="both", expand=True)
        self._build_generate_tab()
        self._build_check_tab()
        self._build_kakao_tab()

        self.refresh_stats()
        root.after(100, self._pump)
        root.after(800, lambda: self.start_update(manual=False))
        root.after(UPDATE_CHECK_MS, self._periodic_check)

    # ------------------------------------------------------------ 스타일
    def _styles(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=(FONT, 10), background=BG, foreground=TEXT)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", font=(FONT, 11), padding=(self.sc(18), self.sc(8)),
                     background="#E5E7EB", foreground=SUBTEXT, borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", TEXT)])
        st.configure("Card.TFrame", background=CARD)
        st.configure("TButton", font=(FONT, 10), padding=(self.sc(12), self.sc(6)),
                     background="#EEF0F3", borderwidth=1, relief="flat")
        st.map("TButton", background=[("active", "#E2E5EA"), ("disabled", "#F3F4F6")])
        st.configure("Accent.TButton", font=(FONT, 11, "bold"), foreground="#FFFFFF", background=ACCENT,
                     padding=(self.sc(20), self.sc(8)))
        st.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#F2A9A4")])
        st.configure("Kakao.TButton", font=(FONT, 10, "bold"), foreground="#191919", background=KAKAO)
        st.map("Kakao.TButton", background=[("active", "#F5D800"), ("disabled", "#FFF6A8")])
        st.configure("Side.TButton", font=(FONT, 10), foreground="#FFFFFF", background="#33415F",
                     borderwidth=0)
        st.map("Side.TButton", background=[("active", "#3F4E70"), ("disabled", "#2A3650")])
        st.configure("Game.Toolbutton", font=(FONT, 10, "bold"), padding=(self.sc(12), self.sc(5)),
                     background="#EEF0F3")
        st.map("Game.Toolbutton", background=[("selected", TEXT), ("active", "#E2E5EA")],
               foreground=[("selected", "#FFFFFF")])
        st.configure("TCheckbutton", background=CARD, font=(FONT, 10))
        st.configure("TEntry", padding=self.sc(4))

    def label(self, parent, text="", size=10, weight="normal", fg=TEXT, bg=CARD, **kw):
        return tk.Label(parent, text=text, font=(FONT, size, weight), fg=fg, bg=bg, **kw)

    # ------------------------------------------------------------ 사이드바
    def _build_sidebar(self):
        sb, sc = self.sidebar, self.sc
        pad = sc(20)
        self.label(sb, "로또 6/45", 11, fg=SIDEBAR_SUB, bg=SIDEBAR).pack(anchor="w", padx=pad, pady=(sc(22), 0))
        self.label(sb, "번호 생성기", 18, "bold", fg="#FFFFFF", bg=SIDEBAR).pack(anchor="w", padx=pad)

        tk.Frame(sb, bg=SIDEBAR_LINE, height=1).pack(fill="x", padx=pad, pady=sc(16))
        self.label(sb, "가장 많이 나온 번호 TOP 6", 11, "bold", fg="#FFFFFF", bg=SIDEBAR).pack(anchor="w", padx=pad)
        self.top_caption = self.label(sb, "", 9, fg=SIDEBAR_SUB, bg=SIDEBAR)
        self.top_caption.pack(anchor="w", padx=pad, pady=(sc(2), sc(8)))
        self.top_cv = tk.Canvas(sb, width=sc(250), height=sc(6 * 36), bg=SIDEBAR, highlightthickness=0)
        self.top_cv.pack(anchor="w", padx=pad)

        tk.Frame(sb, bg=SIDEBAR_LINE, height=1).pack(fill="x", padx=pad, pady=sc(16))
        self.recent_title = self.label(sb, "", 11, "bold", fg="#FFFFFF", bg=SIDEBAR)
        self.recent_title.pack(anchor="w", padx=pad)
        self.recent_cv = tk.Canvas(sb, width=sc(250), height=sc(40), bg=SIDEBAR, highlightthickness=0)
        self.recent_cv.pack(anchor="w", padx=pad, pady=(sc(8), 0))

        bottom = tk.Frame(sb, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=pad, pady=sc(18))
        self.next_label = self.label(bottom, "", 9, fg=SIDEBAR_SUB, bg=SIDEBAR, justify="left")
        self.next_label.pack(anchor="w")
        self.update_label = self.label(bottom, "", 9, fg=SIDEBAR_SUB, bg=SIDEBAR, justify="left",
                                       wraplength=sc(250))
        self.update_label.pack(anchor="w", pady=(sc(4), sc(10)))
        self.update_btn = ttk.Button(bottom, text="당첨번호 업데이트", style="Side.TButton",
                                     command=lambda: self.start_update(manual=True))
        self.update_btn.pack(fill="x")

    def refresh_stats(self):
        sc, cv = self.sc, self.top_cv
        total = len(self.data.draws)
        self.top_caption.config(text="1~%d회 · 보너스 번호 제외 · %%는 출현 회차 비율" % self.data.latest_round)
        cv.delete("all")
        top = self.data.top_numbers(6)
        best = top[0][2] if top else 1
        bar_x0, bar_x1 = sc(72), sc(178)
        for i, (n, cnt, pct) in enumerate(top):
            y = sc(18) + i * sc(36)
            cv.create_text(sc(6), y, text=str(i + 1), fill=SIDEBAR_SUB, font=(FONT, 9), anchor="w")
            draw_ball(cv, sc(40), y, sc(14), n, size=10)
            cv.create_rectangle(bar_x0, y - sc(4), bar_x1, y + sc(4), fill=SIDEBAR_LINE, outline="")
            cv.create_rectangle(bar_x0, y - sc(4), bar_x0 + (bar_x1 - bar_x0) * pct / best, y + sc(4),
                                fill=ball_color(n), outline="")
            cv.create_text(sc(248), y - sc(6), text="%.1f%%" % pct, fill="#FFFFFF",
                           font=(FONT, 10, "bold"), anchor="e")
            cv.create_text(sc(248), y + sc(9), text="%d회" % cnt, fill=SIDEBAR_SUB, font=(FONT, 8), anchor="e")

        d = self.data.latest_draw()
        self.recent_cv.delete("all")
        if d:
            dd = core.draw_date(d["round"])
            self.recent_title.config(text="최근 당첨번호 · %d회 %s" % (d["round"], short_date(dd)))
            r, gap = sc(15), sc(33)
            x = sc(16)
            for n in d["numbers"]:
                draw_ball(self.recent_cv, x, sc(20), r, n, size=10)
                x += gap
            self.recent_cv.create_text(x - sc(8), sc(20), text="+", fill=SIDEBAR_SUB, font=(FONT, 11, "bold"))
            draw_ball(self.recent_cv, x + sc(6), sc(20), r, d["bonus"], size=10)

        nxt = self.data.next_round()
        self.next_label.config(text="다음 추첨: %d회 · %s 오후 8:35" % (nxt, short_date(core.draw_date(nxt))))
        self.gen_hint.config(text="지금까지 1등으로 나온 %s개 조합은 제외하고 만듭니다." % format(total, ","))
        self._render_sets()

    # ------------------------------------------------------------ 탭: 번호 생성
    def _tab(self, title):
        outer = ttk.Frame(self.nb, style="Card.TFrame")
        self.nb.add(outer, text=title)
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="both", expand=True, padx=self.sc(24), pady=self.sc(18))
        return inner

    def _clear_button(self, parent, grid):
        return ttk.Button(parent, text="선택 지우기", command=grid.clear)

    def _build_generate_tab(self):
        t, sc = self._tab("  번호 생성  "), self.sc
        head = tk.Frame(t, bg=CARD)
        head.pack(fill="x")
        self.label(head, "포함할 번호", 13, "bold").pack(side="left")
        self.fixed_info = self.label(head, "", 10, fg=SUBTEXT)
        self.fixed_info.pack(side="left", padx=sc(10))

        body = tk.Frame(t, bg=CARD)
        body.pack(fill="both", expand=True, pady=(sc(8), 0))
        left = tk.Frame(body, bg=CARD)
        left.pack(side="left", anchor="n")
        self.fixed_grid = NumberGrid(left, sc, 6, on_change=self._fixed_changed,
                                     on_limit=lambda: self.fixed_info.config(
                                         text="포함할 번호는 최대 6개까지예요.", fg=WARN))
        self.fixed_grid.pack(anchor="w")
        self._clear_button(left, self.fixed_grid).pack(anchor="w", pady=(sc(8), 0))

        right = tk.Frame(body, bg=CARD)
        right.pack(side="left", fill="both", expand=True, padx=(sc(28), 0))
        self.label(right, "게임 수", 11, "bold").pack(anchor="w")
        games_row = tk.Frame(right, bg=CARD)
        games_row.pack(anchor="w", pady=(sc(6), sc(12)))
        self.games = tk.IntVar(value=int(self.settings.get("games", 5)))
        for i in range(1, 6):
            ttk.Radiobutton(games_row, text="%d게임" % i, value=i, variable=self.games,
                            style="Game.Toolbutton", command=self._save_settings).pack(side="left", padx=(0, sc(4)))

        self.gen_btn = ttk.Button(right, text="번호 생성", style="Accent.TButton", command=self.generate)
        self.gen_btn.pack(anchor="w", fill="x")
        self.auto_send = tk.BooleanVar(value=bool(self.settings.get("auto_send", False)))
        ttk.Checkbutton(right, text="생성하면 카카오톡으로 바로 보내기", variable=self.auto_send,
                        command=self._save_settings).pack(anchor="w", pady=(sc(8), 0))
        self.gen_hint = self.label(right, "", 9, fg=SUBTEXT, wraplength=sc(300), justify="left")
        self.gen_hint.pack(anchor="w", pady=(sc(4), 0))

        res = tk.Frame(t, bg=CARD)
        res.pack(fill="x", pady=(sc(16), 0))
        tk.Frame(res, bg="#E5E7EB", height=1).pack(fill="x", pady=(0, sc(12)))
        res_head = tk.Frame(res, bg=CARD)
        res_head.pack(fill="x")
        self.res_title = self.label(res_head, "생성된 번호", 13, "bold")
        self.res_title.pack(side="left")
        self.copy_btn = ttk.Button(res_head, text="복사", command=self.copy_sets, state="disabled")
        self.copy_btn.pack(side="right")
        self.send_btn = ttk.Button(res_head, text="카카오톡으로 보내기", style="Kakao.TButton",
                                   command=self.send_sets, state="disabled")
        self.send_btn.pack(side="right", padx=(0, sc(6)))
        self.sets_cv = tk.Canvas(res, height=sc(5 * 40 + 8), bg=CARD, highlightthickness=0)
        self.sets_cv.pack(fill="x", pady=(sc(8), 0))
        self._fixed_changed()

    def _fixed_changed(self):
        sel = self.fixed_grid.selected
        if len(sel) == 6:
            self.fixed_info.config(text="%s  (6/6 · 이 조합 1게임만 만들어요)" % ", ".join(map(str, sel)),
                                   fg=ACCENT)
        elif sel:
            self.fixed_info.config(text="%s  (%d/6)" % (", ".join(map(str, sel)), len(sel)), fg=ACCENT)
        else:
            self.fixed_info.config(text="선택하지 않으면 6개 모두 무작위 (최대 6개)", fg=SUBTEXT)

    def generate(self):
        try:
            sets = self.data.generate(self.fixed_grid.selected, self.games.get())
        except ValueError as e:
            messagebox.showwarning(APP_TITLE, str(e))
            return
        self.current_sets = sets
        self.current_fixed = self.fixed_grid.selected
        self.current_round = self.data.next_round()
        self.last_generated = {"round": self.current_round, "sets": sets, "fixed": self.current_fixed}
        core.write_json("last_generated.json", self.last_generated)
        self._render_sets()
        if self.auto_send.get():
            self.send_sets(quiet=True)

    def _render_sets(self):
        if not hasattr(self, "sets_cv"):
            return
        cv, sc = self.sets_cv, self.sc
        cv.delete("all")
        if not self.current_sets:
            self.res_title.config(text="생성된 번호")
            cv.create_text(sc(4), sc(20), anchor="w", fill=SUBTEXT, font=(FONT, 10),
                           text="'번호 생성'을 누르면 여기에 표시됩니다.")
            self.copy_btn.state(["disabled"])
            self.send_btn.state(["disabled"])
            return
        self.res_title.config(text="%d회 추천 번호 · %s 추첨" % (
            self.current_round, short_date(core.draw_date(self.current_round))))
        for i, nums in enumerate(self.current_sets):
            y = sc(20) + i * sc(40)
            cv.create_text(sc(10), y, text=GAME_LABELS[i], fill=SUBTEXT, font=(FONT, 11, "bold"))
            for j, n in enumerate(nums):
                x = sc(46) + j * sc(42)
                draw_ball(cv, x, y, sc(17), n, size=11)
                if n in self.current_fixed:
                    cv.create_oval(x - sc(20), y - sc(20), x + sc(20), y + sc(20), outline=TEXT, width=2)
        self.copy_btn.state(["!disabled"])
        self.send_btn.state(["!disabled"])

    def sets_message(self):
        lines = ["[로또 %d회 추천번호]" % self.current_round,
                 "%s 추첨" % short_date(core.draw_date(self.current_round))]
        lines += ["%s %s" % (GAME_LABELS[i], core.fmt_nums(s)) for i, s in enumerate(self.current_sets)]
        if self.current_fixed:
            lines.append("포함 번호: %s" % ", ".join(map(str, self.current_fixed)))
        return "\n".join(lines)

    def copy_sets(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.sets_message())
        self.flash(self.copy_btn, "복사됨")

    def flash(self, btn, text):
        old = btn.cget("text")
        btn.config(text=text)
        self.root.after(1500, lambda: btn.config(text=old))

    def send_sets(self, quiet=False):
        if not self.current_sets:
            return
        if not self.kakao.is_logged_in():
            if messagebox.askyesno(APP_TITLE, "카카오톡으로 보내려면 먼저 카카오 로그인이 필요합니다.\n"
                                              "설정 탭으로 이동할까요?"):
                self.nb.select(2)
            return
        self.send_kakao(self.sets_message(), on_ok=lambda: self.flash(self.send_btn, "보냈어요 ✓"),
                        quiet=quiet)

    # ------------------------------------------------------------ 탭: 번호 확인
    def _build_check_tab(self):
        t, sc = self._tab("  당첨 이력 확인  "), self.sc
        head = tk.Frame(t, bg=CARD)
        head.pack(fill="x")
        self.label(head, "번호 6개 선택", 13, "bold").pack(side="left")
        self.check_count = self.label(head, "", 10, fg=SUBTEXT)
        self.check_count.pack(side="left", padx=sc(10))
        self.label(t, "고른 6개 번호가 지금까지 1등 당첨번호로 나온 적이 있는지 알려드려요.", 9,
                   fg=SUBTEXT).pack(anchor="w", pady=(sc(2), sc(8)))

        body = tk.Frame(t, bg=CARD)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=CARD)
        left.pack(side="left", anchor="n")
        self.check_grid = NumberGrid(left, sc, 6, on_change=self._check_changed)
        self.check_grid.pack(anchor="w")
        self._clear_button(left, self.check_grid).pack(anchor="w", pady=(sc(8), 0))

        right = tk.Frame(body, bg=CARD)
        right.pack(side="left", fill="both", expand=True, padx=(sc(28), 0))
        self.check_balls = tk.Canvas(right, height=sc(48), bg=CARD, highlightthickness=0)
        self.check_balls.pack(fill="x")
        self.verdict = self.label(right, "", 14, "bold", wraplength=sc(320), justify="left")
        self.verdict.pack(anchor="w", pady=(sc(10), sc(6)))
        self.verdict_detail = self.label(right, "", 10, fg=SUBTEXT, wraplength=sc(320), justify="left")
        self.verdict_detail.pack(anchor="w")
        self._check_changed()

    def _check_changed(self):
        sel = self.check_grid.selected
        self.check_count.config(text="(%d/6)" % len(sel), fg=ACCENT if len(sel) == 6 else SUBTEXT)
        cv, sc = self.check_balls, self.sc
        cv.delete("all")
        for j in range(6):
            x = sc(22) + j * sc(44)
            if j < len(sel):
                draw_ball(cv, x, sc(24), sc(19), sel[j], size=12)
            else:
                cv.create_oval(x - sc(19), sc(5), x + sc(19), sc(43), outline="#D1D5DB", dash=(3, 3))
        if len(sel) < 6:
            self.verdict.config(text="번호를 %d개 더 골라 주세요." % (6 - len(sel)), fg=SUBTEXT)
            self.verdict_detail.config(text="")
            return
        res = self.data.check(sel)
        if res["exact"]:
            d = self.data.draws[res["exact"]]
            self.verdict.config(fg=WARN, text="이미 나온 번호예요!\n%d회 (%s) 1등 당첨번호와 같습니다." % (
                d["round"], d["date"]))
        else:
            self.verdict.config(fg=OK, text="한 번도 1등으로 나온 적 없는 조합이에요.")
        rk = res["ranks"]

        def rounds(lst):
            if not lst:
                return "0번"
            shown = ", ".join("%d회" % r for r in lst[-3:])
            return "%d번 (%s%s)" % (len(lst), shown, " 등" if len(lst) > 3 else "")

        self.verdict_detail.config(text=(
            "1~%d회 매주 이 번호로 샀다면\n\n"
            "1등  %s\n2등  %s\n3등  %s\n4등  %d번\n5등  %d번" % (
                self.data.latest_round, rounds(rk[1]), rounds(rk[2]), rounds(rk[3]),
                rk[4], rk[5])))

    # ------------------------------------------------------------ 탭: 카카오톡
    def _build_kakao_tab(self):
        t, sc = self._tab("  카카오톡 설정  "), self.sc
        self.label(t, "카카오톡 '나에게 보내기'", 13, "bold").pack(anchor="w")
        self.kakao_status = self.label(t, "", 10, "bold")
        self.kakao_status.pack(anchor="w", pady=(sc(4), sc(12)))

        form = tk.Frame(t, bg=CARD)
        form.pack(anchor="w")
        self.key_var = tk.StringVar(value=self.kakao.rest_api_key)
        self.secret_var = tk.StringVar(value=self.kakao.client_secret)
        self.port_var = tk.StringVar(value=str(self.kakao.port))
        rows = [("REST API 키", self.key_var, 44, ""), ("Client Secret", self.secret_var, 44, "•"),
                ("포트", self.port_var, 8, "")]
        for i, (name, var, w, show) in enumerate(rows):
            self.label(form, name, 10).grid(row=i, column=0, sticky="w", pady=sc(4))
            ttk.Entry(form, textvariable=var, width=w, show=show).grid(row=i, column=1, sticky="w",
                                                                       padx=(sc(12), 0))
        self.label(form, "(사용 설정한 경우에만)", 9, fg=SUBTEXT).grid(row=1, column=2, sticky="w", padx=sc(6))
        self.label(form, "Redirect URI", 10).grid(row=3, column=0, sticky="w", pady=sc(4))
        uri_row = tk.Frame(form, bg=CARD)
        uri_row.grid(row=3, column=1, columnspan=2, sticky="w", padx=(sc(12), 0))
        self.uri_var = tk.StringVar(value=self.kakao.redirect_uri)
        ttk.Entry(uri_row, textvariable=self.uri_var, width=32, state="readonly").pack(side="left")
        uri_copy = ttk.Button(uri_row, text="복사")
        uri_copy.config(command=lambda: (self.root.clipboard_clear(),
                                         self.root.clipboard_append(self.uri_var.get()),
                                         self.flash(uri_copy, "복사됨")))
        uri_copy.pack(side="left", padx=(sc(6), 0))
        self.port_var.trace_add("write", lambda *a: self.uri_var.set(
            "http://localhost:%s/callback" % (self.port_var.get().strip() or "?")))

        btns = tk.Frame(t, bg=CARD)
        btns.pack(anchor="w", pady=(sc(14), 0))
        self.kakao_btns = []
        for text, cmd, style in [("설정 저장", self.save_kakao_config, "TButton"),
                                 ("카카오 로그인", self.kakao_login, "Kakao.TButton"),
                                 ("테스트 메시지 보내기", self.kakao_test, "TButton"),
                                 ("로그아웃", self.kakao_logout, "TButton")]:
            b = ttk.Button(btns, text=text, command=cmd, style=style)
            b.pack(side="left", padx=(0, sc(6)))
            self.kakao_btns.append(b)

        self.notify_draw = tk.BooleanVar(value=bool(self.settings.get("notify_draw", True)))
        ttk.Checkbutton(t, text="새 회차 당첨번호가 올라오면 카카오톡으로 알림 (마지막으로 만든 번호의 당첨 결과 포함)",
                        variable=self.notify_draw, command=self._save_settings).pack(anchor="w", pady=(sc(14), 0))

        tk.Frame(t, bg="#E5E7EB", height=1).pack(fill="x", pady=sc(14))
        help_text = (
            "처음 한 번만 설정하면 됩니다\n"
            "1. developers.kakao.com 에서 앱을 만들고 REST API 키를 복사해 위에 붙여넣습니다.\n"
            "2. 카카오 로그인을 사용(ON)으로 켜고, Redirect URI에 위 주소를 그대로 등록합니다.\n"
            "3. 동의항목에서 '카카오톡 메시지 전송(talk_message)'을 선택 동의로 설정합니다.\n"
            "4. '설정 저장' → '카카오 로그인'을 누르고, 브라우저에서 로그인·동의하면 끝입니다.\n\n"
            "로그인 정보는 이 Windows 계정으로만 풀 수 있게 암호화해서 저장합니다.\n"
            "공용 PC라면 사용 후 '로그아웃'을 눌러 이 PC의 로그인 정보를 지워 주세요.")
        self.label(t, help_text, 9, fg=SUBTEXT, justify="left").pack(anchor="w")
        bottom = tk.Frame(t, bg=CARD)
        bottom.pack(anchor="w", pady=(sc(10), 0))
        ttk.Button(bottom, text="이 PC에서 카카오 설정 모두 지우기", command=self.kakao_forget).pack(side="left")
        self.refresh_kakao_status()

    def refresh_kakao_status(self):
        ok = self.kakao.is_logged_in()
        self.kakao_status.config(text=("● " if ok else "○ ") + self.kakao.status_text(),
                                 fg=OK if ok else SUBTEXT)

    def _set_kakao_busy(self, busy):
        self.kakao_busy = busy
        for b in self.kakao_btns:
            b.state(["disabled"] if busy else ["!disabled"])

    def save_kakao_config(self, quiet=False):
        key = self.key_var.get().strip()
        if not key:
            messagebox.showwarning(APP_TITLE, "REST API 키를 입력해 주세요.")
            return False
        try:
            self.kakao.save_config(key, self.secret_var.get(), self.port_var.get().strip())
        except (KakaoError, ValueError) as e:
            messagebox.showwarning(APP_TITLE, str(e) if isinstance(e, KakaoError) else "포트는 숫자로 입력해 주세요.")
            return False
        self.refresh_kakao_status()
        if not quiet:
            messagebox.showinfo(APP_TITLE, "저장했습니다.\nRedirect URI: %s" % self.kakao.redirect_uri)
        return True

    def kakao_login(self):
        if self.kakao_busy or not self.save_kakao_config(quiet=True):
            return
        self._set_kakao_busy(True)
        self.kakao_status.config(text="브라우저에서 카카오 로그인과 동의를 진행해 주세요… (3분 안에)", fg=TEXT)

        def done(_):
            self._set_kakao_busy(False)
            self.refresh_kakao_status()
            messagebox.showinfo(APP_TITLE, "카카오 로그인 완료! '테스트 메시지 보내기'로 확인해 보세요.")
            self.root.lift()

        def fail(e):
            self._set_kakao_busy(False)
            self.refresh_kakao_status()
            messagebox.showerror(APP_TITLE, str(e))

        self.run_bg(self.kakao.login, done, fail)

    def kakao_test(self):
        self.send_kakao("[로또 번호 생성기]\n카카오톡 연결 테스트 메시지입니다.",
                        on_ok=lambda: messagebox.showinfo(APP_TITLE, "보냈습니다. 카카오톡 '나와의 채팅'을 확인해 보세요."))

    def kakao_logout(self):
        if self.kakao_busy:
            return
        self._set_kakao_busy(True)

        def done(_):
            self._set_kakao_busy(False)
            self.refresh_kakao_status()
            messagebox.showinfo(APP_TITLE, "로그아웃했습니다. 이 PC에 저장된 로그인 정보를 지웠습니다.")

        self.run_bg(self.kakao.logout, done, lambda e: done(None))

    def kakao_forget(self):
        if not messagebox.askyesno(APP_TITLE, "REST API 키, Client Secret, 로그인 정보를 이 PC에서 모두 지울까요?"):
            return

        def done(_):
            self.key_var.set("")
            self.secret_var.set("")
            self.port_var.set(str(self.kakao.port))
            self.refresh_kakao_status()

        self.run_bg(self.kakao.forget_everything, done, lambda e: done(None))

    def send_kakao(self, text, on_ok=None, quiet=False):
        if not self.kakao.is_logged_in():
            if not quiet:
                messagebox.showwarning(APP_TITLE, "카카오 로그인이 필요합니다. '카카오톡 설정' 탭에서 로그인해 주세요.")
            return

        def done(_):
            self.refresh_kakao_status()
            if on_ok:
                on_ok()

        def fail(e):
            self.refresh_kakao_status()
            messagebox.showerror(APP_TITLE, "카카오톡 전송 실패\n\n%s" % e)

        self.run_bg(lambda: self.kakao.send_to_me(text), done, fail)

    # ------------------------------------------------------------ 업데이트
    def _periodic_check(self):
        try:
            if not self.updating and self.data.is_update_due():
                self.start_update(manual=False)
        finally:
            self.root.after(UPDATE_CHECK_MS, self._periodic_check)

    def start_update(self, manual):
        if self.updating:
            return
        if not manual and not self.data.is_update_due():
            self.update_label.config(text="최신 상태입니다 (%d회까지)." % self.data.latest_round)
            return
        self.updating = True
        self.update_btn.state(["disabled"])
        self.update_label.config(text="동행복권에서 당첨번호를 확인하는 중…")

        def done(result):
            new, pending = result
            self.updating = False
            self.update_btn.state(["!disabled"])
            if new:
                self.refresh_stats()
                self._check_changed()
                msg = "%d회 당첨번호를 받아왔습니다." % new[-1]["round"]
                self.notify_new_draw(new[-1])
            elif pending:
                msg = "%d회 결과가 아직 올라오지 않았어요.\n5분 뒤 다시 확인합니다." % (self.data.latest_round + 1)
            else:
                msg = "최신 상태입니다 (%d회까지)." % self.data.latest_round
            self.update_label.config(text=msg)

        def fail(e):
            self.updating = False
            self.update_btn.state(["!disabled"])
            self.update_label.config(text="업데이트 실패: %s" % e)
            if manual:
                messagebox.showerror(APP_TITLE, "당첨번호 업데이트 실패\n\n%s" % e)

        self.run_bg(self.data.update, done, fail)

    def notify_new_draw(self, d):
        if not (self.notify_draw.get() and self.kakao.is_logged_in()):
            return
        lines = ["[로또 %d회 당첨번호]" % d["round"],
                 "%s + %02d" % (core.fmt_nums(d["numbers"]), d["bonus"])]
        lg = self.last_generated
        if lg and lg.get("round") == d["round"] and lg.get("sets"):
            lines.append("")
            lines.append("내 번호 결과")
            for i, s in enumerate(lg["sets"]):
                rank = core.rank_of(s, d)
                lines.append("%s %s  %s" % (GAME_LABELS[i], core.fmt_nums(s), "%d등" % rank if rank else "낙첨"))
        self.send_kakao("\n".join(lines), quiet=True)

    # ------------------------------------------------------------ 기타
    def _save_settings(self):
        self.settings.update({"games": self.games.get(), "auto_send": self.auto_send.get(),
                              "notify_draw": self.notify_draw.get() if hasattr(self, "notify_draw") else True})
        try:
            core.write_json("settings.json", self.settings)
        except OSError:
            pass

    def run_bg(self, fn, on_done=None, on_fail=None):
        def worker():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - 화면에 그대로 보여 준다
                self.jobs.put((on_fail, e))
            else:
                self.jobs.put((on_done, res))
        threading.Thread(target=worker, daemon=True).start()

    def _pump(self):
        try:
            while True:
                cb, arg = self.jobs.get_nowait()
                if cb:
                    try:
                        cb(arg)
                    except Exception as e:  # noqa: BLE001
                        messagebox.showerror(APP_TITLE, "오류: %s" % e)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
