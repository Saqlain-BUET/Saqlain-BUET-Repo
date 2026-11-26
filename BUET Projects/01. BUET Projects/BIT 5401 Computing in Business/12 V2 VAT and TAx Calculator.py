import tkinter as tk
from tkinter import messagebox, ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt

# List to store every calculation record
records = []


def update_chart():
    ax.clear()

    if not records:
        ax.set_title("Calculation History (No data yet)")
        canvas.draw()
        return

    labels = [f"#{i + 1}" for i in range(len(records))]
    x = range(len(records))
    width = 0.16  # Width of each bar

    bases = [r["base"] for r in records]
    vats = [r["vat"] for r in records]
    taxes = [r["tax"] for r in records]
    totals = [r["total"] for r in records]
    payments = [r["payment"] for r in records]

    # Plot grouped bars side-by-side
    ax.bar([i - 2 * width for i in x], bases, width, label="Base", color="#1976d2")
    ax.bar([i - width for i in x], vats, width, label="VAT", color="#ffa000")
    ax.bar(x, taxes, width, label="Tax", color="#d32f2f")
    ax.bar([i + width for i in x], totals, width, label="Total", color="#388e3c")
    ax.bar([i + 2 * width for i in x], payments, width, label="Payment", color="#7b1fa2")

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Amount (Taka)")
    ax.set_title("Recorded Calculations Comparison")
    ax.legend(loc="upper left", fontsize=8, ncol=3)
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    fig.tight_layout()
    canvas.draw()


def calculate():
    try:
        choice = input_type_var.get()
        amount = float(amount_entry.get())
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

        # Save record to history list
        records.append({
            "base": base,
            "vat": vat_amount,
            "tax": tax_amount,
            "total": total_amount,
            "payment": payment_value,
        })

        # Update text summary
        result_text.set(
            f"Record #{len(records)} Saved:\n"
            f"Base Value     : {base:.2f} Taka\n"
            f"VAT ({vat_percent}%)    : {vat_amount:.2f} Taka\n"
            f"TAX ({tax_percent}%)    : {tax_amount:.2f} Taka\n"
            f"Total Amt      : {total_amount:.2f} Taka\n"
            f"Payment Value  : {payment_value:.2f} Taka"
        )

        # Redraw graph with new record
        update_chart()

    except ValueError:
        messagebox.showerror("Invalid Input", "Please enter valid numbers in all fields.")


def clear_history():
    records.clear()
    result_text.set("History cleared. Enter values and click Calculate.")
    update_chart()


# Main Window Setup
root = tk.Tk()
root.title("VAT & Tax Calculator with Live Chart")
root.geometry("620x720")

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

tk.Label(input_frame, text="VAT %:", font=("Arial", 10)).grid(row=2, column=0, sticky="e", padx=5, pady=4)
vat_entry = tk.Entry(input_frame, width=25)
vat_entry.grid(row=2, column=1, pady=4)

tk.Label(input_frame, text="Tax %:", font=("Arial", 10)).grid(row=3, column=0, sticky="e", padx=5, pady=4)
tax_entry = tk.Entry(input_frame, width=25)
tax_entry.grid(row=3, column=1, pady=4)

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
result_text = tk.StringVar(value="Enter values and click Calculate to record...")
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

# Embedded Matplotlib Chart Frame
fig, ax = plt.subplots(figsize=(6, 3.5), dpi=100)
ax.set_title("Calculation History (No data yet)")
canvas = FigureCanvasTkAgg(fig, master=root)
canvas.get_tk_widget().pack(fill="both", expand=True, padx=20, pady=10)

root.mainloop()