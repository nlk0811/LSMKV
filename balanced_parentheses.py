"""Check whether an expression with (), {}, [] is balanced using a stack."""


def is_balanced(expression):
    stack = []
    pairs = {")": "(", "}": "{", "]": "["}
    opening = set(pairs.values())

    for char in expression:
        if char in opening:
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False

    return len(stack) == 0


def main():
    print("Balanced Parentheses Checker")
    print("Enter expressions to check (empty line to quit).\n")

    while True:
        expression = input("Expression: ").strip()
        if not expression:
            break

        if is_balanced(expression):
            print("Result: Balanced\n")
        else:
            print("Result: Not Balanced\n")


if __name__ == "__main__":
    main()
