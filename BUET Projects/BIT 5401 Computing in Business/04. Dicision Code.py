Total = float(input("Total Purchased amount: "))
Membership_Status = input("Is he/she a member: ")

if Membership_Status == "YES":
    Final = Total - (Total * 0.1)
else:
    Final = Total

print(Final)