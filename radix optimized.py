n = [52319488, 10485762, 99999999, 10000000, 45671234, 52312000]

largest = max(n)
place = 1

while largest//place >0:
    c = [[] for _ in range(10)]

    for i in n:
        a = (i // place)%10
        c[a].append(i)

    n = []
    for k in c:
       for num in k:
           n.append(num)

    place *= 10

print(n)