Income = int (input("Enter your Income: "))
Expense = int (input("Enter your Expense: "))

Eligibility = Income >= 30000 and Expense < 25000

if Income >= 30000:
    print("Income is greater than 30000")
else:
    print("Income is less than 30000")

if Expense < 25000:
    print("Expense is Less than 25000")
else:
    print("Expense is greater than 25000")

print(Eligibility)
print(Income)
print(Expense)