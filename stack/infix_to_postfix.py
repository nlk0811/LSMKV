"""Convert an infix expression to postfix using a stack."""

PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2, "^": 3}
RIGHT_ASSOCIATIVE = {"^"}


def to_post(expr):
    out = []
    ops = []

    toks = tok(expr)

    for t in toks:
        if opnd(t):
            out.append(t)
        elif t == "(":
            ops.append(t)
        elif t == ")":
            while ops and ops[-1] != "(":
                out.append(ops.pop())
            if not ops:
                raise ValueError("Mismatched parentheses.")
            ops.pop()
        elif t in PRECEDENCE:
            while (
                ops
                and ops[-1] != "("
                and (
                    PRECEDENCE[ops[-1]] > PRECEDENCE[t]
                    or (
                        PRECEDENCE[ops[-1]] == PRECEDENCE[t]
                        and t not in RIGHT_ASSOCIATIVE
                    )
                )
            ):
                out.append(ops.pop())
            ops.append(t)
        else:
            raise ValueError(f"Invalid token: {t}")

    while ops:
        if ops[-1] in "()":
            raise ValueError("Mismatched parentheses.")
        out.append(ops.pop())

    return " ".join(out)


def opnd(t):
    if t.isdigit():
        return True
    if len(t) > 1 and t.replace(".", "", 1).isdigit():
        return True
    return t.isalpha()


def tok(expr):
    t = []
    a = ""

    for c in expr.replace(" ", ""):
        if c.isalnum() or c == ".":
            a += c
        else:
            if a:
                t.append(a)
                a = ""
            t.append(c)

    if a:
        t.append(a)

    return t


def main():
    print("Infix to Postfix Converter")
    print("Supports +, -, *, /, ^ and multi-digit numbers.")
    print("Enter expressions (empty line to quit).\n")

    while True:
        expr = input("Infix expression: ").strip()
        if not expr:
            break

        try:
            postfix = to_post(expr)
            print(f"Postfix: {postfix}\n")
        except ValueError as error:
            print(f"Error: {error}\n")


if __name__ == "__main__":
    main()
