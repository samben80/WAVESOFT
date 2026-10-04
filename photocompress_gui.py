"""Interface graphique de photocompress (utilisée pour l'exécutable Windows)."""

from __future__ import annotations

import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import photocompress as pc

FORMATS = {
    "Garder le format d'origine": "auto",
    "JPEG": "jpg",
    "PNG": "png",
    "WebP (plus léger)": "webp",
}


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PhotoCompress - compression et renommage de photos")
        self.minsize(820, 540)
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.dest_dir: Path | None = None

        self.source = tk.StringVar()
        self.destination = tk.StringVar()
        self.max_size = tk.StringVar(value="50")
        self.pattern = tk.StringVar(value="{name}")
        self.cut = tk.StringVar(value="_photo")
        self.start = tk.StringVar(value="1")
        self.slug = tk.BooleanVar(value=False)
        self.recursive = tk.BooleanVar(value=False)
        self.keep_format = tk.BooleanVar(value=False)
        self.overwrite = tk.BooleanVar(value=False)
        self.out_format = tk.StringVar(value=next(iter(FORMATS)))
        self.min_quality = tk.StringVar(value="10")

        self._build()
        self.pattern.trace_add("write", lambda *_: self._update_preview())
        self.start.trace_add("write", lambda *_: self._update_preview())
        self.slug.trace_add("write", lambda *_: self._update_preview())
        self.cut.trace_add("write", lambda *_: self._update_preview())
        self._update_preview()

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        pad = {"padx": 6, "pady": 4}
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        row = 0
        for label, var, title in (("Dossier des photos :", self.source, "Choisir le dossier des photos"),
                                  ("Dossier de destination :", self.destination,
                                   "Choisir le dossier de destination")):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", **pad)
            ttk.Entry(frame, textvariable=var).grid(row=row, column=1, sticky="ew", **pad)
            ttk.Button(frame, text="Parcourir...",
                       command=lambda v=var, t=title: self._pick_dir(v, t)).grid(
                row=row, column=2, **pad)
            row += 1

        options = ttk.LabelFrame(frame, text="Compression", padding=8)
        options.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Label(options, text="Taille max (Ko) :").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=5, to=10000, increment=5, width=8,
                    textvariable=self.max_size).grid(row=0, column=1, sticky="w", padx=(4, 20))
        ttk.Label(options, text="Format de sortie :").grid(row=0, column=2, sticky="w")
        ttk.Combobox(options, values=list(FORMATS), textvariable=self.out_format,
                     state="readonly", width=26).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(options, text="Qualité minimale (1-95) :").grid(row=1, column=0, sticky="w",
                                                                  pady=(6, 0))
        ttk.Spinbox(options, from_=1, to=95, width=8, textvariable=self.min_quality).grid(
            row=1, column=1, sticky="w", padx=(4, 20), pady=(6, 0))
        ttk.Checkbutton(options, text="Ne jamais changer de format",
                        variable=self.keep_format).grid(row=1, column=2, columnspan=2, sticky="w",
                                                        pady=(6, 0))
        row += 1

        naming = ttk.LabelFrame(frame, text="Renommage", padding=8)
        naming.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        naming.columnconfigure(1, weight=1)
        ttk.Label(naming, text="Modèle de nom :").grid(row=0, column=0, sticky="w")
        ttk.Entry(naming, textvariable=self.pattern).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(naming, text="Premier numéro :").grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Spinbox(naming, from_=0, to=999999, width=7, textvariable=self.start).grid(
            row=0, column=3, sticky="w", padx=4)
        ttk.Label(naming, text="Supprimer à partir de :").grid(row=1, column=0, sticky="w",
                                                               pady=(6, 0))
        ttk.Entry(naming, textvariable=self.cut, width=20).grid(row=1, column=1, sticky="w",
                                                                padx=4, pady=(6, 0))
        ttk.Label(naming, foreground="gray",
                  text="{name} = nom d'origine (sans la partie supprimée)   "
                       "{n} = numéro ({n:03} → 001)   {date} = date du jour").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.preview = ttk.Label(naming, foreground="#1a5fb4")
        self.preview.grid(row=3, column=0, columnspan=4, sticky="w", pady=(2, 0))
        checks = ttk.Frame(naming)
        checks.grid(row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(checks, text="Noms simplifiés (minuscules, sans accents ni espaces)",
                        variable=self.slug).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(checks, text="Inclure les sous-dossiers",
                        variable=self.recursive).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(checks, text="Écraser les fichiers existants",
                        variable=self.overwrite).pack(side="left")
        row += 1

        actions = ttk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self.run_button = ttk.Button(actions, text="Lancer", command=self._start)
        self.run_button.pack(side="left")
        self.preview_button = ttk.Button(actions, text="Aperçu des noms", command=self._dry_run)
        self.preview_button.pack(side="left", padx=6)
        self.open_button = ttk.Button(actions, text="Ouvrir le dossier de destination",
                                      command=self._open_dest, state="disabled")
        self.open_button.pack(side="left")
        self.excel_button = ttk.Button(actions, text="Générer la liste Excel",
                                       command=self._export_excel)
        self.excel_button.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(actions, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))
        row += 1

        log_frame = ttk.Frame(frame)
        log_frame.grid(row=row, column=0, columnspan=3, sticky="nsew", **pad)
        frame.rowconfigure(row, weight=1)
        self.log = tk.Text(log_frame, height=12, wrap="none", state="disabled",
                           font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log.tag_configure("bad", foreground="#c01c28")
        self.log.tag_configure("ok", foreground="#26a269")

    def _pick_dir(self, var: tk.StringVar, title: str) -> None:
        path = filedialog.askdirectory(title=title, initialdir=var.get() or None)
        if path:
            var.set(path)
            if var is self.source and not self.destination.get():
                self.destination.set(str(Path(path).parent / (Path(path).name + "_compressees")))

    def _update_preview(self) -> None:
        try:
            start = int(self.start.get() or 1)
            example = Path("125-1E-D100-2B-3K-W BK_photo_1.jpg")
            name = pc.build_name(self.pattern.get(), example, start, self.slug.get(), self.cut.get())
            self.preview.configure(text=f"Exemple : « {example.name} » → « {name}.jpg »",
                                   foreground="#1a5fb4")
        except ValueError as exc:
            self.preview.configure(text=str(exc), foreground="#c01c28")

    def _write(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ actions --
    def _read_settings(self) -> dict | None:
        try:
            max_size = float(self.max_size.get().replace(",", "."))
            min_quality = int(self.min_quality.get())
            start = int(self.start.get())
        except ValueError:
            messagebox.showerror("Paramètre invalide",
                                 "La taille, la qualité et le premier numéro doivent être des nombres.")
            return None
        if max_size <= 0 or not 1 <= min_quality <= pc.MAX_QUALITY:
            messagebox.showerror("Paramètre invalide",
                                 "La taille doit être positive et la qualité entre 1 et 95.")
            return None
        if not self.source.get() or not self.destination.get():
            messagebox.showerror("Dossiers manquants",
                                 "Choisissez le dossier des photos et le dossier de destination.")
            return None
        return {"max_bytes": int(max_size * 1000), "min_quality": min_quality, "start": start,
                "format": FORMATS[self.out_format.get()]}

    def _prepare(self, settings: dict):
        try:
            return pc.prepare_jobs(Path(self.source.get()), Path(self.destination.get()),
                                   self.pattern.get(), settings["start"], self.slug.get(),
                                   settings["format"], self.overwrite.get(), self.recursive.get(),
                                   self.cut.get())
        except ValueError as exc:
            messagebox.showerror("Erreur", str(exc))
            return None

    def _dry_run(self) -> None:
        settings = self._read_settings()
        prepared = settings and self._prepare(settings)
        if not prepared:
            return
        source_dir, dest_dir, jobs = prepared
        self._clear_log()
        for job in jobs:
            self._write(f"{job.source.relative_to(source_dir)}  →  "
                        f"{job.destination.relative_to(dest_dir)}")
        self._write(f"\n{len(jobs)} image(s) trouvée(s). Rien n'a été écrit.")

    def _start(self) -> None:
        settings = self._read_settings()
        prepared = settings and self._prepare(settings)
        if not prepared:
            return
        source_dir, dest_dir, jobs = prepared
        self._clear_log()
        if not jobs:
            self._write("Aucune image trouvée (formats acceptés : "
                        + ", ".join(sorted(pc.SUPPORTED_EXTENSIONS)) + ").", "bad")
            return
        self.dest_dir = dest_dir
        self.run_button.configure(state="disabled")
        self.preview_button.configure(state="disabled")
        self.progress.configure(maximum=len(jobs), value=0)
        self._write(f"Traitement de {len(jobs)} image(s), limite "
                    f"{pc.human_size(settings['max_bytes'])}...\n")
        self.worker = threading.Thread(
            target=self._work, args=(jobs, settings, source_dir, dest_dir), daemon=True)
        self.worker.start()
        self.after(100, self._poll)

    def _work(self, jobs, settings, source_dir: Path, dest_dir: Path) -> None:
        results = []
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            for r in pc.run_jobs(jobs, settings["max_bytes"], settings["min_quality"],
                                 settings["format"], not self.keep_format.get()):
                results.append(r)
                self.events.put(("result", r, pc.describe_result(r, source_dir, dest_dir)))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", str(exc)))
        self.events.put(("done", results))

    def _poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "result":
                    _, r, line = event
                    self._write(line, "ok" if r.fits and not r.error else "bad")
                    self.progress["value"] = self.progress["value"] + 1
                elif event[0] == "error":
                    self._write(f"Erreur : {event[1]}", "bad")
                elif event[0] == "done":
                    self._finish(event[1])
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _finish(self, results) -> None:
        ok, too_big, errors = pc.summarize(results)
        self._write(f"\nTerminé : {ok} OK, {too_big} au-dessus de la limite, {errors} erreur(s).")
        if too_big:
            self._write("Les fichiers « TROP GROS » ont été enregistrés au plus petit possible\n"
                        "sans changer la résolution. Pistes : baisser la qualité minimale,\n"
                        "choisir WebP, ou augmenter la taille max.", "bad")
        self.run_button.configure(state="normal")
        self.preview_button.configure(state="normal")
        self.open_button.configure(state="normal")

    def _export_excel(self) -> None:
        if not self.destination.get():
            messagebox.showerror("Dossier manquant", "Choisissez le dossier de destination.")
            return
        folder = Path(self.destination.get())
        if not folder.is_dir():
            messagebox.showerror("Dossier introuvable",
                                 f"Le dossier « {folder} » n'existe pas encore.\n"
                                 "Lancez d'abord la compression.")
            return
        output = filedialog.asksaveasfilename(
            title="Enregistrer la liste Excel", initialdir=str(folder),
            initialfile="liste_photos.xlsx", defaultextension=".xlsx",
            filetypes=[("Classeur Excel", "*.xlsx")])
        if not output:
            return
        try:
            count = pc.write_excel_list(folder, Path(output), self.recursive.get())
        except PermissionError:
            messagebox.showerror("Fichier verrouillé",
                                 "Impossible d'écrire le fichier : il est peut-être ouvert dans "
                                 "Excel. Fermez-le puis réessayez.")
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Erreur", str(exc))
            return
        self._write(f"Liste Excel créée ({count} fichier(s)) : {output}", "ok")
        if messagebox.askyesno("Liste Excel créée",
                               f"{count} fichier(s) listé(s).\n\nOuvrir le fichier maintenant ?"):
            self._open_path(Path(output))

    def _open_dest(self) -> None:
        if self.dest_dir and self.dest_dir.exists():
            self._open_path(self.dest_dir)

    @staticmethod
    def _open_path(path: Path) -> None:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


def main() -> None:
    app = App()
    if "--self-test" in sys.argv:  # vérifie que l'exécutable démarre (utilisé par la CI)
        app.update()
        app.destroy()
        return
    app.mainloop()


if __name__ == "__main__":
    multiprocessing.freeze_support()  # nécessaire pour l'exécutable Windows (PyInstaller)
    main()
