import getpass

name = (input("Enter your Name: "))
age = int(input("Enter your Age: "))
password = getpass.getpass('Enter your Password: ')

print(name)
print(age)
print(password)