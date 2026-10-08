w = [2,3,5,7,1,4,1]
p = [10,5,15,7,6,18,3]

n = 7
m = 15

pw = []

i = 0
while i<n:
    a = p[i]/w[i]
    pw.append(a)
    i += 1

total_profit = 0

while m > 0 and max(pw) > 0:
    a = max(pw)
    b = pw.index(a)
    if w[b] <= m:
        total_profit += p[b]
        m -= w[b]
    else:
        total_profit += a * m
        m = 0
    pw[b] = 0

print("Total Profit:", total_profit)
