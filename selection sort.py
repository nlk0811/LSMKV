a = list(map(int,input().spllit()))
for i in range(len(a)):
    min = a[i]
    index = 0
    for j in range(i,len(a)):
        if a[j] <= min:
            min = a[j]
            index = j+1
    a.insert(i,min)
    a.pop(index)
print(a)