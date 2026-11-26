for i in range (3):
    password = input("Enter Password: ")

    if password == "Secret":
        print("Password is Accepted, Access Granted 🫠")
        break
    else:
        print("Password is Rejected, Try Again")
print("Ending Password Test")