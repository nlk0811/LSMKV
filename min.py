a = [29, 10, 14, 37, 13]
for i in range(len(a)):
    n = a[i]
    index = 0
    for j in range(i,len(a)):
        if a[j] <= n:
            n = a[j]
            index = j+1
    a.insert(i,n)
    a.pop(index)
print(a)