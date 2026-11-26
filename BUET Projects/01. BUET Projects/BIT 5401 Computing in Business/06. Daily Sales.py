Sales = float(input("Enter today's Sales: "))
ProductPrice = float(input("Enter Product Price : "))

print(f"Todays Sales: {Sales * ProductPrice}")

Expenses = float(input("Enter Expenses : "))
TotalSales = Sales * ProductPrice

DayEndBalance = TotalSales - Expenses
print(f"DayEndBalance: {DayEndBalance}")

if DayEndBalance > 0:
    print(f"DayEndBalance: We made {DayEndBalance} taka profit today")
else:
    print(f"DayEndBalance: We lost {DayEndBalance} taka today")

if DayEndBalance > 0:
    print("Profit")
elif DayEndBalance < 0:
    print("Loss")
