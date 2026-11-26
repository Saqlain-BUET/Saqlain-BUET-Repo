import json
import os
import tkinter as tk
from tkinter import messagebox, ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt

DATA_FILE = "vat_tax_records.json"


def load_records():
    """Load saved calculations from JSON file if it exists."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def save_records():
    """Save current calculations list to JSON file."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=4)


# Load existing history on startup
records = load_records()


def update_chart():
    ax.clear()

    if not records:
        ax.set_title("Calculation History (No data yet)")
        canvas.draw()
        return

    labels = [f"#{i + 1}" for i in range(len(records))]
    x = list(range(len(records)))

    bases = [r["base"] for r in records]
    vats = [r["vat"] for r in records]
    taxes = [r["tax"] for r in records]
    totals = [r["total"] for r in records]
    payments = [r["payment"] for r in records]

    ax.plot(x, totals, marker="o", linewidth=2, label="Total", color="#388e3c")
    ax.plot(x, bases, marker="o", linewidth=2, label="Base", color="#1976d2")
    ax.plot(x, payments, marker="o", linewidth=2, label="Payment", color="#7b1fa2")
    ax.plot(x, vats, marker="s", linestyle="--", label="VAT", color="#ffa000")
    ax.plot(x, taxes, marker="^", linestyle="--", label="Tax", color="#d32f2f")

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Amount (Taka)")
    ax.set_title("Recorded Calculations Trend")
    ax.legend(loc="upper left", fontsize=8, ncol=3)
    ax.grid(True, linestyle="--", alpha=0.5)

    fig.tight_layout()
    canvas.draw()


def calculate():
    try:
        choice = input_type_var.get()
        amount = float(amount_entry.get())
        increment_val = float(increment_entry.get() or 0)
        vat_percent = float(vat_entry.get())
        tax_percent = float(tax_entry.get())

        vat_rate = vat_percent / 100
        tax_rate = tax_percent / 100

        if choice == "Base Value":
            base = amount
        elif choice == "Total Amount":
            base = amount / (1 + vat_rate + tax_rate)
        elif choice == "Payment Value":
            if tax_rate >= 1:
                messagebox.showerror("Math Error", "Tax % cannot be 100% or more.")
                return
            base = amount / (1 - tax_rate)

        vat_amount = base * vat_rate
        tax_amount = base * tax_rate
        total_amount = base + vat_amount + tax_amount
        payment_value = base - tax_amount

        # Save current calculation
        records.append({
            "base": base,
            "vat": vat_amount,
            "tax": tax_amount,
            "total": total_amount,
            "payment": payment_value,
        })
        save_records()

        result_text.set(
            f"Record #{len(records)} Saved (Input: {amount:.2f} Taka):\n"
            f"Base Value     : {base:.2f} Taka\n"
            f"VAT ({vat_percent}%)    : {vat_amount:.2f} Taka\n"
            f"TAX ({tax_percent}%)    : {tax_amount:.2f} Taka\n"
            f"Total Amt      : {total_amount:.2f} Taka\n"
            f"Payment Value  : {payment_value:.2f} Taka"
        )

        update_chart()

        # Auto-increment the Amount box for the next click
        if increment_val != 0:
            next_amount = amount + increment_val
            amount_entry.delete(0, tk.END)
            amount_entry.insert(0, f"{next_amount:g}")

    except ValueError:
        messagebox.showerror("Invalid Input", "Please enter valid numbers in all fields.")


def clear_history():
    if messagebox.askyesno("Confirm Clear", "Delete all saved calculation records permanently?"):
        records.clear()
        save_records()
        result_text.set("History cleared. Enter values and click Calculate.")
        update_chart()


# Main Window Setup
root = tk.Tk()
root.title("VAT & Tax Calculator (Auto-Increment & Line Graph)")
root.geometry("640x760")

# Top Input Frame
input_frame = tk.Frame(root)
input_frame.pack(pady=10)

tk.Label(input_frame, text="Input Type:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="e", padx=5, pady=4)
input_type_var = tk.StringVar(value="Base Value")
dropdown = ttk.Combobox(
    input_frame,
    textvariable=input_type_var,
    values=["Base Value", "Total Amount", "Payment Value"],
    state="readonly",
    width=22,
)
dropdown.grid(row=0, column=1, pady=4)

tk.Label(input_frame, text="Amount (Taka):", font=("Arial", 10)).grid(row=1, column=0, sticky="e", padx=5, pady=4)
amount_entry = tk.Entry(input_frame, width=25)
amount_entry.grid(row=1, column=1, pady=4)

tk.Label(input_frame, text="Increment By (Taka):", font=("Arial", 10)).grid(row=2, column=0, sticky="e", padx=5, pady=4)
increment_entry = tk.Entry(input_frame, width=25)
increment_entry.insert(0, "0")  # Default to 0 so normal single calculations still work
increment_entry.grid(row=2, column=1, pady=4)

tk.Label(input_frame, text="VAT %:", font=("Arial", 10)).grid(row=3, column=0, sticky="e", padx=5, pady=4)
vat_entry = tk.Entry(input_frame, width=25)
vat_entry.grid(row=3, column=1, pady=4)

tk.Label(input_frame, text="Tax %:", font=("Arial", 10)).grid(row=4, column=0, sticky="e", padx=5, pady=4)
tax_entry = tk.Entry(input_frame, width=25)
tax_entry.grid(row=4, column=1, pady=4)

# Buttons Frame
btn_frame = tk.Frame(root)
btn_frame.pack(pady=5)

calc_btn = tk.Button(
    btn_frame,
    text="Calculate & Record",
    command=calculate,
    bg="#2e7d32",
    fg="white",
    font=("Arial", 10, "bold"),
    width=18,
)
calc_btn.grid(row=0, column=0, padx=5)

clear_btn = tk.Button(
    btn_frame,
    text="Clear History",
    command=clear_history,
    bg="#c62828",
    fg="white",
    font=("Arial", 10),
    width=14,
)
clear_btn.grid(row=0, column=1, padx=5)

# Summary Box
initial_msg = (
    f"Loaded {len(records)} saved record(s) from disk."
    if records
    else "Enter values and click Calculate to record..."
)
result_text = tk.StringVar(value=initial_msg)
result_label = tk.Label(
    root,
    textvariable=result_text,
    font=("Courier", 10),
    justify="left",
    bg="#f4f4f4",
    relief="groove",
    padx=15,
    pady=8,
)
result_label.pack(pady=8, fill="x", padx=25)

# Embedded Matplotlib Line Chart
fig, ax = plt.subplots(figsize=(6, 3.5), dpi=100)
canvas = FigureCanvasTkAgg(fig, master=root)
canvas.get_tk_widget().pack(fill="both", expand=True, padx=20, pady=10)

# Draw saved data immediately upon opening
update_chart()

root.mainloop()