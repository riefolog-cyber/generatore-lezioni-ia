# -*- coding: utf-8 -*-
"""Mini GUI (tkinter, solo standard library) per generare e avviare lezioni.

- Verifica ambiente
- Anteprima veloce del docx (moduli/quiz, senza audio)
- Generazione completa
- Apri lezione nel browser
- Esporta ZIP

Nota tecnica: ogni comando gira in un thread separato; tutte le modifiche ai
widget avvengono sul thread principale via root.after (tkinter non è thread-safe).
"""
import shutil
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import Tk, Button, Listbox, Text, Label, END, SINGLE, Checkbutton, BooleanVar
from tkinter import filedialog, messagebox

BASE = Path(__file__).resolve().parent


def docx_files():
    return sorted(p.name for p in BASE.glob("*.docx"))


def lesson_dirs():
    return sorted(p.name for p in BASE.glob("*_lesson") if p.is_dir() and (p / "index.html").exists())


class App:
    """Tutta la GUI vive qui: stato + widget + comandi asincroni sicuri."""

    def __init__(self, root):
        self.root = root
        self.running = False
        self.actions = {}          # nome -> bottone (per abilitarli/disabilitarli)
        root.title("Simulatore Mindsmith — genera lezioni")
        root.geometry("780x640")

        Label(root, text="1. Documento Word (.docx)").pack(anchor="w", padx=8)
        self.lb_docx = Listbox(root, selectmode=SINGLE, height=4)
        self.lb_docx.pack(fill="x", padx=8)

        Label(root, text="2. Lezioni generate (*_lesson)").pack(anchor="w", padx=8)
        self.lb_lessons = Listbox(root, selectmode=SINGLE, height=4)
        self.lb_lessons.pack(fill="x", padx=8)

        Label(root, text="3. Oppure genera da un link (sito web o video YouTube)").pack(anchor="w", padx=8)
        url_row = tk.Frame(root)
        url_row.pack(fill="x", padx=8)
        self.url_var = tk.StringVar()
        tk.Entry(url_row, textvariable=self.url_var).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(url_row, text="Genera da link", command=self.do_build_url).pack(side="left")

        self.force = BooleanVar(value=False)
        Checkbutton(root, text="Rigenera anche se esiste già (--force)",
                    variable=self.force).pack(anchor="w", padx=8)

        self.out = Text(root, height=16, state="normal")
        self.out.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_bar()

        self.refresh()
        self.put("Pronto. 1) Verifica ambiente  2) Anteprima  3) Genera  4) Apri lezione.\n")

    # ------------------------------------------------------------ UI
    def _build_bar(self):
        bar = tk.Frame(self.root)
        bar.pack(fill="x", padx=8)
        entries = [
            ("Verifica ambiente", "check", self.do_check),
            ("Anteprima", "preview", self.do_preview),
            ("Genera", "build", self.do_build),
            ("Apri lezione", "open", self.do_open),
            ("Esporta ZIP", "export", self.do_export),
            ("Sfoglia/copia docx", "browse", self.do_browse),
            ("Aggiorna", "refresh", self.do_refresh),
        ]
        for txt, key, fn in entries:
            b = Button(bar, text=txt, command=fn)
            b.pack(side="left", padx=2, pady=4)
            self.actions[key] = b

    def put(self, text):
        """Inserimento sicuro: sempre sul thread principale."""
        def _p():
            self.out.insert(END, text)
            self.out.see(END)
        self.root.after(0, _p)

    def set_busy(self, busy):
        def _p():
            self.running = busy
            state = "disabled" if busy else "normal"
            for b in self.actions.values():
                b.config(state=state)
        self.root.after(0, _p)

    def refresh(self):
        def _p():
            for lb, items in ((self.lb_docx, docx_files()), (self.lb_lessons, lesson_dirs())):
                lb.delete(0, END)
                for it in items:
                    lb.insert(END, it)
        self.root.after(0, _p)

    def selected(self, lb):
        s = lb.curselection()
        return lb.get(s[0]) if s else None

    def run(self, cmd):
        """Esegue un comando in un thread; output nel widget, UI mai bloccata."""
        if self.running:
            return
        self.running = True
        self.set_busy(True)
        self.put(f"\n$ {' '.join(cmd)}\n")
        display = " ".join(cmd)

        def work():
            try:
                p = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace")
                for line in p.stdout:
                    self.put(line)
                p.wait()
                self.put(f"[exit {p.returncode}]" +
                         ("" if p.returncode == 0 else "  ⚠ comando terminato con errore") + "\n")
            except Exception as e:  # noqa: BLE001
                self.put(f"ERRORE: {e}\n")
            finally:
                self.set_busy(False)
                self.refresh()
                self.put("— fine " + display + "\n")

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ azioni
    def do_check(self):
        self.run([sys.executable, "check_env.py"])

    def do_preview(self):
        f = self.selected(self.lb_docx)
        if not f:
            messagebox.showinfo("Anteprima",
                                "Seleziona prima un file .docx (o trascinalo nella cartella e premi Aggiorna).")
            return
        self.run([sys.executable, "new_lesson.py", "preview", f])

    def do_build(self):
        f = self.selected(self.lb_docx)
        if not f:
            messagebox.showinfo("Genera", "Seleziona prima un file .docx.")
            return
        cmd = [sys.executable, "new_lesson.py", "build", f]
        if self.force.get():
            cmd.append("--force")
        if messagebox.askyesno("Genera", f"Genero la lezione da {f}? Può durare diversi minuti."):
            self.run(cmd)

    def do_build_url(self):
        u = self.url_var.get().strip()
        if not u.lower().startswith(("http://", "https://")):
            messagebox.showinfo("Genera da link",
                                "Incolla un indirizzo completo (https://…).")
            return
        if messagebox.askyesno("Genera da link",
                               f"Genero la lezione da:\n{u}\n\nPuò durare diversi minuti."):
            self.run([sys.executable, "new_lesson.py", "build", u])

    def do_open(self):
        l = self.selected(self.lb_lessons)
        if not l:
            messagebox.showinfo("Apri", "Seleziona prima una lezione.")
            return
        self.run([sys.executable, "start_lesson.py", l])

    def do_export(self):
        l = self.selected(self.lb_lessons)
        if not l:
            messagebox.showinfo("Esporta", "Seleziona prima una lezione.")
            return
        self.run([sys.executable, "tools/export_zip.py", l])

    def do_refresh(self):
        self.refresh()
        self.put("Liste aggiornate.\n")

    def do_browse(self):
        f = filedialog.askopenfilename(filetypes=[("Word", "*.docx")])
        if f:
            shutil.copy2(f, BASE / Path(f).name)
            self.refresh()


def main():
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
