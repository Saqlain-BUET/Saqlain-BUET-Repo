unite = int(input("Enter your Units: "))

if unite <= 100:
    print("Your total Unite is: ", unite)
    print("Your total Unite is: ", unite * 5)
elif unite > 100 and unite <= 200:
    print("Your Total Expense is: ", unite)
    print("Your Total Expense is: ", (100 * 5) + ((unite - 100) * 7))
elif unite >200:
    print("Your Total Unite is: ", (unite))
    print("Your Total Unite is: ", (100 * 5) + (100 * 7) + ((unite - 200) * 10))
