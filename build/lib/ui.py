import builtins
import contextlib
import io
import tkinter as tk
from tkinter import messagebox, scrolledtext

from agent import run_agent


class _OutputRedirect(io.TextIOBase):
    def __init__(self, text_widget: scrolledtext.ScrolledText) -> None:
        self._widget = text_widget

    def write(self, value: str) -> int:
        if value:
            self._widget.insert(tk.END, value)
            self._widget.see(tk.END)
            self._widget.update_idletasks()
        return len(value)

    def flush(self) -> None:
        pass


class AgentApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Gemini System Agent")
        self.root.geometry("980x700")
        self.root.minsize(700, 500)

        self.header = tk.Label(
            root,
            text="Gemini System Agent",
            font=("Segoe UI", 18, "bold"),
            pady=12,
        )
        self.header.pack(fill=tk.X, padx=16, pady=(12, 0))

        controls = tk.Frame(root)
        controls.pack(fill=tk.X, padx=16, pady=10)

        self.prompt_label = tk.Label(controls, text="Task", font=("Segoe UI", 11, "bold"))
        self.prompt_label.pack(anchor="w")

        self.prompt_box = tk.Text(controls, height=5, wrap="word", font=("Segoe UI", 11))
        self.prompt_box.pack(fill=tk.X, pady=(6, 10))

        options = tk.Frame(controls)
        options.pack(fill=tk.X)

        self.dry_run_var = tk.BooleanVar(value=False)
        self.dry_run_checkbox = tk.Checkbutton(
            options,
            text="Dry run (propose commands without executing them)",
            variable=self.dry_run_var,
            font=("Segoe UI", 10),
        )
        self.dry_run_checkbox.pack(anchor="w")

        self.send_button = tk.Button(
            controls,
            text="Run Agent",
            command=self.run_prompt,
            font=("Segoe UI", 11, "bold"),
            bg="#2f6fed",
            fg="white",
            padx=16,
            pady=8,
        )
        self.send_button.pack(anchor="e", pady=(10, 0))

        self.output_label = tk.Label(
            root,
            text="Output",
            font=("Segoe UI", 11, "bold"),
        )
        self.output_label.pack(anchor="w", padx=16)

        self.output_box = scrolledtext.ScrolledText(
            root,
            wrap=tk.WORD,
            state=tk.NORMAL,
            font=("Consolas", 10),
            padx=10,
            pady=10,
        )
        self.output_box.pack(fill=tk.BOTH, expand=True, padx=16, pady=(6, 16))
        self.output_box.insert(tk.END, "Type a task and click Run Agent.\n")
        self.output_box.configure(state=tk.DISABLED)

    def _prompt_for_confirmation(self, prompt_text: str = "") -> str:
        result = messagebox.askyesno("Confirm action", prompt_text or "Run this action?")
        return "y" if result else "n"

    def _write_output(self, text: str) -> None:
        self.output_box.configure(state=tk.NORMAL)
        self.output_box.insert(tk.END, text)
        self.output_box.see(tk.END)
        self.output_box.configure(state=tk.DISABLED)

    def run_prompt(self) -> None:
        prompt = self.prompt_box.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("No task", "Please enter a task before running the agent.")
            return

        self.output_box.configure(state=tk.NORMAL)
        self.output_box.insert(tk.END, f"\n> {prompt}\n")
        self.output_box.see(tk.END)
        self.output_box.configure(state=tk.DISABLED)

        self.send_button.config(state=tk.DISABLED)
        self.root.update_idletasks()

        original_input = builtins.input
        builtins.input = self._prompt_for_confirmation

        try:
            with contextlib.redirect_stdout(_OutputRedirect(self.output_box)), contextlib.redirect_stderr(
                _OutputRedirect(self.output_box)
            ):
                run_agent(prompt, dry_run=self.dry_run_var.get(), confirm_browser=True)
        except Exception as error:
            self._write_output(f"\nAgent error: {error}\n")
        finally:
            builtins.input = original_input
            self.send_button.config(state=tk.NORMAL)
            self._write_output("\nReady.\n")


def launch_ui() -> None:
    root = tk.Tk()
    app = AgentApp(root)
    root.mainloop()


def main() -> None:
    launch_ui()


if __name__ == "__main__":
    main()
