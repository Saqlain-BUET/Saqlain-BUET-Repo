#S = int(input("Enter the total seconds: "))

#M = S/60

#H = M/60

#print("Total Hours for th given Second is", H)
#print("Total Minutes for th given Second is", M)
#print("Total Seconds for th given Second is", S)

input_second = int(input("Enter the Total Seconds: "))
hr = input_second // 3600
remaining = input_second % 3600
mn = remaining // 60
remaining = remaining % 60

print(f"{hr} hour, {mn} minute, {remaining} seconds")