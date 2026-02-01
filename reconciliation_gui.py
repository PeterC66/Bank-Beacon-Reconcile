"""
Bank Beacon Reconciliation GUI
Graphical interface for reviewing and confirming match suggestions.

Features:
- Navigation with Previous/Next buttons
- Visual status indicators (Confirmed/Rejected/Skipped)
- Editable history (change previous decisions)
- Bank transaction on left, Beacon entries on right
- 1-to-2 match display support
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional
import sys

from reconciliation_system import (
    ReconciliationSystem, MatchSuggestion, MatchStatus
)


class ReconciliationGUI:
    """Main GUI for bank reconciliation."""

    # Color scheme
    COLORS = {
        MatchStatus.PENDING: '#FFF3CD',      # Yellow/amber
        MatchStatus.CONFIRMED: '#D4EDDA',    # Green
        MatchStatus.REJECTED: '#F8D7DA',     # Red/pink
        MatchStatus.SKIPPED: '#D1ECF1',      # Blue/cyan
        'no-match': '#E2E3E5',               # Gray
    }

    STATUS_LABELS = {
        MatchStatus.PENDING: 'PENDING',
        MatchStatus.CONFIRMED: '✓ CONFIRMED',
        MatchStatus.REJECTED: '✗ REJECTED',
        MatchStatus.SKIPPED: '⏭ SKIPPED',
    }

    def __init__(self, master: tk.Tk):
        self.master = master
        self.master.title("Bank Beacon Reconciliation")
        self.master.geometry("1200x700")
        self.master.minsize(900, 600)

        # Initialize reconciliation system
        self.system = ReconciliationSystem()
        self.system.load_data()

        # Show loading summary
        bank_count = len(self.system.bank_transactions)
        beacon_count = len(self.system.beacon_entries)

        if bank_count == 0 or beacon_count == 0:
            messagebox.showwarning(
                "Loading Issue",
                f"Loaded {bank_count} bank transactions and {beacon_count} beacon entries.\n\n"
                f"Looking for files in:\n"
                f"{self.system.base_dir}\n\n"
                f"Expected files:\n"
                f"- {self.system.bank_file}\n"
                f"- {self.system.beacon_file}\n\n"
                f"Please ensure both files exist and have the correct format."
            )

        # Generate suggestions with progress dialog
        self.suggestions = self._generate_with_progress()

        # Current match index - start at first pending match
        self.current_index = self._find_first_pending()

        # Queued changes (for edits made when navigating back)
        self.queued_changes = {}  # match_id -> new_status

        # Setup GUI components
        self._setup_styles()
        self._create_widgets()
        self._update_display()

        # Show summary after GUI loads
        if bank_count > 0 and beacon_count > 0:
            confirmed = len([m for m in self.suggestions if m.status == MatchStatus.CONFIRMED])
            pending = len([m for m in self.suggestions if m.status == MatchStatus.PENDING])
            self.master.after(100, lambda: messagebox.showinfo(
                "Data Loaded",
                f"Loaded {bank_count} bank transactions\n"
                f"Loaded {beacon_count} beacon entries\n"
                f"Generated {len(self.suggestions)} match suggestions\n\n"
                f"Auto-confirmed: {confirmed}\n"
                f"Pending review: {pending}"
            ))

    def _generate_with_progress(self):
        """Generate suggestions with a progress dialog."""
        bank_count = len(self.system.bank_transactions)

        # For small datasets, skip progress dialog
        if bank_count < 50:
            return self.system.generate_suggestions()

        # Create progress dialog
        progress_window = tk.Toplevel(self.master)
        progress_window.title("Generating Matches")
        progress_window.geometry("400x120")
        progress_window.transient(self.master)
        progress_window.grab_set()

        # Center the dialog
        progress_window.update_idletasks()
        x = self.master.winfo_x() + (self.master.winfo_width() - 400) // 2
        y = self.master.winfo_y() + (self.master.winfo_height() - 120) // 2
        progress_window.geometry(f"+{x}+{y}")

        ttk.Label(progress_window, text="Processing transactions...",
                  font=('Segoe UI', 11)).pack(pady=(15, 5))

        progress_label = ttk.Label(progress_window, text="Initializing...")
        progress_label.pack(pady=5)

        progress_bar = ttk.Progressbar(progress_window, length=350, mode='determinate')
        progress_bar.pack(pady=10)

        def update_progress(current, total, message):
            progress_bar['maximum'] = total
            progress_bar['value'] = current
            progress_label.config(text=f"[{current}/{total}] {message[:40]}")
            progress_window.update()

        # Generate suggestions
        suggestions = self.system.generate_suggestions(progress_callback=update_progress)

        progress_window.destroy()
        return suggestions

    def _find_first_pending(self):
        """Find the index of the first pending match."""
        for i, match in enumerate(self.suggestions):
            if match.status == MatchStatus.PENDING:
                return i
        return 0

    def _setup_styles(self):
        """Configure ttk styles."""
        style = ttk.Style()
        style.configure('Title.TLabel', font=('Segoe UI', 14, 'bold'))
        style.configure('Header.TLabel', font=('Segoe UI', 11, 'bold'))
        style.configure('Amount.TLabel', font=('Segoe UI', 12, 'bold'))
        style.configure('Status.TLabel', font=('Segoe UI', 10, 'bold'))
        style.configure('Nav.TButton', font=('Segoe UI', 10))
        style.configure('Action.TButton', font=('Segoe UI', 10, 'bold'), padding=10)

    def _create_widgets(self):
        """Create all GUI widgets."""
        # Main container
        main_frame = ttk.Frame(self.master, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Top section: Statistics and progress
        self._create_stats_section(main_frame)

        # Middle section: Transaction display
        self._create_transaction_section(main_frame)

        # Bottom section: Actions and navigation
        self._create_action_section(main_frame)

    def _create_stats_section(self, parent):
        """Create statistics display section."""
        stats_frame = ttk.LabelFrame(parent, text="Reconciliation Progress", padding="10")
        stats_frame.pack(fill=tk.X, pady=(0, 10))

        # Progress info
        self.progress_label = ttk.Label(stats_frame, text="Match 0 of 0")
        self.progress_label.pack(side=tk.LEFT)

        # Stats summary
        self.stats_label = ttk.Label(stats_frame, text="")
        self.stats_label.pack(side=tk.RIGHT)

        # Progress bar
        self.progress_bar = ttk.Progressbar(
            stats_frame, mode='determinate', length=300
        )
        self.progress_bar.pack(side=tk.LEFT, padx=20)

    def _create_transaction_section(self, parent):
        """Create transaction display section with Bank on left, Beacon on right."""
        txn_frame = ttk.Frame(parent)
        txn_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        # Configure grid weights for responsive layout
        txn_frame.columnconfigure(0, weight=1)
        txn_frame.columnconfigure(1, weight=1)
        txn_frame.rowconfigure(0, weight=1)

        # Left side: Bank Transaction
        self._create_bank_panel(txn_frame)

        # Right side: Beacon Entries
        self._create_beacon_panel(txn_frame)

    def _create_bank_panel(self, parent):
        """Create bank transaction panel (left side)."""
        bank_frame = ttk.LabelFrame(parent, text="Bank Transaction", padding="15")
        bank_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 5))

        # Status indicator
        self.bank_status_frame = tk.Frame(bank_frame)
        self.bank_status_frame.pack(fill=tk.X, pady=(0, 10))

        self.status_label = ttk.Label(
            self.bank_status_frame, text="PENDING",
            style='Status.TLabel'
        )
        self.status_label.pack(side=tk.LEFT)

        self.match_type_label = ttk.Label(
            self.bank_status_frame, text="1-to-1",
            style='Status.TLabel'
        )
        self.match_type_label.pack(side=tk.RIGHT)

        # Bank details
        details_frame = ttk.Frame(bank_frame)
        details_frame.pack(fill=tk.BOTH, expand=True)

        # Date
        ttk.Label(details_frame, text="Date:", style='Header.TLabel').grid(
            row=0, column=0, sticky='w', pady=5
        )
        self.bank_date_label = ttk.Label(details_frame, text="")
        self.bank_date_label.grid(row=0, column=1, sticky='w', padx=10, pady=5)

        # Type
        ttk.Label(details_frame, text="Type:", style='Header.TLabel').grid(
            row=1, column=0, sticky='w', pady=5
        )
        self.bank_type_label = ttk.Label(details_frame, text="")
        self.bank_type_label.grid(row=1, column=1, sticky='w', padx=10, pady=5)

        # Description
        ttk.Label(details_frame, text="Description:", style='Header.TLabel').grid(
            row=2, column=0, sticky='w', pady=5
        )
        self.bank_desc_label = ttk.Label(details_frame, text="", wraplength=300)
        self.bank_desc_label.grid(row=2, column=1, sticky='w', padx=10, pady=5)

        # Amount (prominent)
        amount_frame = ttk.Frame(bank_frame)
        amount_frame.pack(fill=tk.X, pady=(20, 0))

        ttk.Label(amount_frame, text="Amount:", style='Header.TLabel').pack(side=tk.LEFT)
        self.bank_amount_label = ttk.Label(
            amount_frame, text="£0.00", style='Amount.TLabel',
            foreground='#0066CC'
        )
        self.bank_amount_label.pack(side=tk.LEFT, padx=10)

        # Confidence score
        confidence_frame = ttk.Frame(bank_frame)
        confidence_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(confidence_frame, text="Confidence:", style='Header.TLabel').pack(side=tk.LEFT)
        self.confidence_label = ttk.Label(confidence_frame, text="0%")
        self.confidence_label.pack(side=tk.LEFT, padx=10)

        # Score breakdown
        self.score_breakdown_label = ttk.Label(bank_frame, text="", foreground='gray')
        self.score_breakdown_label.pack(fill=tk.X, pady=(5, 0))

    def _create_beacon_panel(self, parent):
        """Create beacon entries panel (right side) - supports 1-2 entries."""
        beacon_frame = ttk.LabelFrame(parent, text="Beacon Entries", padding="15")
        beacon_frame.grid(row=0, column=1, sticky='nsew', padx=(5, 0))

        # Container for beacon entries (will hold 1 or 2 entry frames)
        self.beacon_container = ttk.Frame(beacon_frame)
        self.beacon_container.pack(fill=tk.BOTH, expand=True)

        # Create two entry frames (second one may be hidden)
        self.beacon_frames = []
        self.beacon_widgets = []

        for i in range(2):
            frame = ttk.Frame(self.beacon_container)
            widgets = self._create_beacon_entry_widgets(frame, i + 1)
            self.beacon_frames.append(frame)
            self.beacon_widgets.append(widgets)

        # Total amount (for 1-to-2 matches)
        self.total_frame = ttk.Frame(beacon_frame)
        ttk.Label(self.total_frame, text="Total:", style='Header.TLabel').pack(side=tk.LEFT)
        self.beacon_total_label = ttk.Label(
            self.total_frame, text="£0.00", style='Amount.TLabel',
            foreground='#0066CC'
        )
        self.beacon_total_label.pack(side=tk.LEFT, padx=10)

        # No match message
        self.no_match_label = ttk.Label(
            beacon_frame,
            text="No matching Beacon entries found",
            font=('Segoe UI', 11, 'italic'),
            foreground='gray'
        )

    def _create_beacon_entry_widgets(self, frame, entry_num):
        """Create widgets for a single beacon entry display."""
        widgets = {}

        # Entry header
        header_frame = ttk.Frame(frame)
        header_frame.pack(fill=tk.X, pady=(0, 5))

        widgets['header'] = ttk.Label(
            header_frame, text=f"Entry {entry_num}", style='Header.TLabel'
        )
        widgets['header'].pack(side=tk.LEFT)

        # Separator
        sep = ttk.Separator(frame, orient='horizontal')
        sep.pack(fill=tk.X, pady=5)

        # Details grid
        details = ttk.Frame(frame)
        details.pack(fill=tk.X)

        # Trans No
        ttk.Label(details, text="Trans No:").grid(row=0, column=0, sticky='w', pady=3)
        widgets['trans_no'] = ttk.Label(details, text="")
        widgets['trans_no'].grid(row=0, column=1, sticky='w', padx=10, pady=3)

        # Date
        ttk.Label(details, text="Date:").grid(row=1, column=0, sticky='w', pady=3)
        widgets['date'] = ttk.Label(details, text="")
        widgets['date'].grid(row=1, column=1, sticky='w', padx=10, pady=3)

        # Payee
        ttk.Label(details, text="Payee:").grid(row=2, column=0, sticky='w', pady=3)
        widgets['payee'] = ttk.Label(details, text="", wraplength=250)
        widgets['payee'].grid(row=2, column=1, sticky='w', padx=10, pady=3)

        # Detail
        ttk.Label(details, text="Detail:").grid(row=3, column=0, sticky='w', pady=3)
        widgets['detail'] = ttk.Label(details, text="", wraplength=250)
        widgets['detail'].grid(row=3, column=1, sticky='w', padx=10, pady=3)

        # Amount
        ttk.Label(details, text="Amount:").grid(row=4, column=0, sticky='w', pady=3)
        widgets['amount'] = ttk.Label(details, text="£0.00", foreground='#0066CC')
        widgets['amount'].grid(row=4, column=1, sticky='w', padx=10, pady=3)

        return widgets

    def _create_action_section(self, parent):
        """Create action buttons and navigation section."""
        action_frame = ttk.Frame(parent)
        action_frame.pack(fill=tk.X, pady=(10, 0))

        # Navigation buttons (left)
        nav_frame = ttk.Frame(action_frame)
        nav_frame.pack(side=tk.LEFT)

        self.prev_button = ttk.Button(
            nav_frame, text="◀ Previous", style='Nav.TButton',
            command=self._on_previous
        )
        self.prev_button.pack(side=tk.LEFT, padx=5)

        self.next_button = ttk.Button(
            nav_frame, text="Next ▶", style='Nav.TButton',
            command=self._on_next
        )
        self.next_button.pack(side=tk.LEFT, padx=5)

        # Jump to input
        ttk.Label(nav_frame, text="  Go to:").pack(side=tk.LEFT)
        self.jump_entry = ttk.Entry(nav_frame, width=5)
        self.jump_entry.pack(side=tk.LEFT, padx=5)
        self.jump_entry.bind('<Return>', self._on_jump)

        ttk.Button(nav_frame, text="Go", command=self._on_jump).pack(side=tk.LEFT)

        # Action buttons (center/right)
        btn_frame = ttk.Frame(action_frame)
        btn_frame.pack(side=tk.RIGHT)

        self.confirm_button = ttk.Button(
            btn_frame, text="✓ Confirm Match", style='Action.TButton',
            command=self._on_confirm
        )
        self.confirm_button.pack(side=tk.LEFT, padx=5)

        self.reject_button = ttk.Button(
            btn_frame, text="✗ Reject", style='Action.TButton',
            command=self._on_reject
        )
        self.reject_button.pack(side=tk.LEFT, padx=5)

        self.skip_button = ttk.Button(
            btn_frame, text="⏭ Skip", style='Action.TButton',
            command=self._on_skip
        )
        self.skip_button.pack(side=tk.LEFT, padx=5)

        # Control buttons (far right)
        ctrl_frame = ttk.Frame(action_frame)
        ctrl_frame.pack(side=tk.RIGHT, padx=20)

        ttk.Button(
            ctrl_frame, text="Save Progress",
            command=self._on_save
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            ctrl_frame, text="Export Results",
            command=self._on_export
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            ctrl_frame, text="Refresh Suggestions",
            command=self._on_refresh
        ).pack(side=tk.LEFT, padx=5)

    def _update_display(self):
        """Update the display for the current match."""
        if not self.suggestions:
            self._show_no_matches()
            return

        # Ensure index is valid
        self.current_index = max(0, min(self.current_index, len(self.suggestions) - 1))

        match = self.suggestions[self.current_index]

        # Check if there's a queued change for this match
        if match.id in self.queued_changes:
            display_status = self.queued_changes[match.id]
        else:
            display_status = match.status

        # Update progress
        self.progress_label.config(
            text=f"Match {self.current_index + 1} of {len(self.suggestions)}"
        )
        self.progress_bar['maximum'] = len(self.suggestions)
        self.progress_bar['value'] = self.current_index + 1

        # Update stats
        stats = self.system.get_statistics()
        self.stats_label.config(
            text=f"Confirmed: {stats['confirmed_matches']} | "
                 f"Pending: {stats['pending_suggestions']} | "
                 f"Unmatched Bank: {stats['unmatched_bank']}"
        )

        # Update status indicator
        self.status_label.config(text=self.STATUS_LABELS.get(display_status, 'PENDING'))
        self.bank_status_frame.config(bg=self.COLORS.get(display_status, self.COLORS[MatchStatus.PENDING]))

        # Update match type
        self.match_type_label.config(text=match.match_type.upper())

        # Update bank transaction details
        bank = match.bank_transaction
        self.bank_date_label.config(text=bank.date.strftime('%d-%b-%Y'))
        self.bank_type_label.config(text=bank.type)
        self.bank_desc_label.config(text=bank.description)
        self.bank_amount_label.config(text=f"£{bank.amount}")

        # Update confidence
        confidence_pct = int(match.confidence_score * 100)
        self.confidence_label.config(text=f"{confidence_pct}%")

        # Score breakdown
        self.score_breakdown_label.config(
            text=f"(Amount: {match.amount_score:.0%} | "
                 f"Date: {match.date_score:.0%} | "
                 f"Name: {match.name_score:.0%})"
        )

        # Update beacon entries display
        self._update_beacon_display(match)

        # Update navigation buttons
        self.prev_button.config(state=tk.NORMAL if self.current_index > 0 else tk.DISABLED)
        self.next_button.config(
            state=tk.NORMAL if self.current_index < len(self.suggestions) - 1 else tk.DISABLED
        )

        # Update action buttons based on match type
        has_beacon = len(match.beacon_entries) > 0
        self.confirm_button.config(state=tk.NORMAL if has_beacon else tk.DISABLED)

    def _update_beacon_display(self, match: MatchSuggestion):
        """Update beacon entries display for current match."""
        # Hide all frames first
        for frame in self.beacon_frames:
            frame.pack_forget()
        self.total_frame.pack_forget()
        self.no_match_label.pack_forget()

        if not match.beacon_entries:
            # No match found
            self.no_match_label.pack(fill=tk.BOTH, expand=True, pady=50)
            return

        # Show beacon entries
        total_amount = 0
        for i, beacon in enumerate(match.beacon_entries):
            if i >= 2:
                break

            widgets = self.beacon_widgets[i]
            widgets['trans_no'].config(text=beacon.trans_no)
            widgets['date'].config(text=beacon.date.strftime('%d/%m/%Y'))
            widgets['payee'].config(text=beacon.payee)
            widgets['detail'].config(text=beacon.detail)
            widgets['amount'].config(text=f"£{beacon.amount}")

            self.beacon_frames[i].pack(fill=tk.X, pady=(0, 10))
            total_amount += float(beacon.amount)

        # Show total for 1-to-2 matches
        if len(match.beacon_entries) == 2:
            self.beacon_total_label.config(text=f"£{total_amount:.2f}")
            self.total_frame.pack(fill=tk.X, pady=(10, 0))

    def _show_no_matches(self):
        """Show message when there are no matches to review."""
        messagebox.showinfo(
            "No Matches",
            "No match suggestions available.\n\n"
            "Either all bank transactions have been matched,\n"
            "or no suitable matches were found."
        )

    def _on_previous(self):
        """Navigate to previous match."""
        if self.current_index > 0:
            self.current_index -= 1
            self._update_display()

    def _on_next(self):
        """Navigate to next match."""
        if self.current_index < len(self.suggestions) - 1:
            self.current_index += 1
            self._update_display()

    def _on_jump(self, event=None):
        """Jump to a specific match number."""
        try:
            target = int(self.jump_entry.get()) - 1
            if 0 <= target < len(self.suggestions):
                self.current_index = target
                self._update_display()
            else:
                messagebox.showwarning(
                    "Invalid Index",
                    f"Please enter a number between 1 and {len(self.suggestions)}"
                )
        except ValueError:
            messagebox.showwarning("Invalid Input", "Please enter a valid number")

    def _on_confirm(self):
        """Confirm the current match."""
        if not self.suggestions:
            return

        match = self.suggestions[self.current_index]

        # Queue the change (will be applied when moving forward or saving)
        self.queued_changes[match.id] = MatchStatus.CONFIRMED
        self._auto_save()
        self._update_display()

        # Auto-advance to next
        if self.current_index < len(self.suggestions) - 1:
            self._on_next()

    def _on_reject(self):
        """Reject the current match."""
        if not self.suggestions:
            return

        match = self.suggestions[self.current_index]
        self.queued_changes[match.id] = MatchStatus.REJECTED
        self._auto_save()
        self._update_display()

        # Auto-advance
        if self.current_index < len(self.suggestions) - 1:
            self._on_next()

    def _on_skip(self):
        """Skip the current match."""
        if not self.suggestions:
            return

        match = self.suggestions[self.current_index]
        self.queued_changes[match.id] = MatchStatus.SKIPPED
        self._auto_save()
        self._update_display()

        # Auto-advance
        if self.current_index < len(self.suggestions) - 1:
            self._on_next()

    def _auto_save(self):
        """Auto-save progress without showing a message."""
        self._apply_queued_changes()
        self.system.save_state()

    def _apply_queued_changes(self):
        """Apply all queued status changes."""
        for match_id, new_status in self.queued_changes.items():
            # Find the match
            for match in self.suggestions:
                if match.id == match_id:
                    self.system.update_match_status(match, new_status)
                    break

        self.queued_changes.clear()

    def _on_save(self):
        """Save current progress."""
        self._apply_queued_changes()
        self.system.save_state()
        messagebox.showinfo("Saved", "Progress saved successfully!")

    def _on_export(self):
        """Export results to CSV."""
        self._apply_queued_changes()
        self.system.export_results()
        messagebox.showinfo(
            "Exported",
            "Results exported to reconciliation_results.csv"
        )

    def _on_refresh(self):
        """Refresh suggestions after applying changes."""
        self._apply_queued_changes()
        self.system.save_state()

        # Regenerate suggestions
        self.suggestions = self.system.generate_suggestions()
        self.current_index = 0

        self._update_display()
        messagebox.showinfo(
            "Refreshed",
            f"Generated {len(self.suggestions)} new match suggestions"
        )


def main():
    """Main entry point for GUI application."""
    root = tk.Tk()

    # Set icon if available
    try:
        root.iconbitmap('icon.ico')
    except tk.TclError:
        pass

    app = ReconciliationGUI(root)

    # Save on window close
    def on_closing():
        app._auto_save()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
