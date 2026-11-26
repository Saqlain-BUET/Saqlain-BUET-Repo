print("WELCOME TO VAT AND TAX CALCULATOR")

BASE = float(input("Please enter your BASE value: "))

VAT = float(input("Please enter your VAT %: "))

Tax = float(input("Please enter your Tax *: "))

VATAmount = BASE * VAT
TaxAmount = BASE * Tax
Total = VATAmount + BASE

print(f"Base Value is {BASE}")
print(f"Total Tax is {TaxAmount}")
print(f"Total VAT is {VATAmount}")
print(f"Grand Total is {Total}")
print()