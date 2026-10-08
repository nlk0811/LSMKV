a = list(map(int, input("enter the list of numbers:").split()))
a = a.sort()
n = int(input("enter the number to find:"))

def bi(arr, num, exes=0):
    b = len(arr)
    mid = (0+b-1)//2
    if arr[mid] != num and mid == 1:
        return None
    if arr[mid] == num:
        return mid + exes
    else:
        if arr[mid]>num:
            return bi(arr[:mid],num)
        else:
            exes = exes+len(arr[mid:])-1
            return bi(arr[mid:],num,exes)

sol = bi(a,n)
if sol == None:
    print("number does not exist")
else:
    print("the number is in",sol,"index")