a = list(map(int, input("List of numbers:").split()))
n = int(input("number to find:"))
for i in range(len(a)):
    if n==(a[i]):
        print("number is found in", i, "index")
        break