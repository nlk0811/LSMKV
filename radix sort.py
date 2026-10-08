n = [4721, 1354, 8236, 2415, 5632, 7198 ]

a = n[0]
b = 0
while a > 0:
    a = a//10**b
    b = b+1
c = []
for j in range(0,b+1):
    c = [[] for _ in range(10)]
    for i in n:
        p = 10**j
        z = (i // p) % 10
        c[z].append(i)

    n = []
    for k in range(10):
        n.extend(c[k])
    print(n)

print(n)

     