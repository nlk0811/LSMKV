data = "B C C A B B D D A E C C B B A E D D C C"
chars = data.split()
n = len(chars)

uc = []
freq = []
i = 0
while i < n:
    if chars[i] in uc:
        j = uc.index(chars[i])
        freq[j] += 1
    else:
        uc.append(chars[i])
        freq.append(1)
    i += 1

nodes = []
i = 0
while i < len(uc):
    nodes.append([freq[i], uc[i], None, None])
    i += 1

while len(nodes) > 1:
    mi = 0
    j = 1
    while j < len(nodes):
        if nodes[j][0] < nodes[mi][0]:
            mi = j
        j += 1
    left = nodes.pop(mi)

    mi = 0
    j = 1
    while j < len(nodes):
        if nodes[j][0] < nodes[mi][0]:
            mi = j
        j += 1
    right = nodes.pop(mi)

    nodes.append([left[0] + right[0], "", left, right])

root = nodes[0]

codes = []
stack = [[root, ""]]
while len(stack) > 0:
    cur = stack.pop()
    node = cur[0]
    code = cur[1]
    if node[2] is None and node[3] is None:
        codes.append([node[1], code])
    else:
        if node[2] is not None:
            stack.append([node[2], code + "0"])
        if node[3] is not None:
            stack.append([node[3], code + "1"])

print("Character Frequencies:")
i = 0
while i < len(uc):
    print(uc[i], ":", freq[i])
    i += 1

print("\nHuffman Codes:")
i = 0
while i < len(codes):
    print(codes[i][0], ":", codes[i][1])
    i += 1

encoded = ""
i = 0
while i < n:
    j = 0
    while j < len(codes):
        if codes[j][0] == chars[i]:
            encoded += codes[j][1]
        j += 1
    i += 1

print("\nEncoded String:", encoded)
