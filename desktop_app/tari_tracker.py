"""Small Windows UI for the cloud-hosted XTM alert settings."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable


REPOSITORY_URL = "https://github.com/Nonos1911/xtm-price-discord-bot.git"
BRANCH = "main"
SETTINGS_RELATIVE_PATH = Path("alert_settings.json")
ALLOWED_THRESHOLDS = (10, 20, 30, 40)
DEFAULT_SETTINGS = {"paused": False, "upward_threshold_percent": 10}


def normalize_settings(value: Any) -> dict[str, bool | int]:
    if not isinstance(value, dict):
        raise ValueError("Le fichier de réglages doit contenir un objet JSON.")
    paused = value.get("paused", DEFAULT_SETTINGS["paused"])
    threshold = value.get(
        "upward_threshold_percent", DEFAULT_SETTINGS["upward_threshold_percent"]
    )
    if not isinstance(paused, bool):
        raise ValueError("Le réglage « pause » doit être vrai ou faux.")
    if isinstance(threshold, bool) or threshold not in ALLOWED_THRESHOLDS:
        raise ValueError("Le seuil autorisé est 10, 20, 30 ou 40 %.")
    return {"paused": paused, "upward_threshold_percent": int(threshold)}


def read_settings(path: Path) -> dict[str, bool | int]:
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Impossible de lire les réglages Tari tracker : {exc}") from exc
    return normalize_settings(payload)


def write_settings(path: Path, settings: Any) -> dict[str, bool | int]:
    normalized = normalize_settings(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return normalized


class SettingsRepository:
    """Keep an isolated clone and publish only alert_settings.json to GitHub."""

    def __init__(self, root: Path | None = None) -> None:
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.root = root or app_data / "TariTracker" / "repository"
        self.settings_path = self.root / SETTINGS_RELATIVE_PATH

    @staticmethod
    def _git(args: list[str], *, cwd: Path | None = None) -> str:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=str(cwd) if cwd else None,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=creation_flags,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Git n'a pas pu terminer l'opération : {exc}") from exc
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(detail or f"Git a échoué ({completed.returncode}).")
        return completed.stdout.strip()

    def _clone_if_needed(self) -> None:
        if (self.root / ".git").is_dir():
            return
        if self.root.exists():
            raise RuntimeError(
                f"Le dossier local {self.root} existe mais ne contient pas le dépôt attendu. "
                "Aucun fichier n'a été modifié."
            )
        self.root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tari-tracker-clone-") as temporary:
            clone_path = Path(temporary) / "repository"
            self._git([
                "clone", "--branch", BRANCH, "--single-branch",
                REPOSITORY_URL, str(clone_path),
            ])
            clone_path.replace(self.root)

    def _commit_pending_settings(self) -> None:
        tracked = set(self._git(["diff", "--name-only"], cwd=self.root).splitlines())
        staged = set(self._git(["diff", "--cached", "--name-only"], cwd=self.root).splitlines())
        untracked = set(self._git(
            ["ls-files", "--others", "--exclude-standard"], cwd=self.root
        ).splitlines())
        changed = tracked | staged | untracked
        expected = SETTINGS_RELATIVE_PATH.as_posix()
        if not changed:
            return
        if changed != {expected}:
            raise RuntimeError(
                "Le clone Tari tracker contient d'autres changements locaux; "
                "par sécurité, l'application ne les publiera pas."
            )
        self._git(["add", "--", expected], cwd=self.root)
        staged = set(self._git(["diff", "--cached", "--name-only"], cwd=self.root).splitlines())
        if staged != {expected}:
            raise RuntimeError("L'application a refusé de publier un fichier autre que les réglages.")
        self._git([
            "-c", "user.name=Tari tracker",
            "-c", "user.email=tari-tracker@users.noreply.github.com",
            "commit", "-m", "Tari tracker: update alert settings",
        ], cwd=self.root)

    def _sync(self) -> None:
        self._clone_if_needed()
        self._commit_pending_settings()
        self._git(["fetch", "origin", BRANCH], cwd=self.root)
        counts = self._git(
            ["rev-list", "--left-right", "--count", f"HEAD...origin/{BRANCH}"],
            cwd=self.root,
        ).split()
        ahead, behind = (int(value) for value in counts)
        if ahead and behind:
            raise RuntimeError(
                "Le dépôt a reçu des changements concurrents. Aucun écrasement n'a été fait; "
                "réessaie après synchronisation."
            )
        if behind:
            self._git(["merge", "--ff-only", f"origin/{BRANCH}"], cwd=self.root)
        if ahead:
            self._git(["push", "origin", BRANCH], cwd=self.root)

    def load(self) -> dict[str, bool | int]:
        self._sync()
        return read_settings(self.settings_path)

    def save(self, settings: Any) -> tuple[dict[str, bool | int], bool]:
        normalized = normalize_settings(settings)
        self._sync()
        current = read_settings(self.settings_path)
        if current == normalized:
            return current, False
        write_settings(self.settings_path, normalized)
        self._commit_pending_settings()
        self._git(["push", "origin", BRANCH], cwd=self.root)
        return normalized, True


class TariTrackerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.repository = SettingsRepository()
        self.busy = False
        self.closing = False
        self.threshold_var = tk.StringVar(value="10 %")
        self.paused_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Connexion au dépôt GitHub…")

        root.title("Tari tracker")
        root.geometry("430x300")
        root.minsize(400, 280)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(root, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Tari tracker", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Réglages des alertes XTM et wXTM dans mog-post",
            wraplength=380,
        ).pack(anchor="w", pady=(3, 18))

        ttk.Label(frame, text="Seuil de déclenchement à la hausse").pack(anchor="w")
        self.threshold_box = ttk.Combobox(
            frame,
            textvariable=self.threshold_var,
            values=[f"{value} %" for value in ALLOWED_THRESHOLDS],
            state="readonly",
            width=12,
        )
        self.threshold_box.pack(anchor="w", pady=(5, 2))
        ttk.Label(frame, text="Le seuil à la baisse reste fixé à −10 %.").pack(anchor="w")

        self.pause_check = ttk.Checkbutton(
            frame,
            text="Mettre en pause les alertes dans mog-post",
            variable=self.paused_var,
        )
        self.pause_check.pack(anchor="w", pady=(17, 14))

        self.save_button = ttk.Button(frame, text="Enregistrer", command=self.save)
        self.save_button.pack(anchor="w")
        ttk.Separator(frame).pack(fill="x", pady=(17, 10))
        self.status_label = ttk.Label(frame, textvariable=self.status_var, wraplength=380)
        self.status_label.pack(anchor="w")

        self.controls = [self.threshold_box, self.pause_check, self.save_button]
        self._background("Lecture des réglages cloud…", self.repository.load, self._loaded)

    def _background(
        self,
        message: str,
        work: Callable[[], Any],
        on_success: Callable[[Any], None],
    ) -> None:
        self.busy = True
        self.status_var.set(message)
        for control in self.controls:
            control.configure(state="disabled")

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # shown in the app instead of a hidden console
                if not self.closing:
                    self.root.after(0, self._failed, str(exc))
                return
            if not self.closing:
                self.root.after(0, on_success, result)

        threading.Thread(target=runner, daemon=True).start()

    def _finish(self) -> None:
        self.busy = False
        for control in self.controls:
            control.configure(state="readonly" if control is self.threshold_box else "normal")

    def _failed(self, detail: str) -> None:
        self._finish()
        self.status_var.set("Échec de synchronisation. Les réglages cloud n'ont pas été confirmés.")
        messagebox.showerror(
            "Tari tracker — synchronisation impossible",
            f"{detail}\n\nVérifie que Git est installé et que ton compte GitHub a accès en écriture au dépôt.",
            parent=self.root,
        )

    def _loaded(self, settings: dict[str, bool | int]) -> None:
        self._finish()
        self._show_settings(settings)
        self.status_var.set("Réglages synchronisés avec GitHub.")

    def _show_settings(self, settings: dict[str, bool | int]) -> None:
        self.threshold_var.set(f"{settings['upward_threshold_percent']} %")
        self.paused_var.set(bool(settings["paused"]))

    def save(self) -> None:
        if self.busy:
            return
        settings = {
            "paused": self.paused_var.get(),
            "upward_threshold_percent": int(self.threshold_var.get().split()[0]),
        }

        def save_settings() -> tuple[dict[str, bool | int], bool]:
            return self.repository.save(settings)

        self._background("Publication des réglages sur GitHub…", save_settings, self._saved)

    def _saved(self, result: tuple[dict[str, bool | int], bool]) -> None:
        self._finish()
        settings, changed = result
        self._show_settings(settings)
        if changed:
            self.status_var.set("Réglages enregistrés sur GitHub; appliqués au prochain cycle cloud.")
        else:
            self.status_var.set("Ces réglages sont déjà actifs sur GitHub.")

    def close(self) -> None:
        self.closing = True
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    TariTrackerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
