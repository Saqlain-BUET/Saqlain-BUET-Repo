S = int(input("Put the Value of S: " ))
T = int(input("Put the Value of T: " ))

Subs = S-T

print(Subs)
S = S-Subs
T = T+Subs

print(S, T)
