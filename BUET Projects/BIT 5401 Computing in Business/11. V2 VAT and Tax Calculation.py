import tkinter as tk
from tkinter import messagebox, ttk


def calculate():
    try:
        choice = input_type_var.get()
        amount = float(amount_entry.get())
        vat_percent = float(vat_entry.get())
        tax_percent = float(tax_entry.get())

        vat_rate = vat_percent / 100
        tax_rate = tax_percent / 100

        # Calculate Base Value based on selected dropdown option
        if choice == "Base Value":
            base = amount
        elif choice == "Total Amount":
            base = amount / (1 + vat_rate + tax_rate)
        elif choice == "Payment Value":
            if tax_rate >= 1:
                messagebox.showerror(
                    "Math Error", "Tax % cannot be 100% or more."
                )
                return
            base = amount / (1 - tax_rate)

        # Calculate remaining values
        vat_amount = base * vat_rate
        tax_amount = base * tax_rate
        total_amount = base + vat_amount + tax_amount
        payment_value = base - tax_amount

        # Update result labels inside the window
        result_text.set(
            f"Base Value     : {base:.2f} Taka\n"
            f"VAT ({vat_percent}%)    : {vat_amount:.2f} Taka\n"
            f"TAX ({tax_percent}%)    : {tax_amount:.2f} Taka\n"
            f"Total Amt      : {total_amount:.2f} Taka\n"
            f"Payment Value  : {payment_value:.2f} Taka"
        )

    except ValueError:
        messagebox.showerror(
            "Invalid Input", "Please enter valid numbers in all fields."
        )


# Create main window
root = tk.Tk()
root.title("VAT & Tax Calculator")
root.geometry("360x380")
root.resizable(False, False)

# Input Type Dropdown
tk.Label(root, text="Select Input Type:", font=("Arial", 10, "bold")).pack(
    pady=(15, 2)
)
input_type_var = tk.StringVar(value="Base Value")
dropdown = ttk.Combobox(
    root,
    textvariable=input_type_var,
    values=["Base Value", "Total Amount", "Payment Value"],
    state="readonly",
    width=25,
)
dropdown.pack(pady=5)

# Amount Input
tk.Label(root, text="Enter Amount:", font=("Arial", 10)).pack(pady=(8, 2))
amount_entry = tk.Entry(root, width=28)
amount_entry.pack()

# VAT % Input
tk.Label(root, text="Enter VAT %:", font=("Arial", 10)).pack(pady=(8, 2))
vat_entry = tk.Entry(root, width=28)
vat_entry.pack()

# Tax % Input
tk.Label(root, text="Enter Tax %:", font=("Arial", 10)).pack(pady=(8, 2))
tax_entry = tk.Entry(root, width=28)
tax_entry.pack()

# Calculate Button
calc_btn = tk.Button(
    root,
    text="Calculate",
    command=calculate,
    bg="#2e7d32",
    fg="white",
    font=("Arial", 10, "bold"),
    width=18,
)
calc_btn.pack(pady=15)

# Results Box
result_text = tk.StringVar(value="Results will appear here...")
result_label = tk.Label(
    root,
    textvariable=result_text,
    font=("Courier", 10),
    justify="left",
    bg="#f4f4f4",
    relief="groove",
    padx=15,
    pady=10,
)
result_label.pack(pady=5, fill="x", padx=20)

# Keep window open
root.mainloop()