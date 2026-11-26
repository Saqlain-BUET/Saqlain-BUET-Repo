print("Choose from below")
print("1. The value is Base Value")
print("2. The value is  Total Value")
print("3. The value is  Payment Value")

choice = int(input("Enter your choice: "))

amount = float(input("Enter your amount: "))
vat_per = float(input("Enter your vat: "))/100
tax_per = float(input("Enter your tax: "))/100

if  choice == 1:
    base_value = amount
elif choice == 2:
    base_value = amount * 100 / (100+vat_per*100)
elif choice == 3:
    base_value = amount * 100 / (100-tax_per*100)


vat_amount = base_value * vat_per
tax_amount =  base_value * tax_per
total_amount = base_value + vat_amount + tax_amount
payment_amount = base_value - tax_amount

print("Base Value", base_value)
print("VAT amount is: ", vat_amount)
print("TAX amount is: ", tax_amount)
print("Total amount is: ", total_amount)
print("Payment amount is: ", payment_amount)