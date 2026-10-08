"""Check whether an expression with (), {}, [] is balanced using a stack."""


def balanced(expr):
    s = []
    p = {')': '(', '}': '{', ']': '['}
    o = set(p.values())

    for c in expr:
        if c in o:
            s.append(c)
        elif c in p:
            if not s or s.pop() != p[c]:
                return False

    return len(s) == 0


def main():
    print("Balanced Parentheses Checker")
    print("Enter expressions to check.\n")

    while True:
        expr = input("Expression: ").strip()
        if not expr:
            break

        if balanced(expr):
            print("Result: Balanced\n")
        else:
            print("Result: Not Balanced\n")


if __name__ == "__main__":
    main()
